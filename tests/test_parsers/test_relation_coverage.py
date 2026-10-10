"""Coverage declarations must not turn unknown evidence into complete evidence."""

import pytest

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.algorithms.concern import detect_concern_overload
from arcade_agent.parsers.graph import DependencyGraph, Entity
from arcade_agent.parsers.multilang import merge_and_relink
from arcade_agent.serialization import dict_to_graph, graph_to_dict


def _sparse_graph(declaration: object) -> DependencyGraph:
    entities = {
        f"ui.method{index}": Entity(
            fqn=f"ui.method{index}", name=f"method{index}", package="ui",
            file_path="Ui.kt", kind="method", language="kotlin",
        )
        for index in range(41)
    }
    return DependencyGraph(
        entities=entities, packages={"ui": list(entities)},
        metadata={"relation_coverage": declaration},
    )


@pytest.mark.parametrize("declaration", [
    None,
    [],
    {"kotlin": None},
    {"kotlin": {}},
    {"kotlin": {"collected_relations": "calls", "call_coverage": "complete"}},
    {"kotlin": {"collected_relations": [1], "call_coverage": "complete"}},
    {"kotlin": {"collected_relations": ["import"], "call_coverage": "corrupt"}},
    {"kotlin": {"collected_relations": ["import"], "call_coverage": "complete"}},
    {"kotlin": {"collected_relations": [], "call_coverage": "unknown"}},
])
def test_invalid_or_unknown_declaration_cannot_support_high_cohesion_claim(
    declaration: object,
) -> None:
    graph = _sparse_graph(declaration)
    architecture = Architecture(components=[Component("Ui", "", list(graph.entities))])

    # Loading raw serialized metadata must preserve the uncertainty, not discard it.
    for candidate in (graph, dict_to_graph(graph_to_dict(graph))):
        assert candidate.relation_coverage()["kotlin"]["call_coverage"] == "unknown"
        finding, = detect_concern_overload(architecture, candidate)
        assert finding["severity"] == "low"
        assert finding["coverage_status"] == "insufficient"
        assert finding["incomplete_call_languages"] == ["kotlin"]


def test_declared_map_covers_each_entity_language_conservatively() -> None:
    graph = _sparse_graph({"kotlin": {
        "collected_relations": ["calls"], "call_coverage": "complete",
    }})
    graph.entities["java.Helper"] = Entity(
        fqn="java.Helper", name="Helper", package="java", file_path="Helper.java",
        kind="class", language="java",
    )

    coverage = graph.relation_coverage()
    assert coverage["kotlin"]["call_coverage"] == "complete"
    assert coverage["java"]["call_coverage"] == "unknown"


def test_undeclared_legacy_graph_retains_existing_heuristic() -> None:
    graph = _sparse_graph(None)
    graph.metadata = {}
    architecture = Architecture(components=[Component("Ui", "", list(graph.entities))])
    assert graph.relation_coverage() == {}
    finding, = detect_concern_overload(architecture, graph)
    assert finding["severity"] == "high"
    assert "coverage_status" not in finding


def test_unknown_source_set_cannot_be_promoted_by_complete_source_set() -> None:
    unknown = _sparse_graph({"kotlin": {"call_coverage": "corrupt"}})
    complete = _sparse_graph({"kotlin": {
        "collected_relations": ["calls", "import"], "call_coverage": "complete",
    }})
    for left, right in ((unknown, complete), (complete, unknown)):
        for merged in (left.merge(right), merge_and_relink(left, right)):
            assert merged.relation_coverage()["kotlin"]["call_coverage"] == "unknown"


def test_complete_call_declaration_accepts_normalized_relations() -> None:
    graph = _sparse_graph({"kotlin": {
        "collected_relations": ["import", "calls", "calls"], "call_coverage": "complete",
    }})
    assert graph.relation_coverage()["kotlin"] == {
        "collected_relations": ["calls", "import"], "call_coverage": "complete",
    }
