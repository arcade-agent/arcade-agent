"""ACDC must recover a disjoint partition, including dominator cycles."""

from collections import Counter

import pytest

from arcade_agent.algorithms.acdc import acdc
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity


@pytest.mark.parametrize(
    "edges",
    [
        [("A", "B"), ("B", "A")],
        [("A", "B"), ("B", "C"), ("C", "A")],
        [("A", "B"), ("B", "C")],
    ],
)
def test_dominator_cycles_and_chains_are_disjoint(edges: list[tuple[str, str]]) -> None:
    nodes = sorted({node for edge in edges for node in edge})
    graph = DependencyGraph(
        entities={
            node: Entity(node, node, "pkg", f"{node}.py", "module", "python")
            for node in nodes
        },
        edges=[Edge(source, target, "calls") for source, target in edges],
    )
    architecture = acdc(graph)
    counts = Counter(node for component in architecture.components for node in component.entities)
    assert counts == Counter({node: 1 for node in nodes})


def test_partition_is_independent_of_entity_insertion_order() -> None:
    edges = [Edge("A", "B", "calls"), Edge("B", "C", "calls")]
    partitions = []
    for nodes in (["A", "B", "C", "D"], ["D", "C", "B", "A"]):
        graph = DependencyGraph(
            entities={
                node: Entity(node, node, "pkg", f"{node}.py", "module", "python")
                for node in nodes
            },
            edges=edges,
        )
        partitions.append([component.entities for component in acdc(graph).components])
    assert partitions[0] == partitions[1]
