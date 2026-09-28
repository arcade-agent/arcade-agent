#!/usr/bin/env python3
"""Compare current analysis results against a baseline and output Markdown.

Uses arcade-agent's compare tool (A2A analysis with Hungarian algorithm) for
proper architecture-to-architecture comparison when entity data is available.

Usage:
    arcade-compare-baseline current.json baseline.json [--output comment.md]

Exits with code 0 always (comparison is informational, not a pass/fail gate).
"""

import argparse
import copy
import json
import os
import sys
from pathlib import Path

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.exporters.html import build_snapshot_mermaid, export_evolution_html
from arcade_agent.tools.compare import compare


def _delta(new: float, old: float) -> str:
    diff = new - old
    if abs(diff) < 0.0001:
        return "→ (no change)"
    arrow = "↑" if diff > 0 else "↓"
    return f"{arrow} ({diff:+.4f})"


def _delta_with_impact(metric_name: str, new: float, old: float) -> str:
    """Format delta using metric quality semantics, not raw direction."""
    diff = new - old
    if abs(diff) < 0.0001:
        return "⚪ **→ (no change)**"

    arrow = "↑" if diff > 0 else "↓"
    low_impact = {"📦 Components", "🧩 Entities", "🔗 Edges", "IntraConnectivity"}
    better_when_higher, better_when_lower = _metric_semantics(metric_name)

    if better_when_higher:
        icon = "🟢" if diff > 0 else "🔴"
    elif better_when_lower:
        icon = "🟢" if diff < 0 else "🔴"
    elif metric_name in low_impact:
        icon = "🟡"
    else:
        icon = "🟡"

    return f"{icon} **{arrow} ({diff:+.4f})**"


def _severity_icon(severity: str) -> str:
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity.lower(), "⚪")


def _rci_icon(rci: float) -> str:
    if rci >= 0.8:
        return "🟢"
    if rci >= 0.6:
        return "🟡"
    return "🔴"


def _quality_label(rci: float) -> str:
    if rci >= 0.85:
        return "Excellent"
    if rci >= 0.7:
        return "Good"
    if rci >= 0.55:
        return "Fair"
    return "Poor"


def _graph_quality_warning_lines(snapshot: dict[str, object] | None) -> list[str]:
    """Render stored dependency-resolution qualification as Markdown quotes."""
    if not snapshot:
        return []
    quality = snapshot.get("graph_quality")
    if not isinstance(quality, dict) or quality.get("status") != "qualified":
        return []

    lines = [
        "> ⚠️ **Qualified dependency-graph metrics:** discovered local-import coverage "
        "is incomplete; treat scores as directional signals, not a complete architecture "
        "assessment."
    ]
    resolution = quality.get("dependency_resolution")
    if isinstance(resolution, dict):
        for language, summary in sorted(resolution.items()):
            if not isinstance(summary, dict):
                continue
            lines.append(
                f"> `{language}`: {summary.get('resolved_local', 0)} resolved / "
                f"{summary.get('unresolved_local', 0)} unresolved; "
                f"{summary.get('linked_local', 0)} linked / "
                f"{summary.get('unlinked_local', 0)} unlinked."
            )
    return lines


def _numeric_delta(new: float, old: float) -> str:
    diff = new - old
    if abs(diff) < 0.0001:
        return "0"
    sign = "+" if diff > 0 else ""
    if float(diff).is_integer():
        return f"{sign}{int(diff)}"
    return f"{sign}{diff:.4f}"


def _delta_class(new: float, old: float) -> str:
    diff = new - old
    if abs(diff) < 0.0001:
        return "delta-neutral"
    return "delta-positive" if diff > 0 else "delta-negative"


def _metric_semantics(metric_name: str) -> tuple[bool, bool]:
    """Return whether higher or lower values indicate better quality."""
    better_when_higher = {
        "RCI",
        "TurboMQ",
        "BasicMQ",
        "DependencyHealth",
        "ComponentBalance",
        "HubBalance",
        "BoundaryClarity",
        "DependencyDistribution",
        "SmellDiscipline",
        "PrincipleAlignmentScore",
        "BalancedArchitectureScore",
    }
    better_when_lower = {"InterConnectivity", "TwoWayPairRatio"}
    return metric_name in better_when_higher, metric_name in better_when_lower


def _metric_delta_class(metric_name: str, new: float, old: float) -> str:
    """Assign delta classes using metric quality semantics."""
    diff = new - old
    if abs(diff) < 0.0001:
        return "delta-neutral"

    better_when_higher, better_when_lower = _metric_semantics(metric_name)
    if better_when_lower:
        return "delta-positive" if diff < 0 else "delta-negative"
    if better_when_higher:
        return "delta-positive" if diff > 0 else "delta-negative"
    return _delta_class(new, old)


def _all_metric_values(snapshot: dict | None) -> dict[str, float]:
    """Merge core and derived metrics from a stored snapshot."""
    if not snapshot:
        return {}

    merged = dict(snapshot.get("metrics") or {})
    merged.update(snapshot.get("derived_metrics") or {})
    return merged


def _metric_value(snapshot: dict | None, metric_name: str) -> float | None:
    """Return a metric only when it exists in the snapshot schema."""
    return _all_metric_values(snapshot).get(metric_name)


def _quality_snapshot_score(snapshot: dict | None) -> float:
    """Prefer balanced architecture score when available."""
    metrics = _all_metric_values(snapshot)
    return metrics.get("BalancedArchitectureScore", metrics.get("RCI", 0.0))


def _quality_snapshot_metric_name(snapshot: dict | None) -> str:
    """Return the score name represented by _quality_snapshot_score."""
    if _metric_value(snapshot, "BalancedArchitectureScore") is not None:
        return "BalancedArchitectureScore"
    return "QualityScore"


def _score_driver_lines(drivers: list[dict], prefix: str) -> list[str]:
    """Format score driver entries for Markdown output."""
    lines: list[str] = []
    for item in drivers:
        lines.append(
            f"- **{item['name']}**: {prefix}{float(item['gap_to_ideal']):.4f} "
            f"(signal={float(item['value']):.4f})"
        )
    return lines


