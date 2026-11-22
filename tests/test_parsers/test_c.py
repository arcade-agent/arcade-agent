"""Tests for the C/C++ parser."""

from pathlib import Path

import pytest

try:
    from arcade_agent.parsers.c import CParser
except ImportError:
    pytest.skip("tree-sitter-c not installed", allow_module_level=True)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "c_project"


def test_c_parser_properties():
    parser = CParser()
    assert parser.language == "c"
    assert ".c" in parser.file_extensions
    assert ".cpp" in parser.file_extensions
    assert ".h" in parser.file_extensions


def test_c_parser_entities():
    parser = CParser()
    c_files = sorted(FIXTURES_DIR.glob("*.c")) + sorted(FIXTURES_DIR.glob("*.h"))
    graph = parser.parse(c_files, FIXTURES_DIR)

    assert graph.num_entities >= 3
    entity_names = {e.name for e in graph.entities.values()}
    assert "Point" in entity_names
    assert "Rectangle" in entity_names


def test_c_parser_includes():
    parser = CParser()
    c_files = sorted(FIXTURES_DIR.glob("*.c")) + sorted(FIXTURES_DIR.glob("*.h"))
    graph = parser.parse(c_files, FIXTURES_DIR)

    # main.c includes util.h, so there should be an import edge
    import_edges = [e for e in graph.edges if e.relation == "import"]
    assert len(import_edges) >= 1


def test_cpp_parser_classes():
    parser = CParser()
    cpp_files = sorted(FIXTURES_DIR.glob("*.cpp")) + sorted(FIXTURES_DIR.glob("*.hpp"))
    graph = parser.parse(cpp_files, FIXTURES_DIR)

    entity_names = {e.name for e in graph.entities.values()}
    assert "Shape" in entity_names
    assert "Circle" in entity_names
    assert "Square" in entity_names


def test_cpp_parser_inheritance():
    parser = CParser()
    cpp_files = sorted(FIXTURES_DIR.glob("*.cpp")) + sorted(FIXTURES_DIR.glob("*.hpp"))
    graph = parser.parse(cpp_files, FIXTURES_DIR)

    # Circle and Square extend Shape
    extends_edges = [e for e in graph.edges if e.relation == "extends"]
    assert len(extends_edges) >= 2

    extends_targets = {e.target for e in extends_edges}
    # Should extend Shape
    shape_fqns = [fqn for fqn, e in graph.entities.items() if e.name == "Shape"]
    assert any(t in shape_fqns for t in extends_targets)


def test_c_parser_empty():
    parser = CParser()
    graph = parser.parse([], Path("/tmp"))
    assert graph.num_entities == 0
    assert graph.num_edges == 0


def test_c_parser_mixed():
    """Test parsing all C and C++ files together."""
    parser = CParser()
    all_files = (
        sorted(FIXTURES_DIR.glob("*.c"))
        + sorted(FIXTURES_DIR.glob("*.h"))
        + sorted(FIXTURES_DIR.glob("*.cpp"))
        + sorted(FIXTURES_DIR.glob("*.hpp"))
    )
    graph = parser.parse(all_files, FIXTURES_DIR)

    # Should have entities from both C and C++ files
    assert graph.num_entities >= 5
    languages = {e.language for e in graph.entities.values()}
    assert "c" in languages
    assert "cpp" in languages


def test_c_parser_deeply_nested_ast_no_recursion_error(tmp_path):
    """Deeply nested C ASTs (e.g. >1500 levels) must parse without RecursionError."""
    depth = 1500
    nested_expr = "(" * depth + "1" + ")" * depth
    src = tmp_path / "deep.c"
    src.write_text(f"int deep_func(void) {{ return {nested_expr}; }}\n")

    parser = CParser()
    graph = parser.parse([src], tmp_path)

    assert any(
        e.name == "deep_func" and e.kind == "function"
        for e in graph.entities.values()
    )
