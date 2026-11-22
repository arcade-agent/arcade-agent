"""Regression: naming-only concern candidates must not alter quality scores.

The Java HA dogfood corpus exposed a misleading score improvement when broad
suffix-only warnings disappeared. Even specific shared name stems are still
unverified hypotheses, so they belong in the report without score penalties.
"""

from dataclasses import asdict

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.algorithms.coupling import compute_balanced_scores
from arcade_agent.algorithms.smells import SmellInstance, SmellType
from arcade_agent.ci.run_self_analysis import _smell_to_dict
from arcade_agent.parsers.graph import DependencyGraph, Entity
from arcade_agent.tools.detect_smells import detect_smells


def _naming_fixture() -> tuple[Architecture, DependencyGraph]:
    entities = {}
    packages = {}
    components = []
    for component, class_name in (
        ("billing", "AuditService"),
        ("checkout", "AuditHandler"),
        ("support", "AuditAdapter"),
    ):
        fqn = f"example.{component}.{class_name}"
        entities[fqn] = Entity(
            fqn=fqn,
            name=class_name,
            package=f"example.{component}",
            file_path=f"{component}/{class_name}.java",
            kind="class",
            language="java",
        )
        packages[f"example.{component}"] = [fqn]
        components.append(Component(name=component, responsibility="", entities=[fqn]))
    return Architecture(components=components, algorithm="pkg"), DependencyGraph(
        entities=entities, edges=[], packages=packages,
    )


def _scores(architecture, graph, smells):
    derived, signals, _ = compute_balanced_scores(architecture, graph, smells)
    return {metric.name: metric.value for metric in derived}, signals


def test_non_scoring_candidate_does_not_change_any_derived_score():
    arch, graph = _naming_fixture()
    candidate = SmellInstance(
        smell_type=SmellType.SCATTERED_FUNCTIONALITY,
        severity="low",
        affected_components=["billing", "checkout", "support"],
        description="Possible Audit stem shared by three components",
    )
    # Set dynamically so the regression fails on the old dataclass by score,
    # not merely because the new field is absent.
    candidate.scoring_eligible = False
    assert _scores(arch, graph, [candidate]) == _scores(arch, graph, [])


def test_detector_marks_naming_candidate_as_non_scoring():
    arch, graph = _naming_fixture()
    smells = detect_smells(arch, graph)
    candidates = [s for s in smells if s.smell_type == SmellType.SCATTERED_FUNCTIONALITY]
    assert len(candidates) == 1
    assert candidates[0].scoring_eligible is False
    assert candidates[0].severity == "low"
    assert "naming-only" in candidates[0].explanation


def test_structural_finding_still_penalizes_scores():
    arch, graph = _naming_fixture()
    cycle = SmellInstance(
        smell_type=SmellType.DEPENDENCY_CYCLE,
        severity="medium",
        affected_components=["billing", "checkout", "support"],
        description="Confirmed dependency cycle in fixture",
    )
    without, _ = _scores(arch, graph, [])
    with_cycle, _ = _scores(arch, graph, [cycle])
    assert with_cycle["SmellDiscipline"] < without["SmellDiscipline"]
    assert with_cycle["BalancedArchitectureScore"] < without["BalancedArchitectureScore"]


def test_non_scoring_candidate_is_visible_in_json_proof():
    arch, graph = _naming_fixture()
    candidate = next(
        s for s in detect_smells(arch, graph)
        if s.smell_type == SmellType.SCATTERED_FUNCTIONALITY
    )
    record = _smell_to_dict(candidate)
    assert record["scoring_eligible"] is False
    assert asdict(candidate)["scoring_eligible"] is False
