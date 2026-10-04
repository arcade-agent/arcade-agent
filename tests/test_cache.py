"""Tests for parse result caching."""

import hashlib
import os
from unittest.mock import Mock

import pytest

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.algorithms.cycles import detect_dependency_cycles
from arcade_agent.cache import (
    cache_key,
    get_cached_graph,
    invalidate_cache,
    put_cached_graph,
)
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity
from arcade_agent.parsers.java import JavaParser
from arcade_agent.source.parse import parse


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project directory with source files."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "Main.java").write_text("public class Main {}")
    (src / "Helper.java").write_text("public class Helper {}")
    return tmp_path


@pytest.fixture
def small_graph():
    return DependencyGraph(
        entities={
            "Main": Entity(
                fqn="Main", name="Main", package="", file_path="Main.java",
                kind="class", language="java",
            ),
        },
        edges=[Edge(source="Main", target="Helper", relation="import")],
        packages={"": ["Main"]},
    )


def test_cache_key_deterministic(tmp_project):
    k1 = cache_key(str(tmp_project), "java", None)
    k2 = cache_key(str(tmp_project), "java", None)
    assert k1 == k2


def test_cache_key_changes_with_language(tmp_project):
    k1 = cache_key(str(tmp_project), "java", None)
    k2 = cache_key(str(tmp_project), "python", None)
    assert k1 != k2


def test_cache_key_changes_with_file_modification(tmp_project):
    k1 = cache_key(str(tmp_project), "java", None)
    # Modify a file and bump mtime past filesystem second resolution
    target = tmp_project / "src" / "Main.java"
    target.write_text("public class Main { int x; }")
    newer = target.stat().st_mtime + 2
    os.utime(target, (newer, newer))
    k2 = cache_key(str(tmp_project), "java", None)
    assert k1 != k2


def test_cache_key_tracks_rust_source_files(tmp_project):
    rust_file = tmp_project / "src" / "lib.rs"
    rust_file.write_text("pub struct Before;")
    k1 = cache_key(str(tmp_project), "rust", None)
    rust_file.write_text("pub struct After;")
    k2 = cache_key(str(tmp_project), "rust", None)
    assert k1 != k2


def test_cache_key_tracks_cargo_manifests_with_explicit_rust_files(tmp_project):
    rust_file = tmp_project / "src" / "lib.rs"
    rust_file.write_text("pub struct App;")
    manifest = tmp_project / "Cargo.toml"
    manifest.write_text('[package]\nname = "before"\n')
    files = [str(rust_file)]
    k1 = cache_key(str(tmp_project), "rust", files)

    manifest.write_text('[package]\nname = "after"\n')
    newer = manifest.stat().st_mtime + 2
    os.utime(manifest, (newer, newer))
    k2 = cache_key(str(tmp_project), "rust", files)

    assert k1 != k2


def test_cache_key_tracks_cargo_manifests_for_composite_language_keys(tmp_project):
    """source.parse passes keys like "rust|<exclusions>", not a bare language."""
    rust_file = tmp_project / "src" / "lib.rs"
    rust_file.write_text("pub struct App;")
    manifest = tmp_project / "Cargo.toml"
    manifest.write_text('[package]\nname = "before"\n')
    k1 = cache_key(str(tmp_project), "rust|excl:default", None)

    manifest.write_text('[package]\nname = "after"\n')
    newer = manifest.stat().st_mtime + 2
    os.utime(manifest, (newer, newer))
    k2 = cache_key(str(tmp_project), "rust|excl:default", None)

    assert k1 != k2


def test_cache_key_changes_with_exclude_tests(tmp_project):
    """Rust graphs differ by exclude_tests, so cached graphs must not collide."""
    k1 = cache_key(str(tmp_project), "rust", None)
    k2 = cache_key(str(tmp_project), "rust", None, exclude_tests=False)
    assert k1 != k2


def test_cache_key_changes_with_graph_cache_schema_version(tmp_project, monkeypatch):
    k1 = cache_key(str(tmp_project), "java", None)
    monkeypatch.setattr("arcade_agent.cache._GRAPH_CACHE_SCHEMA_VERSION", "next")
    k2 = cache_key(str(tmp_project), "java", None)
    assert k1 != k2


@pytest.fixture
def java_project_with_unused_import(tmp_path):
    pool = tmp_path / "p" / "a" / "Pool.java"
    doc = tmp_path / "p" / "b" / "Doc.java"
    pool.parent.mkdir(parents=True)
    doc.parent.mkdir(parents=True)
    pool.write_text(
        "package p.a;\nimport p.b.Doc;\n"
        "/** See {@link Doc}. */\npublic class Pool {}\n"
    )
    doc.write_text(
        "package p.b;\nimport p.a.Pool;\npublic class Doc { Pool pool; }\n"
    )
    return tmp_path


