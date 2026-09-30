"""ACDC (Algorithm for Comprehension-Driven Clustering).

Pattern-based clustering that uses structural patterns like:
- Subgraph dominance (body-header pattern)
- Orphan adoption
to recover architectural components.
"""

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.parsers.graph import DependencyGraph


def acdc(dep_graph: DependencyGraph) -> Architecture:
    """ACDC pattern-based architecture recovery.

    Uses structural patterns in the dependency graph to identify components:
    1. Identify dominant entities (those depended on by many others)
    2. Group dependents with their dominators (subgraph dominance)
    3. Adopt orphans into nearest component

    Args:
        dep_graph: The dependency graph to cluster.

    Returns:
        Architecture with recovered components.
    """
    if not dep_graph.entities:
        return Architecture(algorithm="acdc")

    adjacency = dep_graph.to_adjacency()

    # Build reverse adjacency (who depends on whom)
    reverse: dict[str, list[str]] = {}
    for src, targets in adjacency.items():
        for tgt in targets:
            reverse.setdefault(tgt, []).append(src)

    # Step 1: Find dominators — entities with many dependents
    # An entity dominates another if it's the sole dependency target
    dominators: dict[str, str] = {}  # entity -> its dominator
    for fqn in dep_graph.entities:
        deps = [t for t in adjacency.get(fqn, []) if t in dep_graph.entities]
        if len(deps) == 1:
            # This entity has a single dependency — that dependency dominates it
            dominators[fqn] = deps[0]

    # Step 2: Build clusters around dominator targets
    clusters: dict[str, list[str]] = {}
    assigned: set[str] = set()

    # Find entities that are dominators of others
    dominator_targets: set[str] = set(dominators.values())

    # A dominator can already be a member of an earlier cluster (chains/cycles).
    # Stable traversal makes the ownership decision independent of hash order.
    for target in sorted(dominator_targets):
        if target not in dep_graph.entities or target in assigned:
            continue
        cluster_members = [target]
        assigned.add(target)

        for fqn, dom in dominators.items():
            if dom == target and fqn not in assigned:
                cluster_members.append(fqn)
                assigned.add(fqn)

        clusters[target] = cluster_members

    # Step 3: Adopt orphans — assign unassigned entities to nearest cluster
    orphans = sorted(fqn for fqn in dep_graph.entities if fqn not in assigned)

    for orphan in orphans:
        best_cluster = None
        best_score = -1

        orphan_deps = set(adjacency.get(orphan, []))
        preds = set(reverse.get(orphan, []))
        connections = orphan_deps | preds

        for cluster_key, members in clusters.items():
            member_set = set(members)
            overlap = len(connections & member_set)
            if overlap > best_score:
                best_score = overlap
                best_cluster = cluster_key

        if best_cluster is not None and best_score > 0:
            clusters[best_cluster].append(orphan)
            assigned.add(orphan)

    # Step 4: Remaining orphans get their own clusters
    remaining = sorted(fqn for fqn in dep_graph.entities if fqn not in assigned)
    if remaining:
        # Group remaining by package
        pkg_groups: dict[str, list[str]] = {}
        for fqn in remaining:
            entity = dep_graph.entities[fqn]
            pkg_groups.setdefault(entity.package, []).append(fqn)

        for pkg, members in pkg_groups.items():
            key = members[0]
            clusters[key] = members

    # Build architecture
    components: list[Component] = []
    for key, members in sorted(clusters.items(), key=lambda x: -len(x[1])):
        anchor = dep_graph.entities.get(key)
        if anchor:
            name = anchor.package.split(".")[-1].title() if anchor.package else anchor.name
        else:
            name = key.split(".")[-1]

        # Ensure unique names
        existing_names = {c.name for c in components}
        base_name = name
        counter = 1
        while name in existing_names:
            counter += 1
            name = f"{base_name}{counter}"

        components.append(
            Component(
                name=name,
                responsibility=f"ACDC cluster around {key}",
                entities=sorted(members),
            )
        )

    return Architecture(
        components=components,
        rationale="ACDC pattern-based clustering using subgraph dominance",
        algorithm="acdc",
    )
