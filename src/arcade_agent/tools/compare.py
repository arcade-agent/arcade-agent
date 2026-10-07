"""Tool: Compare architectures across versions (component matching)."""

from typing import Any

from arcade_agent.algorithms.architecture import Architecture
from arcade_agent.algorithms.matching import compute_matched_similarity, match_components
from arcade_agent.algorithms.provenance import classify_structural_changes, structural_dict
from arcade_agent.tools.registry import tool


@tool(
    name="compare",
    description="Compare two architectures. Matches components one-to-one using "
    "the Hungarian algorithm and classifies additions, removals, splits and merges "
    "by entity provenance.",
)
def compare(
    arch_a: Architecture,
    arch_b: Architecture,
) -> dict[str, Any]:
    """Compare two architectures and report differences.

    Uses the Hungarian algorithm to find optimal component matching based
    on entity overlap, then reports additions, removals, and changes.

    Args:
        arch_a: Source architecture (e.g., version N).
        arch_b: Target architecture (e.g., version N+1).

    Returns:
        Dict with overall similarity, component matches, the provenance-derived
        ``structural`` classification (see ``structural_dict``), and summary
        stats. ``matches`` is the raw 1:1 Hungarian view and is kept as-is for
        similarity scoring; ``structural`` is the accurate split/merge-aware
        view and is what ``components_added``/``components_removed``/
        ``components_rewritten`` in ``summary`` are derived from. A component
        that kept its name while its entities churned entirely is a
        ``structural["rewritten"]`` entry, counted in neither added nor
        removed. Consumers rendering component names as
        "added" or "removed" should read from ``structural``, not by
        inspecting ``matches`` for missing sides, to avoid reporting the same
        diff two different ways in one place.
    """
    matches = match_components(arch_a, arch_b)
    overall_similarity = compute_matched_similarity(arch_a, arch_b)
    changes = classify_structural_changes(arch_a, arch_b)

    return {
        "overall_similarity": overall_similarity,
        "matches": matches,
        "structural": structural_dict(changes),
        "summary": {
            "total_matches": len([m for m in matches if m["source"] and m["target"]]),
            "components_added": len(changes.added),
            "components_removed": len(changes.removed),
            "components_rewritten": len(changes.rewritten),
            "splits": len(changes.split),
            "merges": len(changes.merged),
            "arch_a_components": len(arch_a.components),
            "arch_b_components": len(arch_b.components),
        },
    }
