"""Coupling, cohesion, and modularization quality metrics.

Implements the 7 decay metrics from ARCADE Core:
- RCI (Ratio of Cohesive Interactions)
- TurboMQ (Modularization Quality)
- BasicMQ (Basic Modularization Quality)
- IntraConnectivity
- InterConnectivity
- TwoWayPairRatio
- ArchitecturalStability (requires two versions)

Also derives balanced architecture scores that combine the core metrics with
principle-oriented signals such as acyclic dependencies, interface segregation,
smell discipline, and component balance.
"""

from arcade_agent.algorithms.architecture import Architecture
from arcade_agent.algorithms.metrics import MetricResult
from arcade_agent.algorithms.smells import SmellInstance, SmellType
from arcade_agent.parsers.graph import DependencyGraph

PRINCIPLE_SIGNAL_WEIGHTS = {
    "AcyclicDependencies": 0.18,
    "LayeringHealth": 0.17,
    "ResponsibilityFocus": 0.15,
    "InterfaceSegregation": 0.12,
    "ComponentBalance": 0.10,
    "HubBalance": 0.10,
    "BoundaryClarity": 0.10,
    "DependencyDistribution": 0.08,
}

BALANCED_SCORE_WEIGHTS = {
    "cohesion_family": 0.50,
    "principle_alignment": 0.35,
    "smell_discipline": 0.15,
}


def _clamp(value: float) -> float:
    """Clamp a floating-point score to the [0, 1] range."""
    return max(0.0, min(1.0, value))


def _round_score(value: float) -> float:
    """Clamp and round a score for stable reporting."""
    return round(_clamp(value), 4)


def _gini_coefficient(values: list[int]) -> float:
    """Compute the Gini coefficient for positive component sizes."""
    if not values:
        return 0.0

    sorted_values = sorted(values)
    total = sum(sorted_values)
    if total <= 0:
        return 0.0

    weighted_sum = 0
    count = len(sorted_values)
    for index, value in enumerate(sorted_values, start=1):
        weighted_sum += index * value

    return ((2 * weighted_sum) / (count * total)) - ((count + 1) / count)


def _metric_lookup(metrics: list[MetricResult]) -> dict[str, float]:
    """Build a metric-name lookup."""
    return {metric.name: metric.value for metric in metrics}


def _normalized_turbo_mq(metrics: list[MetricResult]) -> float:
    """Return TurboMQ scaled to [0, 1].

    TurboMQ is the raw sum of cluster factors and can reach k for k components,
    so derived scores must use its normalized form to stay comparable with the
    other [0, 1] signals.
    """
    for metric in metrics:
        if metric.name != "TurboMQ":
            continue
        normalized = metric.details.get("normalized")
        if isinstance(normalized, int | float):
            return float(normalized)
        num_components = metric.details.get("num_components") or 0
        if isinstance(num_components, int) and num_components > 0:
            return metric.value / num_components
        return metric.value
    return 0.0


def _severity_weight(severity: str) -> float:
    """Weight smell severities for derived score calculations."""
    return {
        "high": 1.0,
        "medium": 0.6,
        "low": 0.3,
    }.get(severity.lower(), 0.3)


def _smell_burden(
    smells: list[SmellInstance],
    component_count: int,
    relevant_types: set[str] | None = None,
) -> float:
    """Estimate how much a set of smells should penalize balanced scores."""
    if component_count <= 0:
        return 0.0

    burden = 0.0
    for smell in smells:
        smell_type = (
            smell.smell_type.value
            if hasattr(smell.smell_type, "value")
            else smell.smell_type
        )
        if relevant_types and smell_type not in relevant_types:
            continue

        affected_ratio = len(smell.affected_components) / component_count
        burden += _severity_weight(smell.severity) * affected_ratio

    return _clamp(burden)


