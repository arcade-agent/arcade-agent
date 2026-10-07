"""ARC: Architecture Recovery using Concerns.

Modern reimplementation of the original ARC algorithm from ARCADE Core.
Instead of MALLET topic modeling, uses Claude CLI to assign semantic concern
labels to entities, then clusters using Jensen-Shannon divergence on the
resulting concern vectors — optionally combined with structural similarity.

This is an approximation, not the published algorithm: concerns come from an
LLM reading entity names, packages, imports and inheritance (not a topic model
over source text), and labels are not seeded or cached, so results can vary
between runs. Use ``pkg``, ``wca`` or ``acdc`` where determinism matters.

Reference:
    Garcia, Popescu, Mattmann, Medvidovic & Cai (2011). "Enhancing
    Architectural Recovery Using Concerns." Proc. IEEE/ACM ASE, pp. 552-555.
"""

from __future__ import annotations

import json
import logging
import math

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.parsers.graph import DependencyGraph

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step 1 — LLM-based concern tagging
# ---------------------------------------------------------------------------

def _tag_entities_llm(
    dep_graph: DependencyGraph,
    max_entities_per_batch: int = 60,
) -> dict[str, list[str]]:
    """Ask Claude to assign concern labels to every entity.

    Entities are sent in package-grouped batches to stay within the token
    budget.  Returns a mapping ``{entity_fqn: [concern_label, ...]}``.
    """
    from arcade_agent.algorithms.llm import MOCK_MODE, ask_claude_json

    if MOCK_MODE:
        return _tag_entities_heuristic(dep_graph)

    # Group entities by package for coherent batches
    pkg_entities: dict[str, list[str]] = {}
    for fqn, entity in dep_graph.entities.items():
        pkg_entities.setdefault(entity.package, []).append(fqn)

    # Build batches that respect max size
    batches: list[list[str]] = []
    current: list[str] = []
    for _pkg, fqns in sorted(pkg_entities.items()):
        for fqn in sorted(fqns):
            current.append(fqn)
            if len(current) >= max_entities_per_batch:
                batches.append(current)
                current = []
    if current:
        batches.append(current)

    all_tags: dict[str, list[str]] = {}

    for batch in batches:
        entity_info = []
        for fqn in batch:
            e = dep_graph.entities[fqn]
            info: dict[str, str | list[str]] = {
                "fqn": fqn,
                "name": e.name,
                "package": e.package,
                "kind": e.kind,
            }
            if e.imports:
                info["imports"] = e.imports[:10]
            if e.superclass:
                info["superclass"] = e.superclass
            if e.interfaces:
                info["interfaces"] = e.interfaces
            entity_info.append(info)

        prompt = f"""Assign 1-3 short concern labels to each source code entity below.
Concerns are functional or cross-cutting topics such as: "clustering",
"persistence", "UI rendering", "graph algorithms", "configuration",
"serialization", "metrics", "logging", etc.  Use consistent, lowercase labels.

## Entities
{json.dumps(entity_info, indent=2)}

Respond with ONLY valid JSON mapping each FQN to its concern labels:
{{
    "entity.Foo": ["concern1", "concern2"],
    "entity.Bar": ["concern1"]
}}"""

        system = (
            "You are a software architecture expert.  Assign semantic concern "
            "labels to source code entities based on their name, package, kind, "
            "imports, and inheritance.  Be concise and consistent."
        )

        result = ask_claude_json(prompt, system=system)

        for fqn in batch:
            labels = result.get(fqn, [])
            if isinstance(labels, list) and labels:
                all_tags[fqn] = [str(label).lower().strip() for label in labels[:3]]
            else:
                # Fallback: derive from package name
                e = dep_graph.entities[fqn]
                all_tags[fqn] = [e.package.split(".")[-1].lower()]

    return all_tags


def _tag_entities_heuristic(dep_graph: DependencyGraph) -> dict[str, list[str]]:
    """Heuristic concern tagging (mock mode fallback).

    Derives concern labels from package name and entity name suffixes.
    """
    tags: dict[str, list[str]] = {}
    for fqn, entity in dep_graph.entities.items():
        labels = []
        # Package-derived concern
        if entity.package:
            labels.append(entity.package.split(".")[-1].lower())
        # Suffix-derived concern
        suffixes = [
            ("Service", "service layer"), ("Controller", "request handling"),
            ("Repository", "data access"), ("Factory", "object creation"),
            ("Handler", "event handling"), ("Listener", "event handling"),
            ("Config", "configuration"), ("Util", "utilities"),
            ("Test", "testing"), ("Exception", "error handling"),
            ("Adapter", "adaptation"), ("Manager", "coordination"),
        ]
        for suffix, concern in suffixes:
            if entity.name.endswith(suffix):
                labels.append(concern)
                break
        tags[fqn] = labels if labels else ["general"]
    return tags


