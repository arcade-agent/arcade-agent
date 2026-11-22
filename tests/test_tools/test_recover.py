"""Tests for the recover tool."""

import pytest

from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity
from arcade_agent.tools.recover import recover


def test_package_based_recovery(sample_graph):
    arch = recover(sample_graph, algorithm="pkg")

    assert len(arch.components) >= 2
    assert arch.algorithm == "pkg"

    # All entities should be assigned
    all_entities = set()
    for comp in arch.components:
        all_entities.update(comp.entities)
    assert all_entities == set(sample_graph.entities.keys())


def test_wca_recovery(sample_graph):
    arch = recover(sample_graph, algorithm="wca", num_clusters=2)

    assert len(arch.components) >= 1
    assert arch.algorithm == "wca"


def test_wca_recovery_uses_unique_component_names(sample_graph):
    arch = recover(sample_graph, algorithm="wca", num_clusters=3)

    names = [component.name for component in arch.components]
    assert len(names) == len(set(names))


def test_acdc_recovery(sample_graph):
    arch = recover(sample_graph, algorithm="acdc")

    assert len(arch.components) >= 1
    assert arch.algorithm == "acdc"


def test_package_based_recovery_reassigns_thin_facades():
    graph = DependencyGraph(
        entities={
            "com.example.api.facade": Entity(
                fqn="com.example.api.facade",
                name="facade",
                package="com.example.api",
                file_path="api.py",
                kind="function",
                language="python",
            ),
            "com.example.api.registry": Entity(
                fqn="com.example.api.registry",
                name="registry",
                package="com.example.api",
                file_path="api.py",
                kind="function",
                language="python",
            ),
            "com.example.api.tool": Entity(
                fqn="com.example.api.tool",
                name="tool",
                package="com.example.api",
                file_path="api.py",
                kind="function",
                language="python",
            ),
            "com.example.impl.worker": Entity(
                fqn="com.example.impl.worker",
                name="worker",
                package="com.example.impl",
                file_path="impl.py",
                kind="function",
                language="python",
            ),
        },
        edges=[
            Edge(
                source="com.example.api.facade",
                target="com.example.api.tool",
                relation="import",
            ),
            Edge(
                source="com.example.api.registry",
                target="com.example.api.tool",
                relation="import",
            ),
            Edge(
                source="com.example.api.facade",
                target="com.example.impl.worker",
                relation="import",
            )
        ],
        packages={
            "com.example.api": [
                "com.example.api.facade",
                "com.example.api.registry",
                "com.example.api.tool",
            ],
            "com.example.impl": ["com.example.impl.worker"],
        },
    )

    arch = recover(graph, algorithm="pkg")
    membership = {
        entity_fqn: component.name
        for component in arch.components
        for entity_fqn in component.entities
    }

    assert membership["com.example.api.facade"] == membership["com.example.impl.worker"]
    assert membership["com.example.api.registry"] != membership["com.example.impl.worker"]
    assert "facade refinement" in arch.rationale


def test_unknown_algorithm(sample_graph):
    with pytest.raises(ValueError, match="Unknown algorithm"):
        recover(sample_graph, algorithm="unknown")


def _java_entity(fqn: str, package: str, kind: str = "class") -> Entity:
    return Entity(
        fqn=fqn,
        name=fqn.rsplit(".", 1)[-1],
        package=package,
        file_path=fqn.replace(".", "/") + ".java",
        kind=kind,
        language="java",
    )


def test_package_based_recovery_keeps_root_package_classes_together():
    # Disruptor-shaped: most classes (and their methods) live in the root
    # package that is also the common prefix. They used to become one
    # component per class or even per method.
    root = "com.lmax.disruptor"
    entities = [
        _java_entity(f"{root}.Sequencer", root, "interface"),
        _java_entity(f"{root}.Sequencer.next", root, "method"),
        _java_entity(f"{root}.RingBuffer", root),
        _java_entity(f"{root}.RingBuffer.publish", root, "method"),
        _java_entity(f"{root}.dsl.Disruptor", f"{root}.dsl"),
        _java_entity(f"{root}.dsl.Disruptor.start", f"{root}.dsl", "method"),
        _java_entity(f"{root}.util.Util", f"{root}.util"),
    ]
    graph = DependencyGraph(entities={e.fqn: e for e in entities}, edges=[])

    arch = recover(graph, algorithm="pkg")

    by_name = {c.name: set(c.entities) for c in arch.components}
    assert set(by_name) == {"Disruptor", "Dsl", "Util"}
    assert by_name["Disruptor"] == {
        f"{root}.Sequencer",
        f"{root}.Sequencer.next",
        f"{root}.RingBuffer",
        f"{root}.RingBuffer.publish",
    }