def _component_dependency_profiles(
    architecture: Architecture,
    dep_graph: DependencyGraph,
) -> dict[str, dict[str, float]]:
    """Compute component-level fan-in/fan-out profiles and normalized ratios."""
    dependencies = architecture.component_dependencies(dep_graph)
    component_names = [component.name for component in architecture.components]
    others = max(1, len(component_names) - 1)
    profiles = {
        name: {"fan_in": 0.0, "fan_out": 0.0, "fan_total": 0.0}
        for name in component_names
    }

    for source, target in dependencies:
        if source in profiles:
            profiles[source]["fan_out"] += 1
        if target in profiles:
            profiles[target]["fan_in"] += 1

    for values in profiles.values():
        fan_in = values["fan_in"]
        fan_out = values["fan_out"]
        values["fan_total"] = fan_in + fan_out
        values["fan_in_ratio"] = fan_in / others
        values["fan_out_ratio"] = fan_out / others

    return profiles


def _score_drivers(signals: dict[str, float]) -> dict[str, list[dict[str, float | str]]]:
    """Return the strongest and weakest quality drivers for reporting."""
    ordered = sorted(signals.items(), key=lambda item: (item[1], item[0]))
    risks = [
        {
            "name": name,
            "value": _round_score(value),
            "gap_to_ideal": _round_score(1.0 - value),
        }
        for name, value in ordered[:3]
    ]
    strengths = [
        {
            "name": name,
            "value": _round_score(value),
            "gap_to_ideal": _round_score(1.0 - value),
        }
        for name, value in sorted(ordered, key=lambda item: (-item[1], item[0]))[:3]
    ]
    return {
        "risks": risks,
        "strengths": strengths,
    }


def _build_membership(architecture: Architecture) -> dict[str, str]:
    """Build entity -> component mapping."""
    membership: dict[str, str] = {}
    for comp in architecture.components:
        for fqn in comp.entities:
            membership[fqn] = comp.name
    return membership


def _count_edges(
    dep_graph: DependencyGraph,
    membership: dict[str, str],
) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    """Count intra-component and inter-component edges.

    Returns:
        Tuple of (intra_counts, inter_counts) where:
        - intra_counts: {component_name: count}
        - inter_counts: {(src_comp, tgt_comp): count}
    """
    intra: dict[str, int] = {}
    inter: dict[tuple[str, str], int] = {}

    for edge in dep_graph.edges:
        src_comp = membership.get(edge.source)
        tgt_comp = membership.get(edge.target)
        if not src_comp or not tgt_comp:
            continue

        if src_comp == tgt_comp:
            intra[src_comp] = intra.get(src_comp, 0) + 1
        else:
            key = (src_comp, tgt_comp)
            inter[key] = inter.get(key, 0) + 1

    return intra, inter


def compute_rci(architecture: Architecture, dep_graph: DependencyGraph) -> MetricResult:
    """Compute Ratio of Cohesive Interactions.

    RCI = intra_edges / total_edges
    Higher is better (more cohesive architecture).
    """
    membership = _build_membership(architecture)
    intra, inter = _count_edges(dep_graph, membership)

    total_intra = sum(intra.values())
    total_inter = sum(inter.values())
    total = total_intra + total_inter

    value = total_intra / total if total > 0 else 0.0

    return MetricResult(
        name="RCI",
        value=round(value, 4),
        details={
            "intra_edges": total_intra,
            "inter_edges": total_inter,
            "total_edges": total,
        },
    )


