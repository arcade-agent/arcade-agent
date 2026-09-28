"""TypeScript / JavaScript parser using tree-sitter.

Extracts classes, interfaces, enums, top-level functions (including arrow
functions bound to a const), and class methods, plus import/extends/implements
edges. Relative imports, tsconfig ``baseUrl``/``paths`` aliases, and local
workspace package imports are resolved against the exact parsed source set.
Resolution coverage is carried in ``DependencyGraph.metadata`` so downstream
architecture scores can visibly qualify incomplete graphs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_typescript as ts_ts
from tree_sitter import Language, Node, Parser

from arcade_agent.parsers.base import LanguageParser, register_parser
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity
from arcade_agent.parsers.typescript_resolution import (
    ImportResolution,
    ResolvedLocal,
    TypeScriptModuleResolver,
)

TS_LANGUAGE = Language(ts_ts.language_typescript())
TSX_LANGUAGE = Language(ts_ts.language_tsx())
logger = logging.getLogger(__name__)

_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]

# Skip very large files: these are almost always minified/bundled vendor output
# (e.g. a 3 MB terminal.js), not human-authored source. Walking one flat
# multi-thousand-node file per entity is O(n^2) and dominates parse time.
_MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True)
class _Import:
    source: str
    names: tuple[tuple[str, str], ...]
    namespaces: tuple[str, ...]
    default: str | None


@dataclass(frozen=True)
class _Declaration:
    name: str
    kind: str
    superclass: str | None
    interfaces: tuple[str, ...]
    node: Node
    owner: str | None = None
    default_export: bool = False


@dataclass
class _ExtractedFile:
    path: Path
    module: str
    path_key: str
    entities: dict[str, Entity]
    imports: tuple[_Import, ...]
    references: dict[str, set[str]]
    member_references: dict[str, dict[str, set[str]]]
    default_export: str | None


def _get_text(node: Node | None) -> str:
    raw = None if node is None else node.text
    return "" if raw is None else raw.decode(errors="replace")


def _strip_ext(name: str) -> str:
    for extension in _EXTENSIONS:
        if name.endswith(extension):
            name = name[:-len(extension)]
            break
    if name.endswith(".d"):  # foo.d.ts -> foo
        name = name[:-2]
    return name


def _module_name(file_path: Path, root: Path) -> str:
    """Return a dotted module name; a trailing ``index`` is omitted."""
    relative = file_path.relative_to(root)
    parts = list(relative.parts)
    parts[-1] = _strip_ext(parts[-1])
    if parts and parts[-1] == "index":
        parts = parts[:-1]
    return ".".join(parts)


def _path_key(file_path: Path, root: Path) -> str:
    """Return a POSIX relative path without its source extension."""
    relative = file_path.relative_to(root)
    parts = list(relative.parts)
    parts[-1] = _strip_ext(parts[-1])
    return "/".join(parts)


def _unwrap_export(node: Node) -> Node:
    """Return the declaration wrapped by an export statement, if present."""
    if node.type == "export_statement":
        supported = {
            "class_declaration",
            "abstract_class_declaration",
            "interface_declaration",
            "enum_declaration",
            "function_declaration",
            "lexical_declaration",
            "variable_declaration",
        }
        for child in node.children:
            if child.type in supported:
                return child
    return node


def _is_default_export(node: Node) -> bool:
    return node.type == "export_statement" and any(
        _get_text(child) == "default" for child in node.children
    )


def _heritage(class_node: Node) -> tuple[str | None, list[str]]:
    """Return ``(superclass, interfaces)`` from a class heritage clause."""
    superclass: str | None = None
    interfaces: list[str] = []
    for child in class_node.children:
        if child.type != "class_heritage":
            continue
        for clause in child.children:
            names = [
                _get_text(name)
                for name in clause.children
                if name.type
                in ("identifier", "type_identifier", "generic_type", "member_expression")
            ]
            if clause.type == "extends_clause" and names:
                superclass = names[0].split("<")[0]
            elif clause.type == "implements_clause":
                interfaces.extend(name.split("<")[0] for name in names)
    return superclass, interfaces


def _arrow_or_func_name(lexical_node: Node) -> tuple[str | None, Node | None]:
    """Return the name/declarator for a variable-bound function."""
    for declarator in lexical_node.children:
        if declarator.type != "variable_declarator":
            continue
        name_node = declarator.child_by_field_name("name")
        value = declarator.child_by_field_name("value")
        if (
            name_node is not None
            and value is not None
            and value.type in ("arrow_function", "function_expression", "function")
        ):
            return _get_text(name_node), declarator
    return None, None


def _referenced_names(node: Node) -> set[str]:
    """Collect identifiers/type names iteratively so source depth is untrusted."""
    names: set[str] = set()
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in (
            "identifier",
            "type_identifier",
            "property_identifier",
            "shorthand_property_identifier",
        ):
            names.add(_get_text(current))
        stack.extend(current.children)
    return names


def _member_references(node: Node) -> dict[str, set[str]]:
    """Collect direct ``namespace.member`` references for namespace imports."""
    references: dict[str, set[str]] = {}
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "member_expression":
            object_node = current.child_by_field_name("object")
            property_node = current.child_by_field_name("property")
            if (
                object_node is not None
                and property_node is not None
                and object_node.type == "identifier"
            ):
                references.setdefault(_get_text(object_node), set()).add(
                    _get_text(property_node)
                )
        stack.extend(current.children)
    return references


def _extract_imports(root_node: Node) -> tuple[_Import, ...]:
    """Extract static ES import clauses from one syntax tree."""
    imports: list[_Import] = []
    for child in root_node.children:
        if child.type != "import_statement":
            continue
        source = ""
        for string_node in child.children:
            if string_node.type != "string":
                continue
            fragment = next(
                (part for part in string_node.children if part.type == "string_fragment"),
                None,
            )
            source = _get_text(fragment)

        names: list[tuple[str, str]] = []
        namespaces: list[str] = []
        default: str | None = None
        clause = next(
            (part for part in child.children if part.type == "import_clause"),
            None,
        )
        if clause is not None:
            for part in clause.children:
                if part.type == "identifier":
                    default = _get_text(part)
                elif part.type == "namespace_import":
                    identifier = next(
                        (item for item in part.children if item.type == "identifier"),
                        None,
                    )
                    if identifier is not None:
                        namespaces.append(_get_text(identifier))
                elif part.type == "named_imports":
                    for specifier in part.children:
                        if specifier.type != "import_specifier":
                            continue
                        name_node = specifier.child_by_field_name("name")
                        alias = specifier.child_by_field_name("alias")
                        original = _get_text(name_node)
                        local = _get_text(alias) if alias is not None else original
                        if original:
                            names.append((original, local))
        if source:
            imports.append(_Import(source, tuple(names), tuple(namespaces), default))
    return tuple(imports)


def _extract_file(path: Path, root: Path, parser: Parser) -> _ExtractedFile | None:
    """Extract one file transactionally; callers publish only this return value."""
    if path.stat().st_size > _MAX_FILE_BYTES:
        logger.info("Skipping likely bundled TypeScript source over size limit: %s", path)
        return None

    tree = parser.parse(path.read_bytes())
    root_node = tree.root_node
    module = _module_name(path, root)
    package = ".".join(module.split(".")[:-1]) if "." in module else ""
    relative_path = str(path.relative_to(root))
    imports = _extract_imports(root_node)
    import_sources = [item.source for item in imports]
    declarations: list[_Declaration] = []

    for top_level in root_node.children:
        node = _unwrap_export(top_level)
        is_default = _is_default_export(top_level)
        if node.type in ("class_declaration", "abstract_class_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = _get_text(name_node)
            superclass, interfaces = _heritage(node)
            declarations.append(
                _Declaration(
                    name,
                    "class",
                    superclass,
                    tuple(interfaces),
                    node,
                    default_export=is_default,
                )
            )
            class_fqn = f"{module}.{name}" if module else name
            body = node.child_by_field_name("body")
            if body is not None:
                for method in body.children:
                    if method.type != "method_definition":
                        continue
                    method_name = method.child_by_field_name("name")
                    if method_name is not None:
                        declarations.append(
                            _Declaration(
                                _get_text(method_name),
                                "method",
                                None,
                                (),
                                method,
                                owner=class_fqn,
                            )
                        )
        elif node.type == "interface_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                declarations.append(
                    _Declaration(_get_text(name_node), "interface", None, (), node)
                )
        elif node.type == "enum_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                declarations.append(
                    _Declaration(_get_text(name_node), "enum", None, (), node)
                )
        elif node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                declarations.append(
                    _Declaration(
                        _get_text(name_node),
                        "function",
                        None,
                        (),
                        node,
                        default_export=is_default,
                    )
                )
        elif node.type in ("lexical_declaration", "variable_declaration"):
            function_name, declaration_node = _arrow_or_func_name(node)
            if function_name is not None and declaration_node is not None:
                declarations.append(
                    _Declaration(function_name, "function", None, (), declaration_node)
                )

    entities: dict[str, Entity] = {}
    references: dict[str, set[str]] = {}
    member_references: dict[str, dict[str, set[str]]] = {}
    default_export: str | None = None
    if not declarations and module:
        fqn = module
        entities[fqn] = Entity(
            fqn=fqn,
            name=module.split(".")[-1],
            package=package,
            file_path=relative_path,
            kind="module",
            language="typescript",
            imports=import_sources,
        )
        references[fqn] = _referenced_names(root_node)
        member_references[fqn] = _member_references(root_node)
    else:
        for declaration in declarations:
            fqn = (
                f"{declaration.owner}.{declaration.name}"
                if declaration.owner
                else (f"{module}.{declaration.name}" if module else declaration.name)
            )
            entities[fqn] = Entity(
                fqn=fqn,
                name=declaration.name,
                package=package,
                file_path=relative_path,
                kind=declaration.kind,
                language="typescript",
                imports=import_sources,
                superclass=declaration.superclass,
                interfaces=list(declaration.interfaces),
                properties={"owner": declaration.owner} if declaration.owner else {},
            )
            references[fqn] = _referenced_names(declaration.node)
            member_references[fqn] = _member_references(declaration.node)
            if declaration.default_export:
                default_export = fqn

    return _ExtractedFile(
        path=path,
        module=module,
        path_key=_path_key(path, root),
        entities=entities,
        imports=imports,
        references=references,
        member_references=member_references,
        default_export=default_export,
    )


@register_parser
class TypeScriptParser(LanguageParser):
    """TypeScript / JavaScript parser using tree-sitter."""

    @property
    def language(self) -> str:
        return "typescript"

    @property
    def file_extensions(self) -> list[str]:
        return _EXTENSIONS

    def parse(self, files: list[Path], root: Path) -> DependencyGraph:
        ts_parser = Parser(TS_LANGUAGE)
        tsx_parser = Parser(TSX_LANGUAGE)
        root = root.resolve()

        entities: dict[str, Entity] = {}
        edges: list[Edge] = []
        packages: dict[str, list[str]] = {}
        package_members: dict[str, set[str]] = {}
        module_by_pathkey: dict[str, str] = {}
        imports_by_file: dict[Path, tuple[_Import, ...]] = {}
        entity_references: dict[str, set[str]] = {}
        entity_member_references: dict[str, dict[str, set[str]]] = {}
        entity_file: dict[str, Path] = {}
        default_export_by_module: dict[str, str] = {}
        parsed_files: list[Path] = []
        duplicate_entities: list[tuple[str, str]] = []

        resolved_files: list[Path] = []
        for candidate in files:
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                logger.warning("Skipping TypeScript source outside project root: %s", candidate)
                continue
            resolved_files.append(resolved)

        # Each file is extracted into isolated state. An unexpected failure can
        # never leak partial entities or erase healthy siblings.
        for source_file in sorted(dict.fromkeys(resolved_files)):
            parser = tsx_parser if source_file.suffix in (".tsx", ".jsx") else ts_parser
            try:
                extracted = _extract_file(source_file, root, parser)
            except Exception as error:
                logger.warning(
                    "Skipping TypeScript source after extraction failure (%s): %s",
                    type(error).__name__,
                    source_file,
                )
                continue
            if extracted is None:
                continue

            parsed_files.append(source_file)
            imports_by_file[source_file] = extracted.imports
            module_by_pathkey[extracted.path_key] = extracted.module
            if extracted.path_key.endswith("/index"):
                module_by_pathkey[extracted.path_key[:-len("/index")]] = extracted.module

            for fqn, entity in extracted.entities.items():
                if fqn in entities:
                    duplicate_entities.append((fqn, str(source_file.relative_to(root))))
                    continue
                entities[fqn] = entity
                entity_references[fqn] = extracted.references[fqn]
                entity_member_references[fqn] = extracted.member_references[fqn]
                entity_file[fqn] = source_file
                members = package_members.setdefault(entity.package, set())
                if fqn not in members:
                    members.add(fqn)
                    packages.setdefault(entity.package, []).append(fqn)
            if extracted.default_export is not None and extracted.default_export in entities:
                default_export_by_module[extracted.module] = extracted.default_export

        if duplicate_entities:
            logger.warning(
                "Kept the first declaration for %d duplicate TypeScript entity FQN(s) "
                "(e.g. %s in %s)",
                len(duplicate_entities),
                duplicate_entities[0][0],
                duplicate_entities[0][1],
            )

        names_to_fqns: dict[str, list[str]] = {}
        for entity in entities.values():
            if entity.kind != "method":
                names_to_fqns.setdefault(entity.name, []).append(entity.fqn)
        unique_fqn_by_name = {
            name: fqns[0] for name, fqns in names_to_fqns.items() if len(fqns) == 1
        }

        resolver = TypeScriptModuleResolver(root, module_by_pathkey, parsed_files)
        resolutions: dict[Path, dict[str, ImportResolution]] = {
            path: {
                specifier: resolver.resolve(specifier, path)
                for specifier in sorted({item.source for item in imports})
            }
            for path, imports in imports_by_file.items()
        }
        linked_local: set[tuple[Path, str]] = set()

        def target_for(module: str, name: str) -> str | None:
            exact = f"{module}.{name}" if module else name
            if exact in entities:
                return exact
            # Re-export barrels often contain no declaration themselves. A
            # unique repository-wide symbol is a safe fallback; ambiguous leaf
            # names remain explicitly unlinked in coverage metadata.
            return unique_fqn_by_name.get(name)

        def emit(source: str, target: str | None) -> bool:
            if target is None or target not in entities or source == target:
                return False
            edges.append(Edge(source=source, target=target, relation="import"))
            return True

        for fqn, entity in entities.items():
            references = entity_references.get(fqn, set())
            member_references = entity_member_references.get(fqn, {})
            importing_file = entity_file.get(fqn)
            if importing_file is None:
                continue
            file_resolution = resolutions.get(importing_file, {})
            for imported in imports_by_file.get(importing_file, ()):
                resolution = file_resolution.get(imported.source)
                if not isinstance(resolution, ResolvedLocal):
                    continue

                emitted = False
                for original, local in imported.names:
                    if local not in references and entity.kind != "module":
                        continue
                    emitted = emit(fqn, target_for(resolution.module, original)) or emitted

                if imported.default is not None and (
                    imported.default in references or entity.kind == "module"
                ):
                    target = default_export_by_module.get(resolution.module)
                    if target is None and resolution.module in entities:
                        target = resolution.module
                    emitted = emit(fqn, target) or emitted

                for namespace in imported.namespaces:
                    if namespace not in references and entity.kind != "module":
                        continue
                    namespace_emitted = False
                    for member in member_references.get(namespace, set()):
                        namespace_emitted = (
                            emit(fqn, target_for(resolution.module, member))
                            or namespace_emitted
                        )
                    if not namespace_emitted and resolution.module in entities:
                        namespace_emitted = emit(fqn, resolution.module)
                    emitted = emitted or namespace_emitted

                if not imported.names and not imported.namespaces and imported.default is None:
                    if resolution.module in entities:
                        emitted = emit(fqn, resolution.module) or emitted

                if emitted:
                    linked_local.add((importing_file, imported.source))

            if entity.superclass:
                target = unique_fqn_by_name.get(entity.superclass)
                if target:
                    edges.append(Edge(source=fqn, target=target, relation="extends"))
            for interface in entity.interfaces or []:
                target = unique_fqn_by_name.get(interface)
                if target:
                    edges.append(Edge(source=fqn, target=target, relation="implements"))

        seen: set[tuple[str, str, str]] = set()
        unique_edges: list[Edge] = []
        for edge in edges:
            key = (edge.source, edge.target, edge.relation)
            if key not in seen and edge.source != edge.target:
                seen.add(key)
                unique_edges.append(edge)

        resolution_summary = resolver.summary(resolutions, linked_local)
        metadata: dict[str, object] = {
            "dependency_resolution": {"typescript": resolution_summary}
        }
        if duplicate_entities:
            metadata["typescript_parser"] = {
                "duplicate_entity_fqns": len(duplicate_entities),
                "duplicate_entity_examples": [
                    {"fqn": fqn, "file_path": file_path}
                    for fqn, file_path in duplicate_entities[:20]
                ],
                "duplicate_entity_examples_truncated": max(
                    0, len(duplicate_entities) - 20
                ),
            }
        return DependencyGraph(
            entities=entities,
            edges=unique_edges,
            packages=packages,
            metadata=metadata,
        )
