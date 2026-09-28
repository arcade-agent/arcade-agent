"""HTML report tests for dependency-graph quality qualification."""

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.algorithms.coupling import compute_all_metrics
from arcade_agent.exporters.html import (
    AlgorithmResult,
    export_comparison_html,
    export_html,
)
from arcade_agent.parsers.graph import DependencyGraph, Entity


def test_html_report_renders_qualified_dependency_resolution_warning(tmp_path):
    entity = Entity(
        fqn="app.App",
        name="App",
        package="app",
        file_path="app.ts",
        kind="class",
        language="typescript",
    )
    graph = DependencyGraph(
        entities={entity.fqn: entity},
        packages={"app": [entity.fqn]},
        metadata={
            "dependency_resolution": {
                "typescript": {
                    "import_specifiers": 5,
                    "resolved_local": 2,
                    "external": 1,
                    "unresolved_local": 2,
                    "linked_local": 1,
                    "unlinked_local": 1,
                    "local_resolution_rate": 0.5,
                    "local_edge_rate": 0.5,
                    "configuration_errors": [],
                    "metrics_qualified": True,
                }
            }
        },
    )
    architecture = Architecture(
        components=[Component(name="App", responsibility="Application", entities=[entity.fqn])],
        algorithm="pkg",
    )
    output = tmp_path / "report.html"

    export_html(
        "example",
        "current",
        graph,
        architecture,
        [],
        compute_all_metrics(architecture, graph),
        output,
    )

    html = output.read_text()
    assert "Qualified dependency-graph metrics" in html
    assert "2 resolved" in html
    assert "2 unresolved" in html
    assert "1 linked" in html
    assert "1 unlinked" in html


def test_algorithm_comparison_renders_qualified_graph_warning(tmp_path):
    entity = Entity(
        fqn="app.App",
        name="App",
        package="app",
        file_path="app.ts",
        kind="class",
        language="typescript",
    )
    graph = DependencyGraph(
        entities={entity.fqn: entity},
        packages={"app": [entity.fqn]},
        metadata={
            "dependency_resolution": {
                "typescript": {
                    "resolved_local": 2,
                    "unresolved_local": 1,
                    "linked_local": 1,
                    "unlinked_local": 1,
                    "metrics_qualified": True,
                }
            }
        },
    )
    architecture = Architecture(
        components=[
            Component(name="App", responsibility="Application", entities=[entity.fqn])
        ],
        algorithm="pkg",
    )
    output = tmp_path / "comparison.html"

    export_comparison_html(
        "example",
        "current",
        graph,
        [
            AlgorithmResult(
                algorithm="pkg",
                architecture=architecture,
                smells=[],
                metrics=compute_all_metrics(architecture, graph),
                concerns={},
            )
        ],
        output,
    )

    html = output.read_text()
    assert "Qualified dependency-graph metrics" in html
    assert "1 unresolved" in html