# ---------------------------------------------------------------------------
# Step 2 — Concern vectors
# ---------------------------------------------------------------------------

def _build_concern_vectors(
    entities: list[str],
    entity_tags: dict[str, list[str]],
) -> tuple[list[str], dict[str, list[float]]]:
    """Build normalized concern vectors from entity tags.

    Args:
        entities: List of entity FQNs.
        entity_tags: Mapping of FQN to concern labels.

    Returns:
        Tuple of (concern_labels, {fqn: vector}).
        Each vector has one dimension per unique concern label, with values
        normalized to sum to 1.0 (a probability distribution for JS divergence).
    """
    # Collect all unique concerns
    all_concerns: set[str] = set()
    for labels in entity_tags.values():
        all_concerns.update(labels)
    concern_list = sorted(all_concerns)
    concern_idx = {c: i for i, c in enumerate(concern_list)}

    dim = len(concern_list)
    vectors: dict[str, list[float]] = {}

    for fqn in entities:
        labels = entity_tags.get(fqn, ["general"])
        vec = [0.0] * dim
        for label in labels:
            idx = concern_idx.get(label)
            if idx is not None:
                vec[idx] = 1.0

        # Normalize to probability distribution (add small epsilon to avoid zeros)
        total = sum(vec)
        if total > 0:
            vec = [(v / total) for v in vec]
        # Smooth: add epsilon so JS divergence is defined
        epsilon = 1e-10
        vec = [v + epsilon for v in vec]
        total = sum(vec)
        vec = [v / total for v in vec]

        vectors[fqn] = vec

    return concern_list, vectors


# ---------------------------------------------------------------------------
# Step 3 — Jensen-Shannon divergence
# ---------------------------------------------------------------------------

def _kl_divergence(p: list[float], q: list[float]) -> float:
    """Compute KL divergence D_KL(P || Q)."""
    return sum(
        p[i] * math.log(p[i] / q[i])
        for i in range(len(p))
        if p[i] > 0 and q[i] > 0
    )


def _js_divergence(p: list[float], q: list[float]) -> float:
    """Compute Jensen-Shannon divergence (0 = identical, 1 = maximally different)."""
    m = [(p[i] + q[i]) / 2 for i in range(len(p))]
    return (_kl_divergence(p, m) + _kl_divergence(q, m)) / 2


def _js_similarity(p: list[float], q: list[float]) -> float:
    """Compute similarity from JS divergence (1 = identical, 0 = different)."""
    return 1.0 - _js_divergence(p, q)


# ---------------------------------------------------------------------------
# Step 4 — Weighted vector merge (like original ARC)
# ---------------------------------------------------------------------------

def _merge_vectors(
    vec_a: list[float],
    size_a: int,
    vec_b: list[float],
    size_b: int,
) -> list[float]:
    """Merge two concern vectors using size-weighted average.

    This matches the original ARC's ``setLimboFeatureMap()`` merge strategy:
    the merged vector is a weighted average based on cluster sizes.
    """
    total = size_a + size_b
    merged = [
        (vec_a[i] * size_a + vec_b[i] * size_b) / total
        for i in range(len(vec_a))
    ]
    # Re-normalize
    s = sum(merged)
    if s > 0:
        merged = [v / s for v in merged]
    return merged


# ---------------------------------------------------------------------------
# Step 5 — ARC clustering
# ---------------------------------------------------------------------------

