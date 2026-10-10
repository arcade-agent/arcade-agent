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
    is_source_import,
)

TS_LANGUAGE = Language(ts_ts.language_typescript())
TSX_LANGUAGE = Language(ts_ts.language_tsx())
logger = logging.getLogger(__name__)

_EXTENSIONS = [".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"]

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
    exported: bool = False


@dataclass(frozen=True)
class _ReExport:
    source: str
    names: tuple[tuple[str, str], ...]
    wildcard: bool = False


@dataclass
class _ExtractedFile:
    path: Path
    module: str
    entities: dict[str, Entity]
    imports: tuple[_Import, ...]
    references: dict[str, set[str]]
    member_references: dict[str, dict[str, set[str]]]
    exports: dict[str, str]
    reexports: tuple[_ReExport, ...]


def _get_text(node: Node | None) -> str:
    raw = None if node is None else node.text
    return "" if raw is None else raw.decode(errors="replace")


def _strip_ext(name: str) -> str:
    for ext in _EXTENSIONS:
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    if name.endswith(".d"):  # foo.d.ts -> foo
        name = name[:-2]
    return name


def _module_name(file_path: Path, root: Path) -> str:
    """Dotted module name from a file path; trailing 'index' is dropped."""
    rel = file_path.relative_to(root)
    parts = list(rel.parts)
    parts[-1] = _strip_ext(parts[-1])
    if parts and parts[-1] == "index":
        parts = parts[:-1]
    return ".".join(parts)


def _unwrap_export(node: Node) -> Node:
    """export <decl> wraps the real declaration; return the inner node."""
    if node.type == "export_statement":
        for child in node.children:
            if child.type in ("class_declaration", "abstract_class_declaration",
                              "interface_declaration", "enum_declaration",
                              "function_declaration", "lexical_declaration",
                              "variable_declaration"):
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
    """For `const foo = () => {}` / `const foo = function(){}`, return (name, body_node)."""
    for declr in lexical_node.children:
        if declr.type != "variable_declarator":
            continue
        name_node = declr.child_by_field_name("name")
        value = declr.child_by_field_name("value")
        if name_node and value and value.type in ("arrow_function", "function_expression",
                                                   "function"):
            return _get_text(name_node), declr
    return None, None


def _referenced_names(node: Node) -> set[str]:
    """Identifiers / type names used within a node (for edge filtering)."""
    names: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in ("identifier", "type_identifier", "property_identifier",
                     "shorthand_property_identifier"):
            names.add(_get_text(n))
        stack.extend(n.children)
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
        if source and is_source_import(source):
            imports.append(_Import(source, tuple(names), tuple(namespaces), default))
    return tuple(imports)


def _require_source(node: Node | None) -> str | None:
    """The module specifier of a ``require('…')`` call, else None."""
    if node is None or node.type != "call_expression":
        return None
    function = node.child_by_field_name("function")
    if function is None or function.type != "identifier" or _get_text(function) != "require":
        return None
    arguments = node.child_by_field_name("arguments")
    values = arguments.named_children if arguments is not None else []
    if len(values) != 1 or values[0].type != "string":
        return None
    fragment = next((part for part in values[0].children if part.type == "string_fragment"), None)
    return _get_text(fragment) if fragment is not None else None