def test_package_entity_joins_its_sub_package_group():
    # A Python package's __init__ module: package == common prefix, FQN names
    # the sub-package.
    entities = {
        "app.api": Entity(fqn="app.api", name="api", package="app",
                          file_path="app/api/__init__.py", kind="module",
                          language="python"),
        "app.api.routes.handler": Entity(fqn="app.api.routes.handler", name="handler",
                                         package="app.api.routes",
                                         file_path="app/api/routes.py", kind="function",
                                         language="python"),
        "app.store.repo.Repo": Entity(fqn="app.store.repo.Repo", name="Repo",
                                      package="app.store.repo",
                                      file_path="app/store/repo.py", kind="class",
                                      language="python"),
    }
    arch = recover(DependencyGraph(entities=entities, edges=[]), algorithm="pkg", pkg_depth=1)

    members = {c.name: set(c.entities) for c in arch.components}
    assert "app.api" in members["Api"]


def test_package_based_recovery_does_not_reassign_across_rust_crates():
    """Rust crate boundaries are compiler-enforced DAGs; never move entities across crates."""
    graph = DependencyGraph(
        entities={
            "crate_a.adapter.wrap": Entity(
                fqn="crate_a.adapter.wrap",
                name="wrap",
                package="crate_a.adapter",
                file_path="crates/crate-a/src/adapter.rs",
                kind="function",
                language="rust",
            ),
            "crate_a.lib.run": Entity(
                fqn="crate_a.lib.run",
                name="run",
                package="crate_a.lib",
                file_path="crates/crate-a/src/lib.rs",
                kind="function",
                language="rust",
            ),
            "crate_b.core.Core": Entity(
                fqn="crate_b.core.Core",
                name="Core",
                package="crate_b.core",
                file_path="crates/crate-b/src/core.rs",
                kind="struct",
                language="rust",
            ),
        },
        edges=[
            Edge(source="crate_a.adapter.wrap", target="crate_b.core.Core", relation="import"),
        ],
    )

    arch = recover(graph, algorithm="pkg", pkg_depth=1)
    membership = arch.membership()

    assert membership["crate_a.adapter.wrap"] == membership["crate_a.lib.run"]
    assert membership["crate_a.adapter.wrap"] != membership["crate_b.core.Core"]
    assert "facade refinement" not in arch.rationale


def test_package_based_recovery_does_not_reassign_across_go_packages():
    """Go forbids circular package imports; never merge internal.webhook.v1 into api.v1."""
    graph = DependencyGraph(
        entities={
            "internal.webhook.v1.ValidateCluster": Entity(
                fqn="internal.webhook.v1.ValidateCluster",
                name="ValidateCluster",
                package="internal.webhook.v1",
                file_path="internal/webhook/v1/cluster_webhook.go",
                kind="function",
                language="go",
            ),
            "internal.webhook.v1.Setup": Entity(
                fqn="internal.webhook.v1.Setup",
                name="Setup",
                package="internal.webhook.v1",
                file_path="internal/webhook/v1/setup.go",
                kind="function",
                language="go",
            ),
            "api.v1.Cluster": Entity(
                fqn="api.v1.Cluster",
                name="Cluster",
                package="api.v1",
                file_path="api/v1/cluster_types.go",
                kind="struct",
                language="go",
            ),
        },
        edges=[
            Edge(
                source="internal.webhook.v1.ValidateCluster",
                target="api.v1.Cluster",
                relation="import",
            ),
        ],
    )

    arch = recover(graph, algorithm="pkg", pkg_depth=2)
    membership = arch.membership()

    assert (
        membership["internal.webhook.v1.ValidateCluster"]
        == membership["internal.webhook.v1.Setup"]
    )
    assert membership["internal.webhook.v1.ValidateCluster"] != membership["api.v1.Cluster"]
    assert "facade refinement" not in arch.rationale


def test_package_based_recovery_does_not_reassign_across_explicit_modules():
    """Entities tagged with distinct module properties stay within their declared module."""
    graph = DependencyGraph(
        entities={
            "com.example.chat.facade": Entity(
                fqn="com.example.chat.facade",
                name="facade",
                package="com.example.chat",
                file_path="mod-chat/chat.py",
                kind="function",
                language="python",
                properties={"module": "mod-chat"},
            ),
            "com.example.chat.other": Entity(
                fqn="com.example.chat.other",
                name="other",
                package="com.example.chat",
                file_path="mod-chat/other.py",
                kind="function",
                language="python",
                properties={"module": "mod-chat"},
            ),
            "com.example.core.worker": Entity(
                fqn="com.example.core.worker",
                name="worker",
                package="com.example.core",
                file_path="mod-core/worker.py",
                kind="function",
                language="python",
                properties={"module": "mod-core"},
            ),
        },
        edges=[
            Edge(
                source="com.example.chat.facade",
                target="com.example.core.worker",
                relation="import",
            ),
        ],
    )

    arch = recover(graph, algorithm="pkg")
    membership = arch.membership()

    assert membership["com.example.chat.facade"] == membership["com.example.chat.other"]
    assert membership["com.example.chat.facade"] != membership["com.example.core.worker"]
    assert "facade refinement" not in arch.rationale
