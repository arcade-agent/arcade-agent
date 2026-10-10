"""Rust parser using tree-sitter.

The parser follows Rust's file-module convention (``lib.rs``/``main.rs`` at
the crate root, ``mod.rs`` for a directory module) and extracts structs,
enums, unions, traits, type aliases, functions, and methods.  A second pass
resolves ``use`` declarations, same-module references, qualified paths, and
trait inheritance/implementations into dependency edges. Cargo workspaces are
kept intact and each member crate gets a stable graph prefix.

Rust unit tests are conventionally written inline as ``#[cfg(test)] mod tests``
inside the production file, so path-based test exclusion never sees them. When
``exclude_tests`` is set (the default), test-only code is left out of the graph;
with ``exclude_tests=False`` it is extracted like any other code. Five shapes
are recognized:

* ``#[cfg(test)]`` on any item (module, type, function, impl, ``use``);
* compound predicates such as ``#[cfg(all(test, feature = "x"))]`` and
  ``#[cfg(any(test, fuzzing))]`` (``#[cfg(not(test))]`` stays production);
* an inner ``#![cfg(test)]`` at the top of a file or module body, which makes
  the whole container test-only;
* out-of-line ``#[cfg(test)] mod helpers;``, whose backing ``helpers.rs`` /
  ``helpers/mod.rs`` file is skipped instead of being parsed as production;
* ``#[cfg(test)] use ...``, so dev-dependency imports (mockall, proptest,
  rstest, ...) never appear on production entities.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tree_sitter_rust as tsrust
from tree_sitter import Language, Node, Parser

from arcade_agent.parsers.base import LanguageParser, register_parser
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity

RUST_LANGUAGE = Language(tsrust.language())
logger = logging.getLogger(__name__)

_TYPE_ITEMS = {
    "struct_item": "struct",
    "enum_item": "enum",
    "union_item": "union",
    "trait_item": "trait",
    "type_item": "type",
}


@dataclass(frozen=True)
class _Import:
    path: tuple[str, ...]
    alias: str
    wildcard: bool = False

    @property
    def display(self) -> str:
        suffix = "::*" if self.wildcard else ""
        return "::".join(self.path) + suffix


@dataclass
class _References:
    simple: set[str]
    qualified: set[tuple[str, ...]]


@dataclass
class _PendingMethod:
    name: str
    references: _References


@dataclass
class _PendingImpl:
    owner_path: tuple[str, ...]
    trait_path: tuple[str, ...]
    generic_parameters: frozenset[str]
    module: tuple[str, ...]
    crate: tuple[str, ...]
    imports: list[_Import]
    rel_path: str
    methods: list[_PendingMethod]


def _get_text(node: Node | None) -> str:
    raw_text = None if node is None else node.text
    return "" if raw_text is None else raw_text.decode(errors="replace")


def _identifier(text: str) -> str:
    """Normalize raw Rust identifiers (``r#type`` -> ``type``)."""
    return text[2:] if text.startswith("r#") else text


def _cargo_data(directory: Path) -> dict[str, Any]:
    manifest = directory / "Cargo.toml"
    try:
        if not manifest.is_file():
            return {}
        with manifest.open("rb") as manifest_file:
            return tomllib.load(manifest_file)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def _crate_context(file_path: Path, root: Path, is_workspace: bool) -> tuple[Path, tuple[str, ...]]:
    """Return the source root and graph prefix for the file's Cargo crate."""
    if not is_workspace:
        conventional_source = root / "src"
        if conventional_source in file_path.parents:
            return conventional_source, ()
        return root, ()

    current = file_path.parent
    crate_dir: Path | None = None
    while current == root or root in current.parents:
        if (current / "Cargo.toml").is_file():
            crate_dir = current
            break
        if current == root:
            break
        current = current.parent
    if crate_dir is None:
        return root, ()

    package = _cargo_data(crate_dir).get("package", {})
    crate_name = str(package.get("name", crate_dir.name)).replace("-", "_")
    source_root = crate_dir / "src"
    if source_root not in file_path.parents:
        source_root = crate_dir
    return source_root, (_identifier(crate_name),)


def _module_name(file_path: Path, source_root: Path, crate: tuple[str, ...]) -> tuple[str, ...]:
    """Return the Rust module path represented by a source file."""
    rel = file_path.relative_to(source_root)
    parts = list(rel.parts)
    stem = Path(parts[-1]).stem
    directory = parts[:-1]
    if stem in {"lib", "main"}:
        return (*crate, *directory)
    if stem == "mod":
        return (*crate, *directory)
    return tuple([*crate, *directory, stem])


def _path_segments(node: Node | None) -> tuple[str, ...]:
    """Extract ``a::b::Name`` segments from a path-like AST node."""
    if node is None:
        return ()

    segments: list[str] = []
    stack = [node]
    leaf_types = {
        "identifier",
        "type_identifier",
        "crate",
        "self",
        "super",
        "metavariable",
    }
    while stack:
        current = stack.pop()
        if current.type in leaf_types:
            segments.append(_identifier(_get_text(current)))
            continue

        path = current.child_by_field_name("path")
        name = current.child_by_field_name("name")
        if path is not None or name is not None:
            # LIFO order: visit the path before its final name.
            if name is not None:
                stack.append(name)
            if path is not None:
                stack.append(path)
            continue

        # use_wildcard has no named fields in tree-sitter-rust 0.24.
        if current.type == "use_wildcard" and current.named_children:
            stack.append(current.named_children[0])
            continue

        # References through generic paths only need the base type name.
        if current.type == "generic_type":
            generic_type = current.child_by_field_name("type")
            if generic_type is not None:
                stack.append(generic_type)

    return tuple(segments)


def _flatten_use(node: Node, prefix: tuple[str, ...] = ()) -> list[_Import]:
    """Flatten a Rust use tree into concrete imports."""
    imports: list[_Import] = []
    pending: list[tuple[Node, tuple[str, ...]]] = [(node, prefix)]
    while pending:
        current, current_prefix = pending.pop()

        if current.type == "use_declaration":
            argument = current.child_by_field_name("argument")
            if argument is not None:
                pending.append((argument, current_prefix))
            continue

        if current.type == "scoped_use_list":
            path = (
                *current_prefix,
                *_path_segments(current.child_by_field_name("path")),
            )
            use_list = current.child_by_field_name("list")
            if use_list is not None:
                pending.extend((child, path) for child in reversed(use_list.named_children))
            continue

        if current.type == "use_list":
            pending.extend((child, current_prefix) for child in reversed(current.named_children))
            continue

        if current.type == "self" and current_prefix:
            imports.append(_Import(path=current_prefix, alias=current_prefix[-1]))
            continue

        if current.type == "use_as_clause":
            path = (
                *current_prefix,
                *_path_segments(current.child_by_field_name("path")),
            )
            if not path:
                continue
            alias_node = current.child_by_field_name("alias")
            alias = _identifier(_get_text(alias_node)) if alias_node is not None else path[-1]
            imports.append(_Import(path=path, alias=alias))
            continue

        if current.type == "use_wildcard":
            path = (*current_prefix, *_path_segments(current))
            if path:
                imports.append(_Import(path=path, alias="*", wildcard=True))
            continue

        path = (*current_prefix, *_path_segments(current))
        if path:
            imports.append(_Import(path=path, alias=path[-1]))

    return imports


_ATTRIBUTE_ITEMS = {"attribute_item", "inner_attribute_item"}
_COMMENT_ITEMS = {"line_comment", "block_comment"}


def _cfg_predicate_enables_test(token_tree: Node) -> bool:
    """Whether a ``cfg(...)`` predicate is satisfied by the ``test`` cfg flag.

    ``cfg(test)``, ``cfg(all(test, unix))`` and ``cfg(any(test, fuzzing))`` all
    gate test-only code, so the whole predicate tree is searched rather than
    string-matching ``cfg(test)``. ``not(...)`` sub-predicates are skipped:
    ``#[cfg(not(test))]`` marks production-only code and must not be dropped.
    """
    children = token_tree.named_children
    index = 0
    while index < len(children):
        child = children[index]
        following = children[index + 1] if index + 1 < len(children) else None
        if child.type == "identifier" and following is not None and following.type == "token_tree":
            # A predicate function: all(...), any(...), not(...).
            if _get_text(child) != "not" and _cfg_predicate_enables_test(following):
                return True
            index += 2
            continue
        if child.type == "identifier" and _get_text(child) == "test":
            return True
        if child.type == "token_tree" and _cfg_predicate_enables_test(child):
            return True
        index += 1
    return False


def _is_cfg_test_attribute(node: Node) -> bool:
    """Return whether an attribute gates its item on the ``test`` cfg flag."""
    if node.type not in _ATTRIBUTE_ITEMS:
        return False
    for child in node.named_children:
        if child.type != "attribute":
            continue
        parts = child.named_children
        if not parts or _get_text(parts[0]) != "cfg":
            continue
        if any(
            part.type == "token_tree" and _cfg_predicate_enables_test(part) for part in parts[1:]
        ):
            return True
    return False


def _has_inner_cfg_test(container: Node) -> bool:
    """Whether a file or module body is wholly test-only via ``#![cfg(test)]``."""
    return any(
        child.type == "inner_attribute_item" and _is_cfg_test_attribute(child)
        for child in container.named_children
    )


def _extract_imports(container: Node, exclude_tests: bool) -> list[_Import]:
    """Collect ``use`` declarations, optionally dropping test-only ones.

    ``#[cfg(test)] use mockall::predicate::Eq;`` is a dev-dependency import. It
    is attached to no production entity, so including it would give every
    entity in the file phantom coupling to the test-only crate.
    """
    imports: list[_Import] = []
    pending_attributes: list[Node] = []
    for child in container.named_children:
        if child.type == "attribute_item":
            pending_attributes.append(child)
            continue
        if child.type in _COMMENT_ITEMS or child.type == "inner_attribute_item":
            continue
        is_cfg_test = any(_is_cfg_test_attribute(item) for item in pending_attributes)
        pending_attributes.clear()
        if child.type != "use_declaration":
            continue
        if is_cfg_test and exclude_tests:
            continue
        imports.extend(_flatten_use(child))
    return imports


def _module_file_order(file_path: Path) -> tuple[int, int, str]:
    """Order files so a module's declaring file is always parsed before it.

    ``#[cfg(test)] mod helpers;`` can only exclude ``helpers.rs`` if the
    declaring file (``lib.rs``/``mod.rs``/the parent module file) has already
    been visited. Parents are either shallower or, at equal depth, a crate/
    directory root.
    """
    return (
        len(file_path.parts),
        0 if file_path.stem in {"lib", "main", "mod"} else 1,
        str(file_path),
    )


def _child_module_files(module_dir: Path, name: str) -> tuple[Path, Path]:
    """Candidate files backing an out-of-line ``mod <name>;`` declaration."""
    return module_dir / f"{name}.rs", module_dir / name / "mod.rs"


def _references(node: Node) -> _References:
    simple: set[str] = set()
    qualified: set[tuple[str, ...]] = set()
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in {"scoped_identifier", "scoped_type_identifier"}:
            path = _path_segments(current)
            if len(path) > 1:
                qualified.add(path)
                # The leading segment drives import-alias resolution. The
                # immediate owner prefix preserves references such as
                # ``Type::associated_item`` without recomputing every nested
                # scoped path (which is quadratic for generated long paths).
                simple.add(path[0])
                if len(path) > 2:
                    qualified.add(path[:-1])
                continue
        elif current.type in {"identifier", "type_identifier"}:
            simple.add(_identifier(_get_text(current)))
        stack.extend(current.named_children)
    return _References(simple=simple, qualified=qualified)


def _base_type_path(node: Node | None) -> tuple[str, ...]:
    """Get the implemented type path without generic arguments."""
    if node is None:
        return ()

    stack = [node]
    wrapper_types = {"reference_type", "pointer_type", "array_type", "slice_type"}
    while stack:
        current = stack.pop()
        # Wrapper nodes can contain lifetimes before the actual type. Follow
        # the explicit type field so ``&'a mut T`` resolves to T, not ``a``.
        if current.type in wrapper_types:
            wrapped_type = current.child_by_field_name("type")
            if wrapped_type is not None:
                stack.append(wrapped_type)
            continue

        direct = _path_segments(current)
        if direct:
            return direct
        stack.extend(reversed(current.named_children))
    return ()


def _generic_type_parameters(node: Node) -> frozenset[str]:
    """Return type parameter names declared by an impl (excluding lifetimes)."""
    parameters = node.child_by_field_name("type_parameters")
    if parameters is None:
        return frozenset()
    return frozenset(
        _identifier(_get_text(name))
        for parameter in parameters.named_children
        if parameter.type == "type_parameter"
        for name in [parameter.child_by_field_name("name")]
        if name is not None
    )


def _normalize_path(
    path: tuple[str, ...],
    current: tuple[str, ...],
    crate: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if not path:
        return ()
    parts = list(path)
    if parts[0] == "crate":
        return (*crate, *parts[1:])
    if parts[0] == "self":
        return (*current, *parts[1:])
    if parts[0] == "super":
        base = list(current)
        while parts and parts[0] == "super":
            if base:
                base.pop()
            parts.pop(0)
        return (*base, *parts)
    return (*current, *parts)


def _deduplicate(edges: list[Edge]) -> list[Edge]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[Edge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.relation)
        if edge.source != edge.target and key not in seen:
            seen.add(key)
            unique.append(edge)
    return unique


@register_parser
class RustParser(LanguageParser):
    """Rust source code parser using tree-sitter.

    Honors ``exclude_tests`` (default ``True``) by skipping inline test-only
    code, which path-based test exclusion cannot reach. See the module
    docstring for the recognized ``#[cfg(test)]`` shapes.
    """

    @property
    def language(self) -> str:
        return "rust"

    @property
    def file_extensions(self) -> list[str]:
        return [".rs"]

    def parse(self, files: list[Path], root: Path) -> DependencyGraph:
        parser = Parser(RUST_LANGUAGE)
        root = root.resolve()

        entities: dict[str, Entity] = {}
        packages: dict[str, list[str]] = {}
        # Companion membership index for ``packages``. ``fqn not in list`` is
        # linear, which made ``add_entity`` quadratic in the number of entities
        # per package (minutes on multi-MB generated sources).
        package_members: dict[str, set[str]] = {}
        entity_refs: dict[str, _References] = {}
        entity_imports: dict[str, list[_Import]] = {}
        entity_crates: dict[str, tuple[str, ...]] = {}
        pending_impls: list[_PendingImpl] = []
        pending_trait_bounds: list[
            tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...], list[_Import]]
        ] = []
        is_workspace = "workspace" in _cargo_data(root)
        # Files backing an out-of-line ``#[cfg(test)] mod x;`` declaration.
        # Populated while visiting the declaring file, which ``_module_file_order``
        # guarantees is visited first.
        excluded_module_files: set[Path] = set()

        def add_entity(
            *,
            name: str,
            kind: str,
            module: tuple[str, ...],
            rel_path: str,
            imports: list[_Import],
            node: Node | None,
            crate: tuple[str, ...],
            owner: str | None = None,
            fqn_override: str | None = None,
            references: _References | None = None,
        ) -> str:
            package = ".".join(module)
            fqn = fqn_override or (f"{package}.{name}" if package else name)
            entities[fqn] = Entity(
                fqn=fqn,
                name=name,
                package=package,
                file_path=rel_path,
                kind=kind,
                language="rust",
                imports=[item.display for item in imports],
                properties={"owner": owner} if owner else {},
            )
            package_entities = packages.setdefault(package, [])
            members = package_members.setdefault(package, set())
            if fqn not in members:
                members.add(fqn)
                package_entities.append(fqn)
            if references is not None:
                entity_refs[fqn] = references
            elif node is not None:
                entity_refs[fqn] = _references(node)
            else:
                entity_refs[fqn] = _References(set(), set())
            entity_imports[fqn] = imports
            entity_crates[fqn] = crate
            return fqn

        def visit_container(
            container: Node,
            module: tuple[str, ...],
            rel_path: str,
            file_stem: str,
            crate: tuple[str, ...],
            module_dir: Path,
            is_file_root: bool = False,
        ) -> None:
            container_stack = [(container, module, module_dir, is_file_root)]
            while container_stack:
                (
                    current_container,
                    current_module,
                    current_dir,
                    current_is_file_root,
                ) = container_stack.pop()

                # ``#![cfg(test)]`` at the top of a file or module body makes the
                # whole container test-only; there is no outer attribute to see.
                if self.exclude_tests and _has_inner_cfg_test(current_container):
                    continue

                imports = _extract_imports(current_container, self.exclude_tests)
                direct_entities = 0
                nested_containers: list[tuple[Node, tuple[str, ...], Path, bool]] = []
                pending_attributes: list[Node] = []

                for node in current_container.named_children:
                    if node.type == "attribute_item":
                        pending_attributes.append(node)
                        continue

                    # Comments between an attribute and its item must not
                    # clear the pending attribute list (tree-sitter emits
                    # line_comment/block_comment as named children).
                    if node.type in _COMMENT_ITEMS:
                        continue

                    is_cfg_test = any(
                        _is_cfg_test_attribute(attribute) for attribute in pending_attributes
                    )
                    pending_attributes.clear()

                    # Rust unit tests live inline, so path-based test
                    # exclusion in ``ingest`` cannot see them. Drop
                    # ``#[cfg(test)]`` items only when the caller asked
                    # for test code to be excluded.
                    if is_cfg_test and self.exclude_tests:
                        # An out-of-line ``#[cfg(test)] mod helpers;`` carries no
                        # body here — its code lives in a sibling file that would
                        # otherwise be parsed as an independent production file.
                        if node.type == "mod_item" and node.child_by_field_name("body") is None:
                            name_node = node.child_by_field_name("name")
                            if name_node is not None:
                                excluded_module_files.update(
                                    _child_module_files(
                                        current_dir, _identifier(_get_text(name_node))
                                    )
                                )
                        continue

                    if node.type in _TYPE_ITEMS:
                        name_node = node.child_by_field_name("name")
                        if name_node is None:
                            continue
                        name = _identifier(_get_text(name_node))
                        owner = add_entity(
                            name=name,
                            kind=_TYPE_ITEMS[node.type],
                            module=current_module,
                            rel_path=rel_path,
                            imports=imports,
                            node=node,
                            crate=crate,
                        )
                        direct_entities += 1
                        if node.type == "trait_item":
                            bounds = node.child_by_field_name("bounds")
                            if bounds is not None:
                                for bound in bounds.named_children:
                                    bound_path = _base_type_path(bound)
                                    if bound_path:
                                        pending_trait_bounds.append(
                                            (
                                                owner,
                                                bound_path,
                                                current_module,
                                                crate,
                                                imports,
                                            )
                                        )
                            body = node.child_by_field_name("body")
                            if body is not None:
                                for member in body.named_children:
                                    if member.type not in {
                                        "function_item",
                                        "function_signature_item",
                                    }:
                                        continue
                                    method_name = member.child_by_field_name("name")
                                    if method_name is None:
                                        continue
                                    normalized_name = _identifier(_get_text(method_name))
                                    add_entity(
                                        name=normalized_name,
                                        kind="method",
                                        module=current_module,
                                        rel_path=rel_path,
                                        imports=imports,
                                        node=member,
                                        crate=crate,
                                        owner=owner,
                                        fqn_override=f"{owner}.{normalized_name}",
                                    )
                    elif node.type == "function_item":
                        name_node = node.child_by_field_name("name")
                        if name_node is not None:
                            add_entity(
                                name=_identifier(_get_text(name_node)),
                                kind="function",
                                module=current_module,
                                rel_path=rel_path,
                                imports=imports,
                                node=node,
                                crate=crate,
                            )
                            direct_entities += 1
                    elif node.type == "impl_item":
                        type_node = node.child_by_field_name("type")
                        trait_node = node.child_by_field_name("trait")
                        body = node.child_by_field_name("body")
                        owner_path = _base_type_path(type_node)
                        if not owner_path or body is None:
                            continue
                        methods: list[_PendingMethod] = []
                        for member in body.named_children:
                            if member.type != "function_item":
                                continue
                            method_name = member.child_by_field_name("name")
                            if method_name is None:
                                continue
                            name = _identifier(_get_text(method_name))
                            methods.append(
                                _PendingMethod(
                                    name=name,
                                    references=_references(member),
                                )
                            )
                        pending_impls.append(
                            _PendingImpl(
                                owner_path=owner_path,
                                trait_path=_base_type_path(trait_node),
                                generic_parameters=_generic_type_parameters(node),
                                module=current_module,
                                crate=crate,
                                imports=imports,
                                rel_path=rel_path,
                                methods=methods,
                            )
                        )
                    elif node.type == "mod_item":
                        name_node = node.child_by_field_name("name")
                        body = node.child_by_field_name("body")
                        if name_node is not None and body is not None:
                            child_name = _identifier(_get_text(name_node))
                            child_module = (*current_module, child_name)
                            nested_containers.append(
                                (body, child_module, current_dir / child_name, False)
                            )

                container_stack.extend(reversed(nested_containers))
                if current_is_file_root and direct_entities == 0:
                    module_name = ".".join(current_module)
                    name = current_module[-1] if current_module else file_stem
                    fqn = module_name or file_stem
                    add_entity(
                        name=name,
                        kind="module",
                        module=current_module[:-1] if current_module else (),
                        rel_path=rel_path,
                        imports=imports,
                        node=current_container,
                        crate=crate,
                        fqn_override=fqn,
                    )

        all_entities: dict[str, Entity] = {}
        all_packages: dict[str, list[str]] = {}
        all_package_members: dict[str, set[str]] = {}
        all_entity_refs: dict[str, _References] = {}
        all_entity_imports: dict[str, list[_Import]] = {}
        all_entity_crates: dict[str, tuple[str, ...]] = {}
        all_pending_impls: list[_PendingImpl] = []
        all_pending_trait_bounds: list[
            tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...], list[_Import]]
        ] = []

        resolved_files: list[Path] = []
        for candidate in files:
            try:
                resolved_files.append(candidate.resolve())
            except OSError:
                resolved_files.append(candidate)
        resolved_files.sort(key=_module_file_order)

        for source_file in resolved_files:
            if source_file in excluded_module_files:
                continue

            # Extract each file transactionally. If a malformed or adversarial
            # file trips an unexpected parser edge case, discard its partial
            # state and preserve entities from healthy sibling files.
            entities = {}
            packages = {}
            package_members = {}
            entity_refs = {}
            entity_imports = {}
            entity_crates = {}
            pending_impls = []
            pending_trait_bounds = []
            try:
                rel_path = str(source_file.relative_to(root))
                tree = parser.parse(source_file.read_bytes())
                source_root, crate = _crate_context(source_file, root, is_workspace)
                module_dir = (
                    source_file.parent
                    if source_file.stem in {"lib", "main", "mod"}
                    else source_file.parent / source_file.stem
                )
                visit_container(
                    tree.root_node,
                    _module_name(source_file, source_root, crate),
                    rel_path,
                    source_file.stem,
                    crate,
                    module_dir,
                    is_file_root=True,
                )
            except Exception as error:
                logger.warning(
                    "Skipping Rust source after extraction failure (%s): %s",
                    type(error).__name__,
                    source_file,
                )
                continue

            all_entities.update(entities)
            all_entity_refs.update(entity_refs)
            all_entity_imports.update(entity_imports)
            all_entity_crates.update(entity_crates)
            all_pending_impls.extend(pending_impls)
            all_pending_trait_bounds.extend(pending_trait_bounds)
            for package, fqns in packages.items():
                package_entities = all_packages.setdefault(package, [])
                members = all_package_members.setdefault(package, set())
                for fqn in fqns:
                    if fqn not in members:
                        members.add(fqn)
                        package_entities.append(fqn)

        entities = all_entities
        packages = all_packages
        package_members = all_package_members
        entity_refs = all_entity_refs
        entity_imports = all_entity_imports
        entity_crates = all_entity_crates
        pending_impls = all_pending_impls
        pending_trait_bounds = all_pending_trait_bounds

        non_member_entities = [e for e in entities.values() if e.kind != "method"]
        by_module_name = {(e.package, e.name): e.fqn for e in non_member_entities}
        by_simple_name: dict[str, list[str]] = {}
        for entity in non_member_entities:
            by_simple_name.setdefault(entity.name, []).append(entity.fqn)

        def resolve(
            path: tuple[str, ...],
            current_package: str,
            crate: tuple[str, ...] = (),
        ) -> str | None:
            current = tuple(part for part in current_package.split(".") if part)
            candidates: list[tuple[str, ...]] = []
            if path and path[0] in {"crate", "self", "super"}:
                candidates.append(_normalize_path(path, current, crate))
            else:
                candidates.extend([(*current, *path), path])
            for candidate in candidates:
                fqn = ".".join(candidate)
                if fqn in entities:
                    return fqn
            # A qualified path that did not match is external or unresolved;
            # falling back by its final name could link ``std::io::Error`` to
            # an unrelated local ``Error``. Keep the unique-name fallback only
            # for simple paths (notably owners imported through a glob).
            if len(path) == 1:
                matches = by_simple_name.get(path[-1], [])
                if len(matches) == 1:
                    return matches[0]
            return None

        def expand_alias(path: tuple[str, ...], imports: list[_Import]) -> tuple[str, ...]:
            if not path:
                return path
            for item in imports:
                if not item.wildcard and path[0] == item.alias:
                    return (*item.path, *path[1:])
            return path

        # Resolve impl owners only after all type declarations are known. This
        # correctly attaches impls written in sibling modules and avoids graph
        # entities for external/blanket owners such as std::io::Error, Box<T>,
        # or &'a mut T.
        edges: list[Edge] = []
        owner_kinds = {"struct", "enum", "union", "type"}

        for (
            bound_owner,
            bound_path,
            bound_module,
            bound_crate,
            bound_imports,
        ) in pending_trait_bounds:
            package = ".".join(bound_module)
            bound_target = resolve(expand_alias(bound_path, bound_imports), package, bound_crate)
            if bound_target and entities[bound_target].kind == "trait":
                bound_name = "::".join(bound_path)
                if bound_name not in entities[bound_owner].interfaces:
                    entities[bound_owner].interfaces.append(bound_name)
                edges.append(Edge(bound_owner, bound_target, "extends"))

        for pending in pending_impls:
            package = ".".join(pending.module)
            owner_path = expand_alias(pending.owner_path, pending.imports)
            if len(owner_path) == 1 and owner_path[0] in pending.generic_parameters:
                continue
            resolved_owner = resolve(owner_path, package, pending.crate)
            if resolved_owner is None or entities[resolved_owner].kind not in owner_kinds:
                continue

            owner_module = tuple(
                part for part in entities[resolved_owner].package.split(".") if part
            )
            for method in pending.methods:
                add_entity(
                    name=method.name,
                    kind="method",
                    module=owner_module,
                    rel_path=pending.rel_path,
                    imports=pending.imports,
                    node=None,
                    crate=pending.crate,
                    owner=resolved_owner,
                    fqn_override=f"{resolved_owner}.{method.name}",
                    references=method.references,
                )

            if pending.trait_path:
                trait_path = expand_alias(pending.trait_path, pending.imports)
                trait_target = resolve(trait_path, package, pending.crate)
                if trait_target and entities[trait_target].kind == "trait":
                    trait_name = "::".join(pending.trait_path)
                    if trait_name not in entities[resolved_owner].interfaces:
                        entities[resolved_owner].interfaces.append(trait_name)
                    edges.append(Edge(resolved_owner, trait_target, "implements"))

        for fqn, entity in entities.items():
            refs = entity_refs.get(fqn, _References(set(), set()))
            imports = entity_imports.get(fqn, [])
            crate = entity_crates.get(fqn, ())

            for item in imports:
                if item.wildcard:
                    module_path = _normalize_path(
                        item.path,
                        tuple(part for part in entity.package.split(".") if part),
                        crate,
                    )
                    wildcard_module = ".".join(module_path)
                    for ref in refs.simple:
                        wildcard_target = by_module_name.get((wildcard_module, ref))
                        if wildcard_target:
                            edges.append(Edge(fqn, wildcard_target, "import"))
                    continue
                if item.alias not in refs.simple and entity.kind != "module":
                    continue
                import_target = resolve(item.path, entity.package, crate)
                if import_target:
                    edges.append(Edge(fqn, import_target, "import"))

            for ref in refs.simple:
                same_module_target = by_module_name.get((entity.package, ref))
                if same_module_target:
                    edges.append(Edge(fqn, same_module_target, "uses"))

            for qualified_path in refs.qualified:
                qualified_target = resolve(
                    expand_alias(qualified_path, imports), entity.package, crate
                )
                if qualified_target:
                    edges.append(Edge(fqn, qualified_target, "uses"))

        return DependencyGraph(
            entities=entities,
            edges=_deduplicate(edges),
            packages=packages,
            metadata={"relation_coverage": {"rust": {
                "collected_relations": ["extends", "implements", "import", "uses"],
                "call_coverage": "not_collected",
            }}},
        )
