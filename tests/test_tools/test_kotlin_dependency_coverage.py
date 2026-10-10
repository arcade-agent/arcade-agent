"""Guard Kotlin findings without freezing today's parser capabilities."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

pytest.importorskip("tree_sitter_kotlin")

from arcade_agent.algorithms.architecture import Architecture, Component  # noqa: E402
from arcade_agent.algorithms.concern import (  # noqa: E402
    ConcernOverloadFinding,
    detect_concern_overload,
)
from arcade_agent.algorithms.smells import SmellInstance, SmellType  # noqa: E402
from arcade_agent.cache import cache_key, get_cached_graph, put_cached_graph  # noqa: E402
from arcade_agent.ci.graph_filter import _filter_non_architectural_entities  # noqa: E402
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity  # noqa: E402
from arcade_agent.parsers.multilang import merge_and_relink  # noqa: E402
from arcade_agent.serialization import dict_to_graph, graph_to_dict, serialize_result  # noqa: E402
from arcade_agent.tools.analyze import AnalysisResult, analyze  # noqa: E402
from arcade_agent.tools.detect_smells import detect_smells  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/kotlin_dependency_coverage/src"
CallCoverage = Literal["unknown", "not_collected", "partial", "complete"]


def _has_structured_qualification(finding: ConcernOverloadFinding) -> bool:
    return (
        finding.get("coverage_status") == "insufficient"
        and bool(finding.get("incomplete_call_languages"))
    )


def _is_unqualified_high(
    smell: SmellInstance, findings: list[ConcernOverloadFinding],
) -> bool:
    if smell.smell_type != SmellType.CONCERN_OVERLOAD or smell.severity != "high":
        return False
    # SmellInstance has no structured coverage field. Require the detector's
    # structured evidence AND a limitation visible to the report consumer;
    # matching a phrase alone would let an unsupported HIGH pass this regression.
    has_evidence = bool(smell.affected_components) and all(
        any(
            item["component"] == component and _has_structured_qualification(item)
            for item in findings
        )
        for component in smell.affected_components
    )
    visible_text = f"{smell.description} {smell.explanation}".casefold()
    return not (has_evidence and "insufficient coverage" in visible_text)


def _sparse_graph(status: CallCoverage | None) -> tuple[Architecture, DependencyGraph]:
    """Model known sparse source, independent of the Kotlin parser's progress."""
    entities = {
        f"ui.task{i}": Entity(
            fqn=f"ui.task{i}", name=f"task{i}", package="ui",
            file_path="Tasks.kt", kind="function", language="kotlin",
        )
        for i in range(45)
    }
    graph = DependencyGraph(entities=entities, packages={"ui": list(entities)})
    if status is not None:
        graph.metadata["relation_coverage"] = {"kotlin": {
            "collected_relations": (
                ["import", "calls"] if status in {"partial", "complete"} else ["import"]
            ),
            "call_coverage": status,
        }}
    architecture = Architecture(components=[Component(
        name="Ui", responsibility="Synthetic sparse component", entities=list(entities),
    )], algorithm="test")
    return architecture, graph


@pytest.fixture(scope="module")
def analysis() -> Iterator[AnalysisResult]:
    result = asyncio.run(analyze(
        str(FIXTURE), language="kotlin", algorithm="pkg",
        exclude_tests=True, use_cache=False, use_llm=False,
    ))
    yield result
    result.repository.cleanup()


def test_file_import_attribution_is_not_method_usage(analysis: AnalysisResult) -> None:
    graph = analysis.graph
    edges = [
        edge for edge in graph.edges
        if edge.source.startswith("renderer.") and edge.relation == "import"
    ]
    assert edges
    assert all(graph.entities[edge.target].package == "canvas" for edge in edges)
    file_targets = {(graph.entities[e.source].file_path, e.target) for e in edges}
    assert {target for _, target in file_targets} == {
        "canvas.Bitmap", "canvas.Canvas", "canvas.Paint",
    }
    # Import attribution, including any edge from close(), is not a call.
    assert "fun close() {}" in (FIXTURE / "TimelineFrameRenderer.kt").read_text()


def test_explicit_internal_calls_restore_cohesion(analysis: AnalysisResult) -> None:
    graph = analysis.graph
    # Controlled counterfactual: add exactly the calls explicitly present in source.
    # This is evidence for detector sensitivity, not a replacement Kotlin resolver.
    owner = "ui.MainActivity"
    calls = [Edge(f"{owner}.render", f"{owner}.step01", "calls")]
    calls.extend(
        Edge(f"{owner}.step{i:02d}", f"{owner}.step{i+1:02d}", "calls")
        for i in range(1, 40)
    )
    source = (FIXTURE / "MainActivity.kt").read_text()
    for edge in calls:
        caller = edge.source.rsplit(".", 1)[1]
        callee = edge.target.rsplit(".", 1)[1]
        assert f"fun {caller}(): Int = {callee}()" in source
        assert edge.source in graph.entities and edge.target in graph.entities
    # A future extractor may already collect some or all of these calls.
    existing = set(graph.to_edge_tuples())
    missing = [
        edge for edge in calls
        if (edge.source, edge.target, edge.relation) not in existing
    ]
    findings = detect_concern_overload(
        analysis.architecture, replace(graph, edges=[*graph.edges, *missing]),
    )
    assert not any(item["component"] == "Ui" for item in findings)


