"""Tool: Recover software architecture from dependency graph."""

from arcade_agent.algorithms.acdc import acdc
from arcade_agent.algorithms.arc import arc
from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.algorithms.clustering import wca
from arcade_agent.algorithms.limbo import limbo
from arcade_agent.parsers.graph import DependencyGraph
from arcade_agent.tools.registry import tool

_JVM_LANGUAGES = frozenset({"java", "kotlin"})


def _build_package_groups(
    dep_graph: DependencyGraph,
    common: list[str],
    depth: int,
) -> dict[str, list[str]]:
    """Assign entities to groups using package segments after the common prefix.

    For an entity whose package *is* the common prefix, the FQN decides:

    - A package-level entity such as a Python ``__init__`` module names a
      sub-package (FQN ``arcade_agent.algorithms``) and joins that group.
    - In JVM languages the next FQN segment is a type and the rest are members
      (``com.lmax.disruptor.Sequencer.next``), so the entity joins the root
      package's group; grouping by FQN would yield one component per class or
      even per method.
    - Elsewhere the next segment is a module (``arcade_agent.budget``), which is
      a meaningful unit, so the module becomes the group. Deeper FQN segments
      are members and are never used.
    """
    sub_packages = {
        tuple(parts[len(common):][:depth])
        for parts in (e.package.split(".") for e in dep_graph.entities.values() if e.package)
        if len(parts) > len(common)
    }
    groups: dict[str, list[str]] = {}
    for fqn, entity in dep_graph.entities.items():
        if not entity.package:
            groups.setdefault("(default)", []).append(fqn)
            continue
        parts = entity.package.split(".")
        remainder = parts[len(common):]
        if remainder:
            key = ".".join(remainder[:depth])
        else:
            fqn_remainder = tuple(fqn.split(".")[len(common):][:depth])
            if fqn_remainder in sub_packages:
                key = ".".join(fqn_remainder)
            elif fqn_remainder and entity.language not in _JVM_LANGUAGES:
                key = fqn_remainder[0]
            else:
                key = parts[-1] if parts[0] else "(default)"
        groups.setdefault(key, []).append(fqn)
    return groups


def _entity_group_membership(groups: dict[str, list[str]]) -> dict[str, str]:
    """Build an entity-to-group index for package groups."""
    return {
        entity_fqn: group_key
        for group_key, entity_fqns in groups.items()
        for entity_fqn in entity_fqns
    }


def _local_utility_hubs(
    dep_graph: DependencyGraph,
    membership: dict[str, str],
) -> set[str]:
    """Identify group-local utility entities such as decorators or registries.

    These entities often attract many same-group import edges but do not define a
    standalone architectural responsibility. They should not stop thin facade
    entities from joining the subsystem they primarily front.
    """
    same_group_incoming: dict[str, int] = {}
    external_incident: dict[str, int] = {}

    for edge in dep_graph.edges:
        source_group = membership.get(edge.source)
        target_group = membership.get(edge.target)
        if not source_group or not target_group:
            continue
        if source_group == target_group:
            same_group_incoming[edge.target] = same_group_incoming.get(edge.target, 0) + 1
        else:
            external_incident[edge.source] = external_incident.get(edge.source, 0) + 1
            external_incident[edge.target] = external_incident.get(edge.target, 0) + 1

    return {
        entity_fqn
        for entity_fqn, incoming_count in same_group_incoming.items()
        if incoming_count >= 2 and external_incident.get(entity_fqn, 0) == 0
    }


