"""Java parser using tree-sitter."""

from pathlib import Path
from typing import Any

import tree_sitter_java as tsjava
from tree_sitter import Language, Node, Parser

from arcade_agent.parsers.base import LanguageParser, register_parser
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity

JAVA_LANGUAGE = Language(tsjava.language())


def _get_text(node: Node | None) -> str:
    """Get the text content of a node."""
    if node is None or node.text is None:
        return ""
    return node.text.decode()


def _extract_package(root_node: Node) -> str:
    """Extract the package declaration from a Java file."""
    for child in root_node.children:
        if child.type == "package_declaration":
            for sub in child.children:
                if sub.type == "scoped_identifier":
                    return _get_text(sub)
    return ""


def _extract_imports(root_node: Node) -> list[tuple[str, bool]]:
    """Extract all import declarations as ``(name, is_wildcard)`` pairs."""
    imports = []
    for child in root_node.children:
        if child.type == "import_declaration":
            wildcard = any(sub.type == "asterisk" for sub in child.children)
            for sub in child.children:
                if sub.type == "scoped_identifier":
                    imports.append((_get_text(sub), wildcard))
    return imports


def _extract_referenced_names(node: Node) -> set[str]:
    """Collect identifiers used in code within a node.

    Comments (including Javadoc) are separate tree-sitter nodes without
    identifier children, so ``{@link Foo}`` never counts as a reference.
    """
    names: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in ("identifier", "type_identifier"):
            names.add(_get_text(n))
        stack.extend(n.children)
    return names


def _used_imports(imports: list[tuple[str, bool]], refs: set[str]) -> list[str]:
    """Keep imports referenced in code; wildcard imports can't be verified, so stay."""
    return [name for name, wildcard in imports if wildcard or name.split(".")[-1] in refs]


def _extract_type_declarations(root_node: Node) -> list[dict[str, Any]]:
    """Extract class, interface, and enum declarations with inheritance info."""
    decls = []
    for node in root_node.children:
        if node.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            decl = _parse_type_declaration(node)
            if decl:
                decls.append(decl)
    return decls


def _parse_type_declaration(node: Node) -> dict[str, Any] | None:
    """Parse a single type declaration node."""
    name_node = node.child_by_field_name("name")
    if not name_node:
        return None

    kind_map = {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
    }

    decl: dict[str, Any] = {
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


def _extract_methods(type_decl: dict[str, Any], package: str) -> list[dict[str, Any]]:
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
                "node": member,
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
        # Simple names each entity references; same-package types need no
        # import, so these are the only evidence of that coupling.
        refs_by_fqn: dict[str, set[str]] = {}

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

                decl_refs = _extract_referenced_names(decl["node"])
                refs_by_fqn[fqn] = decl_refs
                entity = Entity(
                    fqn=fqn,
                    name=class_name,
                    package=package,
                    file_path=rel_path,
                    kind=decl["kind"],
                    language="java",
                    imports=_used_imports(imports, decl_refs),
                    superclass=decl["superclass"],
                    interfaces=decl["interfaces"],
                )

                entities[fqn] = entity
                packages.setdefault(package, []).append(fqn)

                for method_decl in _extract_methods(decl, package):
                    method_fqn = f"{method_decl['owner_fqn']}.{method_decl['name']}"
                    method_refs = _extract_referenced_names(method_decl["node"])
                    refs_by_fqn[method_fqn] = method_refs
                    entities[method_fqn] = Entity(
                        fqn=method_fqn,
                        name=method_decl["name"],
                        package=package,
                        file_path=rel_path,
                        kind="method",
                        language="java",
                        imports=_used_imports(imports, method_refs),
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

            # Same-package type references (no import needed in Java)
            owner = entity.properties.get("owner") if entity.properties else None
            for name in refs_by_fqn.get(entity.fqn, ()):
                if not name[:1].isupper():
                    continue
                target = f"{entity.package}.{name}" if entity.package else name
                if (
                    target in entities
                    and target not in (entity.fqn, owner)
                    and entities[target].kind != "method"
                ):
                    edges.append(Edge(source=entity.fqn, target=target, relation="uses"))

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

        return DependencyGraph(entities=entities, edges=unique_edges, packages=packages)