def compute_turbo_mq(architecture: Architecture, dep_graph: DependencyGraph) -> MetricResult:
    """Compute TurboMQ (Modularization Quality).

    TurboMQ = sum of cluster factors CF(i) for each component i, per
    Mitchell & Mancoridis (Bunch). The value is therefore in [0, k] for k
    components and is *not* normalized -- ``BasicMQ`` is the normalized (mean)
    variant of the same family, so the two metrics carry different signals.

    CF(i) = 2 * mu_i / (2 * mu_i + sum_j(epsilon_ij + epsilon_ji))
    where mu_i = intra-edges, epsilon_ij = inter-edges from i to j.

    Higher is better.

    Returns:
        MetricResult whose ``value`` is the raw sum of cluster factors and whose
        ``details`` carry the per-component factors plus ``normalized``
        (sum / k) for consumers that need a [0, 1] score.
    """
    membership = _build_membership(architecture)
    intra, inter = _count_edges(dep_graph, membership)

    comp_names = [comp.name for comp in architecture.components]
    turbo_mq = 0.0
    cf_details: dict[str, float] = {}

    for comp_name in comp_names:
        mu = intra.get(comp_name, 0)

        # Sum of inter-edges involving this component
        epsilon_sum = 0
        for (src, tgt), count in inter.items():
            if src == comp_name or tgt == comp_name:
                epsilon_sum += count

        denominator = 2 * mu + epsilon_sum
        if denominator > 0:
            cf = (2 * mu) / denominator
        else:
            cf = 0.0

        cf_details[comp_name] = round(cf, 4)
        turbo_mq += cf

    num_components = len(comp_names)
    normalized = turbo_mq / num_components if num_components > 0 else 0.0

    return MetricResult(
        name="TurboMQ",
        value=round(turbo_mq, 4),
        details={
            "cluster_factors": cf_details,
            "num_components": num_components,
            "raw_sum": round(turbo_mq, 4),
            "normalized": round(normalized, 4),
        },
    )


def compute_basic_mq(architecture: Architecture, dep_graph: DependencyGraph) -> MetricResult:
    """Compute BasicMQ.

    BasicMQ = (1/k) * sum_i(intra_i / (intra_i + 0.5*inter_i))
    where k = number of components, inter_i = inter-edges touching component i.

    This is the normalized member of the MQ family: its per-component term is
    algebraically the same cluster factor TurboMQ sums, so BasicMQ equals
    ``TurboMQ / k`` (reported as ``TurboMQ.details["normalized"]``). Use BasicMQ
    to compare architectures with different component counts and TurboMQ when
    the raw Bunch objective value is wanted.

    Higher is better.
    """
    membership = _build_membership(architecture)
    intra, inter = _count_edges(dep_graph, membership)

    comp_names = [comp.name for comp in architecture.components]
    total = 0.0

    for comp_name in comp_names:
        intra_count = intra.get(comp_name, 0)
        inter_count = 0
        for (src, tgt), count in inter.items():
            if src == comp_name or tgt == comp_name:
                inter_count += count

        denominator = intra_count + 0.5 * inter_count
        if denominator > 0:
            total += intra_count / denominator

    k = len(comp_names)
    value = total / k if k > 0 else 0.0

    return MetricResult(name="BasicMQ", value=round(value, 4), details={})


def compute_intra_connectivity(
    architecture: Architecture, dep_graph: DependencyGraph
) -> MetricResult:
    """Compute average intra-connectivity across components.

    IntraConnectivity(i) = intra_edges(i) / (N_i * (N_i - 1))
    where N_i = number of entities in component i.

    Higher is better (denser internal connections).
    """
    membership = _build_membership(architecture)
    intra, _ = _count_edges(dep_graph, membership)

    comp_values: dict[str, float] = {}
    total = 0.0
    count = 0

    for comp in architecture.components:
        n = len(comp.entities)
        max_edges = n * (n - 1)
        if max_edges > 0:
            val = intra.get(comp.name, 0) / max_edges
        else:
            val = 0.0
        comp_values[comp.name] = round(val, 4)
        total += val
        count += 1

    avg = total / count if count > 0 else 0.0

    return MetricResult(
        name="IntraConnectivity",
        value=round(avg, 4),
        details={"per_component": comp_values},
    )


def compute_inter_connectivity(
    architecture: Architecture, dep_graph: DependencyGraph
) -> MetricResult:
    """Compute average inter-connectivity between component pairs.

    InterConnectivity(i,j) = inter_edges(i,j) / (N_i * N_j)

    Lower is better (less coupling between components).
    """
    membership = _build_membership(architecture)
    _, inter = _count_edges(dep_graph, membership)

    comp_sizes = {comp.name: len(comp.entities) for comp in architecture.components}

    total = 0.0
    pair_count = 0

    for (src, tgt), edge_count in inter.items():
        n_src = comp_sizes.get(src, 0)
        n_tgt = comp_sizes.get(tgt, 0)
        max_edges = n_src * n_tgt
        if max_edges > 0:
            total += edge_count / max_edges
        pair_count += 1

    avg = total / pair_count if pair_count > 0 else 0.0

    return MetricResult(
        name="InterConnectivity",
        value=round(avg, 4),
        details={"num_connected_pairs": pair_count},
    )


