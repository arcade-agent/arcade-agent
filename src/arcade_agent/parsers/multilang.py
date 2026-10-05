"""Cross-language graph merge and edge relinking (roadmap #18).

Scope — supported polyglot pairs
--------------------------------
Merging and relinking only ever happens *within a language family*. A family is
a set of languages that genuinely share one fully-qualified-name space, so that
a name resolved in one of them may legitimately denote an entity written in
another:

- ``jvm``: ``java`` + ``kotlin`` — the validated MVP pair. Both compile to the
  same JVM namespace, use dotted package FQNs, and routinely extend/implement
  each other's types.
- every other language is its own family (``python``, ``go``, ``typescript``,
  ``c``, ...). Their graphs are still merged into one ``DependencyGraph``, but
  no edge is ever fabricated between two different families, because a
  ``com.auth.service`` Python module and a ``com.auth.service`` Java class are
  unrelated things that merely spell alike.

The resolution heuristics here are dotted-name tuned (packages, leaf names,
``import a.b.C``) and only the JVM pair has test coverage. ``language="multi"``
on a repository containing, say, Go and TypeScript will therefore parse each
language correctly but will not attempt cross-language linking between them.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity

logger = logging.getLogger(__name__)

# Languages that share a single FQN space. Anything not listed forms its own
# single-member family (see language_family). Only add a language here when its
# names really do resolve against the other members at compile/runtime.
_LANGUAGE_FAMILIES: dict[str, str] = {
    "java": "jvm",
    "kotlin": "jvm",
}

#: Language pairs whose cross-language relinking is exercised by tests.
SUPPORTED_POLYGLOT_PAIRS: tuple[tuple[str, str], ...] = (("java", "kotlin"),)


def language_family(language: str | None) -> str:
    """Return the FQN-space family of *language* (its own name when unknown).

    Args:
        language: Entity language such as "java", "kotlin" or "python".

    Returns:
        Family name; "jvm" for Java/Kotlin, the language itself otherwise.
    """
    if not language:
        return "unknown"
    lang = language.lower()
    return _LANGUAGE_FAMILIES.get(lang, lang)


def resolve_name(
    simple_name: str,
    source_entity: Entity,
    fqn_index: dict[str, dict[str, str]],
    entities: dict[str, Entity],
    aliases: dict[str, str] | None = None,
) -> str | None:
    """Resolve a simple or qualified type name to an entity FQN.

    Resolution never crosses a language family boundary: a Python class named
    ``Base`` can only bind to another Python entity, never to a Java one that
    happens to share the leaf name.

    Args:
        simple_name: Simple or dotted type name as written in the source.
        source_entity: Entity that referenced *simple_name*.
        fqn_index: Family-scoped unique-leaf index from ``_build_fqn_index``.
        entities: All entities of the merged graph, keyed by FQN.
        aliases: Optional import aliases declared by *source_entity*.

    Returns:
        The resolved FQN, or None when no family-compatible entity matches.
    """
    family = language_family(source_entity.language)

    def compatible(fqn: str) -> bool:
        target = entities.get(fqn)
        return target is not None and language_family(target.language) == family

    if compatible(simple_name):
        return simple_name

    if aliases and simple_name in aliases:
        aliased = aliases[simple_name]
        if compatible(aliased):
            return aliased

    for imp in source_entity.imports:
        if imp.endswith(f".{simple_name}") and compatible(imp):
            return imp

    if source_entity.package:
        same_pkg_fqn = f"{source_entity.package}.{simple_name}"
        if compatible(same_pkg_fqn):
            return same_pkg_fqn

    # A qualified name is already explicit. Falling back to its leaf could link
    # an unavailable external type (e.g. external.Base) to an unrelated local
    # Base entity, creating a false cross-language dependency.
    if "." in simple_name:
        return None

    # Unqualified fallback: only same-family entities are candidates, and the
    # leaf name must be unique inside that family.
    return fqn_index.get(family, {}).get(simple_name)


def _aliases_for(entity: Entity) -> dict[str, str]:
    raw = entity.properties.get("import_aliases")
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return {}


def _build_fqn_index(entities: dict[str, Entity]) -> dict[str, dict[str, str]]:
    """Index leaf name -> FQN, scoped per language family.

    Uniqueness is evaluated inside a family: a Java ``Base`` and a Python
    ``Base`` do not make each other ambiguous, and neither can resolve to the
    other.

    Args:
        entities: All entities of the merged graph, keyed by FQN.

    Returns:
        Mapping family -> {leaf name: FQN} for leaves unique in that family.
    """
    candidates: dict[str, dict[str, list[str]]] = {}
    for entity in entities.values():
        family = language_family(entity.language)
        candidates.setdefault(family, {}).setdefault(entity.name, []).append(entity.fqn)
    # Unqualified fallback is only safe when the leaf name is unique in-family.
    return {
        family: {name: fqns[0] for name, fqns in names.items() if len(fqns) == 1}
        for family, names in candidates.items()
    }


def relink_edges(graph: DependencyGraph) -> DependencyGraph:
    """Add import/extends/implements edges resolvable against the full entity set.

    Language parsers only resolve against their own entities. After merging
    Java+Kotlin (the supported polyglot pair) re-run resolution so same-package
    and imported cross-language types become real edges. Every resolution path
    is gated on language-family compatibility, so no edge is ever created
    between entities of unrelated languages.

    Args:
        graph: Merged graph whose entities may come from several languages.

    Returns:
        New DependencyGraph with the additional resolvable edges.
    """
    entities = graph.entities
    fqn_index = _build_fqn_index(entities)
    seen = {(e.source, e.target, e.relation) for e in graph.edges}
    new_edges: list[Edge] = list(graph.edges)

    def add(source: str, target: str, relation: str) -> None:
        key = (source, target, relation)
        if key in seen or source == target:
            return
        seen.add(key)
        new_edges.append(Edge(source=source, target=target, relation=relation))

    for entity in entities.values():
        family = language_family(entity.language)
        aliases = _aliases_for(entity)
        for imp in entity.imports:
            imported = entities.get(imp)
            if imported is not None:
                # An FQN that merely coincides across families (a Python module
                # com.auth.service vs a Java class of the same name) is not an
                # import edge.
                if language_family(imported.language) == family:
                    add(entity.fqn, imp, "import")
            elif "." not in imp:
                resolved = fqn_index.get(family, {}).get(imp)
                if resolved and resolved != entity.fqn:
                    add(entity.fqn, resolved, "import")

        if entity.superclass:
            target = resolve_name(
                entity.superclass, entity, fqn_index, entities, aliases
            )
            if target:
                add(entity.fqn, target, "extends")

        for iface in entity.interfaces:
            target = resolve_name(iface, entity, fqn_index, entities, aliases)
            if target:
                add(entity.fqn, target, "implements")

    packages: dict[str, list[str]] = {
        pkg: list(dict.fromkeys(fqns)) for pkg, fqns in graph.packages.items()
    }
    return DependencyGraph(
        entities=entities,
        edges=new_edges,
        packages=packages,
        metadata=dict(graph.metadata),
    )


def _disambiguate_fqn(fqn: str, language: str, taken: dict[str, Entity]) -> str:
    """Return an unused graph key for a cross-family collision on *fqn*."""
    candidate = f"{fqn}#{language}"
    suffix = 2
    while candidate in taken:
        candidate = f"{fqn}#{language}{suffix}"
        suffix += 1
    return candidate


def merge_and_relink(*graphs: DependencyGraph) -> DependencyGraph:
    """Union graphs then relink edges across the combined entity set.

    Entities are merged inside language families only. When two *different*
    families produce the same FQN (e.g. Python ``com/auth/service.py::login``
    and Java ``com.auth.service.login``) neither entity is dropped: the later
    one is re-keyed as ``<fqn>#<language>`` and its own edges and package
    listings are remapped, so a cross-family name coincidence no longer causes
    silent data loss. Within a family the first entity still wins.

    Collision counts land in ``graph.metadata`` (``fqn_collisions``,
    ``fqn_collisions_same_family``, ``fqn_collisions_cross_family`` and
    ``fqn_collision_details``) so agents see them without reading log output.

    Args:
        *graphs: Per-language graphs to union, in priority order.

    Returns:
        Merged DependencyGraph with relinked edges and collision metadata.
    """
    if not graphs:
        return DependencyGraph()

    entities: dict[str, Entity] = {}
    edges: list[Edge] = []
    packages: dict[str, list[str]] = {}
    collision_details: list[dict[str, str]] = []
    same_family_collisions = 0
    cross_family_collisions = 0

    for graph in graphs:
        renamed: dict[str, str] = {}
        for fqn, entity in graph.entities.items():
            existing = entities.get(fqn)
            if existing is None:
                entities[fqn] = entity
                continue
            if language_family(existing.language) == language_family(entity.language):
                if existing.language != entity.language:
                    same_family_collisions += 1
                    collision_details.append(
                        {
                            "fqn": fqn,
                            "kept": existing.language,
                            "other": entity.language,
                            "resolution": "kept_first",
                        }
                    )
                    logger.warning(
                        "FQN collision within language family at %s (%s vs %s); "
                        "keeping first",
                        fqn,
                        existing.language,
                        entity.language,
                    )
                continue
            new_fqn = _disambiguate_fqn(fqn, entity.language, entities)
            renamed[fqn] = new_fqn
            entities[new_fqn] = replace(entity, fqn=new_fqn)
            cross_family_collisions += 1
            collision_details.append(
                {
                    "fqn": fqn,
                    "kept": existing.language,
                    "other": entity.language,
                    "resolution": "renamed",
                    "renamed_to": new_fqn,
                }
            )
            logger.warning(
                "FQN collision across language families at %s (%s vs %s); "
                "keeping both, re-keyed the %s entity as %s",
                fqn,
                existing.language,
                entity.language,
                entity.language,
                new_fqn,
            )
        if renamed:
            edges.extend(
                Edge(
                    source=renamed.get(edge.source, edge.source),
                    target=renamed.get(edge.target, edge.target),
                    relation=edge.relation,
                )
                for edge in graph.edges
            )
        else:
            edges.extend(graph.edges)
        for pkg, fqns in graph.packages.items():
            packages.setdefault(pkg, []).extend(renamed.get(f, f) for f in fqns)

    packages = {pkg: list(dict.fromkeys(fqns)) for pkg, fqns in packages.items()}
    metadata: dict[str, Any] = {
        "fqn_collisions": same_family_collisions + cross_family_collisions,
        "fqn_collisions_same_family": same_family_collisions,
        "fqn_collisions_cross_family": cross_family_collisions,
    }
    if collision_details:
        metadata["fqn_collision_details"] = collision_details
    return relink_edges(
        DependencyGraph(
            entities=entities,
            edges=edges,
            packages=packages,
            metadata=metadata,
        )
    )