@pytest.mark.parametrize("language", ["java", None, "multi"])
@pytest.mark.parametrize("explicit_files", [False, True])
def test_parse_invalidates_legacy_java_import_cycle(
    java_project_with_unused_import, language, explicit_files,
):
    root = java_project_with_unused_import
    files = sorted(root.rglob("*.java"))
    mtimes = [path.stat().st_mtime_ns for path in files]

    # Fingerprint used before parser-version invalidation; sources stay unchanged.
    hasher = hashlib.sha256()
    hasher.update(str(root.resolve()).encode())
    cache_language = language or "auto"
    if not explicit_files:
        cache_language = f"{cache_language}|default=True;extra=()"
    hasher.update(cache_language.encode())
    hasher.update(b"tests:excluded")
    for path in files:
        hasher.update(str(path).encode())
        hasher.update(str(path.stat().st_mtime_ns).encode())
    legacy_key = hasher.hexdigest()
    legacy_graph = DependencyGraph(
        entities={
            "p.a.Pool": Entity(
                "p.a.Pool", "Pool", "p.a", str(files[0]), "class", "java", ["p.b.Doc"],
            ),
            "p.b.Doc": Entity(
                "p.b.Doc", "Doc", "p.b", str(files[1]), "class", "java", ["p.a.Pool"],
            ),
        },
        edges=[Edge("p.a.Pool", "p.b.Doc", "import"), Edge("p.b.Doc", "p.a.Pool", "import")],
        packages={"p.a": ["p.a.Pool"], "p.b": ["p.b.Doc"]},
    )
    architecture = Architecture(components=[
        Component(name="A", responsibility="", entities=["p.a.Pool"]),
        Component(name="B", responsibility="", entities=["p.b.Doc"]),
    ])
    assert detect_dependency_cycles(architecture, legacy_graph) == [["A", "B"]]
    put_cached_graph(str(root), legacy_key, legacy_graph)
    assert get_cached_graph(str(root), legacy_key) is not None

    graph = parse(
        str(root), language=language,
        files=[str(path) for path in files] if explicit_files else None,
    )

    assert set(graph.entities) == {"p.a.Pool", "p.b.Doc"}
    assert ("p.b.Doc", "p.a.Pool", "import") in graph.to_edge_tuples()
    assert ("p.a.Pool", "p.b.Doc", "import") not in graph.to_edge_tuples()
    assert detect_dependency_cycles(architecture, graph) == []
    assert mtimes == [path.stat().st_mtime_ns for path in files]


def test_parse_reuses_current_java_cache(java_project_with_unused_import, monkeypatch):
    root = java_project_with_unused_import
    cold = parse(str(root), language="java")
    parser = Mock(side_effect=AssertionError("Warm cache must skip parsing"))
    monkeypatch.setattr(JavaParser, "parse", parser)

    warm = parse(str(root), language="java")

    assert warm == cold
    assert ("p.b.Doc", "p.a.Pool", "import") in warm.to_edge_tuples()
    assert ("p.a.Pool", "p.b.Doc", "import") not in warm.to_edge_tuples()
    parser.assert_not_called()


def test_cache_miss_returns_none(tmp_project):
    result = get_cached_graph(str(tmp_project), "nonexistent_key")
    assert result is None


def test_cache_roundtrip(tmp_project, small_graph):
    key = "test_key_123"
    put_cached_graph(str(tmp_project), key, small_graph)
    loaded = get_cached_graph(str(tmp_project), key)
    assert loaded is not None
    assert loaded.num_entities == 1
    assert loaded.num_edges == 1
    assert loaded.entities["Main"].name == "Main"
    assert loaded.edges[0].source == "Main"
    # Atomic write should leave no torn tmp siblings
    cache_dir = tmp_project / ".arcade-cache"
    assert list(cache_dir.glob("*.tmp")) == []


def test_invalidate_cache(tmp_project, small_graph):
    put_cached_graph(str(tmp_project), "key1", small_graph)
    put_cached_graph(str(tmp_project), "key2", small_graph)
    removed = invalidate_cache(str(tmp_project))
    assert removed == 2
    assert get_cached_graph(str(tmp_project), "key1") is None
    assert get_cached_graph(str(tmp_project), "key2") is None


def test_invalidate_empty_cache(tmp_project):
    removed = invalidate_cache(str(tmp_project))
    assert removed == 0


def test_corrupt_cache_file_returns_none(tmp_project):
    cache_dir = tmp_project / ".arcade-cache"
    cache_dir.mkdir()
    (cache_dir / "bad_key.json").write_text("not valid json {{{")
    result = get_cached_graph(str(tmp_project), "bad_key")
    assert result is None
    # Corrupt file should be cleaned up
    assert not (cache_dir / "bad_key.json").exists()