def arc(
    dep_graph: DependencyGraph,
    num_clusters: int | None = None,
    hybrid_weight: float = 0.5,
) -> Architecture:
    """Architecture Recovery using Concerns (ARC).

    Agglomerative hierarchical clustering driven by semantic concern
    similarity (Jensen-Shannon divergence on LLM-assigned concern vectors),
    optionally blended with structural similarity (UEM).

    Args:
        dep_graph: Dependency graph to cluster.
        num_clusters: Target number of clusters.  If None, auto-selects
            (sqrt of entity count, clamped to 5-20).
        hybrid_weight: Weight for semantic similarity (0-1).
            1.0 = pure semantic (JS only), 0.0 = pure structural (UEM only),
            0.5 = equal blend (default, like original ARC hybrid measures).

    Returns:
        Architecture with recovered components.
    """

    entities = list(dep_graph.entities.keys())
    if not entities:
        return Architecture(algorithm="arc")

    # Auto-select target clusters
    if num_clusters is None:
        num_clusters = max(5, min(20, int(len(entities) ** 0.5)))

    # 1. Tag entities with concerns via LLM
    log.info("ARC: tagging %d entities with concerns...", len(entities))
    entity_tags = _tag_entities_llm(dep_graph)

    # 2. Build concern vectors
    concern_labels, vectors = _build_concern_vectors(entities, entity_tags)
    log.info("ARC: %d unique concerns identified", len(concern_labels))

    # 3. Initialize clusters (each entity = one cluster)
    clusters: dict[str, list[str]] = {fqn: [fqn] for fqn in entities}
    cluster_vectors: dict[str, list[float]] = {fqn: vectors[fqn] for fqn in entities}

    # Precompute structural adjacency for hybrid mode
    adjacency = dep_graph.to_adjacency()

    # 4. Agglomerative clustering
    while len(clusters) > num_clusters:
        best_sim = -1.0
        best_pair: tuple[str, str] | None = None

        keys = list(clusters.keys())
        for i, key_a in enumerate(keys):
            for j in range(i + 1, len(keys)):
                key_b = keys[j]

                # Semantic similarity (JS on concern vectors)
                sem_sim = _js_similarity(
                    cluster_vectors[key_a], cluster_vectors[key_b]
                )

                # Structural similarity (average-link UEM)
                if hybrid_weight < 1.0:
                    struct_sim = _avg_structural_sim(
                        clusters[key_a], clusters[key_b], adjacency
                    )
                    sim = hybrid_weight * sem_sim + (1 - hybrid_weight) * struct_sim
                else:
                    sim = sem_sim

                if sim > best_sim:
                    best_sim = sim
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
        # Derive responsibility from top concerns of this cluster
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
            f"ARC: concern-based clustering using LLM-assigned semantic vectors "
            f"(hybrid_weight={hybrid_weight}, {len(concern_labels)} concerns)"
        ),
        algorithm="arc",
        metadata={
            "hybrid_weight": hybrid_weight,
            "num_concerns": len(concern_labels),
            "concern_labels": concern_labels,
            "num_clusters": len(components),
        },
    )


def _avg_structural_sim(
    members_a: list[str],
    members_b: list[str],
    adjacency: dict[str, list[str]],
) -> float:
    """Average-link UEM between two clusters."""
    from arcade_agent.algorithms.similarity import unbiased_ellenberg

    total = 0.0
    count = 0
    for a in members_a:
        for b in members_b:
            total += unbiased_ellenberg(a, b, adjacency)
            count += 1
    return total / count if count > 0 else 0.0


def _arc_cluster_name(
    members: list[str],
    dep_graph: DependencyGraph,
    entity_tags: dict[str, list[str]],
) -> str:
    """Generate a name for an ARC cluster from dominant concerns."""
    # Count concern frequency across members
    concern_counts: dict[str, int] = {}
    for fqn in members:
        for label in entity_tags.get(fqn, []):
            concern_counts[label] = concern_counts.get(label, 0) + 1

    if concern_counts:
        top_concern = max(concern_counts, key=lambda c: concern_counts[c])
        return top_concern.replace(" ", "_").title().replace("_", "")

    # Fallback to package-based naming
    packages = {dep_graph.entities[fqn].package.split(".")[-1]
                for fqn in members if fqn in dep_graph.entities}
    if len(packages) == 1:
        return packages.pop().title()
    return "Cluster"


def _cluster_responsibility(
    vector: list[float],
    concern_labels: list[str],
    top_n: int = 3,
) -> str:
    """Derive a responsibility string from top concerns in the vector."""
    indexed = sorted(
        enumerate(vector), key=lambda x: x[1], reverse=True
    )[:top_n]
    top = [concern_labels[i] for i, v in indexed if v > 1e-6]
    if top:
        return ", ".join(top)
    return "General functionality"