def test_source_import_cycle_survives_independently(analysis: AnalysisResult) -> None:
    graph = analysis.graph
    assert {
        ("data.DataNode", "export.ExportNode", "import"),
        ("export.ExportNode", "view.ViewNode", "import"),
        ("view.ViewNode", "data.DataNode", "import"),
    } <= set(graph.to_edge_tuples())
    cycles = [smell for smell in analysis.smells if smell.smell_type == "Dependency Cycle"]
    assert any(
        {"Data", "Export", "View"} <= set(smell.affected_components) for smell in cycles
    )


def test_kotlin_fixture_does_not_emit_unqualified_high_cohesion_finding(
    analysis: AnalysisResult,
) -> None:
    ui = next(component for component in analysis.architecture.components if component.name == "Ui")
    assert {"ui.MainActivity.render", "ui.MainActivity.step40"} <= set(ui.entities)
    findings = detect_concern_overload(analysis.architecture, analysis.graph)
    assert not any(
        item["component"] == "Ui" and item["severity"] == "high"
        and not _has_structured_qualification(item)
        for item in findings
    )
    assert not any(
        "Ui" in smell.affected_components and _is_unqualified_high(smell, findings)
        for smell in analysis.smells
    )


@pytest.mark.parametrize("status", ["unknown", "not_collected", "partial", "complete"])
def test_sparse_finding_respects_declared_call_coverage(status: CallCoverage) -> None:
    architecture, graph = _sparse_graph(status)
    findings = detect_concern_overload(architecture, graph)
    if status == "complete":
        assert findings and all(item["severity"] == "high" for item in findings)
        assert all("coverage_status" not in item for item in findings)
    else:
        # Suppression, downgrade, and a qualified HIGH are all valid policies.
        assert all(
            item["severity"] != "high" or _has_structured_qualification(item)
            for item in findings
        )
        assert not any(
            _is_unqualified_high(smell, findings) for smell in detect_smells(architecture, graph)
        )


def test_coverage_survives_graph_boundaries_and_cache(
    analysis: AnalysisResult, tmp_path: Path,
) -> None:
    graph = analysis.graph
    expected = graph.relation_coverage()
    assert "kotlin" in expected
    assert graph.merge(graph).relation_coverage() == expected
    assert merge_and_relink(graph).relation_coverage() == expected
    assert _filter_non_architectural_entities(graph).relation_coverage() == expected
    assert dict_to_graph(graph_to_dict(graph)).relation_coverage() == expected
    assert serialize_result(graph)["metadata"]["relation_coverage"] == expected
    put_cached_graph(str(tmp_path), "coverage", graph)
    cached = get_cached_graph(str(tmp_path), "coverage")
    assert cached is not None
    assert cached.relation_coverage() == expected


def test_merge_does_not_upgrade_partial_or_unknown_coverage() -> None:
    _, complete = _sparse_graph("complete")
    _, partial = _sparse_graph("partial")
    _, unknown = _sparse_graph(None)
    for merge in (lambda *graphs: graphs[0].merge(graphs[1]), merge_and_relink):
        for graphs in ((complete, partial), (partial, complete), (complete, unknown)):
            merged = merge(*graphs)
            assert merged.relation_coverage()["kotlin"]["call_coverage"] != "complete"


@pytest.mark.parametrize("language", ["java", "kotlin", "rust"])
def test_cache_schema_invalidates_precoverage_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str,
) -> None:
    import arcade_agent.cache as cache

    (tmp_path / "Main.kt").write_text("class Main {}")
    current = cache_key(str(tmp_path), language, None)
    # Earlier coverage graphs did not declare Java's missing call relations.
    monkeypatch.setattr(cache, "_GRAPH_CACHE_SCHEMA_VERSION", "2")
    assert cache_key(str(tmp_path), language, None) != current


def test_rust_sparse_component_respects_call_coverage(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_rust")
    from arcade_agent.parsers.rust import RustParser

    source = tmp_path / "lib.rs"
    source.write_text("\n".join(f"pub fn task_{i}() {{}}" for i in range(45)))
    graph = RustParser().parse([source], tmp_path)
    architecture = Architecture(components=[Component(
        name="Tasks", responsibility="Fixture", entities=list(graph.entities),
    )], algorithm="test")
    smells = detect_smells(architecture, graph)
    findings = detect_concern_overload(architecture, graph)
    coverage = graph.relation_coverage()["rust"]["call_coverage"]
    if coverage == "complete":
        # These independent functions really are sparse if calls are fully covered.
        assert findings and all("coverage_status" not in item for item in findings)
    else:
        assert all(
            item["severity"] != "high" or _has_structured_qualification(item) for item in findings
        )
        assert not any(_is_unqualified_high(smell, findings) for smell in smells)
