"""Tests for the matching algorithm."""

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.algorithms.matching import (
    compute_a2a_similarity,
    compute_matched_similarity,
    match_components,
)


def test_identical_architectures():
    arch = Architecture(
        components=[
            Component(name="A", responsibility="test", entities=["e1", "e2"]),
            Component(name="B", responsibility="test", entities=["e3", "e4"]),
        ],
    )
    matches = match_components(arch, arch)
    assert len(matches) == 2
    for m in matches:
        assert m["similarity"] == 1.0


def test_completely_different():
    arch_a = Architecture(
        components=[Component(name="A", responsibility="test", entities=["e1", "e2"])],
    )
    arch_b = Architecture(
        components=[Component(name="B", responsibility="test", entities=["e3", "e4"])],
    )
    similarity = compute_a2a_similarity(arch_a, arch_b)
    assert similarity == 0.0


def test_partial_overlap():
    arch_a = Architecture(
        components=[Component(name="A", responsibility="test", entities=["e1", "e2", "e3"])],
    )
    arch_b = Architecture(
        components=[Component(name="B", responsibility="test", entities=["e2", "e3", "e4"])],
    )
    matches = match_components(arch_a, arch_b)
    assert len(matches) == 1
    assert 0.0 < matches[0]["similarity"] < 1.0


def test_a2a_name_is_an_alias_of_matched_similarity(sample_architecture):
    assert compute_a2a_similarity(
        sample_architecture, sample_architecture
    ) == compute_matched_similarity(sample_architecture, sample_architecture)
