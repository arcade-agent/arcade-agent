"""Kotlin parser using tree-sitter."""

from __future__ import annotations

from pathlib import Path

import tree_sitter_kotlin as tskotlin
from tree_sitter import Language, Node, Parser

from arcade_agent.parsers.base import LanguageParser, register_parser
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity

KOTLIN_LANGUAGE = Language(tskotlin.language())

_TYPE_NODE_TYPES = frozenset({"class_declaration", "object_declaration"})
_BODY_NODE_TYPES = frozenset({"class_body", "enum_class_body"})


def _get_text(node: Node | None) -> str:
    if node is None:
        return ""
    return node.text.decode()


def _extract_package(root_node: Node) -> str:
    for child in root_node.children:
        if child.type == "package_header":
            for sub in child.children:
                if sub.type == "qualified_identifier":
                    return _get_text(sub)
    return ""


def _extract_imports(root_node: Node) -> tuple[list[str], dict[str, str]]:
    """Return (import FQNs, alias -> FQN map)."""
    imports: list[str] = []
    aliases: dict[str, str] = {}
    for child in root_node.children:
        if child.type != "import":
            continue
        qualified: str | None = None
        alias: str | None = None
        saw_as = False
        for sub in child.children:
            if sub.type == "qualified_identifier":
                qualified = _get_text(sub)
            elif sub.type == "as":
                saw_as = True
            elif sub.type == "identifier" and saw_as:
                alias = _get_text(sub)
        if not qualified:
            continue
        imports.append(qualified)
        if alias:
            aliases[alias] = qualified
    return imports, aliases


def _has_modifier(node: Node, keyword: str) -> bool:
    for child in node.children:
        if child.type != "modifiers":
            continue
        if keyword in _get_text(child).split():
            return True
    return False


def _has_named_child(node: Node, child_type: str) -> bool:
    return any(child.type == child_type for child in node.children)


def _detect_kind(node: Node) -> str:
    if node.type == "object_declaration":
        return "object"
    if _has_named_child(node, "interface"):
        return "interface"
    if _has_modifier(node, "enum") or _has_named_child(node, "enum_class_body"):
        return "enum"
    return "class"


def _extract_delegation_types(node: Node) -> tuple[str | None, list[str]]:
    """Extract superclass / interfaces from `: Base(), Runnable` clauses.

    Kotlin AST hint: a parent written as a constructor invocation (`Base()`)
    is treated as a class; a bare user type (`Runnable`) is treated as an
    interface. This matches common Kotlin style and keeps edges aligned with
    the Java parser's extends/implements relations. A class parent written
    without constructor parentheses is therefore conservatively classified as
    an interface.
    """
    superclass: str | None = None
    interfaces: list[str] = []
    for child in node.children:
        if child.type != "delegation_specifiers":
            continue
        for specifier in child.children:
            if specifier.type != "delegation_specifier":
                continue
            type_name = _first_user_type_name(specifier)
            if not type_name:
                continue
            if _has_named_child(specifier, "constructor_invocation"):
                if superclass is None:
                    superclass = type_name
                else:
                    # Unusual multiple class parents — keep as interface edge target.
                    interfaces.append(type_name)
            else:
                interfaces.append(type_name)
    return superclass, interfaces