def compute_two_way_pair_ratio(
    architecture: Architecture, dep_graph: DependencyGraph
) -> MetricResult:
    """Compute ratio of bidirectional component dependencies.

    TwoWayPairRatio = bidirectional_pairs / total_connected_pairs

    Lower is better (fewer mutual dependencies suggest cleaner layering).
    """
    comp_deps = architecture.component_dependencies(dep_graph)
    edge_set = set(comp_deps)

    total_pairs: set[frozenset[str]] = set()
    bidirectional = 0

    for src, tgt in comp_deps:
        pair = frozenset([src, tgt])
        if pair not in total_pairs:
            total_pairs.add(pair)
            if (tgt, src) in edge_set:
                bidirectional += 1

    total = len(total_pairs)
    value = bidirectional / total if total > 0 else 0.0

    return MetricResult(
        name="TwoWayPairRatio",
        value=round(value, 4),
        details={
            "bidirectional_pairs": bidirectional,
            "total_pairs": total,
        },
    )


def compute_all_metrics(
    architecture: Architecture, dep_graph: DependencyGraph
) -> list[MetricResult]:
    """Compute all single-version architecture quality metrics."""
    return [
        compute_rci(architecture, dep_graph),
        compute_turbo_mq(architecture, dep_graph),
        compute_basic_mq(architecture, dep_graph),
        compute_intra_connectivity(architecture, dep_graph),
        compute_inter_connectivity(architecture, dep_graph),
        compute_two_way_pair_ratio(architecture, dep_graph),
    ]


