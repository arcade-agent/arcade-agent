"""Tests for coupling and metric algorithms."""

from arcade_agent.algorithms.coupling import (
    compute_all_metrics,
    compute_balanced_scores,
    compute_basic_mq,
    compute_rci,
    compute_turbo_mq,
)
from arcade_agent.algorithms.smells import SmellInstance


def test_rci(sample_architecture, sample_graph):
    result = compute_rci(sample_architecture, sample_graph)
    assert result.name == "RCI"
    assert 0.0 <= result.value <= 1.0


def test_turbo_mq(sample_architecture, sample_graph):
    result = compute_turbo_mq(sample_architecture, sample_graph)
    assert result.name == "TurboMQ"
    num_components = len(sample_architecture.components)
    assert 0.0 <= result.value <= num_components


def test_turbo_mq_is_the_sum_of_cluster_factors(sample_architecture, sample_graph):
    """TurboMQ = sum_i CF(i), per Mitchell & Mancoridis (Bunch)."""
    result = compute_turbo_mq(sample_architecture, sample_graph)

    cluster_factors = result.details["cluster_factors"]
    assert set(cluster_factors) == {comp.name for comp in sample_architecture.components}
    assert result.value == round(sum(cluster_factors.values()), 4)

    # Fixture arithmetic: Calc has 1 intra edge and 2 inter edges -> CF = 2/(2+2) = 0.5;
    # Util has 0 intra edges -> CF = 0. Sum = 0.5, mean would be 0.25.
    assert cluster_factors == {"Calc": 0.5, "Util": 0.0}
    assert result.value == 0.5


def test_turbo_mq_and_basic_mq_are_not_identical(sample_architecture, sample_graph):
    """Regression for #25: the two metrics must be distinguishable signals.

    BasicMQ is the normalized (mean) variant, TurboMQ the raw sum, so on any
    architecture with k > 1 components TurboMQ == k * BasicMQ != BasicMQ.
    """
    num_components = len(sample_architecture.components)
    assert num_components > 1

    turbo = compute_turbo_mq(sample_architecture, sample_graph)
    basic = compute_basic_mq(sample_architecture, sample_graph)

    assert turbo.value != basic.value
    assert turbo.value == round(num_components * basic.value, 4)
    assert turbo.details["normalized"] == basic.value


def test_all_metrics(sample_architecture, sample_graph):
    results = compute_all_metrics(sample_architecture, sample_graph)
    assert len(results) == 6


def test_balanced_scores_are_bounded(sample_architecture, sample_graph):
    metrics = compute_all_metrics(sample_architecture, sample_graph)

    derived_metrics, principle_signals, score_drivers = compute_balanced_scores(
        sample_architecture,
        sample_graph,
        [],
        metrics=metrics,
    )

    assert {metric.name for metric in derived_metrics} == {
        "DependencyHealth",
        "ComponentBalance",
        "HubBalance",
        "BoundaryClarity",
        "DependencyDistribution",
        "SmellDiscipline",
        "PrincipleAlignmentScore",
        "BalancedArchitectureScore",
    }
    assert all(0.0 <= metric.value <= 1.0 for metric in derived_metrics)
    assert all(0.0 <= value <= 1.0 for value in principle_signals.values())
    assert {entry["name"] for entry in score_drivers["risks"]}
    assert {entry["name"] for entry in score_drivers["strengths"]}


def test_balanced_scores_drop_when_principle_smells_increase(sample_architecture, sample_graph):
    metrics = compute_all_metrics(sample_architecture, sample_graph)
    clean_metrics, clean_signals, _ = compute_balanced_scores(
        sample_architecture,
        sample_graph,
        [],
        metrics=metrics,
    )
    smelly_metrics, smelly_signals, _ = compute_balanced_scores(
        sample_architecture,
        sample_graph,
        [
            SmellInstance(
                smell_type="Dependency Cycle",
                severity="high",
                affected_components=["Calc", "Util"],
            ),
            SmellInstance(
                smell_type="Link/Upstream Overload",
                severity="high",
                affected_components=["Util"],
            ),
        ],
        metrics=metrics,
    )

    clean_lookup = {metric.name: metric.value for metric in clean_metrics}
    smelly_lookup = {metric.name: metric.value for metric in smelly_metrics}

    assert smelly_lookup["SmellDiscipline"] < clean_lookup["SmellDiscipline"]
    assert smelly_lookup["PrincipleAlignmentScore"] < clean_lookup["PrincipleAlignmentScore"]
    assert smelly_lookup["BalancedArchitectureScore"] < clean_lookup["BalancedArchitectureScore"]
    assert smelly_signals["AcyclicDependencies"] < clean_signals["AcyclicDependencies"]
    assert smelly_signals["InterfaceSegregation"] < clean_signals["InterfaceSegregation"]
