"""Tests for the Java parser."""

from pathlib import Path

from arcade_agent.parsers.java import JavaParser


def test_java_parser_entities(java_files, fixtures_dir):
    parser = JavaParser()
    graph = parser.parse(java_files, fixtures_dir)

    assert graph.num_entities >= 3
    assert "com.example.calc.Calculator" in graph.entities
    assert "com.example.util.MathHelper" in graph.entities
    assert "com.example.calc.AdvancedCalculator" in graph.entities


def test_java_parser_entity_details(java_files, fixtures_dir):
    parser = JavaParser()
    graph = parser.parse(java_files, fixtures_dir)

    calc = graph.entities["com.example.calc.Calculator"]
    assert calc.name == "Calculator"
    assert calc.package == "com.example.calc"
    assert calc.kind == "class"
    assert calc.language == "java"
    assert "com.example.util.MathHelper" in calc.imports


def test_java_parser_edges(java_files, fixtures_dir):
    parser = JavaParser()
    graph = parser.parse(java_files, fixtures_dir)

    edge_tuples = {(e.source, e.target, e.relation) for e in graph.edges}

    # Calculator imports MathHelper
    assert ("com.example.calc.Calculator", "com.example.util.MathHelper", "import") in edge_tuples

    # AdvancedCalculator extends Calculator
    assert (
        "com.example.calc.AdvancedCalculator",
        "com.example.calc.Calculator",
        "extends",
    ) in edge_tuples


def test_java_parser_packages(java_files, fixtures_dir):
    parser = JavaParser()
    graph = parser.parse(java_files, fixtures_dir)

    assert "com.example.calc" in graph.packages
    assert "com.example.util" in graph.packages
    assert len(graph.packages["com.example.calc"]) >= 2
    assert len(graph.packages["com.example.util"]) >= 1


def test_java_parser_empty():
    parser = JavaParser()
    graph = parser.parse([], Path("/tmp"))
    assert graph.num_entities == 0
    assert graph.num_edges == 0


def test_java_parser_properties():
    parser = JavaParser()
    assert parser.language == "java"
    assert ".java" in parser.file_extensions


def test_java_parser_extracts_methods(java_files, fixtures_dir):
    parser = JavaParser()
    graph = parser.parse(java_files, fixtures_dir)

    assert "com.example.calc.Calculator.add" in graph.entities
    assert graph.entities["com.example.calc.Calculator.add"].kind == "method"
    assert graph.entities["com.example.calc.Calculator.add"].properties["owner"] == (
        "com.example.calc.Calculator"
    )


def test_java_parser_ignores_javadoc_only_and_unused_imports(tmp_path):
    """Issue #47: only imports referenced in code produce edges (no phantom cycles)."""
    src = tmp_path / "src"
    (src / "p" / "a").mkdir(parents=True)
    (src / "p" / "b").mkdir()
    (src / "p" / "a" / "Pool.java").write_text(
        "package p.a;\n"
        "import p.b.Doc;\n"
        "import p.b.Dead;\n"
        "import p.b.Real;\n"
        "import p.b.util.*;\n"
        "/** See {@link Doc#evict}. */\n"
        "public class Pool {\n"
        "    // Dead is only mentioned in a comment\n"
        "    private Real real;\n"
        "    public void uses() { new Real(); }\n"
        "    public void idle() {}\n"
        "}\n"
    )
    for name in ("Doc", "Dead", "Real"):
        (src / "p" / "b" / f"{name}.java").write_text(
            f"package p.b;\npublic class {name} {{ p.a.Pool pool; }}\n"
        )

    graph = JavaParser().parse(sorted(src.rglob("*.java")), src)
    edges = {(e.source, e.target) for e in graph.edges if e.relation == "import"}

    assert ("p.a.Pool", "p.b.Real") in edges
    assert ("p.a.Pool", "p.b.Doc") not in edges
    assert ("p.a.Pool", "p.b.Dead") not in edges
    assert graph.entities["p.a.Pool"].imports == ["p.b.Real", "p.b.util"]  # wildcard kept
    # Method entities only carry imports their own body references.
    assert graph.entities["p.a.Pool.uses"].imports == ["p.b.Real", "p.b.util"]
    assert graph.entities["p.a.Pool.idle"].imports == ["p.b.util"]


def test_java_parser_links_same_package_references(tmp_path):
    from arcade_agent.parsers.java import JavaParser

    pkg = tmp_path / "com" / "x"
    pkg.mkdir(parents=True)
    a = pkg / "A.java"
    a.write_text("package com.x;\npublic class A {\n  public int v() { return 1; }\n}\n")
    b = pkg / "B.java"
    b.write_text(
        "package com.x;\n"
        "public class B {\n"
        "  private final A a = new A();\n"
        "  public int w() { return a.v(); }\n"
        "  public B self() { return this; }\n"
        "}\n"
    )

    edges = JavaParser().parse([a, b], tmp_path).to_edge_tuples()

    assert ("com.x.B", "com.x.A", "uses") in edges
    # No self-edges, and nothing pointing at the owning class from its methods.
    assert not any(src == dst for src, dst, _ in edges)
    assert ("com.x.B.self", "com.x.B", "uses") not in edges
