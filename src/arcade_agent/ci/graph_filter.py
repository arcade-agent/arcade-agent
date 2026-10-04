"""Graph profiles shared by architecture-analysis entry points."""

from arcade_agent.parsers.graph import DependencyGraph

SELF_DOGFOOD_PROFILE = "arcade-self-dogfood-v1"


def _filter_non_architectural_entities(
    graph: DependencyGraph,
) -> DependencyGraph:
    """Remove low-signal helper entities from repository self-analysis.

    Private Python helpers and methods inflate component size without
    representing stable architectural responsibilities. Decorator-registration
    imports such as ``@tool`` and ``@register_parser`` likewise create
    implementation-level coupling rather than runtime architectural coupling.
    """
    kept_entities = {
        fqn: entity
        for fqn, entity in graph.entities.items()
        if entity.kind != "method"
        and not (
            entity.language == "python"
            and entity.kind == "function"
            and entity.name.startswith("_")
        )
    }

    kept_edges = []
    registration_helpers = {"tool", "register_parser"}
    for edge in graph.edges:
        if edge.source not in kept_entities or edge.target not in kept_entities:
            continue

        source_entity = kept_entities[edge.source]
        target_entity = kept_entities[edge.target]
        if (
            edge.relation == "import"
            and source_entity.package
            and source_entity.package == target_entity.package
            and target_entity.kind == "function"
            and target_entity.name in registration_helpers
        ):
            continue

        kept_edges.append(edge)

    kept_packages: dict[str, list[str]] = {}
    for package, fqns in graph.packages.items():
        filtered_fqns = [fqn for fqn in fqns if fqn in kept_entities]
        if filtered_fqns:
            kept_packages[package] = filtered_fqns

    return DependencyGraph(
        entities=kept_entities,
        edges=kept_edges,
        packages=kept_packages,
        metadata=dict(graph.metadata),
    )