def _summary_stat_values(
    current: dict,
    baseline: dict | None,
    key: str,
    *,
    current_fallback_key: str | None = None,
    baseline_fallback_key: str | None = None,
    default: int = 0,
) -> tuple[int | None, int]:
    """Return schema-aware baseline/current values for summary statistics."""
    current_value = current.get(key)
    if current_value is None and current_fallback_key:
        current_value = current.get(current_fallback_key, default)
    if current_value is None:
        current_value = default

    if not baseline:
        return default, int(current_value)

    baseline_value = baseline.get(key)
    if baseline_value is None and baseline_fallback_key:
        baseline_value = baseline.get(baseline_fallback_key)

    return (None if baseline_value is None else int(baseline_value), int(current_value))


def _build_summary_metric_row(name: str, baseline_value: int | None, current_value: int) -> dict:
    """Build a metric row while preserving missing baseline schema values."""
    if baseline_value is None:
        return {
            "name": name,
            "baseline": "n/a",
            "current": current_value,
            "delta": "new in schema",
            "delta_class": "delta-neutral",
        }

    return {
        "name": name,
        "baseline": baseline_value,
        "current": current_value,
        "delta": _numeric_delta(current_value, baseline_value),
        "delta_class": _delta_class(current_value, baseline_value),
    }


def _impact_delta_with_optional_baseline(
    metric_name: str,
    current_value: float,
    baseline_value: float | None,
) -> str:
    """Format a delta while preserving unknown baseline values."""
    if baseline_value is None:
        return "⚪ **new in schema**"
    return _delta_with_impact(metric_name, current_value, baseline_value)


def _component_metric_transition(
    source: dict | None,
    target: dict | None,
    key: str,
) -> str:
    """Format component-level metric changes while preserving missing schema values."""
    source_has_value = source is not None and key in source
    target_has_value = target is not None and key in target
    source_value = int(source.get(key, 0)) if source and source_has_value else 0
    target_value = int(target.get(key, 0)) if target and target_has_value else 0

    if source is not None and target is not None and not source_has_value and target_has_value:
        return f"n/a → {target_value} (new in schema)"
    if source is not None and target is None and not source_has_value:
        return "n/a → 0"
    if source is None and target is not None and not target_has_value:
        return "0 → n/a"

    return f"{source_value} → {target_value} ({_numeric_delta(target_value, source_value)})"


def _component_count(component: dict, key: str) -> int:
    return int(component.get(key, 0))


def _component_size(component: dict) -> int:
    return int(component.get("num_entities") or len(component.get("entities", [])))


def _component_map(data: dict | None) -> dict[str, dict]:
    if not data:
        return {}
    return {
        component.get("comparison_name", component["name"]): component
        for component in data.get("components", [])
    }


def _title_token(token: str) -> str:
    """Convert a token into a readable component-name fragment."""
    return token.replace("_", " ").title().replace(" ", "")