def _extract_requires(root_node: Node) -> tuple[_Import, ...]:
    """Extract CommonJS ``require`` calls as imports (#68).

    ``const x = require(s)`` binds ``x`` as both the default export and a
    namespace (``x.member``); ``const { a, b: c } = require(s)`` and
    ``const c = require(s).b`` are named imports; any other ``require(s)`` is
    a side-effect import.
    """
    imports: list[_Import] = []
    stack = [root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        source = _require_source(node)
        if not source or not is_source_import(source):
            continue
        names: tuple[tuple[str, str], ...] = ()
        namespaces: tuple[str, ...] = ()
        default: str | None = None
        parent = node.parent
        if parent is not None and parent.type == "member_expression":
            declarator = parent.parent
            target = (declarator.child_by_field_name("name")
                      if declarator is not None and declarator.type == "variable_declarator"
                      else None)
            member = parent.child_by_field_name("property")
            if target is not None and target.type == "identifier" and member is not None:
                names = ((_get_text(member), _get_text(target)),)
        elif parent is not None and parent.type == "variable_declarator":
            target = parent.child_by_field_name("name")
            if target is not None and target.type == "identifier":
                default = _get_text(target)
                namespaces = (default,)
            elif target is not None and target.type == "object_pattern":
                pairs: list[tuple[str, str]] = []
                for item in target.named_children:
                    if item.type == "shorthand_property_identifier_pattern":
                        pairs.append((_get_text(item), _get_text(item)))
                    elif item.type == "pair_pattern":
                        key = item.child_by_field_name("key")
                        value = item.child_by_field_name("value")
                        if key is not None and value is not None and value.type == "identifier":
                            pairs.append((_get_text(key), _get_text(value)))
                names = tuple(pairs)
        imports.append(_Import(source, names, namespaces, default))
    return tuple(imports)


def _is_module_exports(node: Node | None) -> bool:
    return (
        node is not None
        and node.type == "member_expression"
        and _get_text(node.child_by_field_name("object")) == "module"
        and _get_text(node.child_by_field_name("property")) == "exports"
    )


def _extract_commonjs_exports(root_node: Node) -> tuple[list[tuple[str, str]], list[str]]:
    """Top-level CommonJS exports as ``(exported name, local name)`` pairs.

    ``module.exports = X`` exports ``X`` as the default; ``module.exports =
    { a, b: c }``, ``exports.a = a`` and ``module.exports.a = a`` export names;
    ``module.exports = require(s)`` re-exports ``s`` wholesale (second list).
    """
    named: list[tuple[str, str]] = []
    wildcard_sources: list[str] = []
    for statement in root_node.children:
        if statement.type != "expression_statement":
            continue
        for expression in statement.named_children:
            if expression.type != "assignment_expression":
                continue
            left = expression.child_by_field_name("left")
            right = expression.child_by_field_name("right")
            if left is None or right is None:
                continue
            if _is_module_exports(left):
                if right.type == "identifier":
                    named.append(("default", _get_text(right)))
                elif right.type == "object":
                    for prop in right.named_children:
                        if prop.type == "shorthand_property_identifier":
                            named.append((_get_text(prop), _get_text(prop)))
                        elif prop.type == "pair":
                            key = prop.child_by_field_name("key")
                            value = prop.child_by_field_name("value")
                            if key is not None and value is not None and value.type == "identifier":
                                named.append((_get_text(key).strip("\"'"), _get_text(value)))
                else:
                    source = _require_source(right)
                    if source and is_source_import(source):
                        wildcard_sources.append(source)
            elif left.type == "member_expression" and right.type == "identifier":
                target = left.child_by_field_name("object")
                member = left.child_by_field_name("property")
                exports_object = _is_module_exports(target) or (
                    target is not None and target.type == "identifier"
                    and _get_text(target) == "exports"
                )
                if exports_object and member is not None:
                    named.append((_get_text(member), _get_text(right)))
    return named, wildcard_sources


def _extract_reexports(root_node: Node) -> tuple[_ReExport, ...]:
    reexports = []
    for statement in root_node.children:
        if statement.type != "export_statement":
            continue
        source_node = statement.child_by_field_name("source")
        if source_node is None:
            continue
        source = _get_text(source_node).strip("\"'")
        if not is_source_import(source):
            continue
        names = []
        for clause in statement.children:
            if clause.type != "export_clause":
                continue
            for specifier in clause.children:
                if specifier.type != "export_specifier":
                    continue
                name = _get_text(specifier.child_by_field_name("name"))
                alias = specifier.child_by_field_name("alias")
                names.append((name, _get_text(alias) if alias is not None else name))
        wildcard = any(child.type == "*" for child in statement.children)
        reexports.append(_ReExport(source, tuple(names), wildcard))
    return tuple(reexports)


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
    imports = _extract_imports(root_node) + _extract_requires(root_node)
    reexports = _extract_reexports(root_node)
    imports += tuple(_Import(item.source, item.names, (), None) for item in reexports)
    import_sources = [item.source for item in imports]
    declarations: list[_Declaration] = []

    for top_level in root_node.children:
        node = _unwrap_export(top_level)
        is_default = _is_default_export(top_level)
        exported = top_level.type == "export_statement"
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
                    exported=exported,
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
                    _Declaration(_get_text(name_node), "interface", None, (), node,
                                 default_export=is_default, exported=exported)
                )
        elif node.type == "enum_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                declarations.append(
                    _Declaration(_get_text(name_node), "enum", None, (), node, exported=exported)
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
                        exported=exported,
                    )
                )
        elif node.type in ("lexical_declaration", "variable_declaration"):
            function_name, declaration_node = _arrow_or_func_name(node)
            if function_name is not None and declaration_node is not None:
                declarations.append(
                    _Declaration(function_name, "function", None, (), declaration_node,
                                 exported=exported)
                )

    entities: dict[str, Entity] = {}
    references: dict[str, set[str]] = {}
    member_references: dict[str, dict[str, set[str]]] = {}
    exports: dict[str, str] = {}
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
                exports["default"] = fqn
            if declaration.exported and not declaration.default_export:
                exports[declaration.name] = fqn

    for statement in root_node.children:
        if statement.type != "export_statement" or statement.child_by_field_name("source"):
            continue
        value = statement.child_by_field_name("value")
        if _is_default_export(statement) and value is not None and value.type == "identifier":
            fqn = f"{module}.{_get_text(value)}" if module else _get_text(value)
            if fqn in entities:
                exports["default"] = fqn
        for clause in statement.children:
            if clause.type != "export_clause":
                continue
            for specifier in clause.children:
                if specifier.type != "export_specifier":
                    continue
                local = _get_text(specifier.child_by_field_name("name"))
                alias = specifier.child_by_field_name("alias")
                name = _get_text(alias) if alias is not None else local
                fqn = f"{module}.{local}" if module else local
                if fqn in entities:
                    exports[name] = fqn
                else:
                    for imported in imports:
                        for original, binding in imported.names:
                            if binding == local:
                                reexports += (_ReExport(imported.source, ((original, name),)),)
                        if imported.default == local:
                            reexports += (_ReExport(imported.source, (("default", name),)),)

    commonjs_exports, commonjs_wildcards = _extract_commonjs_exports(root_node)
    for name, local in commonjs_exports:
        fqn = f"{module}.{local}" if module else local
        if fqn in entities:
            exports.setdefault(name, fqn)
            continue
        for imported in imports:
            for original, binding in imported.names:
                if binding == local:
                    reexports += (_ReExport(imported.source, ((original, name),)),)
            if imported.default == local:
                reexports += (_ReExport(imported.source, (("default", name),)),)
    for source in commonjs_wildcards:
        reexports += (_ReExport(source, (("default", "default"),), wildcard=True),)

    return _ExtractedFile(
        path=path,
        module=module,
        entities=entities,
        imports=imports,
        references=references,
        member_references=member_references,
        exports=exports,
        reexports=reexports,
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
        exports_by_module: dict[str, dict[str, str]] = {}
        reexports_by_module: dict[str, tuple[_ReExport, ...]] = {}
        file_by_module: dict[str, Path] = {}
        module_entity_by_module: dict[str, str] = {}
        module_by_file: dict[Path, str] = {}
        parsed_files: list[Path] = []
        duplicate_entities: list[tuple[str, str]] = []

        resolved_files: list[Path] = []
        for candidate in files:
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
            except (OSError, RuntimeError, ValueError) as error:
                logger.warning(
                    "Skipping TypeScript source after path failure (%s): %s",
                    type(error).__name__, candidate,
                )
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
            source_key = source_file.relative_to(root).as_posix()
            reexports_by_module[source_key] = extracted.reexports
            file_by_module[source_key] = source_file
            module_by_file[source_file] = extracted.module
            module_by_pathkey[source_key] = source_key

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
            exports_by_module[source_key] = {
                name: fqn for name, fqn in extracted.exports.items()
                if entity_file.get(fqn) == source_file
            }
            if entity_file.get(extracted.module) == source_file:
                module_entity_by_module[source_key] = extracted.module
        if duplicate_entities:
            logger.warning(
                "Kept the first declaration for %d duplicate TypeScript entity FQN(s) "
                "(e.g. %s in %s)",
                len(duplicate_entities),
                duplicate_entities[0][0],
                duplicate_entities[0][1],
            )

        resolver = TypeScriptModuleResolver(root, module_by_pathkey, parsed_files)
        resolutions: dict[Path, dict[str, ImportResolution]] = {
            path: {
                specifier: resolver.resolve(specifier, path)
                for specifier in sorted({item.source for item in imports})
            }
            for path, imports in imports_by_file.items()
        }
        linked_local: set[tuple[Path, str]] = set()
        target_cache: dict[tuple[str, str], str | None] = {}

        def target_for(module: str, name: str) -> str | None:
            key = (module, name)
            if key in target_cache:
                return target_cache[key]
            targets: set[str] = set()
            seen: set[tuple[str, str]] = set()
            stack = [key]
            while stack:
                current_module, current_name = stack.pop()
                if (current_module, current_name) in seen:
                    continue
                seen.add((current_module, current_name))
                direct = exports_by_module.get(current_module, {}).get(current_name)
                if direct is not None:
                    targets.add(direct)
                    continue
                reexports = reexports_by_module.get(current_module, ())
                named = [(item, original) for item in reexports
                         for original, exported in item.names if exported == current_name]
                candidates = named or [
                    (item, current_name) for item in reexports
                    if item.wildcard and current_name != "default"
                ]
                source_file = file_by_module.get(current_module)
                if source_file is None:
                    continue
                for item, original in candidates:
                    resolution = resolutions.get(source_file, {}).get(item.source)
                    if isinstance(resolution, ResolvedLocal):
                        stack.append((resolution.module, original))
            target = next(iter(targets)) if len(targets) == 1 else None
            target_cache[key] = target
            return target

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
                    target = target_for(resolution.module, "default")
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
                    module_entity = module_entity_by_module.get(resolution.module)
                    if not namespace_emitted and module_entity is not None:
                        namespace_emitted = emit(fqn, module_entity)
                    emitted = emitted or namespace_emitted

                if not imported.names and not imported.namespaces and imported.default is None:
                    module_entity = module_entity_by_module.get(resolution.module)
                    if module_entity is not None:
                        emitted = emit(fqn, module_entity) or emitted

                if emitted:
                    linked_local.add((importing_file, imported.source))

            def heritage_target(name: str) -> str | None:
                module = module_by_file[importing_file]
                local = f"{module}.{name}" if module else name
                if local in entities:
                    return local
                for imported in imports_by_file.get(importing_file, ()):
                    resolution = file_resolution.get(imported.source)
                    if not isinstance(resolution, ResolvedLocal):
                        continue
                    for original, binding in imported.names:
                        if binding == name:
                            return target_for(resolution.module, original)
                    if imported.default == name:
                        return target_for(resolution.module, "default")
                    for namespace in imported.namespaces:
                        if name.startswith(namespace + "."):
                            return target_for(resolution.module, name[len(namespace) + 1:])
                return None

            heritage = [(interface, "implements") for interface in entity.interfaces or []]
            if entity.superclass:
                heritage.append((entity.superclass, "extends"))
            for name, relation in heritage:
                target = heritage_target(name)
                if target and target != fqn:
                    edges.append(Edge(source=fqn, target=target, relation=relation))

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
