#!/usr/bin/env python3
"""Architecture drift detection script.

Parses a codebase, recovers architecture using PKG, compares against a stored
baseline, and prints a markdown report suitable for a PR comment.

Usage:
    arcade-arch-diff --source /path/to/project --language java
    arcade-arch-diff --source . --update-baseline
"""

import argparse
import sys
from pathlib import Path
from typing import Any

from arcade_agent.algorithms.architecture import Architecture
from arcade_agent.algorithms.coupling import compute_balanced_scores
from arcade_agent.algorithms.metrics import MetricResult
from arcade_agent.algorithms.smells import SmellInstance
from arcade_agent.ci.graph_filter import (
    SELF_DOGFOOD_PROFILE,
    _filter_non_architectural_entities,
)
from arcade_agent.display import display_value
from arcade_agent.exporters.changelog_md import render_changelog_markdown
from arcade_agent.exporters.mermaid import mermaid_label_text, mermaid_node_id
from arcade_agent.parsers.graph import DependencyGraph
from arcade_agent.serialization import load_architecture, save_architecture
from arcade_agent.tools.changelog_architecture import changelog_architecture
from arcade_agent.tools.compare import compare
from arcade_agent.tools.compute_metrics import compute_metrics
from arcade_agent.tools.detect_smells import detect_smells
from arcade_agent.tools.parse import parse
from arcade_agent.tools.recover import recover

# Metrics where higher is better (for direction indicators).
_HIGHER_IS_BETTER = {
    "BalancedArchitectureScore",
    "PrincipleAlignmentScore",
    "RCI",
    "TurboMQ",
    "BasicMQ",
    "IntraConnectivity",
    "DependencyHealth",
    "ComponentBalance",
    "HubBalance",
    "BoundaryClarity",
    "DependencyDistribution",
    "SmellDiscipline",
}

# Metrics where lower is better.
_LOWER_IS_BETTER = {
    "InterConnectivity",
    "TwoWayPairRatio",
}

# Smell → actionable recommendation mapping.
_SMELL_RECOMMENDATIONS = {
    "concern_overload": (
        "Split the overloaded component into smaller, focused units. "
        "Consider extracting sub-packages or introducing an interface layer."
    ),
    "hub_like_dependency": (
        "Reduce fan-in/fan-out by introducing abstractions or splitting "
        "the hub into domain-specific modules."
    ),
    "cyclic_dependency": (
        "Break the cycle by extracting shared abstractions or inverting "
        "dependencies via interfaces/events."
    ),
    "unstable_interface": (
        "Stabilize the interface by reducing outgoing dependencies or "
        "applying the Stable Abstractions Principle."
    ),
}


