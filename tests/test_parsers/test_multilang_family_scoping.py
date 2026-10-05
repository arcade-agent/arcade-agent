"""Language-family scoping of merge+relink (review findings 1 and 2).

Before family scoping, three resolution paths in ``multilang`` were
language-blind and fabricated cross-language edges on a Python+Java fixture:

- a Python ``class PaymentHandler(Base)`` with no local ``Base`` linked to the
  globally unique Java ``com.example.core.Base`` via the leaf fallback;
- a Python ``import com.auth.service`` (its own module) linked to the Java class
  with the coinciding FQN;
- the same coincidence at method level silently dropped one entity on merge.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity
from arcade_agent.parsers.multilang import (
    language_family,
    merge_and_relink,
    relink_edges,
)
from arcade_agent.serialization import graph_to_dict
from arcade_agent.tools.parse import parse


def _entity(fqn: str, language: str, **kwargs) -> Entity:
    name = fqn.split(".")[-1]
    package = fqn.rsplit(".", 1)[0] if "." in fqn else ""
    return Entity(
        fqn=fqn,
        name=name,
        package=package,
        file_path=f"{fqn}.src",
        kind=kwargs.pop("kind", "class"),
        language=language,
        **kwargs,
    )


def _cross_family_edges(graph: DependencyGraph) -> list[tuple[str, str, str]]:
    bad = []
    for edge in graph.edges:
        source = graph.entities.get(edge.source)
        target = graph.entities.get(edge.target)
        if source is None or target is None:
            continue
        if language_family(source.language) != language_family(target.language):
            bad.append((edge.source, edge.target, edge.relation))
    return bad


def test_language_family_groups_jvm_only():
    assert language_family("java") == language_family("kotlin") == "jvm"
    assert language_family("python") == "python"
    assert language_family("go") != language_family("typescript")
    assert language_family(None) == "unknown"


def test_unique_leaf_fallback_does_not_cross_family():
    """Python subclass of an unresolved Base must not link to a Java Base."""
    graph = DependencyGraph(
        entities={
            "app.service.PaymentHandler": _entity(
                "app.service.PaymentHandler", "python", superclass="Base"
            ),
            "com.example.core.Base": _entity("com.example.core.Base", "java"),
        }
    )

    linked = relink_edges(graph)
    assert linked.to_edge_tuples() == []
    assert _cross_family_edges(linked) == []


def test_unique_leaf_fallback_still_works_inside_a_family():
    """The same fallback remains available for Kotlin -> Java (the MVP pair)."""
    graph = DependencyGraph(
        entities={
            "app.KotlinChild": _entity("app.KotlinChild", "kotlin", superclass="Base"),
            "com.example.core.Base": _entity("com.example.core.Base", "java"),
        }
    )

    assert relink_edges(graph).to_edge_tuples() == [
        ("app.KotlinChild", "com.example.core.Base", "extends")
    ]


def test_leaf_import_fallback_does_not_cross_family():
    graph = DependencyGraph(
        entities={
            "app.service.Handler": _entity(
                "app.service.Handler", "python", imports=["Base"]
            ),
            "com.example.core.Base": _entity("com.example.core.Base", "java"),
        }
    )

    assert relink_edges(graph).to_edge_tuples() == []


def test_exact_fqn_import_match_does_not_cross_family():
    """Python `import com.auth.service` must not bind to a Java class."""
    graph = DependencyGraph(
        entities={
            "app.service.PaymentHandler": _entity(
                "app.service.PaymentHandler", "python", imports=["com.auth.service"]
            ),
            "com.auth.service": _entity("com.auth.service", "java"),
        }
    )

    assert relink_edges(graph).to_edge_tuples() == []


def test_exact_fqn_import_match_still_links_inside_a_family():
    graph = DependencyGraph(
        entities={
            "app.KotlinCaller": _entity(
                "app.KotlinCaller", "kotlin", imports=["com.auth.Service"]
            ),
            "com.auth.Service": _entity("com.auth.Service", "java"),
        }
    )

    assert relink_edges(graph).to_edge_tuples() == [
        ("app.KotlinCaller", "com.auth.Service", "import")
    ]


def test_same_package_fallback_does_not_cross_family():
    graph = DependencyGraph(
        entities={
            "com.auth.PyHandler": _entity(
                "com.auth.PyHandler", "python", superclass="Service"
            ),
            "com.auth.Service": _entity("com.auth.Service", "java"),
        }
    )

    assert relink_edges(graph).to_edge_tuples() == []


def test_cross_family_fqn_collision_keeps_both_entities():
    java = DependencyGraph(
        entities={"com.auth.service.login": _entity("com.auth.service.login", "java")},
        packages={"com.auth.service": ["com.auth.service.login"]},
    )
    python = DependencyGraph(
        entities={
            "com.auth.service.login": _entity("com.auth.service.login", "python")
        },
        edges=[
            Edge(
                source="com.auth.service.login",
                target="com.auth.service.login",
                relation="calls",
            )
        ],
        packages={"com.auth.service": ["com.auth.service.login"]},
    )

    merged = merge_and_relink(java, python)

    assert merged.entities["com.auth.service.login"].language == "java"
    renamed = merged.entities["com.auth.service.login#python"]
    assert renamed.language == "python"
    assert renamed.fqn == "com.auth.service.login#python"
    assert merged.metadata["fqn_collisions"] == 1
    assert merged.metadata["fqn_collisions_cross_family"] == 1
    assert merged.metadata["fqn_collisions_same_family"] == 0
    assert merged.metadata["fqn_collision_details"] == [
        {
            "fqn": "com.auth.service.login",
            "kept": "java",
            "other": "python",
            "resolution": "renamed",
            "renamed_to": "com.auth.service.login#python",
        }
    ]
    # The Python graph's own edges follow the renamed entity.
    assert (
        "com.auth.service.login#python",
        "com.auth.service.login#python",
        "calls",
    ) in merged.to_edge_tuples()
    assert sorted(merged.packages["com.auth.service"]) == [
        "com.auth.service.login",
        "com.auth.service.login#python",
    ]


def test_same_family_fqn_collision_keeps_first_and_is_counted():
    java = DependencyGraph(
        entities={"a.Shared": _entity("a.Shared", "java")},
        packages={"a": ["a.Shared"]},
    )
    kotlin = DependencyGraph(
        entities={"a.Shared": _entity("a.Shared", "kotlin")},
        packages={"a": ["a.Shared"]},
    )

    merged = merge_and_relink(java, kotlin)

    assert merged.entities["a.Shared"].language == "java"
    assert merged.metadata["fqn_collisions"] == 1
    assert merged.metadata["fqn_collisions_same_family"] == 1
    assert merged.metadata["fqn_collisions_cross_family"] == 0


def test_merge_reports_zero_collisions_when_there_are_none():
    merged = merge_and_relink(
        DependencyGraph(entities={"a.A": _entity("a.A", "java")}),
        DependencyGraph(entities={"b.B": _entity("b.B", "kotlin")}),
    )
    assert merged.metadata["fqn_collisions"] == 0
    assert "fqn_collision_details" not in merged.metadata


def test_polyglot_fixture_has_no_fabricated_cross_language_edges(fixtures_dir: Path):
    """End-to-end reproduction of the review's Python+Java fabrication."""
    root = fixtures_dir / "python_java_mixed"
    graph = parse(str(root), languages=["java", "python"], use_cache=False)

    assert _cross_family_edges(graph) == []
    tuples = set(graph.to_edge_tuples())
    assert (
        "app.service.PaymentHandler",
        "com.example.core.Base",
        "extends",
    ) not in tuples
    assert ("app.service.PaymentHandler", "com.auth.service", "import") not in tuples
    # No entity is silently dropped by the com.auth.service.login coincidence.
    assert "com.auth.service.login" in graph.entities
    assert "com.auth.service.login#python" in graph.entities
    assert graph.metadata["fqn_collisions_cross_family"] == 1