def _refine_facade_groups(
    dep_graph: DependencyGraph,
    groups: dict[str, list[str]],
) -> tuple[dict[str, list[str]], int]:
    """Move thin facade entities to the single subsystem they front.

    Package-only grouping can overstate architectural coupling when a package
    mainly exposes adapter functions that delegate straight into one subsystem.
    This refinement moves an entity only when it has no ties to peers in its
    current group and all of its known dependencies point outward to one other
    group. Entities without dependencies or with mixed responsibilities stay put.
    """
    membership = _entity_group_membership(groups)
    utility_hubs = _local_utility_hubs(dep_graph, membership)
    outgoing_by_entity: dict[str, list[str]] = {fqn: [] for fqn in dep_graph.entities}
    incoming_by_entity: dict[str, list[str]] = {fqn: [] for fqn in dep_graph.entities}

    for edge in dep_graph.edges:
        if edge.source in outgoing_by_entity:
            outgoing_by_entity[edge.source].append(edge.target)
        if edge.target in incoming_by_entity:
            incoming_by_entity[edge.target].append(edge.source)

    moves: dict[str, str] = {}
    for entity_fqn, own_group in membership.items():
        if len(groups.get(own_group, [])) <= 1:
            continue

        outgoing_targets = outgoing_by_entity.get(entity_fqn, [])
        if not outgoing_targets:
            continue

        incoming_sources = incoming_by_entity.get(entity_fqn, [])
        own_group_links = 0
        target_groups: set[str] = set()
        disqualify = False

        for neighbor in outgoing_targets:
            neighbor_group = membership.get(neighbor)
            if not neighbor_group:
                continue
            if neighbor_group == own_group:
                if neighbor in utility_hubs:
                    continue
                own_group_links += 1
                continue
            target_groups.add(neighbor_group)

        for neighbor in incoming_sources:
            neighbor_group = membership.get(neighbor)
            if not neighbor_group:
                continue
            if neighbor_group == own_group:
                if neighbor in utility_hubs:
                    continue
                own_group_links += 1
            else:
                disqualify = True
                break

        if disqualify or own_group_links > 0 or len(target_groups) != 1:
            continue

        moves[entity_fqn] = next(iter(target_groups))

    if not moves:
        return groups, 0

    refined = {group_key: list(entity_fqns) for group_key, entity_fqns in groups.items()}
    for entity_fqn, target_group in moves.items():
        source_group = membership[entity_fqn]
        refined[source_group].remove(entity_fqn)
        refined[target_group].append(entity_fqn)

    refined = {
        group_key: sorted(entity_fqns)
        for group_key, entity_fqns in refined.items()
        if entity_fqns
    }
    return refined, len(moves)


def _groups_to_components(groups: dict[str, list[str]]) -> list[Component]:
    """Convert grouped entity assignments into uniquely named components."""
    components = []
    seen_names: set[str] = set()
    for key in sorted(groups.keys()):
        name = _component_name_from_key(key)
        base_name = name
        counter = 1
        while name in seen_names:
            counter += 1
            name = f"{base_name}{counter}"
        seen_names.add(name)

        components.append(
            Component(
                name=name,
                responsibility=f"Entities in {key}",
                entities=sorted(groups[key]),
            )
        )

    return components


def _package_based_recovery(
    dep_graph: DependencyGraph,
    depth: int | None = None,
) -> Architecture:
    """Group entities by meaningful package segments.

    Uses adaptive depth: finds the common prefix, then groups by enough
    remaining segments to produce meaningful components (target: 5-20).

    Entities whose package equals the common prefix (e.g. __init__ modules)
    are assigned using their FQN so they join the correct sub-package group.

    Args:
        dep_graph: Dependency graph to cluster.
        depth: Number of package segments to use after common prefix.
            If None, auto-selects depth to target 5-20 components.
    """
    all_pkgs = [e.package for e in dep_graph.entities.values() if e.package]
    common = _common_prefix_segments(all_pkgs) if all_pkgs else []

    if depth is None:
        depth = _auto_depth(all_pkgs, common)

    groups = _build_package_groups(dep_graph, common, depth)
    groups, reassigned_count = _refine_facade_groups(dep_graph, groups)
    components = _groups_to_components(groups)

    return Architecture(
        components=components,
        rationale=(
            f"Package-based grouping (depth={depth} "
            f"after common prefix '{'.'.join(common)}')"
            + (
                f" with dependency-affinity facade refinement "
                f"({reassigned_count} entities reassigned)."
                if reassigned_count
                else "."
            )
        ),
        algorithm="pkg",
    )


