"""C/C++ parser using tree-sitter."""

from pathlib import Path

import tree_sitter_c as tsc
import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser

from arcade_agent.parsers.base import LanguageParser, register_parser
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity

C_LANGUAGE = Language(tsc.language())
CPP_LANGUAGE = Language(tscpp.language())

_CPP_EXTENSIONS = {".cpp", ".hpp", ".cc", ".cxx", ".hxx", ".C", ".H"}
_C_EXTENSIONS = {".c", ".h"}


def _get_text(node) -> str:
    if node is None:
        return ""
    return node.text.decode()


def _extract_includes(root_node) -> list[str]:
    """Extract #include directives."""
    includes = []
    for child in root_node.children:
        if child.type == "preproc_include":
            for sub in child.children:
                if sub.type in ("string_literal", "system_lib_string"):
                    path = _get_text(sub).strip('"<>')
                    includes.append(path)
    return includes


def _collect_nodes(node, type_names: set[str]) -> list:
    """Iteratively collect nodes of given types in pre-order."""
    results = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in type_names:
            results.append(current)
        if current.children:
            stack.extend(reversed(current.children))
    return results


def _extract_declarations(root_node, is_cpp: bool) -> list[dict]:
    """Extract struct, class, enum, union, and function declarations.

    Searches the entire AST recursively to handle declarations nested
    inside preprocessor guards (#ifndef/#ifdef).
    """
    decls = []

    type_specifiers = {"struct_specifier", "enum_specifier", "union_specifier"}
    if is_cpp:
        type_specifiers.add("class_specifier")

    # Collect all function definitions
    for node in _collect_nodes(root_node, {"function_definition"}):
        decl = _parse_function(node)
        if decl:
            decls.append(decl)

    # Collect all type specifiers (struct, class, enum, union)
    for node in _collect_nodes(root_node, type_specifiers):
        decl = _parse_type_decl(node, is_cpp)
        if decl:
            decls.append(decl)

    return decls


def _parse_function(node) -> dict | None:
    """Parse a function definition."""
    declarator = node.child_by_field_name("declarator")
    if not declarator:
        return None

    # Walk down to find the function name
    name = _find_identifier(declarator)
    if not name:
        return None

    return {
        "name": name,
        "kind": "function",
        "superclass": None,
        "bases": [],
    }


def _find_identifier(node) -> str | None:
    """Iteratively find the first identifier name in a declarator (pre-order)."""
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in ("identifier", "field_identifier"):
            return _get_text(current)
        if current.children:
            stack.extend(reversed(current.children))
    return None


def _parse_type_decl(node, is_cpp: bool) -> dict | None:
    """Parse a struct/class/enum/union declaration."""
    name_node = node.child_by_field_name("name")
    if not name_node:
        return None

    name = _get_text(name_node)
    if not name:
        return None

    kind_map = {
        "struct_specifier": "struct",
        "class_specifier": "class",
        "enum_specifier": "enum",
        "union_specifier": "union",
    }
    kind = kind_map.get(node.type, "struct")

    # Extract base classes (C++ only)
    bases = []
    superclass = None
    if is_cpp:
        for child in node.children:
            if child.type == "base_class_clause":
                for base in child.children:
                    if base.type == "type_identifier":
                        bases.append(_get_text(base))
                    elif base.type == "qualified_identifier":
                        bases.append(_get_text(base))
        if bases:
            superclass = bases[0]

    return {
        "name": name,
        "kind": kind,
        "superclass": superclass,
        "bases": bases[1:] if len(bases) > 1 else [],
    }


def _module_from_path(file_path: Path, root: Path) -> str:
    """Derive a module/namespace from file path."""
    rel = file_path.relative_to(root)
    parts = list(rel.parts)
    # Remove extension from last part
    if parts:
        stem = parts[-1].rsplit(".", 1)[0]
        parts[-1] = stem
    return "/".join(parts)


def _include_to_fqn(include: str) -> str:
    """Convert an include path to a lookup key."""
    # "mylib/util.h" -> "mylib/util"
    return include.rsplit(".", 1)[0] if "." in include else include


