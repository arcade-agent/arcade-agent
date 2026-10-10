"""Keep structural Java call limitations visible, including Java/Kotlin graphs."""

from pathlib import Path

import pytest

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.algorithms.concern import detect_concern_overload
from arcade_agent.algorithms.smells import SmellType
from arcade_agent.parsers.graph import DependencyGraph
from arcade_agent.parsers.java import JavaParser
from arcade_agent.parsers.multilang import merge_and_relink
from arcade_agent.tools.detect_smells import detect_smells


def _parse_java_chain(root: Path, *, imports_helper: bool = False) -> DependencyGraph:
    source = root / "Activity.java"
    helper_import = "import demo.support.Helper;" if imports_helper else ""
    terminal_value = "new Helper().value()" if imports_helper else "1"
    source.write_text(
        "package demo.ui;\n"
        f"{helper_import}\n"
        "class Activity {\n"
        "  int render() { return step0(); }\n"
        + "\n".join(
            f"  int step{i}() {{ return step{i + 1}(); }}" for i in range(40)
        )
        + f"\n  int step40() {{ return {terminal_value}; }}\n}}\n"
    )
    return JavaParser().parse([source], root)


def _architecture_for_java(graph: DependencyGraph) -> Architecture:
    return Architecture(components=[Component(
        name="Ui", responsibility="Single-computation chain",
        entities=[fqn for fqn, entity in graph.entities.items() if entity.language == "java"],
    )], algorithm="test")


def test_java_sparse_chain_reports_insufficient_call_coverage(tmp_path: Path) -> None:
    graph = _parse_java_chain(tmp_path)
    coverage = graph.relation_coverage()["java"]
    assert coverage["call_coverage"] == "not_collected"
    assert set(coverage["collected_relations"]) == {"extends", "implements", "import"}

    architecture = _architecture_for_java(graph)
    assert len(architecture.components[0].entities) > 40
    finding, = detect_concern_overload(architecture, graph)
    assert finding["coverage_status"] == "insufficient"
    assert finding["incomplete_call_languages"] == ["java"]
    assert finding["severity"] != "high"


def test_java_component_is_qualified_in_java_kotlin_graph(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_kotlin")
    from arcade_agent.parsers.kotlin import KotlinParser

    java = _parse_java_chain(tmp_path, imports_helper=True)
    kotlin_source = tmp_path / "Helper.kt"
    kotlin_source.write_text("package demo.support\nclass Helper { fun value(): Int = 1 }\n")
    kotlin = KotlinParser().parse([kotlin_source], tmp_path)
    graph = merge_and_relink(java, kotlin)

    # Cross-language imports remain real structural evidence; they do not supply
    # the Java chain's internal calls, which the parser has not collected.
    assert ("demo.ui.Activity", "demo.support.Helper", "import") in graph.to_edge_tuples()
    assert {"java", "kotlin"} <= graph.relation_coverage().keys()
    architecture = _architecture_for_java(graph)
    architecture.components.append(Component(
        name="Support", responsibility="Kotlin helper",
        entities=list(kotlin.entities),
    ))
    finding, = detect_concern_overload(architecture, graph)
    assert finding["component"] == "Ui"
    assert finding["coverage_status"] == "insufficient"
    assert finding["incomplete_call_languages"] == ["java"]

    smells = detect_smells(architecture, graph)
    concern, = [smell for smell in smells if smell.smell_type == SmellType.CONCERN_OVERLOAD]
    assert concern.affected_components == ["Ui"]
    assert "insufficient coverage" in concern.description
    assert concern.severity != "high"
