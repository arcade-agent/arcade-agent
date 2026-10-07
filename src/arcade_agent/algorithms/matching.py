"""Cluster matching for comparing two recovered architectures.

Uses the Hungarian algorithm (linear sum assignment) to find optimal
one-to-one matching between components of two architectures.
"""

import numpy as np
from scipy.optimize import linear_sum_assignment

from arcade_agent.algorithms.architecture import Architecture


def match_components(
    arch_a: Architecture,
    arch_b: Architecture,
) -> list[dict]:
    """Match components between two architectures using the Hungarian algorithm.

    Computes entity overlap (Jaccard similarity) between all pairs of
    components from arch_a and arch_b, then finds the optimal matching.

    Args:
        arch_a: Source architecture (e.g., version N).
        arch_b: Target architecture (e.g., version N+1).

    Returns:
        List of match dicts with:
        - source: component name from arch_a (or None if added)
        - target: component name from arch_b (or None if removed)
        - similarity: Jaccard overlap score
        - entities_added: entities in target but not source
        - entities_removed: entities in source but not target
    """
    comps_a = arch_a.components
    comps_b = arch_b.components

    if not comps_a and not comps_b:
        return []

    # Build cost matrix (negative similarity for minimization)
    n = max(len(comps_a), len(comps_b))
    cost_matrix = np.zeros((n, n))

    for i, ca in enumerate(comps_a):
        set_a = set(ca.entities)
        for j, cb in enumerate(comps_b):
            set_b = set(cb.entities)
            intersection = set_a & set_b
            union = set_a | set_b
            similarity = len(intersection) / len(union) if union else 0.0
            cost_matrix[i, j] = -similarity  # negative for minimization

    # Solve assignment problem
    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    matches = []
    matched_b: set[int] = set()

    for i, j in zip(row_ind, col_ind):
        if i < len(comps_a) and j < len(comps_b):
            ca = comps_a[i]
            cb = comps_b[j]
            set_a = set(ca.entities)
            set_b = set(cb.entities)
            similarity = -cost_matrix[i, j]

            matches.append({
                "source": ca.name,
                "target": cb.name,
                "similarity": round(similarity, 4),
                "entities_added": sorted(set_b - set_a),
                "entities_removed": sorted(set_a - set_b),
            })
            matched_b.add(j)
        elif i < len(comps_a):
            # Component removed (no match in arch_b)
            ca = comps_a[i]
            matches.append({
                "source": ca.name,
                "target": None,
                "similarity": 0.0,
                "entities_added": [],
                "entities_removed": sorted(ca.entities),
            })
        elif j < len(comps_b):
            # Component added (no match in arch_a)
            cb = comps_b[j]
            matches.append({
                "source": None,
                "target": cb.name,
                "similarity": 0.0,
                "entities_added": sorted(cb.entities),
                "entities_removed": [],
            })
            matched_b.add(j)

    # Any unmatched components in arch_b
    for j, cb in enumerate(comps_b):
        if j not in matched_b:
            matches.append({
                "source": None,
                "target": cb.name,
                "similarity": 0.0,
                "entities_added": sorted(cb.entities),
                "entities_removed": [],
            })

    return matches


def compute_matched_similarity(arch_a: Architecture, arch_b: Architecture) -> float:
    """Mean Jaccard similarity over the optimal one-to-one component matching.

    Unmatched components count as 0. This is *not* the a2a metric of
    Behnamghader et al. (EMSE 2017), which is defined through the minimum
    number of transform operations between two architectures; the two are not
    comparable and should not be reported under the same name.
    """
    matches = match_components(arch_a, arch_b)
    if not matches:
        return 0.0

    total_sim = sum(m["similarity"] for m in matches)
    return round(total_sim / len(matches), 4)


def compute_a2a_similarity(arch_a: Architecture, arch_b: Architecture) -> float:
    """Deprecated alias of :func:`compute_matched_similarity` (not the published a2a)."""
    return compute_matched_similarity(arch_a, arch_b)