def _first_user_type_name(node: Node) -> str | None:
    """Return a simple or dotted type name from a user_type subtree.

    Kotlin parents are often qualified (`com.example.Base`). The grammar stores
    that as multiple `identifier` children under `user_type`, so we join them.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "user_type":
            parts = [
                _get_text(child)
                for child in current.children
                if child.type == "identifier"
            ]
            if parts:
                return ".".join(parts)
            continue
        stack.extend(reversed(current.children))
    return None


def _parse_type_declaration(node: Node) -> dict | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        # Some grammars put the name as a direct identifier child.
        for child in node.children:
            if child.type == "identifier":
                name_node = child
                break
    if name_node is None:
        return None

    kind = _detect_kind(node)
    superclass, interfaces = _extract_delegation_types(node)
    if kind == "interface":
        # Keep Java-compatible edge vocabulary: interface inheritance is
        # represented by the graph's "implements" relation.
        if superclass:
            interfaces = [superclass, *interfaces]
            superclass = None

    return {
        "name": _get_text(name_node),
        "kind": kind,
        "superclass": superclass,
        "interfaces": interfaces,
        "node": node,
        "is_data": _has_modifier(node, "data"),
        "is_sealed": _has_modifier(node, "sealed"),
    }


def _extract_type_declarations(root_node: Node) -> list[dict]:
    decls: list[dict] = []
    for node in root_node.children:
        if node.type in _TYPE_NODE_TYPES:
            decl = _parse_type_declaration(node)
            if decl:
                decls.append(decl)
                decls.extend(_extract_nested_types(decl))
        else:
            # Meta-annotated `annotation class` can be parsed as annotated_expression
            # / infix_expression instead of class_declaration (seen in embabel).
            for fallback in _extract_annotation_classes_from_misc(node):
                decls.append(fallback)
    return decls


def _extract_annotation_classes_from_misc(node: Node) -> list[dict]:
    """Recover annotation classes from known tree-sitter error-recovery shapes.

    Traversal is intentionally iterative and limited to annotation/error
    wrappers. Descending through arbitrary function bodies would hoist local
    classes into package-level entities.
    """
    found: list[dict] = []
    recovery_containers = {"annotated_expression", "infix_expression", "ERROR"}
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "class_declaration":
            decl = _parse_type_declaration(current)
            if decl:
                found.append(decl)
                found.extend(_extract_nested_types(decl))
            continue
        if current.type == "infix_expression":
            idents = [c for c in current.children if c.type == "identifier"]
            if (
                len(idents) >= 3
                and _get_text(idents[0]) == "annotation"
                and _get_text(idents[1]) == "class"
            ):
                name = _get_text(idents[2])
                found.append(
                    {
                        "name": name,
                        "kind": "class",
                        "superclass": None,
                        "interfaces": [],
                        "node": current,
                        "is_data": False,
                        "is_sealed": False,
                    }
                )
                continue
        if current.type in recovery_containers:
            stack.extend(reversed(current.children))
    return found


def _extract_nested_types(owner: dict) -> list[dict]:
    """Extract nested classes/objects (including companion objects)."""
    nested: list[dict] = []
    owner_name = owner["name"]
    for child in owner["node"].children:
        if child.type not in _BODY_NODE_TYPES:
            continue
        for member in child.children:
            if member.type == "companion_object":
                companion_name = "Companion"
                for sub in member.children:
                    if sub.type == "identifier":
                        companion_name = _get_text(sub)
                        break
                superclass, interfaces = _extract_delegation_types(member)
                nested.append(
                    {
                        "name": f"{owner_name}.{companion_name}",
                        "kind": "object",
                        "superclass": superclass,
                        "interfaces": interfaces,
                        "node": member,
                        "is_data": False,
                        "is_sealed": False,
                        "owner_simple_name": owner_name,
                    }
                )
                continue
            if member.type not in _TYPE_NODE_TYPES:
                continue
            decl = _parse_type_declaration(member)
            if not decl:
                continue
            decl["name"] = f"{owner_name}.{decl['name']}"
            decl["owner_simple_name"] = owner_name
            nested.append(decl)
            nested.extend(_extract_nested_types(decl))
    return nested


def _extract_methods(type_decl: dict, package: str) -> list[dict]:
    methods: list[dict] = []
    owner_name = type_decl["name"]
    owner_fqn = f"{package}.{owner_name}" if package else owner_name

    for child in type_decl["node"].children:
        if child.type not in _BODY_NODE_TYPES:
            continue
        for member in child.children:
            if member.type != "function_declaration":
                continue
            name_node = member.child_by_field_name("name")
            if name_node is None:
                for sub in member.children:
                    if sub.type == "identifier":
                        name_node = sub
                        break
            if name_node is None:
                continue
            methods.append(
                {
                    "name": _get_text(name_node),
                    "kind": "method",
                    "owner_fqn": owner_fqn,
                }
            )
    return methods


def _extract_top_level_functions(root_node: Node) -> list[dict]:
    functions: list[dict] = []
    for child in root_node.children:
        if child.type != "function_declaration":
            continue
        name_node = child.child_by_field_name("name")
        if name_node is None:
            for sub in child.children:
                if sub.type == "identifier":
                    name_node = sub
                    break
        if name_node is None:
            continue
        functions.append({"name": _get_text(name_node), "kind": "function"})
    return functions


def _resolve_name(
    simple_name: str,
    source_entity: Entity,
    fqn_index: dict[str, str],
    entities: dict[str, Entity],
    aliases: dict[str, str],
) -> str | None:
    if simple_name in entities:
        return simple_name

    if simple_name in aliases:
        aliased = aliases[simple_name]
        if aliased in entities:
            return aliased

    for imp in source_entity.imports:
        if imp.endswith(f".{simple_name}") and imp in entities:
            return imp

    same_pkg_fqn = f"{source_entity.package}.{simple_name}"
    if same_pkg_fqn in entities:
        return same_pkg_fqn

    # A qualified name that did not resolve exactly must not bind to an
    # unrelated same-leaf type in another package.
    if "." in simple_name:
        return None

    if simple_name in fqn_index:
        return fqn_index[simple_name]

    return None


@register_parser
class KotlinParser(LanguageParser):
    """Kotlin source code parser using tree-sitter."""

    @property
    def language(self) -> str:
        return "kotlin"

    @property
    def file_extensions(self) -> list[str]:
        return [".kt", ".kts"]

    def parse(self, files: list[Path], root: Path) -> DependencyGraph:
        """Parse Kotlin source files and extract a dependency graph.

        Two-pass approach mirrors the Java parser:
        1. Collect entities (classes, interfaces, objects, enums, methods, top-level functions)
        2. Resolve imports / inheritance edges to FQNs
        """
        parser = Parser(KOTLIN_LANGUAGE)
        entities: dict[str, Entity] = {}
        edges: list[Edge] = []
        packages: dict[str, list[str]] = {}
        entity_aliases: dict[str, dict[str, str]] = {}

        for kotlin_file in files:
            try:
                source = kotlin_file.read_bytes()
                tree = parser.parse(source)
            except Exception:
                continue

            root_node = tree.root_node
            package = _extract_package(root_node)
            imports, aliases = _extract_imports(root_node)
            rel_path = str(kotlin_file.relative_to(root))

            type_decls = _extract_type_declarations(root_node)
            for decl in type_decls:
                type_name = decl["name"]
                fqn = f"{package}.{type_name}" if package else type_name
                props: dict[str, object] = {}
                if decl.get("is_data"):
                    props["data"] = True
                if decl.get("is_sealed"):
                    props["sealed"] = True
                if aliases:
                    props["import_aliases"] = dict(aliases)

                entity = Entity(
                    fqn=fqn,
                    name=type_name.split(".")[-1],
                    package=package,
                    file_path=rel_path,
                    kind=decl["kind"],
                    language="kotlin",
                    imports=imports,
                    superclass=decl["superclass"],
                    interfaces=decl["interfaces"],
                    properties=props,
                )
                entities[fqn] = entity
                entity_aliases[fqn] = dict(aliases)
                packages.setdefault(package, []).append(fqn)

                for method_decl in _extract_methods(decl, package):
                    method_fqn = f"{method_decl['owner_fqn']}.{method_decl['name']}"
                    entities[method_fqn] = Entity(
                        fqn=method_fqn,
                        name=method_decl["name"],
                        package=package,
                        file_path=rel_path,
                        kind="method",
                        language="kotlin",
                        imports=imports,
                        properties={"owner": method_decl["owner_fqn"]},
                    )
                    packages.setdefault(package, []).append(method_fqn)

            for fn in _extract_top_level_functions(root_node):
                fqn = f"{package}.{fn['name']}" if package else fn["name"]
                entities[fqn] = Entity(
                    fqn=fqn,
                    name=fn["name"],
                    package=package,
                    file_path=rel_path,
                    kind="function",
                    language="kotlin",
                    imports=imports,
                    properties={"import_aliases": dict(aliases)} if aliases else {},
                )
                entity_aliases[fqn] = dict(aliases)
                packages.setdefault(package, []).append(fqn)

        fqn_index: dict[str, str] = {}
        for entity in entities.values():
            # Prefer leaf name for nested types; later duplicates keep last write
            # which is acceptable for same-name types across packages (same as Java).
            fqn_index[entity.name] = entity.fqn

        for entity in entities.values():
            aliases = entity_aliases.get(entity.fqn, {})
            for imp in entity.imports:
                if imp in entities:
                    edges.append(Edge(source=entity.fqn, target=imp, relation="import"))
                else:
                    simple = imp.split(".")[-1]
                    if simple in fqn_index and fqn_index[simple] != entity.fqn:
                        edges.append(
                            Edge(
                                source=entity.fqn,
                                target=fqn_index[simple],
                                relation="import",
                            )
                        )

            if entity.superclass:
                target_fqn = _resolve_name(
                    entity.superclass, entity, fqn_index, entities, aliases
                )
                if target_fqn:
                    edges.append(
                        Edge(source=entity.fqn, target=target_fqn, relation="extends")
                    )

            for iface in entity.interfaces:
                target_fqn = _resolve_name(iface, entity, fqn_index, entities, aliases)
                if target_fqn:
                    edges.append(
                        Edge(
                            source=entity.fqn,
                            target=target_fqn,
                            relation="implements",
                        )
                    )

        seen: set[tuple[str, str, str]] = set()
        unique_edges: list[Edge] = []
        for edge in edges:
            key = (edge.source, edge.target, edge.relation)
            if key not in seen:
                seen.add(key)
                unique_edges.append(edge)

        return DependencyGraph(
            entities=entities, edges=unique_edges, packages=packages,
            metadata={"relation_coverage": {"kotlin": {
                "collected_relations": ["extends", "implements", "import"],
                "call_coverage": "not_collected",
            }}},
        )