@register_parser
class CParser(LanguageParser):
    """C/C++ source code parser using tree-sitter."""

    @property
    def language(self) -> str:
        return "c"

    @property
    def file_extensions(self) -> list[str]:
        return [".c", ".h", ".cpp", ".hpp", ".cc", ".cxx"]

    def parse(self, files: list[Path], root: Path) -> DependencyGraph:
        """Parse C/C++ source files and extract a dependency graph.

        Args:
            files: List of C/C++ file paths.
            root: Root directory of the project.

        Returns:
            DependencyGraph with entities, edges, and package info.
        """
        entities: dict[str, Entity] = {}
        edges: list[Edge] = []
        packages: dict[str, list[str]] = {}
        file_includes: dict[str, list[str]] = {}  # fqn -> includes
        # Map from include path stem to entity FQN
        include_index: dict[str, str] = {}

        # First pass: collect entities
        for src_file in files:
            is_cpp = src_file.suffix in _CPP_EXTENSIONS
            lang = CPP_LANGUAGE if is_cpp else C_LANGUAGE

            try:
                source = src_file.read_bytes()
                parser = Parser(lang)
                tree = parser.parse(source)
            except Exception:
                continue

            root_node = tree.root_node
            includes = _extract_includes(root_node)
            decls = _extract_declarations(root_node, is_cpp)
            rel_path = str(src_file.relative_to(root))
            module = _module_from_path(src_file, root)

            # Use directory as "package"
            package = "/".join(module.split("/")[:-1]) if "/" in module else ""

            # Register the include path for this file
            include_index[module] = module

            if not decls:
                # Register the file itself as a module entity
                fqn = module
                entity = Entity(
                    fqn=fqn,
                    name=src_file.stem,
                    package=package,
                    file_path=rel_path,
                    kind="module",
                    language="cpp" if is_cpp else "c",
                    imports=[_include_to_fqn(i) for i in includes],
                )
                entities[fqn] = entity
                packages.setdefault(package, []).append(fqn)
                file_includes[fqn] = includes
            else:
                for decl in decls:
                    fqn = f"{module}::{decl['name']}" if module else decl["name"]
                    entity = Entity(
                        fqn=fqn,
                        name=decl["name"],
                        package=package,
                        file_path=rel_path,
                        kind=decl["kind"],
                        language="cpp" if is_cpp else "c",
                        imports=[_include_to_fqn(i) for i in includes],
                        superclass=decl.get("superclass"),
                        interfaces=decl.get("bases", []),
                    )
                    entities[fqn] = entity
                    packages.setdefault(package, []).append(fqn)
                    file_includes[fqn] = includes
                    include_index[module] = fqn  # last decl wins for module mapping

        # Build name -> fqn index
        fqn_index: dict[str, str] = {}
        for entity in entities.values():
            fqn_index[entity.name] = entity.fqn

        # Second pass: resolve include edges
        for fqn, entity in entities.items():
            for inc in entity.imports:
                target_key = _include_to_fqn(inc)
                # Try direct module match
                if target_key in include_index:
                    target_fqn = include_index[target_key]
                    if target_fqn != fqn and target_fqn in entities:
                        edges.append(Edge(source=fqn, target=target_fqn, relation="import"))
                # Try matching by filename stem
                else:
                    stem = target_key.split("/")[-1]
                    if stem in fqn_index and fqn_index[stem] != fqn:
                        edges.append(Edge(source=fqn, target=fqn_index[stem], relation="import"))

            # Inheritance edges (C++)
            if entity.superclass and entity.superclass in fqn_index:
                edges.append(Edge(
                    source=fqn, target=fqn_index[entity.superclass],
                    relation="extends",
                ))
            for base in entity.interfaces:
                if base in fqn_index:
                    edges.append(Edge(source=fqn, target=fqn_index[base], relation="extends"))

        # Deduplicate
        seen: set[tuple[str, str, str]] = set()
        unique_edges: list[Edge] = []
        for edge in edges:
            key = (edge.source, edge.target, edge.relation)
            if key not in seen:
                seen.add(key)
                unique_edges.append(edge)

        return DependencyGraph(entities=entities, edges=unique_edges, packages=packages)
