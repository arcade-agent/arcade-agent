"""LIMBO: Information-theoretic agglomerative clustering for architecture recovery.

Structurally identical to ARC — same concern vectors, same merge strategy — but
uses **information loss** (size-weighted JS divergence) as the merge criterion
instead of maximizing JS similarity.

This is an approximation, not the published algorithm: it clusters ARC's
LLM-assigned concern vectors rather than structural features summarized in a
DCF-tree, and inherits ARC's run-to-run variation.

Reference:
    Andritsos, Dumitriu & Tzerpos (2005). "Information-Theoretic Software
    Clustering." IEEE Transactions on Software Engineering.
"""

from __future__ import annotations

import logging

from arcade_agent.algorithms.arc import (
    _arc_cluster_name,
    _avg_structural_sim,
    _build_concern_vectors,
    _cluster_responsibility,
    _js_divergence,
    _merge_vectors,
    _tag_entities_llm,
)
from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.parsers.graph import DependencyGraph

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Information loss criterion
# ---------------------------------------------------------------------------

def _info_loss(
    vec_a: list[float],
    size_a: int,
    vec_b: list[float],
    size_b: int,
    total: int,
) -> float:
    """Compute information loss from merging two clusters.

    Information loss is the size-weighted JS divergence between the two
    cluster vectors, normalized by the total number of entities:

        loss = (size_a + size_b) / total * JS(vec_a, vec_b)

    Lower values indicate clusters that can be merged with less information
    loss — i.e., they are already similar and/or small.
    """
    return (size_a + size_b) / total * _js_divergence(vec_a, vec_b)


# ---------------------------------------------------------------------------
# LIMBO clustering
# ---------------------------------------------------------------------------

def limbo(
    dep_graph: DependencyGraph,
    num_clusters: int | None = None,
    hybrid_weight: float = 1.0,
) -> Architecture:
    """Information-theoretic architecture recovery (LIMBO).

    Agglomerative hierarchical clustering that minimizes information loss
    at each merge step, using LLM-assigned concern vectors.

    Args:
        dep_graph: Dependency graph to cluster.
        num_clusters: Target number of clusters.  If None, auto-selects
            (sqrt of entity count, clamped to 5-20).
        hybrid_weight: Weight for semantic criterion (0-1).
            1.0 = pure info-loss (default), 0.0 = pure structural,
            intermediate = blended.

    Returns:
        Architecture with recovered components.
    """
    entities = list(dep_graph.entities.keys())
    if not entities:
        return Architecture(algorithm="limbo")

    # Auto-select target clusters
    if num_clusters is None:
        num_clusters = max(5, min(20, int(len(entities) ** 0.5)))

    # 1. Tag entities with concerns via LLM
    log.info("LIMBO: tagging %d entities with concerns...", len(entities))
    entity_tags = _tag_entities_llm(dep_graph)

    # 2. Build concern vectors
    concern_labels, vectors = _build_concern_vectors(entities, entity_tags)
    log.info("LIMBO: %d unique concerns identified", len(concern_labels))

    # 3. Initialize clusters (each entity = one cluster)
    clusters: dict[str, list[str]] = {fqn: [fqn] for fqn in entities}
    cluster_vectors: dict[str, list[float]] = {fqn: vectors[fqn] for fqn in entities}
    total_entities = len(entities)

    # Precompute structural adjacency for hybrid mode
    adjacency = dep_graph.to_adjacency()

    # 4. Agglomerative clustering — minimize information loss
    while len(clusters) > num_clusters:
        best_loss = float("inf")
        best_pair: tuple[str, str] | None = None

        keys = list(clusters.keys())
        for i, key_a in enumerate(keys):
            for j in range(i + 1, len(keys)):
                key_b = keys[j]

                # Semantic criterion: information loss
                sem_loss = _info_loss(
                    cluster_vectors[key_a], len(clusters[key_a]),
                    cluster_vectors[key_b], len(clusters[key_b]),
                    total_entities,
                )

                # Structural: invert similarity to get a "loss" (lower = better)
                if hybrid_weight < 1.0:
                    struct_sim = _avg_structural_sim(
                        clusters[key_a], clusters[key_b], adjacency
                    )
                    # Combine: minimize weighted loss
                    loss = hybrid_weight * sem_loss + (1 - hybrid_weight) * (1 - struct_sim)
                else:
                    loss = sem_loss

                if loss < best_loss:
                    best_loss = loss
                    best_pair = (key_a, key_b)

        if best_pair is None:
            break

        # Merge
        key_a, key_b = best_pair
        size_a = len(clusters[key_a])
        size_b = len(clusters[key_b])

        merged_members = clusters[key_a] + clusters[key_b]
        merged_vec = _merge_vectors(
            cluster_vectors[key_a], size_a,
            cluster_vectors[key_b], size_b,
        )

        del clusters[key_b]
        del cluster_vectors[key_b]
        clusters[key_a] = merged_members
        cluster_vectors[key_a] = merged_vec

    # 5. Build architecture
    components = []
    for key, members in sorted(clusters.items()):
        name = _arc_cluster_name(members, dep_graph, entity_tags)
        responsibility = _cluster_responsibility(
            cluster_vectors[key], concern_labels
        )
        components.append(
            Component(
                name=name,
                responsibility=responsibility,
                entities=sorted(members),
            )
        )

    return Architecture(
        components=components,
        rationale=(
            f"LIMBO: information-theoretic clustering using LLM-assigned "
            f"concern vectors (hybrid_weight={hybrid_weight}, "
            f"{len(concern_labels)} concerns)"
        ),
        algorithm="limbo",
        metadata={
            "hybrid_weight": hybrid_weight,
            "num_concerns": len(concern_labels),
            "concern_labels": concern_labels,
            "num_clusters": len(components),
        },
    )