def _most_common_token(tokens: list[str], excluded: set[str]) -> str | None:
    counts: dict[str, int] = {}
    for token in tokens:
        if not token or token in excluded:
            continue
        counts[token] = counts.get(token, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _entity_token_parts(entities: list[str]) -> list[list[str]]:
    """Split entity FQNs into token lists."""
    parts_list: list[list[str]] = []
    for fqn in entities:
        parts = [part for part in fqn.split(".") if part]
        if len(parts) > 1:
            parts = parts[:-1]
        parts_list.append(parts)
    return parts_list


def _common_prefix_parts(parts_list: list[list[str]]) -> list[str]:
    """Compute the common prefix shared across entity token lists."""
    if not parts_list:
        return []
    prefix: list[str] = []
    for segments in zip(*parts_list):
        if len(set(segments)) == 1:
            prefix.append(segments[0])
        else:
            break
    return prefix


def _strip_trailing_digits(name: str) -> str:
    """Remove a numeric suffix from a component name."""
    stripped = name.rstrip("0123456789")
    return stripped or name


def _is_generic_component_name(component: dict) -> bool:
    """Return whether a component name is generic enough to relabel."""
    name = component["name"]
    canonical = _strip_trailing_digits(name).replace("_", "").lower()
    if canonical != name.replace("_", "").lower():
        return True
    if canonical in {"default", "cluster", "component", "module"}:
        return True

    entity_parts = _entity_token_parts(component.get("entities", []))
    common_prefix = _common_prefix_parts(entity_parts)
    common_name = "".join(_title_token(part) for part in common_prefix).lower()
    return bool(common_name and canonical == common_name)


def _derive_component_name_from_entities(component: dict) -> str:
    """Infer a stable component label from entity FQNs."""
    entities = component.get("entities", [])
    if not entities:
        return component["name"]

    parts_list = _entity_token_parts(entities)
    common_prefix = _common_prefix_parts(parts_list)

    if common_prefix:
        shared_parts = common_prefix[1:] if len(common_prefix) > 1 else common_prefix
        if len(shared_parts) >= 2:
            return "".join(_title_token(part) for part in shared_parts[-2:])
        if len(shared_parts) == 1:
            return _title_token(shared_parts[0])

    package_heads: list[str] = []
    package_tails: list[str] = []
    module_tokens: list[str] = []

    for parts in parts_list:
        if parts[:len(common_prefix)] == common_prefix:
            remainder = parts[len(common_prefix):]
        else:
            remainder = parts
        if not remainder:
            continue
        package_heads.append(remainder[0])
        if len(remainder) > 1:
            package_tails.append(remainder[1])
        if len(remainder) > 1:
            module_tokens.append(remainder[-2])

    head = _most_common_token(package_heads, excluded=set())
    tail = _most_common_token(package_tails, excluded={head} if head else set())
    module = _most_common_token(module_tokens, excluded={head, tail} - {None})

    if head and tail:
        return f"{_title_token(head)}{_title_token(tail)}"
    if head and module:
        return f"{_title_token(head)}{_title_token(module)}"
    if head:
        return _title_token(head)
    return component["name"]


def _normalize_snapshot(data: dict | None) -> dict | None:
    """Return a copy with stable unique component labels for comparisons."""
    if not data:
        return None

    normalized = copy.deepcopy(data)
    components = normalized.get("components", [])
    total_counts: dict[str, int] = {}
    for component in components:
        total_counts[component["name"]] = total_counts.get(component["name"], 0) + 1

    seen_counts: dict[str, int] = {}
    used_names: set[str] = set()
    rename_map: dict[str, list[str]] = {}
    for component in components:
        base_name = component["name"]
        needs_derived_name = (
            total_counts[base_name] > 1 or _is_generic_component_name(component)
        )
        derived_name = (
            _derive_component_name_from_entities(component)
            if needs_derived_name
            else base_name
        )
        # A derived name may itself end in a digit (``Api2``), which would clash
        # with the suffix minted for the second member of the ``Api`` bucket.
        # Keep bumping the suffix until the label is globally unique.
        suffix = seen_counts.get(derived_name, 0) + 1
        comparison_name = derived_name if suffix == 1 else f"{derived_name}{suffix}"
        while comparison_name in used_names:
            suffix += 1
            comparison_name = f"{derived_name}{suffix}"
        seen_counts[derived_name] = suffix
        used_names.add(comparison_name)
        component["comparison_name"] = comparison_name
        rename_map.setdefault(base_name, []).append(comparison_name)

    dependencies = normalized.get("component_dependencies", [])
    dependency_counters: dict[str, int] = {}
    for dependency in dependencies:
        for key in ("source", "target"):
            base_name = dependency[key]
            labeled_names = rename_map.get(base_name)
            if not labeled_names:
                continue
            dependency_counters[base_name] = dependency_counters.get(base_name, 0) + 1
            index = min(dependency_counters[base_name] - 1, len(labeled_names) - 1)
            dependency[key] = labeled_names[index]

    return normalized


def _dependency_set(data: dict | None) -> set[tuple[str, str]]:
    if not data:
        return set()
    return {
        (dep["source"], dep["target"])
        for dep in data.get("component_dependencies", [])
    }


def _build_metric_rows(current: dict, baseline: dict | None) -> list[dict]:
    metric_rows = []
    baseline_metrics = _all_metric_values(baseline)
    current_metrics = _all_metric_values(current)
    summary_rows = [
        ("Components",) + _summary_stat_values(current, baseline, "num_components"),
        ("Analysis Entities",) + _summary_stat_values(current, baseline, "num_entities"),
        ("Dependencies",) + _summary_stat_values(current, baseline, "num_edges"),
        (
            "Source Entities",
        ) + _summary_stat_values(
            current,
            baseline,
            "source_num_entities",
            current_fallback_key="num_entities",
            baseline_fallback_key="num_entities",
        ),
        ("Classes",) + _summary_stat_values(current, baseline, "class_count"),
        ("Functions",) + _summary_stat_values(current, baseline, "function_count"),
        ("Methods",) + _summary_stat_values(current, baseline, "method_count"),
    ]

    for name, old, new in summary_rows:
        if not baseline:
            metric_rows.append({
                "name": name,
                "baseline": "n/a",
                "current": new,
                "delta": "n/a",
                "delta_class": "delta-neutral",
            })
        else:
            metric_rows.append(_build_summary_metric_row(name, old, new))

    metric_names = sorted(set(baseline_metrics) | set(current_metrics))
    for name in metric_names:
        new = current_metrics.get(name, 0.0)
        if not baseline:
            metric_rows.append({
                "name": name,
                "baseline": "n/a",
                "current": f"{new:.4f}",
                "delta": "n/a",
                "delta_class": "delta-neutral",
            })
            continue

        if name not in baseline_metrics:
            metric_rows.append({
                "name": name,
                "baseline": "n/a",
                "current": f"{new:.4f}",
                "delta": "new in schema",
                "delta_class": "delta-neutral",
            })
            continue

        old = baseline_metrics.get(name, 0.0)
        metric_rows.append({
            "name": name,
            "baseline": f"{old:.4f}",
            "current": f"{new:.4f}",
            "delta": _numeric_delta(new, old),
            "delta_class": _metric_delta_class(name, new, old),
        })

    return metric_rows


def _smell_count(data: dict | None) -> int:
    if not data:
        return 0
    return len(data.get("smells", []))


def _build_component_rows(
    current: dict,
    baseline: dict | None,
    a2a_result: dict | None,
) -> list[dict]:
    current_map = _component_map(current)
    baseline_map = _component_map(baseline)
    rows: list[dict] = []

    if not baseline:
        for component in sorted(
            current_map.values(),
            key=lambda comp: (-_component_size(comp), comp["name"]),
        ):
            rows.append({
                "status": "added",
                "baseline_name": "-",
                "current_name": component["name"],
                "similarity": "-",
                "entities": f"0 → {_component_size(component)}",
                "classes": f"0 → {_component_count(component, 'class_count')}",
                "methods": f"0 → {_component_count(component, 'method_count')}",
            })
        return rows

    structural = (a2a_result or {}).get("structural") or {}
    added_names = set(structural.get("added", []))
    removed_names = set(structural.get("removed", []))

    matched_names: set[str] = set()
    if a2a_result:
        for match in a2a_result.get("matches", []):
            source_name = match.get("source")
            target_name = match.get("target")
            if source_name and target_name:
                source = baseline_map[source_name]
                target = current_map[target_name]
                source_entities = _component_size(source)
                target_entities = _component_size(target)
                rows.append({
                    "status": "matched",
                    "baseline_name": source_name,
                    "current_name": target_name,
                    "similarity": f"{match['similarity']:.4f}",
                    "entities": (
                        f"{source_entities} → {target_entities} "
                        f"({_numeric_delta(target_entities, source_entities)})"
                    ),
                    "classes": (
                        _component_metric_transition(source, target, "class_count")
                    ),
                    "methods": (
                        _component_metric_transition(source, target, "method_count")
                    ),
                })
                matched_names.add(source_name)
                matched_names.add(target_name)
            elif target_name:
                # A Hungarian-unmatched target is only a genuine addition when
                # the provenance-derived `structural` classification agrees --
                # otherwise it is a split product or merge target, already
                # accounted for elsewhere, and reporting it here too would
                # contradict the structural add/remove counts (same class of
                # bug as the "Components Added: 0" vs. "New components: X"
                # inconsistency this replaces).
                matched_names.add(target_name)
                if target_name not in added_names:
                    continue
                target = current_map[target_name]
                rows.append({
                    "status": "added",
                    "baseline_name": "-",
                    "current_name": target_name,
                    "similarity": "-",
                    "entities": f"0 → {_component_size(target)}",
                    "classes": f"0 → {_component_count(target, 'class_count')}",
                    "methods": f"0 → {_component_count(target, 'method_count')}",
                })
            elif source_name:
                matched_names.add(source_name)
                if source_name not in removed_names:
                    continue
                source = baseline_map[source_name]
                rows.append({
                    "status": "removed",
                    "baseline_name": source_name,
                    "current_name": "-",
                    "similarity": "-",
                    "entities": f"{_component_size(source)} → 0",
                    "classes": _component_metric_transition(source, None, "class_count"),
                    "methods": _component_metric_transition(source, None, "method_count"),
                })

    for name, component in sorted(current_map.items()):
        if name not in matched_names:
            rows.append({
                "status": "added",
                "baseline_name": "-",
                "current_name": name,
                "similarity": "-",
                "entities": f"0 → {_component_size(component)}",
                "classes": f"0 → {_component_count(component, 'class_count')}",
                "methods": f"0 → {_component_count(component, 'method_count')}",
            })

    for name, component in sorted(baseline_map.items()):
        if name not in matched_names:
            rows.append({
                "status": "removed",
                "baseline_name": name,
                "current_name": "-",
                "similarity": "-",
                "entities": f"{_component_size(component)} → 0",
                "classes": f"{_component_count(component, 'class_count')} → 0",
                "methods": f"{_component_count(component, 'method_count')} → 0",
            })

    return rows


def _build_dependency_rows(current: dict, baseline: dict | None) -> list[dict]:
    current_deps = _dependency_set(current)
    baseline_deps = _dependency_set(baseline)
    rows = [
        {"status": "added", "source": source, "target": target}
        for source, target in sorted(current_deps - baseline_deps)
    ]
    rows.extend(
        {"status": "removed", "source": source, "target": target}
        for source, target in sorted(baseline_deps - current_deps)
    )
    if not rows:
        rows.append({"status": "matched", "source": "No dependency delta", "target": "-"})
    return rows


def build_report_payload(
    current: dict,
    baseline: dict | None,
    run_url: str = "",
    baseline_note: str = "",
) -> dict:
    """Build a unified before/after comparison payload."""
    baseline = _normalize_snapshot(baseline)
    current = _normalize_snapshot(current)
    a2a_result = _run_a2a_comparison(baseline, current) if baseline else None
    repo_name = current.get("repo_name") or (baseline or {}).get("repo_name") or "repository"
    quality_score = _quality_snapshot_score(current)
    overview_cards = [
        {"label": "Current Components", "value": current.get("num_components", 0)},
        {"label": "Quality Score", "value": f"{quality_score:.4f}"},
        {"label": "Current Classes", "value": current.get("class_count", 0)},
        {"label": "Current Methods", "value": current.get("method_count", 0)},
        {
            "label": "A2A Similarity",
            "value": f"{a2a_result['overall_similarity']:.4f}" if a2a_result else "n/a",
        },
    ]
    return {
        "repo_name": repo_name,
        "baseline_commit": (baseline or {}).get("commit_sha", "none")[:7] or "none",
        "current_commit": current.get("commit_sha", "local")[:7],
        "baseline": baseline,
        "current": current,
        "a2a_result": a2a_result,
        "overview_cards": overview_cards,
        "metric_rows": _build_metric_rows(current, baseline),
        "component_rows": _build_component_rows(current, baseline, a2a_result),
        "dependency_rows": _build_dependency_rows(current, baseline),
        "run_url": run_url,
        "baseline_note": baseline_note,
        "graph_quality": current.get("graph_quality"),
    }


def _write_step_summary(path: Path, report: dict) -> None:
    """Write a compact unified GitHub step summary."""
    current = report["current"]
    baseline = report.get("baseline")
    current_metrics = _all_metric_values(current)
    quality_score = _quality_snapshot_score(current)
    current_rci = current_metrics.get("RCI", 0.0)
    current_tmq = current_metrics.get("TurboMQ", 0.0)
    principle_score = current_metrics.get("PrincipleAlignmentScore", 0.0)
    quality_icon = _rci_icon(quality_score)
    quality_label = _quality_label(quality_score)
    lines = [
        "## 🏛️ Architecture Summary\n",
        "| Metric | Value |",
        "|--------|-------|",
        f"| 📦 Components | {current.get('num_components', 0)} |",
        f"| 🧩 Analysis Entities | {current.get('num_entities', 0)} |",
        f"| 🏷️ Classes | {current.get('class_count', 0)} |",
        f"| 🔧 Methods | {current.get('method_count', 0)} |",
        f"| Quality Score {quality_icon} | {quality_score:.4f} ({quality_label}) |",
        f"| Principle Alignment | {principle_score:.4f} |",
        f"| RCI | {current_rci:.4f} |",
        f"| TurboMQ | {current_tmq:.4f} |",
    ]

    if current.get("smells"):
        smell = current["smells"][0]
        lines.append(
            f"| Top Smell | {smell.get('smell_type', 'Unknown')} ({smell.get('severity', '?')}) |"
        )
    else:
        lines.append("| Smells | None detected |")

    quality_warning = _graph_quality_warning_lines(current)
    if quality_warning:
        lines.append("")
        lines.extend(quality_warning)

    lines.append("\n## 🕸️ High-Level Design\n")
    lines.append("```mermaid")
    lines.append(build_snapshot_mermaid(current))
    lines.append("```")

    if report.get("baseline_note"):
        lines.append(f"\n> {report['baseline_note']}")

    if baseline:
        lines.append("\n## 🔄 Evolution Vs Baseline\n")
        lines.append("| Metric | Baseline | Current | Delta |")
        lines.append("|--------|----------|---------|-------|")
        for row in report["metric_rows"][:9]:
            lines.append(
                f"| {row['name']} | {row['baseline']} | {row['current']} | {row['delta']} |"
            )
        lines.append(
            f"| Smells | {_smell_count(baseline)} | {_smell_count(current)} | "
            f"{_numeric_delta(_smell_count(current), _smell_count(baseline))} |"
        )
        lines.append("\n<details><summary>Before/After Diagrams</summary>\n")
        lines.append("\n**Baseline**")
        lines.append("```mermaid")
        lines.append(build_snapshot_mermaid(baseline))
        lines.append("```")
        lines.append("\n**Current**")
        lines.append("```mermaid")
        lines.append(build_snapshot_mermaid(current))
        lines.append("```")
        lines.append("\n</details>")

    lines.append("\n## 🏗️ High-Level Components\n")
    lines.append("| Component | Entities | Classes | Methods |")
    lines.append("|-----------|----------|---------|---------|")
    for component in sorted(
        current.get("components", []),
        key=lambda comp: (-_component_size(comp), comp.get("comparison_name", comp["name"])),
    ):
        lines.append(
            f"| {component.get('comparison_name', component['name'])} | "
            f"{_component_size(component)} | {component.get('class_count', 0)} | "
            f"{component.get('method_count', 0)} |"
        )

    if baseline:
        lines.append("\n<details><summary>Component Changes</summary>\n")
        lines.append(
            "\n| Status | Baseline | Current | Similarity | Entities | Classes | Methods |"
        )
        lines.append("|--------|----------|---------|------------|----------|---------|---------|")
        for row in report["component_rows"]:
            lines.append(
                f"| {row['status']} | {row['baseline_name']} | {row['current_name']} | "
                f"{row['similarity']} | {row['entities']} | {row['classes']} | {row['methods']} |"
            )
        lines.append("\n</details>")

        dependency_delta = [
            row for row in report["dependency_rows"] if row["status"] != "matched"
        ]
        if dependency_delta:
            lines.append("\n<details><summary>Dependency Delta</summary>\n")
            lines.append("\n| Status | Source | Target |")
            lines.append("|--------|--------|--------|")
            for row in dependency_delta:
                lines.append(f"| {row['status']} | {row['source']} | {row['target']} |")
            lines.append("\n</details>")

    if report.get("run_url"):
        lines.append(
            f"\n📄 [Open workflow run and download comparison artifacts]({report['run_url']})"
        )

    path.write_text("\n".join(lines) + "\n")


def _reconstruct_architecture(data: dict) -> Architecture | None:
    """Reconstruct an Architecture object from stored JSON data."""
    components_data = data.get("components", [])
    if not components_data or "entities" not in components_data[0]:
        return None
    components = [
        Component(
            name=c.get("comparison_name", c["name"]),
            responsibility=c.get("responsibility", ""),
            entities=c.get("entities", []),
        )
        for c in components_data
    ]
    return Architecture(components=components, algorithm=data.get("algorithm", ""))


def _run_a2a_comparison(baseline: dict, current: dict) -> dict | None:
    """Run A2A comparison using arcade-agent's compare tool."""
    arch_a = _reconstruct_architecture(baseline)
    arch_b = _reconstruct_architecture(current)
    if arch_a is None or arch_b is None:
        return None
    return compare(arch_a, arch_b)


def build_comment(
    current: dict,
    baseline: dict | None,
    run_url: str = "",
    baseline_note: str = "",
) -> str:
    """Build a Markdown PR comment body."""
    lines: list[str] = []

    report = build_report_payload(
        current,
        baseline,
        run_url=run_url,
        baseline_note=baseline_note,
    )
    current = report["current"]
    baseline = report.get("baseline")
    cur_metrics = _all_metric_values(current)
    cur_smells = current.get("smells", [])
    cur_components = current.get("components", [])
    cur_rci = cur_metrics.get("RCI", 0.0)
    cur_tmq = cur_metrics.get("TurboMQ", 0.0)
    cur_quality_score = _quality_snapshot_score(current)
    cur_principle_score = cur_metrics.get("PrincipleAlignmentScore", 0.0)
    quality_icon = _rci_icon(cur_quality_score)
    quality_label = _quality_label(cur_quality_score)

    lines.append("## 🤖 Architecture Analysis Summary\n")
    lines.append(
        "_Powered by [arcade-agent](https://github.com/lemduc/arcade-agent) — "
        "automatic architectural self-analysis_\n"
    )
    lines.append("---\n")

    if report.get("baseline_note"):
        lines.append(f"> {report['baseline_note']}\n")

    quality_warning = _graph_quality_warning_lines(current)
    if quality_warning:
        lines.extend(quality_warning)
        lines.append("")

    # -- Metric evolution quick view (top) -------------------------------------
    if baseline:
        bl_metrics = _all_metric_values(baseline)
        bl_rci = bl_metrics.get("RCI", 0.0)
        bl_tmq = bl_metrics.get("TurboMQ", 0.0)
        bl_quality_score = _quality_snapshot_score(baseline)
        bl_balanced_score = _metric_value(baseline, "BalancedArchitectureScore")
        cur_balanced_score = _metric_value(current, "BalancedArchitectureScore")
        bl_commit = baseline.get("commit_sha", "unknown")[:7]
        bl_components = baseline.get("num_components", 0)
        cur_components_count = current.get("num_components", 0)
        bl_entities = baseline.get("num_entities", 0)
        cur_entities_count = current.get("num_entities", 0)
        bl_edges = baseline.get("num_edges", 0)
        cur_edges_count = current.get("num_edges", 0)

        lines.append("### 📈 Metric Evolution\n")
        lines.append(f"_Baseline commit: `{bl_commit}`_\n")
        lines.append("_Legend: 🟢 better · 🔴 worse · 🟡 low impact · ⚪ no change_\n")
        lines.append("| Metric | Baseline | Current | Change |")
        lines.append("|--------|----------|---------|--------|")
        if cur_balanced_score is not None:
            if bl_balanced_score is None:
                lines.append(
                    f"| BalancedArchitectureScore | n/a | {cur_balanced_score:.4f} "
                    "| ⚪ **new in schema** |"
                )
            else:
                balanced_delta = _delta_with_impact(
                    "BalancedArchitectureScore",
                    cur_balanced_score,
                    bl_balanced_score,
                )
                lines.append(
                    f"| BalancedArchitectureScore | {bl_balanced_score:.4f} "
                    f"| {cur_balanced_score:.4f} | {balanced_delta} |"
                )
        lines.append(
            f"| 📦 Components | {bl_components} "
            f"| {cur_components_count} "
            f"| {_delta_with_impact('📦 Components', cur_components_count, bl_components)} |"
        )
        lines.append(
            f"| 🧩 Entities | {bl_entities} "
            f"| {cur_entities_count} "
            f"| {_delta_with_impact('🧩 Entities', cur_entities_count, bl_entities)} |"
        )
        lines.append(
            f"| 🔗 Edges | {bl_edges} "
            f"| {cur_edges_count} "
            f"| {_delta_with_impact('🔗 Edges', cur_edges_count, bl_edges)} |"
        )
        baseline_class_count, current_class_count = _summary_stat_values(
            current, baseline, 'class_count'
        )
        baseline_function_count, current_function_count = _summary_stat_values(
            current, baseline, 'function_count'
        )
        baseline_method_count, current_method_count = _summary_stat_values(
            current, baseline, 'method_count'
        )
        class_delta = _impact_delta_with_optional_baseline(
            '🧩 Entities', current_class_count, baseline_class_count
        )
        function_delta = _impact_delta_with_optional_baseline(
            '🧩 Entities', current_function_count, baseline_function_count
        )
        method_delta = _impact_delta_with_optional_baseline(
            '🧩 Entities', current_method_count, baseline_method_count
        )
        baseline_class_display = baseline_class_count if baseline_class_count is not None else 'n/a'
        baseline_function_display = (
            baseline_function_count if baseline_function_count is not None else 'n/a'
        )
        baseline_method_display = (
            baseline_method_count if baseline_method_count is not None else 'n/a'
        )
        lines.append(
            f"| 🏷️ Classes | {baseline_class_display} | "
            f"{current_class_count} | "
            f"{class_delta} |"
        )
        lines.append(
            f"| ƒ Functions | {baseline_function_display} | "
            f"{current_function_count} | "
            f"{function_delta} |"
        )
        lines.append(
            f"| 🔧 Methods | {baseline_method_display} | "
            f"{current_method_count} | "
            f"{method_delta} |"
        )
        lines.append(
            f"| RCI | {bl_rci:.4f} | {cur_rci:.4f} "
            f"| {_delta_with_impact('RCI', cur_rci, bl_rci)} |"
        )
        lines.append(
            f"| TurboMQ | {bl_tmq:.4f} | {cur_tmq:.4f} "
            f"| {_delta_with_impact('TurboMQ', cur_tmq, bl_tmq)} |"
        )
        for name in bl_metrics:
            if name not in ("RCI", "TurboMQ", "BalancedArchitectureScore"):
                bl_v = bl_metrics.get(name, 0.0)
                cur_v = cur_metrics.get(name, 0.0)
                lines.append(
                    f"| {name} | {bl_v:.4f} | {cur_v:.4f} "
                    f"| {_delta_with_impact(name, cur_v, bl_v)} |"
                )
        lines.append("")

    # -- Current state -----------------------------------------------------------
    lines.append("### 🏛️ Current Architecture\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| 📦 Components | **{current.get('num_components')}** |")
    lines.append(f"| 🧩 Entities | **{current.get('num_entities')}** |")
    lines.append(f"| 🔗 Edges | **{current.get('num_edges')}** |")
    lines.append(f"| 🏷️ Classes | **{current.get('class_count', 0)}** |")
    lines.append(f"| ƒ Functions | **{current.get('function_count', 0)}** |")
    lines.append(f"| 🔧 Methods | **{current.get('method_count', 0)}** |")
    lines.append(
        f"| Balanced Score {quality_icon} | **{cur_quality_score:.4f}** ({quality_label}) |"
    )
    lines.append(f"| Principle Alignment | **{cur_principle_score:.4f}** |")
    lines.append(f"| RCI | **{cur_rci:.4f}** |")
    lines.append(f"| TurboMQ | **{cur_tmq:.4f}** |")
    for name, val in cur_metrics.items():
        if name not in ("RCI", "TurboMQ", "BalancedArchitectureScore", "PrincipleAlignmentScore"):
            lines.append(f"| {name} | {val:.4f} |")
    lines.append("")

    principle_signals = current.get("principle_signals") or {}
    if principle_signals:
        lines.append("### 🧭 Principle Signals\n")
        lines.append("| Signal | Value |")
        lines.append("|--------|-------|")
        for name, value in principle_signals.items():
            lines.append(f"| {name} | {value:.4f} |")
        lines.append("")
    score_drivers = current.get("score_drivers") or {}
    if score_drivers:
        lines.append("### 🎯 Score Drivers\n")
        risk_lines = _score_driver_lines(score_drivers.get("risks") or [], "gap=")
        strength_lines = _score_driver_lines(score_drivers.get("strengths") or [], "gap=")
        if risk_lines:
            lines.append("**Biggest risks**")
            lines.extend(risk_lines)
            lines.append("")
        if strength_lines:
            lines.append("**Strongest areas**")
            lines.extend(strength_lines)
            lines.append("")

    lines.append("### 🕸️ High-Level Design\n")
    lines.append("```mermaid")
    lines.append(build_snapshot_mermaid(current))
    lines.append("```")
    lines.append("")

    # -- Components --------------------------------------------------------------
    if cur_components:
        lines.append("<details><summary>🏗️ Components breakdown</summary>\n")
        lines.append("| Component | Entities | Classes | Methods |")
        lines.append("|-----------|----------|---------|---------|")
        for comp in sorted(
            cur_components,
            key=lambda c: (
                -(c.get("num_entities") or len(c.get("entities", []))),
                c.get("comparison_name", c["name"]),
            ),
        ):
            count = comp.get("num_entities") or len(comp.get("entities", []))
            lines.append(
                f"| {comp.get('comparison_name', comp['name'])} | {count} | "
                f"{comp.get('class_count', 0)} | {comp.get('method_count', 0)} |"
            )
        lines.append("</details>\n")

    # -- Smells ------------------------------------------------------------------
    lines.append("### 🚨 Architectural Smells\n")
    if cur_smells:
        lines.append("| Severity | Type | Affected Components |")
        lines.append("|----------|------|---------------------|")
        for s in cur_smells:
            si = _severity_icon(s.get("severity", ""))
            stype = s.get("smell_type", "Unknown")
            comps = ", ".join(s.get("affected_components", []))
            lines.append(f"| {si} {s.get('severity','?')} | {stype} | {comps} |")
    else:
        lines.append("✅ No architectural smells detected.")
    lines.append("")

    # -- Evolution (before/after) ------------------------------------------------
    if baseline:
        bl_metrics = _all_metric_values(baseline)
        bl_rci = bl_metrics.get("RCI", 0.0)
        bl_tmq = bl_metrics.get("TurboMQ", 0.0)
        bl_smells = baseline.get("smells", [])
        bl_commit = baseline.get("commit_sha", "unknown")[:7]

        # Run A2A comparison via arcade-agent's compare tool
        a2a_result = _run_a2a_comparison(baseline, current)

        lines.append("### 📈 Evolution vs Baseline\n")
        lines.append(f"_Baseline commit: `{bl_commit}`_\n")

        # A2A similarity section
        if a2a_result:
            summary = a2a_result["summary"]
            lines.append("#### Architecture-to-Architecture (A2A) Comparison\n")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| A2A Similarity | **{a2a_result['overall_similarity']:.4f}** |")
            lines.append(f"| Matched Components | {summary['total_matches']} |")
            lines.append(f"| Components Added | {summary['components_added']} |")
            lines.append(f"| Components Removed | {summary['components_removed']} |")
            if summary.get("components_rewritten"):
                lines.append(
                    f"| Components Rewritten | {summary['components_rewritten']} |"
                )
            if summary.get("splits"):
                lines.append(f"| Splits | {summary['splits']} |")
            if summary.get("merges"):
                lines.append(f"| Merges | {summary['merges']} |")
            lines.append("")

            # Component-level matches detail. "Matched" stays the raw Hungarian
            # 1:1 view (similarity scoring). "New"/"Removed" use the
            # provenance-derived `structural` classification -- not whichever
            # side a Hungarian match left empty -- so this list always agrees
            # with the "Components Added"/"Components Removed" counts above.
            # A component with no source or target in `matches` but that is
            # instead a split product or merge target does not appear in
            # either list; it is reported via "Splits"/"Merges" above.
            matches = a2a_result.get("matches", [])
            matched = [m for m in matches if m.get("source") and m.get("target")]
            structural = a2a_result.get("structural") or {}
            added_names = sorted(structural.get("added", []))
            removed_names = sorted(structural.get("removed", []))
            # A component that kept its name while its entities churned
            # entirely belongs to neither list: it used to appear in both,
            # contradicting the "matched" row for the very same component.
            rewritten_entries = sorted(
                structural.get("rewritten", []), key=lambda entry: entry["name"]
            )

            if matched or added_names or removed_names or rewritten_entries:
                lines.append("<details><summary>Component matching details</summary>\n")
                if matched:
                    lines.append("**Matched:**")
                    lines.append("| Baseline | Current | Similarity |")
                    lines.append("|----------|---------|------------|")
                    for m in sorted(matched, key=lambda x: -x["similarity"]):
                        lines.append(
                            f"| {m['source']} | {m['target']} | {m['similarity']:.4f} |"
                        )
                    lines.append("")
                if added_names:
                    lines.append("**New components:** " + ", ".join(
                        f"`{name}`" for name in added_names
                    ))
                    lines.append("")
                if removed_names:
                    lines.append("**Removed components:** " + ", ".join(
                        f"`{name}`" for name in removed_names
                    ))
                    lines.append("")
                if rewritten_entries:
                    lines.append("**Rewritten components** (name kept, entities "
                                 "replaced): " + ", ".join(
                                     f"`{entry['name']}` ({entry['before']} → "
                                     f"{entry['after']} entities, "
                                     f"{entry['retained']} in common)"
                                     for entry in rewritten_entries
                                 ))
                    lines.append("")
                lines.append("</details>\n")

        lines.append("<details><summary>High-level component statistics</summary>\n")
        lines.append("| Status | Baseline | Current | Similarity | Entities | Classes | Methods |")
        lines.append("|--------|----------|---------|------------|----------|---------|---------|")
        for row in report["component_rows"]:
            lines.append(
                f"| {row['status']} | {row['baseline_name']} | {row['current_name']} | "
                f"{row['similarity']} | {row['entities']} | {row['classes']} | {row['methods']} |"
            )
        lines.append("</details>\n")

        lines.append("<details><summary>Before/After Mermaid diagrams</summary>\n")
        lines.append("\n**Baseline**")
        lines.append("```mermaid")
        lines.append(build_snapshot_mermaid(baseline))
        lines.append("```")
        lines.append("\n**Current**")
        lines.append("```mermaid")
        lines.append(build_snapshot_mermaid(current))
        lines.append("```")
        lines.append("</details>\n")

        lines.append("<details><summary>Component dependency delta</summary>\n")
        lines.append("| Status | Source | Target |")
        lines.append("|--------|--------|--------|")
        for row in report["dependency_rows"]:
            lines.append(f"| {row['status']} | {row['source']} | {row['target']} |")
        lines.append("</details>\n")

        # Smell changes
        cur_smell_types = {s.get("smell_type") for s in cur_smells}
        bl_smell_types = {s.get("smell_type") for s in bl_smells}
        new_smells = cur_smell_types - bl_smell_types
        resolved_smells = bl_smell_types - cur_smell_types

        if new_smells or resolved_smells:
            lines.append("**Smell changes:**")
            for t in new_smells:
                lines.append(f"- 🆕 New smell: `{t}`")
            for t in resolved_smells:
                lines.append(f"- ✅ Resolved: `{t}`")
            lines.append("")

    else:
        lines.append(
            "> ℹ️ No baseline available — this is the first analysis run or the "
            "baseline artifact has expired. The current results will be stored "
            "as the new baseline.\n"
        )

    # -- CI/CD Insights ----------------------------------------------------------
    lines.append("### 💡 CI/CD Insights\n")
    quality_metric_name = _quality_snapshot_metric_name(current)
    lines.append(
        f"- **Quality Score**: {quality_icon} {quality_label} "
        f"({quality_metric_name}={cur_quality_score:.4f})"
    )
    lines.append(
        f"- **Principle Alignment**: {cur_principle_score:.4f} "
        f"(higher means cleaner layering, focus, and boundaries)"
    )
    score_drivers = current.get("score_drivers") or {}
    top_risks = score_drivers.get("risks") or []
    if top_risks:
        lines.append(
            f"- **Top Risk Driver**: {top_risks[0]['name']} "
            f"(signal={float(top_risks[0]['value']):.4f})"
        )

    if baseline:
        bl_quality_score = _quality_snapshot_score(baseline)
        quality_trend = cur_quality_score - bl_quality_score
        if quality_trend > 0.01:
            trend = "📈 Improving modularity"
        elif quality_trend < -0.01:
            trend = "📉 Declining cohesion — consider reviewing recent refactoring"
        else:
            trend = "➡️ Stable architectural quality"
        lines.append(f"- **Trend**: {trend}")

        # A2A insight
        a2a_result = _run_a2a_comparison(baseline, current)
        if a2a_result:
            sim = a2a_result["overall_similarity"]
            if sim >= 0.9:
                lines.append(f"- **Architecture Stability**: 🟢 High (A2A={sim:.4f})")
            elif sim >= 0.7:
                lines.append(f"- **Architecture Stability**: 🟡 Moderate (A2A={sim:.4f})")
            else:
                lines.append(
                    f"- **Architecture Stability**: 🔴 Low (A2A={sim:.4f}) "
                    "— significant restructuring detected"
                )

    smell_count = len(cur_smells)
    if smell_count == 0:
        lines.append("- **Smells**: ✅ Clean — no architectural smells")
    elif smell_count <= 2:
        lines.append(f"- **Smells**: ⚠️ {smell_count} smell(s) — review suggested")
    else:
        lines.append(f"- **Smells**: 🔴 {smell_count} smells — refactoring recommended")

    if run_url:
        lines.append(f"\n📄 [View HTML reports and artifacts]({run_url})")

    lines.append("\n---")
    lines.append(
        "_This comment is auto-generated by the self-dogfooding CI job. "
        "It updates on every push to this PR._"
    )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare architecture results vs baseline")
    parser.add_argument("current", help="Path to current analysis JSON")
    parser.add_argument("baseline", nargs="?", help="Path to baseline JSON (optional)")
    parser.add_argument(
        "--repo-name",
        default="",
        help="Optional repository/project name override for reports",
    )
    parser.add_argument(
        "--output",
        default="pr_comment.md",
        help="Output file for the generated Markdown comment",
    )
    parser.add_argument(
        "--run-url",
        default="",
        help="GitHub Actions run URL for artifact linking",
    )
    parser.add_argument(
        "--output-html",
        default="",
        help="Optional output path for the generated HTML comparison report",
    )
    parser.add_argument(
        "--baseline-note",
        default="",
        help="Optional note explaining why baseline comparison is unavailable or partial",
    )
    args = parser.parse_args()

    current_path = Path(args.current)
    if not current_path.exists():
        print(f"Error: current results file not found: {current_path}", file=sys.stderr)
        sys.exit(1)

    current = json.loads(current_path.read_text())
    if args.repo_name:
        current["repo_name"] = args.repo_name
    baseline: dict | None = None

    if args.baseline:
        bl_path = Path(args.baseline)
        if bl_path.exists():
            baseline = json.loads(bl_path.read_text())
            if args.repo_name:
                baseline["repo_name"] = args.repo_name
            print(f"Loaded baseline from {bl_path}")
        else:
            print(f"Baseline file not found: {bl_path} — running without baseline")

    report = build_report_payload(
        current,
        baseline,
        run_url=args.run_url,
        baseline_note=args.baseline_note,
    )
    comment = build_comment(
        current,
        baseline,
        run_url=args.run_url,
        baseline_note=args.baseline_note,
    )

    out = Path(args.output)
    out.write_text(comment)
    print(f"PR comment written to {out}")

    if args.output_html:
        html_out = Path(args.output_html)
        export_evolution_html(report, html_out)
        print(f"HTML comparison report written to {html_out}")

    summary_file = Path(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"))
    if summary_file != Path("/dev/null"):
        _write_step_summary(summary_file, report)

    # Also print inline summary to CI logs
    print("\n" + "=" * 60)
    print("BEFORE/AFTER COMPARISON SUMMARY")
    print("=" * 60)
    if baseline:
        bl_metrics = _all_metric_values(baseline)
        cur_metrics = _all_metric_values(current)
        bl_rci = bl_metrics.get("RCI", 0.0)
        cur_rci = cur_metrics.get("RCI", 0.0)
        bl_quality_score = _quality_snapshot_score(baseline)
        cur_quality_score = _quality_snapshot_score(current)

        # A2A comparison
        a2a_result = _run_a2a_comparison(baseline, current)
        if a2a_result:
            summary = a2a_result["summary"]
            print(f"  A2A Similarity: {a2a_result['overall_similarity']:.4f}")
            print(
                f"  Components:  {summary['arch_a_components']} → "
                f"{summary['arch_b_components']} "
                f"(+{summary['components_added']} / -{summary['components_removed']})"
            )
            if summary.get("splits"):
                print(f"  Splits: {summary['splits']}")
            if summary.get("merges"):
                print(f"  Merges: {summary['merges']}")
        else:
            print(
                f"  Components:  {baseline.get('num_components')} → "
                f"{current.get('num_components')} "
                f"{_delta(current.get('num_components', 0), baseline.get('num_components', 0))}"
            )

        print(
            f"  Entities:    {baseline.get('num_entities')} → "
            f"{current.get('num_entities')} "
            f"{_delta(current.get('num_entities', 0), baseline.get('num_entities', 0))}"
        )
        print(
            f"  Edges:       {baseline.get('num_edges')} → "
            f"{current.get('num_edges')} "
            f"{_delta(current.get('num_edges', 0), baseline.get('num_edges', 0))}"
        )
        print(
            f"  Classes:     {baseline.get('class_count', 0)} → "
            f"{current.get('class_count', 0)} "
            f"{_delta(current.get('class_count', 0), baseline.get('class_count', 0))}"
        )
        print(
            f"  Methods:     {baseline.get('method_count', 0)} → "
            f"{current.get('method_count', 0)} "
            f"{_delta(current.get('method_count', 0), baseline.get('method_count', 0))}"
        )
        print(
            f"  RCI:         {bl_rci:.4f} → {cur_rci:.4f} {_delta(cur_rci, bl_rci)}"
        )
        print(
            f"  TurboMQ:     {bl_metrics.get('TurboMQ', 0):.4f} → "
            f"{cur_metrics.get('TurboMQ', 0):.4f} "
            f"{_delta(cur_metrics.get('TurboMQ', 0), bl_metrics.get('TurboMQ', 0))}"
        )
        print(
            f"  Balanced:    {bl_quality_score:.4f} → {cur_quality_score:.4f} "
            f"{_delta(cur_quality_score, bl_quality_score)}"
        )
        cur_smell_count = len(current.get("smells", []))
        bl_smell_count = len(baseline.get("smells", []))
        print(f"  Smells:      {bl_smell_count} → {cur_smell_count}")

    else:
        print("  No baseline available — first run or baseline expired.")
        cur_metrics = _all_metric_values(current)
        cur_rci = cur_metrics.get("RCI", 0.0)
        print(f"  Components:  {current.get('num_components')}")
        print(f"  Entities:    {current.get('num_entities')}")
        print(f"  Classes:     {current.get('class_count', 0)}")
        print(f"  Methods:     {current.get('method_count', 0)}")
        print(f"  Balanced:    {_quality_snapshot_score(current):.4f}")
        print(f"  RCI:         {cur_rci:.4f}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
