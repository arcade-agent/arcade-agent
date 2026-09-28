"""Tests for parse result caching."""

import os

import pytest

from arcade_agent.cache import (
    cache_key,
    get_cached_graph,
    invalidate_cache,
    put_cached_graph,
)
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity


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


@pytest.mark.parametrize(
    ("configuration_name", "language"),
    [
        ("tsconfig.base.json", "typescript"),
        ("package.json", "typescript|excl:default"),
        ("jsconfig.json", "typescript"),
    ],
)
def test_cache_key_tracks_typescript_resolution_configuration(
    tmp_project, configuration_name, language
):
    source = tmp_project / "src" / "app.ts"
    source.write_text("export class App {}\n")
    configuration = tmp_project / configuration_name
    configuration.write_text('{"compilerOptions":{"baseUrl":"src"}}')
    files = [str(source)]
    first = cache_key(str(tmp_project), language, files)

    configuration.write_text('{"compilerOptions":{"baseUrl":"lib"}}')
    newer = configuration.stat().st_mtime + 2
    os.utime(configuration, (newer, newer))
    second = cache_key(str(tmp_project), language, files)

    assert first != second


@pytest.mark.parametrize("extension", [".mjs", ".cjs"])
def test_cache_key_tracks_all_javascript_parser_extensions(tmp_project, extension):
    source = tmp_project / "src" / f"app{extension}"
    source.write_text("export class Before {}\n")
    first = cache_key(str(tmp_project), "typescript", None)
    source.write_text("export class After {}\n")
    newer = source.stat().st_mtime + 2
    os.utime(source, (newer, newer))
    second = cache_key(str(tmp_project), "typescript", None)

    assert first != second


def test_cache_key_changes_with_exclude_tests(tmp_project):
    """Rust graphs differ by exclude_tests, so cached graphs must not collide."""
    k1 = cache_key(str(tmp_project), "rust", None)
    k2 = cache_key(str(tmp_project), "rust", None, exclude_tests=False)
    assert k1 != k2


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
