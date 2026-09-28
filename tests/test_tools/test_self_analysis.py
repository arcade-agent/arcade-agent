"""Tests for repository self-analysis filtering."""

import json
import sys
from types import SimpleNamespace

import pytest

from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity
from scripts.run_self_analysis import (
    _filter_non_architectural_entities,
)
from scripts.run_self_analysis import (
    main as run_self_analysis_main,
)


def test_self_analysis_filters_registration_import_edges_only():
    graph = DependencyGraph(
        entities={
            "arcade_agent.tools.compare.compare": Entity(
                fqn="arcade_agent.tools.compare.compare",
                name="compare",
                package="arcade_agent.tools",
                file_path="src/arcade_agent/tools/compare.py",
                kind="function",
                language="python",
            ),
            "arcade_agent.tools.registry.tool": Entity(
                fqn="arcade_agent.tools.registry.tool",
                name="tool",
                package="arcade_agent.tools",
                file_path="src/arcade_agent/tools/registry.py",
                kind="function",
                language="python",
            ),
            "arcade_agent.algorithms.matching.match_components": Entity(
                fqn="arcade_agent.algorithms.matching.match_components",
                name="match_components",
                package="arcade_agent.algorithms",
                file_path="src/arcade_agent/algorithms/matching.py",
                kind="function",
                language="python",
            ),
        },
        edges=[
            Edge(
                source="arcade_agent.tools.compare.compare",
                target="arcade_agent.tools.registry.tool",
                relation="import",
            ),
            Edge(
                source="arcade_agent.tools.compare.compare",
                target="arcade_agent.algorithms.matching.match_components",
                relation="import",
            ),
        ],
        packages={
            "arcade_agent.tools": [
                "arcade_agent.tools.compare.compare",
                "arcade_agent.tools.registry.tool",
            ],
            "arcade_agent.algorithms": [
                "arcade_agent.algorithms.matching.match_components",
            ],
        },
        metadata={"dependency_resolution": {"typescript": {"metrics_qualified": True}}},
    )

    filtered = _filter_non_architectural_entities(graph)

    assert (
        "arcade_agent.tools.compare.compare",
        "arcade_agent.tools.registry.tool",
        "import",
    ) not in filtered.to_edge_tuples()
    assert (
        "arcade_agent.tools.compare.compare",
        "arcade_agent.algorithms.matching.match_components",
        "import",
    ) in filtered.to_edge_tuples()
    assert filtered.metadata == graph.metadata


def test_run_self_analysis_writes_balanced_scores(tmp_path, monkeypatch):
    project_dir = tmp_path / "sample-app"
    package_dir = project_dir / "sample_app"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("\n")
    (package_dir / "registry.py").write_text("def tool(fn):\n    return fn\n")
    (package_dir / "service.py").write_text(
        "from sample_app.registry import tool\n\n"
        "@tool\n"
        "def run():\n"
        "    return helper()\n\n"
        "def helper():\n"
        "    return 1\n"
    )

    output_json = tmp_path / "results.json"
    output_html = tmp_path / "report.html"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_self_analysis.py",
            "--source",
            str(project_dir),
            "--language",
            "python",
            "--output-json",
            str(output_json),
            "--output-html",
            str(output_html),
        ],
    )

    run_self_analysis_main()

    payload = json.loads(output_json.read_text())

    assert output_html.exists()
    assert set(payload["derived_metrics"]) == {
        "DependencyHealth",
        "ComponentBalance",
        "HubBalance",
        "BoundaryClarity",
        "DependencyDistribution",
        "SmellDiscipline",
        "PrincipleAlignmentScore",
        "BalancedArchitectureScore",
    }
    assert set(payload["principle_signals"]) == {
        "AcyclicDependencies",
        "LayeringHealth",
        "ResponsibilityFocus",
        "InterfaceSegregation",
        "ComponentBalance",
        "HubBalance",
        "BoundaryClarity",
        "DependencyDistribution",
        "SmellDiscipline",
    }
    assert set(payload["score_drivers"]) == {"risks", "strengths"}
    assert payload["graph_metadata"] == {}
    assert payload["graph_quality"] is None


def test_run_self_analysis_keeps_registration_like_helpers_by_default(tmp_path, monkeypatch):
    project_dir = tmp_path / "sample-app"
    package_dir = project_dir / "sample_app"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("\n")
    (package_dir / "registry.py").write_text("def tool(fn):\n    return fn\n")
    (package_dir / "service.py").write_text(
        "from sample_app.registry import tool\n\n"
        "def run():\n"
        "    return tool\n"
    )

    output_json = tmp_path / "results.json"
    output_html = tmp_path / "report.html"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_self_analysis.py",
            "--source",
            str(project_dir),
            "--language",
            "python",
            "--output-json",
            str(output_json),
            "--output-html",
            str(output_html),
        ],
    )

    run_self_analysis_main()

    payload = json.loads(output_json.read_text())

    assert payload["num_entities"] >= 2
    assert payload["num_edges"] >= 1


def test_self_analysis_forwards_exact_custom_exclusions(
    tmp_path,
    monkeypatch,
):
    captured: dict[str, object] = {}

    def fake_ingest(source, **kwargs):
        captured["source"] = source
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            source_files=[],
            languages=[],
            language="python",
        )

    monkeypatch.setattr(
        "arcade_agent.ci.run_self_analysis.ingest",
        fake_ingest,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "arcade-self-analysis",
            "--source",
            str(tmp_path),
            "--exclude-dirs",
            "integrationTest, src/e2e",
            "--no-exclude-tests",
        ],
    )

    with pytest.raises(SystemExit, match="1"):
        run_self_analysis_main()

    assert captured["kwargs"]["exclude_dirs"] == [
        "integrationTest",
        "src/e2e",
    ]
    assert captured["kwargs"]["exclude_tests"] is False