def build_report(
    current: Architecture,
    graph: DependencyGraph,
    metrics: list[MetricResult],
    smells: list[SmellInstance],
    drift: dict[str, Any] | None = None,
    baseline: Architecture | None = None,
    baseline_metrics: dict[str, float] | None = None,
    baseline_note: str | None = None,
) -> str:
    """Build a markdown drift report.

    Args:
        current: The current Architecture.
        graph: The current DependencyGraph.
        metrics: List of MetricResult for the current architecture.
        smells: List of SmellInstance detected.
        drift: Optional compare() result dict (if baseline exists).
        baseline: Optional baseline Architecture.
        baseline_metrics: Optional dict of baseline metric name→value.
        baseline_note: Optional explanation when baseline comparison is skipped.

    Returns:
        Markdown string.
    """
    num_entities = graph.num_entities
    num_components = len(current.components)

    lines = [
        "## Architecture Drift Report",
        "",
        f"**Algorithm:** {current.algorithm.upper()} | "
        f"**Entities:** {num_entities} | "
        f"**Components:** {num_components}",
        "",
    ]
    if baseline_note:
        lines.extend([f"> {baseline_note}", ""])

    # ── Drift table (only when baseline exists) ──────────────────────────
    if drift and baseline:
        similarity = drift["overall_similarity"]
        summary = drift["summary"]
        baseline_components = summary["arch_a_components"]
        metric_map = {m.name: m.value for m in metrics}
        bl_metrics = baseline_metrics or {}

        lines.append("### Drift from Baseline")
        lines.append("")
        lines.append("| Metric | Baseline | Current | Delta |")
        lines.append("|--------|----------|---------|-------|")
        lines.append(
            f"| Components | {baseline_components} | {num_components} | "
            f"{_delta(num_components - baseline_components)} |"
        )
        lines.append(f"| Similarity | — | {similarity:.2f} | — |")

        preferred_metrics = (
            "BalancedArchitectureScore",
            "PrincipleAlignmentScore",
            "RCI",
            "TurboMQ",
        )
        displayed_metrics: set[str] = set()
        for name in preferred_metrics:
            val = metric_map.get(name)
            if val is not None:
                bl_val = bl_metrics.get(name)
                lines.append(_metric_row(name, bl_val, val))
                displayed_metrics.add(name)
        for metric in metrics:
            if metric.name in displayed_metrics:
                continue
            bl_val = bl_metrics.get(metric.name)
            lines.append(_metric_row(metric.name, bl_val, metric.value))

        lines.append("")

        # Architectural changelog: structure + responsibility shifts.
        #
        # Supersedes the older hand-rolled "### Changes" block, which derived
        # added/removed names from the Hungarian `matches` list and read the
        # `possible_splits`/`possible_merges` summary keys. Those keys no
        # longer exist, and `matches` cannot represent a split or a merge at
        # all — see CHANGELOG.md. The changelog reports the same three facts
        # (components added/removed, entity movements, splits/merges) from
        # entity provenance instead, and names the *source* component of each
        # movement, not just its destination.
        #
        # Smells and metrics are excluded here because the "### Smells" and
        # "### Drift from Baseline" sections below already own them. Rendering
        # either twice would state the same fact in two formats.
        #
        # `graph_a` is deliberately the *current* graph: this CLI stores only a
        # baseline Architecture, never the graph it came from. That is safe
        # only because `smells_a=[]`/`metrics_a=[]` are supplied, so the
        # changelog never runs detect_smells/compute_metrics over the pair --
        # which would score the baseline's FQNs against the current graph. It
        # also means the changelog's language-drift warning can never fire
        # here (both language sets come from one graph); the warning still
        # works for MCP/library callers that pass two real graphs, so it stays.
        changelog = changelog_architecture(
            baseline,
            graph,
            current,
            graph,
            smells_a=[],
            smells_b=smells,
            metrics_a=[],
            metrics_b=metrics,
            ref_a="baseline",
            ref_b="current",
        )
        lines.append(
            render_changelog_markdown(
                changelog,
                include_smells=False,
                include_metrics=False,
                heading_level=3,
            )
        )

    # ── Component breakdown ──────────────────────────────────────────────
    lines.append("### Components")
    lines.append("")
    lines.append("| Component | Entities | Responsibility |")
    lines.append("|-----------|----------|----------------|")
    for component in sorted(current.components, key=lambda c: -len(c.entities)):
        resp = (component.responsibility or "")[:60]
        lines.append(
            f"| {component.name} | {len(component.entities)} | {resp} |"
        )
    lines.append("")

    # ── Mermaid dependency diagram ───────────────────────────────────────
    lines.append("### Architecture Diagram")
    lines.append("")
    lines.append("```mermaid")
    lines.append("graph LR")
    # Build inter-component edges from the dependency graph
    entity_to_comp: dict[str, str] = {}
    for component in current.components:
        for ent in component.entities:
            entity_to_comp[ent] = component.name
    comp_edges: set[tuple[str, str]] = set()
    for edge in graph.edges:
        src_comp = entity_to_comp.get(edge.source)
        tgt_comp = entity_to_comp.get(edge.target)
        if src_comp and tgt_comp and src_comp != tgt_comp:
            comp_edges.add((src_comp, tgt_comp))
    for component in current.components:
        lines.append(
            f"    {mermaid_node_id(component.name)}"
            f"[\"{mermaid_label_text(component.name)}\"]"
        )
    for src, tgt in sorted(comp_edges):
        lines.append(f"    {mermaid_node_id(src)} --> {mermaid_node_id(tgt)}")
    lines.append("```")
    lines.append("")

    # ── Smells section with recommendations ──────────────────────────────
    if smells:
        lines.append(f"### Smells ({len(smells)})")
        lines.append("")
        for smell in smells:
            affected = (
                ", ".join(smell.affected_components)
                if smell.affected_components
                else ""
            )
            smell_name = display_value(smell.smell_type)
            smell_key = smell_name.lower().replace(" ", "_")
            lines.append(f"- **{smell_name}**: {affected}")
            rec = _SMELL_RECOMMENDATIONS.get(smell_key)
            if rec:
                lines.append(f"  - 💡 {rec}")
        lines.append("")
    else:
        lines.append("### Smells")
        lines.append("")
        lines.append("✅ No architectural smells detected.")
        lines.append("")

    # ── Metrics table (when no baseline — still useful) ──────────────────
    if not drift:
        lines.append("### Metrics")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for m in metrics:
            lines.append(f"| {m.name} | {m.value:.2f} |")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(
        "*Generated by [arcade-agent](https://github.com/lemduc/arcade-agent)*"
    )
    lines.append("<!-- arcade-agent-drift-report -->")

    return "\n".join(lines)


