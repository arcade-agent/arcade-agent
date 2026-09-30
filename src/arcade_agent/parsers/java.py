"""Java parser using tree-sitter."""

from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from arcade_agent.parsers.base import LanguageParser, register_parser
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity

JAVA_LANGUAGE = Language(tsjava.language())


def _get_text(node) -> str:
    """Get the text content of a node."""
    if node is None:
        return ""
    return node.text.decode()


def _extract_package(root_node) -> str:
    """Extract the package declaration from a Java file."""
    for child in root_node.children:
        if child.type == "package_declaration":
            for sub in child.children:
                if sub.type == "scoped_identifier":
                    return _get_text(sub)
    return ""


def _extract_imports(root_node) -> list[str]:
    """Extract all import declarations."""
    imports = []
    for child in root_node.children:
        if child.type == "import_declaration":
            for sub in child.children:
                if sub.type == "scoped_identifier":
                    imports.append(_get_text(sub))
    return imports


def _extract_type_declarations(root_node) -> list[dict]:
    """Extract class, interface, and enum declarations with inheritance info."""
    decls = []
    for node in root_node.children:
        if node.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            decl = _parse_type_declaration(node)
            if decl:
                decls.append(decl)
    return decls


def _parse_type_declaration(node) -> dict | None:
    """Parse a single type declaration node."""
    name_node = node.child_by_field_name("name")
    if not name_node:
        return None

    kind_map = {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
    }

    decl = {
        "name": _get_text(name_node),
        "kind": kind_map.get(node.type, "class"),
        "superclass": None,
        "interfaces": [],
        "node": node,
    }

    for child in node.children:
        if child.type == "superclass":
            for sub in child.children:
                if sub.type == "type_identifier":
                    decl["superclass"] = _get_text(sub)
                    break

        if child.type == "super_interfaces":
            for sub in child.children:
                if sub.type == "type_list":
                    for type_node in sub.children:
                        if type_node.type == "type_identifier":
                            decl["interfaces"].append(_get_text(type_node))

    return decl


def _extract_methods(type_decl: dict, package: str) -> list[dict]:
    """Extract methods and constructors from a type declaration."""
    methods = []
    body_types = {"class_body", "interface_body", "enum_body"}
    owner_name = type_decl["name"]
    owner_fqn = f"{package}.{owner_name}" if package else owner_name

    for child in type_decl["node"].children:
        if child.type not in body_types:
            continue
        for member in child.children:
            if member.type not in {"method_declaration", "constructor_declaration"}:
                continue

            name_node = member.child_by_field_name("name")
            if name_node is None:
                continue

            methods.append({
                "name": _get_text(name_node),
                "kind": "method",
                "owner_fqn": owner_fqn,
            })

    return methods


def _resolve_name(
    simple_name: str,
    source_entity: Entity,
    fqn_index: dict[str, str],
    entities: dict[str, Entity],
) -> str | None:
    """Resolve a simple class name to its FQN."""
    if simple_name in entities:
        return simple_name

    for imp in source_entity.imports:
        if imp.endswith(f".{simple_name}") and imp in entities:
            return imp

    same_pkg_fqn = f"{source_entity.package}.{simple_name}"
    if same_pkg_fqn in entities:
        return same_pkg_fqn

    if simple_name in fqn_index:
        return fqn_index[simple_name]

    return None


@register_parser
class JavaParser(LanguageParser):
    """Java source code parser using tree-sitter."""

    @property
    def language(self) -> str:
        return "java"

    @property
    def file_extensions(self) -> list[str]:
        return [".java"]

    def parse(self, files: list[Path], root: Path) -> DependencyGraph:
        """Parse Java source files and extract a dependency graph.

        Uses a two-pass approach:
        1. First pass: collect all entities, build FQN index
        2. Second pass: resolve imports, superclasses, interfaces to FQNs

        Args:
            files: List of .java file paths.
            root: Root directory of the project.

        Returns:
            DependencyGraph with entities, edges, and package info.
        """
        parser = Parser(JAVA_LANGUAGE)
        entities: dict[str, Entity] = {}
        edges: list[Edge] = []
        packages: dict[str, list[str]] = {}

        # First pass: collect all entities
        for java_file in files:
            try:
                source = java_file.read_bytes()
                tree = parser.parse(source)
            except Exception:
                continue

            root_node = tree.root_node
            package = _extract_package(root_node)
            imports = _extract_imports(root_node)
            rel_path = str(java_file.relative_to(root))

            type_decls = _extract_type_declarations(root_node)
            for decl in type_decls:
                class_name = decl["name"]
                fqn = f"{package}.{class_name}" if package else class_name

                entity = Entity(
                    fqn=fqn,
                    name=class_name,
                    package=package,
                    file_path=rel_path,
                    kind=decl["kind"],
                    language="java",
                    imports=imports,
                    superclass=decl["superclass"],
                    interfaces=decl["interfaces"],
                )

                entities[fqn] = entity
                packages.setdefault(package, []).append(fqn)

                for method_decl in _extract_methods(decl, package):
                    method_fqn = f"{method_decl['owner_fqn']}.{method_decl['name']}"
                    entities[method_fqn] = Entity(
                        fqn=method_fqn,
                        name=method_decl["name"],
                        package=package,
                        file_path=rel_path,
                        kind="method",
                        language="java",
                        imports=imports,
                        properties={"owner": method_decl["owner_fqn"]},
                    )
                    packages.setdefault(package, []).append(method_fqn)

        # Build name -> fqn index
        fqn_index: dict[str, str] = {}
        for entity in entities.values():
            fqn_index[entity.name] = entity.fqn

        # Second pass: resolve dependencies
        for entity in entities.values():
            # Import edges
            for imp in entity.imports:
                target = imp
                if target in entities:
                    edges.append(Edge(source=entity.fqn, target=target, relation="import"))
                else:
                    simple = imp.split(".")[-1]
                    if simple in fqn_index and fqn_index[simple] != entity.fqn:
                        edges.append(
                            Edge(source=entity.fqn, target=fqn_index[simple], relation="import")
                        )

            # Inheritance edge
            if entity.superclass:
                target_fqn = _resolve_name(entity.superclass, entity, fqn_index, entities)
                if target_fqn:
                    edges.append(Edge(source=entity.fqn, target=target_fqn, relation="extends"))

            # Interface edges
            for iface in entity.interfaces:
                target_fqn = _resolve_name(iface, entity, fqn_index, entities)
                if target_fqn:
                    edges.append(
                        Edge(source=entity.fqn, target=target_fqn, relation="implements")
                    )

        # Deduplicate edges
        seen: set[tuple[str, str, str]] = set()
        unique_edges: list[Edge] = []
        for edge in edges:
            key = (edge.source, edge.target, edge.relation)
            if key not in seen:
                seen.add(key)
                unique_edges.append(edge)

        return DependencyGraph(
            entities=entities, edges=unique_edges, packages=packages,
            metadata={"relation_coverage": {"java": {
                "collected_relations": ["extends", "implements", "import"],
                "call_coverage": "not_collected",
            }}},
        )
