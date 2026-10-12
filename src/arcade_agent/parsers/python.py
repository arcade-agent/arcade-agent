"""Python parser using tree-sitter."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from arcade_agent.parsers.base import LanguageParser, register_parser
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity

PYTHON_LANGUAGE = Language(tspython.language())

# Only these syntax nodes expose annotation fields in tree-sitter-python.
# Keeping this table explicit avoids two child_by_field_name() calls for every
# child in every declaration subtree. On large Python repositories that was
# millions of redundant C-extension round trips in the cold parse path.
_ANNOTATION_FIELDS: dict[str, tuple[str, ...]] = {
    "assignment": ("type",),
    "function_definition": ("return_type",),
    "typed_default_parameter": ("type",),
    "typed_parameter": ("type",),
}

# Names inside a string (forward-reference) annotation: ``"TaskRepository"``,
# ``"list[models.User]"``.
_ANNOTATION_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


def _get_text(node) -> str:
    if node is None:
        return ""
    return node.text.decode()


def _collect_nodes(node, type_name: str) -> list:
    """Recursively collect all descendant nodes of a given type."""
    results = []
    if node.type == type_name:
        results.append(node)
    for child in node.children:
        results.extend(_collect_nodes(child, type_name))
    return results


def _extract_module_name(file_path: Path, root: Path) -> str:
    """Convert a file path to a Python module name."""
    rel = file_path.relative_to(root)
    parts = list(rel.parts)
    # Remove .py extension from last part
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    # Remove __init__ from the end
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported_name(node) -> tuple[str, str]:
    """Return (name, local alias) for a ``dotted_name`` or ``aliased_import`` node."""
    if node.type == "aliased_import":
        name = node.child_by_field_name("name")
        alias = node.child_by_field_name("alias")
        name_text = _get_text(name) if name is not None else ""
        return name_text, _get_text(alias) if alias is not None else name_text
    text = _get_text(node)
    return text, text


# Module-scope statements whose bodies run at import time. Imports nested in
# them (``if TYPE_CHECKING:``, ``try: ... except ImportError:`` fallbacks,
# version checks) are file imports; function and class bodies are not entered.
_IMPORT_CONTAINERS = frozenset({
    "if_statement", "elif_clause", "else_clause", "try_statement", "except_clause",
    "except_group_clause", "finally_clause", "with_statement", "block",
})


def _is_type_checking(condition) -> bool:
    """True for ``TYPE_CHECKING`` and ``typing.TYPE_CHECKING`` (any qualifier)."""
    text = _get_text(condition)
    return text == "TYPE_CHECKING" or text.endswith(".TYPE_CHECKING")


def _extract_imports(root_node) -> list[dict]:
    """Extract the module-scope import statements of a Python file.

    Returns dicts with ``module`` (as written, without leading dots), ``level``
    (number of leading dots; 0 for absolute imports), ``names`` (imported names)
    and ``aliases`` (name -> local alias, only for ``as`` imports). Relative
    modules are resolved against the importing file's package by
    :func:`_resolve_relative_imports`. Imports under ``if TYPE_CHECKING:`` also
    carry ``type_only: True``: they never run, so the linker makes them ``uses``
    edges.
    """
    imports: list[dict] = []

    def visit(node, type_only: bool) -> None:
        for child in node.children:
            if child.type == "import_statement":
                # import foo, import foo.bar, import foo as f
                for sub in child.children:
                    if sub.type in ("dotted_name", "aliased_import"):
                        module, _alias = _imported_name(sub)
                        imports.append(_import_info(module, 0, [], {}, type_only))
            elif child.type == "import_from_statement":
                imports.append(_from_import_info(child, type_only))
            elif child.type == "if_statement":
                guarded = type_only or _is_type_checking(child.child_by_field_name("condition"))
                consequence = child.child_by_field_name("consequence")
                if consequence is not None:
                    visit(consequence, guarded)
                for alternative in child.children_by_field_name("alternative"):
                    visit(alternative, type_only)
            elif child.type in _IMPORT_CONTAINERS:
                visit(child, type_only)

    visit(root_node, False)
    return imports


def _import_info(
    module: str, level: int, names: list[str], aliases: dict[str, str], type_only: bool
) -> dict:
    info = {"module": module, "level": level, "names": names, "aliases": aliases}
    if type_only:
        info["type_only"] = True
    return info


def _from_import_info(node, type_only: bool) -> dict:
    """``from foo import bar, baz as qux``; ``from ..foo import bar``; ``from . import bar``."""
    module_node = node.child_by_field_name("module_name")
    module, level = "", 0
    if module_node is not None and module_node.type == "relative_import":
        for part in module_node.children:
            if part.type == "import_prefix":
                level = len(_get_text(part).strip())
            elif part.type == "dotted_name":
                module = _get_text(part)
    elif module_node is not None:
        module = _get_text(module_node)
    names: list[str] = []
    aliases: dict[str, str] = {}
    for name_node in node.children_by_field_name("name"):
        name, alias = _imported_name(name_node)
        if name:
            names.append(name)
            if alias != name:
                aliases[name] = alias
    return _import_info(module, level, names, aliases, type_only)


def _resolve_relative_imports(
    imports: list[dict], module_name: str, is_package: bool
) -> list[dict]:
    """Rewrite relative imports to absolute module names.

    ``from ..store import x`` in ``app.api.orders`` resolves to ``app.store``;
    in a package ``__init__`` the package itself is the anchor. Imports that
    climb above the parse root are dropped, as Python itself would reject them.
    """
    anchor = module_name.split(".") if module_name else []
    if not is_package:
        anchor = anchor[:-1]
    resolved = []
    for imp in imports:
        level = imp.get("level", 0)
        if level == 0:
            resolved.append(imp)
            continue
        if level - 1 > len(anchor):
            continue
        base = anchor[: len(anchor) - (level - 1)]
        parts = base + ([imp["module"]] if imp["module"] else [])
        resolved.append({**imp, "module": ".".join(parts), "level": 0})
    return resolved


def _unwrap_decorated(node):
    """Unwrap a decorated_definition to get the inner class/function node."""
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("class_definition", "function_definition"):
                return child
    return node


def _extract_classes(root_node) -> list[dict]:
    """Extract class definitions with bases (including decorated classes)."""
    classes = []
    for node in root_node.children:
        actual = _unwrap_decorated(node)
        if actual.type != "class_definition":
            continue
        name_node = actual.child_by_field_name("name")
        if not name_node:
            continue

        bases = []
        superclass = None
        # Find argument_list (bases)
        for child in actual.children:
            if child.type == "argument_list":
                for arg in child.children:
                    if arg.type == "identifier":
                        bases.append(_get_text(arg))
                    elif arg.type == "attribute":
                        bases.append(_get_text(arg))

        if bases:
            superclass = bases[0]

        # Use the outer node (with decorators) so _extract_referenced_names
        # captures decorator arguments too.
        classes.append({
            "name": _get_text(name_node),
            "kind": "class",
            "superclass": superclass,
            "interfaces": bases[1:] if len(bases) > 1 else [],
            "node": node,
        })
    return classes


def _extract_functions(root_node) -> list[dict]:
    """Extract top-level function definitions (including decorated functions)."""
    functions = []
    for node in root_node.children:
        actual = _unwrap_decorated(node)
        if actual.type != "function_definition":
            continue
        name_node = actual.child_by_field_name("name")
        if name_node:
            # Use the outer node (with decorators) so _extract_referenced_names
            # captures decorator arguments too.
            functions.append({
                "name": _get_text(name_node),
                "kind": "function",
                "superclass": None,
                "interfaces": [],
                "node": node,
            })
    return functions


def _extract_methods(classes: list[dict], module_name: str) -> list[dict]:
    """Extract methods declared directly inside classes."""
    methods = []
    for cls in classes:
        actual = _unwrap_decorated(cls["node"])
        body = actual.child_by_field_name("body")
        if body is None:
            continue

        owner_name = cls["name"]
        owner_fqn = f"{module_name}.{owner_name}" if module_name else owner_name
        for node in body.children:
            actual_method = _unwrap_decorated(node)
            if actual_method.type != "function_definition":
                continue

            name_node = actual_method.child_by_field_name("name")
            if not name_node:
                continue

            methods.append({
                "name": _get_text(name_node),
                "kind": "method",
                "superclass": None,
                "interfaces": [],
                "node": node,
                "owner_fqn": owner_fqn,
            })

    return methods


def _add_name(names: set[str], n) -> None:
    """Add an ``identifier``'s text, or an ``attribute``'s dotted text and head."""
    names.add(_get_text(n))
    if n.type == "attribute":
        for child in n.children:
            if child.type == "identifier":
                names.add(_get_text(child))
                break


def _extract_referenced_names(node) -> tuple[set[str], set[str]]:
    """Collect the names a node uses at runtime and in type annotations.

    Walks the full AST subtree once. Names under a type annotation (parameter
    and return types, ``x: T`` variable annotations) are kept apart from
    runtime names, so the linker can tell a type-only dependency (FastAPI's
    ``repo: Repo = Depends(get_repo)``) from a runtime one. Names inside string
    (forward-reference) annotations count as annotation names; other string
    literals are ignored.

    Returns:
        ``(runtime names, annotation-only names)``; a name used both ways is
        only in the first set.
    """
    names: set[str] = set()
    type_names: set[str] = set()
    stack = [(node, False)]
    while stack:
        n, in_annotation = stack.pop()
        target = type_names if in_annotation else names
        if n.type in ("identifier", "attribute"):
            _add_name(target, n)
        elif n.type == "string_content" and in_annotation:
            for match in _ANNOTATION_NAME.finditer(_get_text(n)):
                dotted = match.group()
                type_names.add(dotted)
                type_names.add(dotted.split(".", 1)[0])
        elif n.type == "decorator":
            for child in n.children:
                if child.type in ("identifier", "attribute", "call"):
                    stack.append((child, in_annotation))
            continue

        annotation_fields = _ANNOTATION_FIELDS.get(n.type)
        annotation_nodes = (
            {n.child_by_field_name(field) for field in annotation_fields}
            if annotation_fields
            else ()
        )
        for child in n.children:
            stack.append((child, in_annotation or child in annotation_nodes))
    return names, type_names - names


def _extract_module_variables(root_node) -> list[str]:
    """Names bound by top-level assignments (``db = SQLAlchemy()``, ``X: int = 1``)."""
    names: list[str] = []
    for child in root_node.children:
        if child.type != "expression_statement":
            continue
        for node in child.children:
            if node.type != "assignment":
                continue
            left = node.child_by_field_name("left")
            targets = [left] if left is not None and left.type == "identifier" else (
                [n for n in left.children if n.type == "identifier"]
                if left is not None and left.type in ("pattern_list", "tuple_pattern")
                else []
            )
            for target in targets:
                name = _get_text(target)
                if name and name not in names:
                    names.append(name)
    return names


def _should_skip_module_entity(py_file: Path, declarations: list[dict]) -> bool:
    """Ignore package marker modules that do not declare any symbols.

    Many Python ``__init__.py`` files only serve packaging or re-export duties.
    Treating them as architectural entities adds singleton components and noisy
    edges without representing a meaningful implementation unit.
    """
    return py_file.name == "__init__.py" and not declarations


@dataclass
class FileFacts:
    """Pass-1 extraction for a single file — purely local, so it can be cached by
    file content (see arcade_agent.incremental). Edges are NOT here; they're
    computed in the global link pass, so they can never go stale."""

    rel_path: str
    package: str
    entities: dict[str, Entity] = field(default_factory=dict)   # fqn -> Entity (this file)
    file_imports: list[dict] = field(default_factory=list)      # shared by entities in the file
    refs: dict[str, set[str]] = field(default_factory=dict)     # fqn -> referenced names
    type_refs: dict[str, set[str]] = field(default_factory=dict)  # fqn -> annotation-only names
    module_vars: list[str] = field(default_factory=list)        # top-level assignment names


_PARSER: Parser | None = None


def _parser() -> Parser:
    global _PARSER
    if _PARSER is None:
        _PARSER = Parser(PYTHON_LANGUAGE)
    return _PARSER


def extract_file(py_file: Path, root: Path) -> FileFacts | None:
    """Pass 1: extract entities + imports + referenced names from ONE file.

    Depends only on (this file's content, its path relative to root) — no other
    file — which is exactly what makes it safe to cache per content hash.
    Returns None for files that contribute no entity (e.g. empty __init__.py).
    """
    try:
        source = py_file.read_bytes()
        tree = _parser().parse(source)
    except Exception:
        return None

    root_node = tree.root_node
    module_name = _extract_module_name(py_file, root)
    package = ".".join(module_name.split(".")[:-1]) if "." in module_name else ""
    rel_path = str(py_file.relative_to(root))
    file_imports = _resolve_relative_imports(
        _extract_imports(root_node), module_name, py_file.name == "__init__.py"
    )

    classes = _extract_classes(root_node)
    functions = _extract_functions(root_node)
    methods = _extract_methods(classes, module_name)
    all_decls = classes + methods + functions

    if _should_skip_module_entity(py_file, all_decls):
        return None

    entities: dict[str, Entity] = {}
    refs_map: dict[str, set[str]] = {}
    type_refs_map: dict[str, set[str]] = {}

    if not all_decls:
        fqn = module_name
        entities[fqn] = Entity(
            fqn=fqn, name=module_name.split(".")[-1], package=package,
            file_path=rel_path, kind="module", language="python",
            imports=[imp["module"] for imp in file_imports],
        )
        refs_map[fqn], type_refs_map[fqn] = _extract_referenced_names(root_node)
    else:
        for decl in all_decls:
            owner_fqn = decl.get("owner_fqn")
            if owner_fqn:
                fqn = f"{owner_fqn}.{decl['name']}"
            else:
                fqn = f"{module_name}.{decl['name']}" if module_name else decl["name"]
            refs, type_refs = (
                _extract_referenced_names(decl["node"]) if decl.get("node") else (set(), set())
            )
            entities[fqn] = Entity(
                fqn=fqn, name=decl["name"], package=package, file_path=rel_path,
                kind=decl["kind"], language="python",
                imports=[imp["module"] for imp in file_imports],
                superclass=decl.get("superclass"), interfaces=decl.get("interfaces", []),
                properties={"owner": owner_fqn} if owner_fqn else {},
            )
            refs_map[fqn] = refs
            type_refs_map[fqn] = type_refs

    return FileFacts(rel_path=rel_path, package=package, entities=entities,
                     file_imports=file_imports, refs=refs_map, type_refs=type_refs_map,
                     module_vars=_extract_module_variables(root_node))


def _module_of(rel_path: str) -> str:
    """Module name for a parse-root-relative ``.py`` path, as in extraction."""
    parts = list(Path(rel_path).parts)
    parts[-1] = parts[-1].removesuffix(".py")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_superclass(
    entity: Entity,
    imports: list[dict],
    entities: dict[str, Entity],
    resolve,
) -> str | None:
    """Resolve a base class the way Python binds it: same module, then imports.

    Args:
        entity: The subclass.
        imports: Resolved imports of the subclass's file.
        entities: All parsed entities.
        resolve: ``(module, name) -> fqn | None`` for ``from module import name``.

    Returns:
        The base class's FQN, or None when it is external or unresolved.
    """
    base = entity.superclass or ""
    head, _, rest = base.partition(".")
    module = entity.fqn.rsplit(".", 1)[0] if "." in entity.fqn else ""
    local = f"{module}.{base}" if module else base
    if local in entities:
        return local
    for imp in imports:
        for name in imp["names"]:
            if imp.get("aliases", {}).get(name, name) == head:
                target = resolve(imp["module"], name)
                if target is None:
                    continue
                if not rest:
                    return target
                dotted = f"{target}.{rest}"
                return dotted if dotted in entities else None
        if not imp["names"] and imp["module"].split(".")[-1] == head and rest:
            dotted = f"{imp['module']}.{rest}"
            if dotted in entities:
                return dotted
    return None


def link(facts: list[FileFacts]) -> DependencyGraph:
    """Pass 2: build the dependency graph from per-file facts.

    Always run in full over the current fact set — cheap (dict lookups) and the
    single source of edge truth, so edges are always consistent with the current
    entities and can never be a stale cache entry.
    """
    entities: dict[str, Entity] = {}
    packages: dict[str, list[str]] = {}
    module_imports: dict[str, list[dict]] = {}
    entity_refs: dict[str, set[str]] = {}
    entity_type_refs: dict[str, set[str]] = {}

    for ff in facts:
        for fqn, ent in ff.entities.items():
            entities[fqn] = ent
            packages.setdefault(ff.package, []).append(fqn)
            module_imports[fqn] = ff.file_imports
            entity_refs[fqn] = ff.refs.get(fqn, set())
            entity_type_refs[fqn] = ff.type_refs.get(fqn, set())

    # Short names of importable (non-method) entities, for package re-exports.
    by_name: dict[str, list[str]] = {}
    for entity in entities.values():
        if entity.kind != "method":
            by_name.setdefault(entity.name, []).append(entity.fqn)

    # Each parsed module's own imports, for following re-exports, and the
    # entity that stands in for the module's top-level variables (#67): the
    # module entity when the file declares nothing, else its first top-level
    # declaration, so the dependency lands on the right module.
    imports_by_module: dict[str, list[dict]] = {}
    variable_owner: dict[tuple[str, str], str] = {}
    for ff in facts:
        module_name = _module_of(ff.rel_path)
        imports_by_module[module_name] = ff.file_imports
        candidates = sorted(f for f, e in ff.entities.items() if e.kind != "method")
        if candidates:
            for var in ff.module_vars:
                variable_owner[(module_name, var)] = candidates[0]

    def canonical(module: str) -> str:
        """Map an imported module to its parsed name under a nested source root.

        With a ``src/`` layout, ``src/app/x.py`` is parsed as ``src.app.x`` but
        imported as ``app.x``; a unique parsed module ending in the imported
        name is that module.
        """
        if module in imports_by_module:
            return module
        suffix = f".{module}"
        matches = [m for m in imports_by_module if m.endswith(suffix)]
        return matches[0] if len(matches) == 1 else module

    def resolve(module: str, name: str, seen: frozenset[str] = frozenset()) -> str | None:
        """Resolve ``from module import name`` to a parsed entity.

        A name not defined in ``module`` itself may be re-exported: by the
        module's own ``from other import name`` (a compatibility shim), or by
        its package (``from app.models import User`` with ``User`` in
        ``app/models/user.py``, where an import-only ``__init__`` is not
        parsed), looked up inside the module's subtree and only when unique. A
        name from an external or unrelated module never resolves by short name
        alone (#66).
        """
        module = canonical(module)
        target = f"{module}.{name}"
        if target in entities:
            return target
        owner = variable_owner.get((module, name))
        if owner is not None:
            return owner
        if module not in seen:
            for imp in imports_by_module.get(module, ()):
                for original in imp["names"]:
                    if imp.get("aliases", {}).get(original, original) == name:
                        found = resolve(imp["module"], original, seen | {module})
                        if found is not None:
                            return found
        prefix = f"{module}."
        matches = [f for f in by_name.get(name, ()) if f.startswith(prefix)]
        return matches[0] if len(matches) == 1 else None

    # An imported name used at runtime is an "import" edge. One named only in
    # type annotations is a "uses" edge, the relation the Java and Go parsers
    # give type references: still an architectural dependency (conformance
    # checks count it), but distinguishable from a runtime import.
    edges: list[Edge] = []
    for fqn, entity in entities.items():
        refs = entity_refs.get(fqn, set())
        type_refs = entity_type_refs.get(fqn, set())
        all_refs = refs | type_refs
        imports = module_imports.get(fqn, [])

        def relation(local: str, type_only: bool) -> str | None:
            if not all_refs or local in refs:
                return "uses" if type_only else "import"
            return "uses" if local in type_refs else None

        for imp_info in imports:
            type_only = imp_info.get("type_only", False)
            module = imp_info["module"]
            names = imp_info["names"]
            aliases = imp_info.get("aliases", {})
            if names:
                for name in names:
                    kind = relation(aliases.get(name, name), type_only)
                    if kind is None:
                        continue
                    target = resolve(module, name)
                    if target is not None:
                        edges.append(Edge(source=fqn, target=target, relation=kind))
            else:
                kind = relation(module.split(".")[-1], type_only)
                if kind is None:
                    continue
                if module in entities:
                    edges.append(Edge(source=fqn, target=module, relation=kind))
        if entity.superclass:
            target = _resolve_superclass(entity, imports, entities, resolve)
            if target is not None:
                edges.append(Edge(source=fqn, target=target, relation="extends"))

    seen: set[tuple[str, str, str]] = set()
    unique_edges: list[Edge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.relation)
        if key not in seen:
            seen.add(key)
            unique_edges.append(edge)

    return DependencyGraph(entities=entities, edges=unique_edges, packages=packages)


@register_parser
class PythonParser(LanguageParser):
    """Python source code parser using tree-sitter."""

    @property
    def language(self) -> str:
        return "python"

    @property
    def file_extensions(self) -> list[str]:
        return [".py"]

    def parse(self, files: list[Path], root: Path) -> DependencyGraph:
        """Parse Python source files and extract a dependency graph.

        Pass 1 (extract_file) runs per file; Pass 2 (link) builds the graph.
        Behaviour is identical to the previous single-method implementation.

        Args:
            files: List of .py file paths.
            root: Root directory of the project.

        Returns:
            DependencyGraph with entities, edges, and package info.
        """
        facts = [ff for ff in (extract_file(f, root) for f in files) if ff is not None]
        return link(facts)

    def parse_incremental(self, files: list[Path], root: Path, cache) -> DependencyGraph:
        """Incremental parse: re-run Pass 1 only for files whose content changed
        (the rest reuse the cache); Pass 2 always runs in full.

        Produces a graph identical to parse() — the cache only short-circuits the
        per-file extraction, never the linking. `cache` is an
        arcade_agent.incremental.ExtractCache.
        """
        facts = []
        for f in files:
            ff = cache.get_or_extract(f, root, extract_file)
            if ff is not None:
                facts.append(ff)
        return link(facts)