def compute_balanced_scores(
    architecture: Architecture,
    dep_graph: DependencyGraph,
    smells: list[SmellInstance],
    metrics: list[MetricResult] | None = None,
) -> tuple[list[MetricResult], dict[str, float], dict[str, list[dict[str, float | str]]]]:
    """Compute balanced derived scores on top of the core ARCADE metrics.

    The existing coupling metrics remain the source of truth for raw structural
    quality. These derived scores make the result easier to interpret by mixing
    in modern architecture signals such as layering health, balance, and smell
    discipline while preserving higher-is-better semantics.

    The weights below are explicit expert-judgment defaults chosen for
    interpretability. They are intentionally reviewable and tuneable, but they
    should not be described as benchmark-calibrated or empirically validated.
    """
    if metrics is None:
        metrics = compute_all_metrics(architecture, dep_graph)

    metric_map = _metric_lookup(metrics)
    component_sizes = [len(component.entities) for component in architecture.components]
    component_count = max(1, len(component_sizes))
    dependency_profiles = _component_dependency_profiles(architecture, dep_graph)
    total_dependency_loads = [
        int(profile["fan_total"])
        for profile in dependency_profiles.values()
    ]

    dependency_health = _clamp(
        0.55 * (1.0 - metric_map.get("InterConnectivity", 0.0))
        + 0.45 * (1.0 - metric_map.get("TwoWayPairRatio", 0.0))
    )
    component_balance = _clamp(1.0 - _gini_coefficient(component_sizes))
    hub_dominance = max(
        (
            max(profile["fan_in_ratio"], profile["fan_out_ratio"])
            for profile in dependency_profiles.values()
        ),
        default=0.0,
    )
    boundary_clarity = _clamp(
        1.0
        - (
            sum(
                min(profile["fan_in_ratio"], profile["fan_out_ratio"])
                for profile in dependency_profiles.values()
            )
            / component_count
        )
    )
    dependency_distribution = _clamp(1.0 - _gini_coefficient(total_dependency_loads))

    cycle_burden = _smell_burden(
        smells,
        component_count,
        {SmellType.DEPENDENCY_CYCLE.value},
    )
    responsibility_burden = _smell_burden(
        smells,
        component_count,
        {
            SmellType.CONCERN_OVERLOAD.value,
            SmellType.SCATTERED_FUNCTIONALITY.value,
        },
    )
    interface_burden = _smell_burden(
        smells,
        component_count,
        {SmellType.LINK_OVERLOAD.value},
    )
    total_smell_burden = _smell_burden(smells, component_count)

    principle_signals = {
        "AcyclicDependencies": _round_score(1.0 - cycle_burden),
        "LayeringHealth": _round_score(
            0.75 * dependency_health + 0.25 * (1.0 - cycle_burden)
        ),
        "ResponsibilityFocus": _round_score(1.0 - responsibility_burden),
        "InterfaceSegregation": _round_score(1.0 - interface_burden),
        "ComponentBalance": _round_score(component_balance),
        "HubBalance": _round_score(1.0 - hub_dominance),
        "BoundaryClarity": _round_score(boundary_clarity),
        "DependencyDistribution": _round_score(dependency_distribution),
        "SmellDiscipline": _round_score(1.0 - total_smell_burden),
    }
    principle_alignment = _clamp(sum(
        PRINCIPLE_SIGNAL_WEIGHTS[name] * principle_signals[name]
        for name in PRINCIPLE_SIGNAL_WEIGHTS
    ))
    cohesion_family = _clamp(
        0.40 * _normalized_turbo_mq(metrics)
        + 0.35 * metric_map.get("RCI", 0.0)
        + 0.25 * metric_map.get("BasicMQ", 0.0)
    )
    balanced_architecture_score = _clamp(sum(
        (
            BALANCED_SCORE_WEIGHTS["cohesion_family"] * cohesion_family,
            BALANCED_SCORE_WEIGHTS["principle_alignment"] * principle_alignment,
            BALANCED_SCORE_WEIGHTS["smell_discipline"] * principle_signals["SmellDiscipline"],
        )
    ))
    score_drivers = _score_drivers(principle_signals)

    score_details = {
        "group": "Balanced / Principle-aligned Scores",
        "higher_is_better": True,
    }
    derived_metrics = [
        MetricResult(
            name="DependencyHealth",
            value=_round_score(dependency_health),
            details={
                **score_details,
                "formula": "0.55*(1-InterConnectivity) + 0.45*(1-TwoWayPairRatio)",
            },
        ),
        MetricResult(
            name="ComponentBalance",
            value=_round_score(component_balance),
            details={
                **score_details,
                "formula": "1 - gini(component_sizes)",
            },
        ),
        MetricResult(
            name="HubBalance",
            value=principle_signals["HubBalance"],
            details={
                **score_details,
                "formula": "1 - max(max(fan_in_ratio), max(fan_out_ratio))",
            },
        ),
        MetricResult(
            name="BoundaryClarity",
            value=principle_signals["BoundaryClarity"],
            details={
                **score_details,
                "formula": "1 - avg(min(fan_in_ratio, fan_out_ratio))",
            },
        ),
        MetricResult(
            name="DependencyDistribution",
            value=principle_signals["DependencyDistribution"],
            details={
                **score_details,
                "formula": "1 - gini(component_fan_loads)",
            },
        ),
        MetricResult(
            name="SmellDiscipline",
            value=principle_signals["SmellDiscipline"],
            details={
                **score_details,
                "formula": "1 - weighted_smell_burden",
            },
        ),
        MetricResult(
            name="PrincipleAlignmentScore",
            value=_round_score(principle_alignment),
            details={
                **score_details,
                "formula": (
                    "weighted average of principle-oriented normalized signals "
                    "(see PRINCIPLE_SIGNAL_WEIGHTS)"
                ),
                "signals": principle_signals,
                "weights": PRINCIPLE_SIGNAL_WEIGHTS,
            },
        ),
        MetricResult(
            name="BalancedArchitectureScore",
            value=_round_score(balanced_architecture_score),
            details={
                **score_details,
                "formula": (
                    "0.50*cohesion_family + 0.35*PrincipleAlignmentScore + "
                    "0.15*SmellDiscipline"
                ),
                "weights": BALANCED_SCORE_WEIGHTS,
            },
        ),
    ]

    return derived_metrics, principle_signals, score_drivers