def _auto_depth(all_pkgs: list[str], common: list[str]) -> int:
    """Auto-select grouping depth to target 5-20 components.

    Uses unique package segments after the common prefix to estimate
    how many components each depth level would produce.
    """
    if not all_pkgs:
        return 2

    for depth in range(1, 6):
        keys: set[str] = set()
        for pkg in all_pkgs:
            parts = pkg.split(".")
            remainder = parts[len(common):]
            if remainder:
                keys.add(".".join(remainder[:depth]))
            else:
                keys.add(parts[-1] if parts[0] else "(default)")
        num_groups = len(keys)
        if num_groups >= 5 or depth >= 4:
            return depth

    return 2


def _component_name_from_key(key: str) -> str:
    """Generate a readable component name from a package key."""
    if key == "(default)":
        return "Default"
    # Use last segment, title-cased
    name = key.split(".")[-1] if "." in key else key
    name = name.replace("_", " ").title().replace(" ", "")
    return name if name else "Default"


def _common_prefix_segments(packages: list[str]) -> list[str]:
    """Find the longest common package prefix across all packages."""
    if not packages:
        return []
    split_pkgs = [p.split(".") for p in packages]
    prefix = []
    for segments in zip(*split_pkgs):
        if len(set(segments)) == 1:
            prefix.append(segments[0])
        else:
            break
    return prefix


@tool(
    name="recover",
    description="Recover software architecture from a dependency graph. "
    "Supports multiple algorithms: pkg (package-based), wca (weighted clustering), "
    "acdc (pattern-based), arc (concern-based via LLM), limbo (information-theoretic).",
)
def recover(
    dep_graph: DependencyGraph,
    algorithm: str = "pkg",
    num_clusters: int | None = None,
    similarity_measure: str = "uem",
    pkg_depth: int | None = None,
    hybrid_weight: float = 0.5,
) -> Architecture:
    """Recover software architecture using the specified algorithm.

    Args:
        dep_graph: Dependency graph to analyze.
        algorithm: Recovery algorithm to use:
            - 'pkg': Package-based grouping (fast, deterministic)
            - 'wca': Weighted Clustering Algorithm (agglomerative)
            - 'acdc': ACDC pattern-based clustering
            - 'arc': Architecture Recovery using Concerns (LLM-powered)
            - 'limbo': Information-theoretic clustering (LLM-powered)
        num_clusters: Target number of clusters (for WCA/ARC/LIMBO). Auto if None.
        similarity_measure: Similarity measure for WCA ('js', 'uem', 'scm').
        pkg_depth: Package depth for 'pkg' algorithm. Auto if None.
        hybrid_weight: ARC semantic/structural blend (0-1). 1.0 = pure semantic,
            0.0 = pure structural, 0.5 = equal blend (default).

    Returns:
        Architecture with recovered components.
    """
    if algorithm == "pkg":
        return _package_based_recovery(dep_graph, depth=pkg_depth)
    elif algorithm == "wca":
        return wca(
            dep_graph,
            similarity_measure=similarity_measure,
            num_clusters=num_clusters,
        )
    elif algorithm == "acdc":
        return acdc(dep_graph)
    elif algorithm == "arc":
        return arc(
            dep_graph,
            num_clusters=num_clusters,
            hybrid_weight=hybrid_weight,
        )
    elif algorithm == "limbo":
        return limbo(
            dep_graph,
            num_clusters=num_clusters,
            hybrid_weight=hybrid_weight,
        )
    else:
        raise ValueError(f"Unknown algorithm: {algorithm}. Use: pkg, wca, acdc, arc, limbo")
