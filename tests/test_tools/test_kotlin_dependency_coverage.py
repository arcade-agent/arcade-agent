"""Characterize structural Kotlin coverage; keep the desired detector guard visible."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_kotlin")

from arcade_agent.algorithms.concern import detect_concern_overload  # noqa: E402
from arcade_agent.cache import cache_key, get_cached_graph, put_cached_graph  # noqa: E402
from arcade_agent.ci.graph_filter import _filter_non_architectural_entities  # noqa: E402
from arcade_agent.parsers.graph import Edge  # noqa: E402
from arcade_agent.parsers.multilang import merge_and_relink  # noqa: E402
from arcade_agent.serialization import dict_to_graph, graph_to_dict, serialize_result  # noqa: E402
from arcade_agent.tools.analyze import analyze  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/kotlin_dependency_coverage/src"


@pytest.fixture(scope="module")
def analysis():
    result = asyncio.run(analyze(
        str(FIXTURE), language="kotlin", algorithm="pkg",
        exclude_tests=True, use_cache=False, use_llm=False,
    ))
    yield result
    result.repository.cleanup()


def test_file_import_attribution_is_not_method_usage(analysis):
    graph = analysis.graph
    edges = [
        edge for edge in graph.edges
        if edge.source.startswith("renderer.") and edge.relation == "import"
    ]
    assert edges
    assert all(graph.entities[edge.target].package == "canvas" for edge in edges)
    file_targets = {(graph.entities[e.source].file_path, e.target) for e in edges}
    assert len(edges) >= len(file_targets)
    # Import attribution, including any edge from close(), is not a call.
    assert "fun close() {}" in (FIXTURE / "TimelineFrameRenderer.kt").read_text()


def test_missing_internal_calls_have_visible_insufficient_coverage(analysis):
    graph = analysis.graph
    assert graph.relation_coverage()["kotlin"]["call_coverage"] == "not_collected"
    ui = next(component for component in analysis.architecture.components if component.name == "Ui")
    assert len(ui.entities) > 40
    finding = next(
        item for item in detect_concern_overload(analysis.architecture, graph)
        if item["component"] == "Ui"
    )
    assert finding["coverage_status"] == "insufficient"
    assert any(
        smell.smell_type == "Concern Overload"
        and smell.affected_components == ["Ui"]
        and "insufficient coverage" in smell.description
        for smell in analysis.smells
    )

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
    assert detect_concern_overload(
        analysis.architecture, replace(graph, edges=[*graph.edges, *calls]),
    ) == []


def test_source_import_cycle_survives_independently(analysis):
    graph = analysis.graph
    assert {
        (edge.source, edge.target) for edge in graph.edges
        if graph.entities[edge.source].package in {"data", "export", "view"}
    } == {
        ("data.DataNode", "export.ExportNode"),
        ("export.ExportNode", "view.ViewNode"),
        ("view.ViewNode", "data.DataNode"),
    }
    cycles = [smell for smell in analysis.smells if smell.smell_type == "Dependency Cycle"]
    assert len(cycles) == 1
    assert set(cycles[0].affected_components) == {"Data", "Export", "View"}


def test_structural_only_graph_should_not_emit_unqualified_high_cohesion_finding(analysis):
    assert not any(
        smell.smell_type == "Concern Overload" and smell.severity == "high"
        and smell.affected_components == ["Ui"]
        and "insufficient coverage" not in smell.description
        for smell in analysis.smells
    )


@pytest.mark.parametrize("status", ["partial", "complete"])
def test_sparse_finding_respects_declared_call_coverage(analysis, status):
    graph = analysis.graph
    metadata = {**graph.metadata, "relation_coverage": {"kotlin": {
        "collected_relations": ["import", "extends", "implements", "calls"],
        "call_coverage": status,
    }}}
    candidate = next(
        item for item in detect_concern_overload(
            analysis.architecture, replace(graph, metadata=metadata),
        )
        if item["component"] == "Ui"
    )
    if status == "complete":
        assert candidate["severity"] == "high"
        assert "coverage_status" not in candidate
    else:
        assert candidate["coverage_status"] == "insufficient"


def test_coverage_survives_graph_boundaries_and_cache(analysis, tmp_path):
    graph = analysis.graph
    expected = graph.relation_coverage()
    assert graph.merge(graph).relation_coverage() == expected
    assert merge_and_relink(graph).relation_coverage() == expected
    assert _filter_non_architectural_entities(graph).relation_coverage() == expected
    assert dict_to_graph(graph_to_dict(graph)).relation_coverage() == expected
    assert serialize_result(graph)["metadata"]["relation_coverage"] == expected
    put_cached_graph(tmp_path, "coverage", graph)
    cached = get_cached_graph(tmp_path, "coverage")
    assert cached is not None
    assert cached.relation_coverage() == expected


def test_merge_does_not_upgrade_partial_or_unknown_coverage(analysis):
    graph = analysis.graph
    complete = replace(graph, metadata={"relation_coverage": {"kotlin": {
        "collected_relations": ["import", "calls"], "call_coverage": "complete",
    }}})
    unknown = replace(graph, metadata={})
    for merge in (lambda *graphs: graphs[0].merge(graphs[1]), merge_and_relink):
        for graphs in ((complete, graph), (graph, complete), (complete, unknown)):
            merged = merge(*graphs)
            assert merged.relation_coverage()["kotlin"]["call_coverage"] != "complete"


def test_cache_schema_invalidates_precoverage_entries(tmp_path, monkeypatch):
    import arcade_agent.cache as cache

    (tmp_path / "Main.kt").write_text("class Main {}")
    current = cache_key(str(tmp_path), "kotlin", None)
    monkeypatch.setattr(cache, "_GRAPH_CACHE_SCHEMA_VERSION", "legacy")
    assert cache_key(str(tmp_path), "kotlin", None) != current


def test_rust_sparse_component_is_qualified(tmp_path):
    pytest.importorskip("tree_sitter_rust")
    from arcade_agent.algorithms.architecture import Architecture, Component
    from arcade_agent.parsers.rust import RustParser
    from arcade_agent.tools.detect_smells import detect_smells

    source = tmp_path / "lib.rs"
    source.write_text("\n".join(f"pub fn task_{i}() {{}}" for i in range(45)))
    graph = RustParser().parse([source], tmp_path)
    assert graph.relation_coverage()["rust"]["call_coverage"] == "not_collected"
    architecture = Architecture(components=[Component(
        name="Tasks", responsibility="Fixture", entities=list(graph.entities),
    )], algorithm="test")
    smells = detect_smells(architecture, graph)
    assert any("insufficient coverage" in smell.description for smell in smells)
    assert not any(smell.severity == "high" for smell in smells)