def _metric_row(name: str, baseline_val: float | None, current_val: float) -> str:
    """Format a single metric row with direction indicator."""
    if baseline_val is None:
        return f"| {name} | — | {current_val:.2f} | — |"
    delta = current_val - baseline_val
    icon = _direction_icon(name, delta)
    return f"| {name} | {baseline_val:.2f} | {current_val:.2f} | {icon} {_delta(delta)} |"


def _direction_icon(name: str, delta: float) -> str:
    """Return 🟢/🔴/⚪ based on whether the metric moved in a good direction."""
    if abs(delta) < 0.005:
        return "⚪"
    if name in _HIGHER_IS_BETTER:
        return "🟢" if delta > 0 else "🔴"
    if name in _LOWER_IS_BETTER:
        return "🟢" if delta < 0 else "🔴"
    # Unknown metric — neutral
    return "⚪"


def _delta(val: int | float) -> str:
    """Format a delta value with sign."""
    if isinstance(val, float):
        return f"+{val:.2f}" if val >= 0 else f"{val:.2f}"
    return f"+{val}" if val >= 0 else str(val)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Detect architecture drift against a stored baseline."
    )
    parser.add_argument(
        "--source",
        default=".",
        help="Path to the source code (default: current directory)",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Language to parse (auto-detect if omitted)",
    )
    parser.add_argument(
        "--baseline",
        default=".arcade/baseline.json",
        help="Path to baseline JSON (default: .arcade/baseline.json)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Overwrite the baseline with the current architecture",
    )
    parser.add_argument(
        "--filter-non-architectural-helpers",
        action="store_true",
        help=(
            "Apply the Arcade Agent self-dogfooding graph profile so private "
            "helpers, methods, and registration edges do not distort architecture."
        ),
    )
    args = parser.parse_args(argv)

    source_path = Path(args.source).resolve()
    baseline_path = Path(args.baseline)

    # 1. Parse
    graph = parse(str(source_path), language=args.language)
    if args.filter_non_architectural_helpers:
        graph = _filter_non_architectural_entities(graph)

    # 2. Recover
    current = recover(graph, algorithm="pkg")
    if args.filter_non_architectural_helpers:
        current.metadata["analysis_profile"] = SELF_DOGFOOD_PROFILE

    # 3. Load baseline and compare (if it exists)
    drift = None
    baseline = None
    baseline_note = None
    baseline_metrics: dict[str, float] = {}
    if baseline_path.exists():
        baseline, baseline_metrics = load_architecture(baseline_path)
        baseline_profile = baseline.metadata.get("analysis_profile")
        current_profile = current.metadata.get("analysis_profile")
        if baseline_profile == current_profile:
            drift = compare(baseline, current)
        else:
            baseline_note = (
                "Baseline comparison skipped because the stored baseline uses "
                f"profile {baseline_profile or 'default'!r}, while this run uses "
                f"{current_profile or 'default'!r}. The next default-branch push "
                "will refresh the baseline with the current profile."
            )

    # 4. Metrics and smells
    metrics = compute_metrics(current, graph)
    smells = detect_smells(current, graph)
    derived_metrics, _, _ = compute_balanced_scores(
        current,
        graph,
        smells,
        metrics=metrics,
    )
    metrics = metrics + derived_metrics

    # 5. Update baseline if requested (persist metrics for future deltas)
    if args.update_baseline:
        metric_map = {m.name: m.value for m in metrics}
        save_architecture(current, baseline_path, metrics=metric_map)
        print(f"Baseline updated: {baseline_path}", file=sys.stderr)

    # 6. Print report
    report = build_report(
        current,
        graph,
        metrics,
        smells,
        drift,
        baseline,
        baseline_metrics,
        baseline_note,
    )
    print(report)


if __name__ == "__main__":
    main()
