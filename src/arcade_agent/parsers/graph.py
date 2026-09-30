"""Dependency graph data models."""

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


class RelationCoverage(TypedDict):
    """Parser capabilities, distinct from the relations present in a graph."""

    collected_relations: list[str]
    call_coverage: Literal["not_collected", "partial", "complete"]


@dataclass
class Entity:
    """A source code entity (class, interface, function, module, etc.)."""

    fqn: str
    name: str
    package: str
    file_path: str
    kind: str  # class, interface, enum, function, module
    language: str
    imports: list[str] = field(default_factory=list)
    superclass: str | None = None
    interfaces: list[str] = field(default_factory=list)
    properties: dict = field(default_factory=dict)


@dataclass
class Edge:
    """A dependency edge between two entities."""

    source: str  # FQN
    target: str  # FQN
    relation: str  # import, extends, implements, calls, uses


@dataclass
class DependencyGraph:
    """Dependency graph extracted from source code."""

    entities: dict[str, Entity] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    packages: dict[str, list[str]] = field(default_factory=dict)
    # Parse-time notes for consumers (e.g. cross-language FQN collision counts
    # from multilang.merge_and_relink). Empty for single-language parses.
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def num_entities(self) -> int:
        return len(self.entities)

    @property
    def num_edges(self) -> int:
        return len(self.edges)

    def to_adjacency(self) -> dict[str, list[str]]:
        """Convert to adjacency list (ignoring edge types)."""
        adj: dict[str, list[str]] = {fqn: [] for fqn in self.entities}
        for edge in self.edges:
            if edge.source in adj:
                adj[edge.source].append(edge.target)
        return adj

    def to_edge_tuples(self) -> list[tuple[str, str, str]]:
        """Convert edges to tuples for compatibility."""
        return [(e.source, e.target, e.relation) for e in self.edges]

    def relation_coverage(self) -> dict[str, RelationCoverage]:
        """Validate coverage metadata loaded from cache or serialization."""
        raw: object = self.metadata.get("relation_coverage")
        if not isinstance(raw, dict):
            return {}
        coverage: dict[str, RelationCoverage] = {}
        for language, value in raw.items():
            if not isinstance(language, str) or not isinstance(value, dict):
                continue
            relations = value.get("collected_relations")
            status = value.get("call_coverage")
            if not isinstance(relations, list) or not all(
                isinstance(relation, str) for relation in relations
            ):
                continue
            if not isinstance(status, str) or status not in (
                "not_collected", "partial", "complete",
            ):
                continue
            coverage[language] = RelationCoverage(
                collected_relations=sorted(set(relations)),
                call_coverage=(
                    "not_collected" if status == "not_collected"
                    else "partial" if status == "partial" else "complete"
                ),
            )
        return coverage

    def merge(self, other: "DependencyGraph") -> "DependencyGraph":
        """Merge another graph into this one, returning a new graph."""
        entities = {**self.entities, **other.entities}
        edges = self.edges + other.edges
        packages: dict[str, list[str]] = {}
        for pkg, fqns in self.packages.items():
            packages.setdefault(pkg, []).extend(fqns)
        for pkg, fqns in other.packages.items():
            packages.setdefault(pkg, []).extend(fqns)
        packages = {pkg: list(dict.fromkeys(fqns)) for pkg, fqns in packages.items()}
        metadata = {**self.metadata, **other.metadata}
        coverage = merge_relation_coverage(self, other)
        if coverage:
            metadata["relation_coverage"] = coverage
        return DependencyGraph(
            entities=entities,
            edges=edges,
            packages=packages,
            metadata=metadata,
        )


def merge_relation_coverage(*graphs: DependencyGraph) -> dict[str, RelationCoverage]:
    """Merge capabilities conservatively when source sets share a language."""
    merged: dict[str, RelationCoverage] = {}
    rank = {"not_collected": 0, "partial": 1, "complete": 2}
    for graph in graphs:
        for language, coverage in graph.relation_coverage().items():
            previous = merged.get(language)
            if previous is None:
                merged[language] = coverage
                continue
            merged[language] = RelationCoverage(
                collected_relations=sorted(
                    set(previous["collected_relations"]) & set(coverage["collected_relations"])
                ),
                call_coverage=min(
                    previous["call_coverage"], coverage["call_coverage"], key=rank.__getitem__,
                ),
            )
    for graph in graphs:
        declared = graph.relation_coverage()
        for language in {entity.language for entity in graph.entities.values()}:
            if language in merged and language not in declared:
                merged[language] = RelationCoverage(
                    collected_relations=[],
                    call_coverage=(
                        "not_collected" if merged[language]["call_coverage"] == "not_collected"
                        else "partial"
                    ),
                )
    return merged