def test_language_order_does_not_change_the_result(fixtures_dir: Path):
    root = fixtures_dir / "python_java_mixed"
    a = parse(str(root), languages=["java", "python"], use_cache=False)
    b = parse(str(root), languages=["python", "java"], use_cache=False)
    assert graph_to_dict(a) == graph_to_dict(b)


def test_single_language_parse_carries_no_metadata(fixtures_dir: Path):
    """Single-language output must stay byte-identical: no metadata key."""
    root = fixtures_dir / "python_java_mixed"
    graph = parse(str(root), language="python", use_cache=False)
    assert graph.metadata == {}
    assert "metadata" not in graph_to_dict(graph)


def test_single_language_parse_matches_direct_parser_output(fixtures_dir: Path):
    from arcade_agent.parsers.java import JavaParser

    root = fixtures_dir / "python_java_mixed"
    files = sorted(root.rglob("*.java"))
    direct = JavaParser().parse(files, root)
    viaparse = parse(str(root), language="java", use_cache=False)
    assert graph_to_dict(viaparse) == graph_to_dict(direct)


@pytest.mark.parametrize("languages", [["java", "java"], ["java"]])
def test_duplicate_language_list_is_deduplicated(fixtures_dir: Path, languages):
    root = fixtures_dir / "python_java_mixed"
    graph = parse(str(root), languages=languages, use_cache=False)
    assert graph.metadata == {}
    assert all(e.language == "java" for e in graph.entities.values())
