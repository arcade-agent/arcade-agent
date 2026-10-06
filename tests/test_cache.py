"""Tests for parse result caching."""

import hashlib
import json
import os
import sys
from unittest.mock import Mock

import pytest

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.algorithms.cycles import detect_dependency_cycles
from arcade_agent.cache import (
    _typescript_configuration_files,
    cache_key,
    get_cached_graph,
    invalidate_cache,
    put_cached_graph,
)
from arcade_agent.parsers.graph import DependencyGraph, Edge, Entity
from arcade_agent.parsers.java import JavaParser
from arcade_agent.parsers.typescript import TypeScriptParser
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


@pytest.mark.parametrize("extension", [".mts", ".cts", ".mjs", ".cjs"])
def test_cache_key_tracks_all_javascript_parser_extensions(tmp_project, extension):
    source = tmp_project / "src" / f"app{extension}"
    source.write_text("export class Before {}\n")
    first = cache_key(str(tmp_project), "typescript", None)
    source.write_text("export class After {}\n")
    newer = source.stat().st_mtime + 2
    os.utime(source, (newer, newer))
    second = cache_key(str(tmp_project), "typescript", None)

    assert first != second


@pytest.fixture
def typescript_project(tmp_path):
    app = tmp_path / "app.ts"
    target = tmp_path / "target.ts"
    app.write_text('import Target from "./target"; export class App { value!: Target; }\n')
    target.write_text("export default class Target {}\n")
    return tmp_path


@pytest.mark.parametrize("explicit_files", [False, True])
def test_parse_invalidates_previous_typescript_cache(typescript_project, explicit_files):
    root = typescript_project
    files = sorted(root.glob("*.ts"))
    language = "typescript" if explicit_files else "typescript|default=True;extra=()"
    legacy_graph = DependencyGraph(entities={
        "app.App": Entity("app.App", "App", "", "app.ts", "class", "typescript"),
        "target.Target": Entity("target.Target", "Target", "", "target.ts", "class", "typescript"),
    })
    for version in (None, "4"):
        hasher = hashlib.sha256()
        if version is not None:
            hasher.update(f"graph-schema:{version}".encode())
        hasher.update(str(root.resolve()).encode())
        hasher.update(language.encode())
        hasher.update(b"tests:excluded")
        for path in files:
            hasher.update(str(path).encode())
            hasher.update(str(path.stat().st_mtime_ns).encode())
        put_cached_graph(str(root), hasher.hexdigest(), legacy_graph)

    graph = parse(
        str(root), language="typescript",
        files=[str(path) for path in files] if explicit_files else None,
    )

    assert ("app.App", "target.Target", "import") in graph.to_edge_tuples()


def test_parse_reuses_current_typescript_cache(typescript_project, monkeypatch):
    cold = parse(str(typescript_project), language="typescript")
    parser = Mock(side_effect=AssertionError("Warm cache must skip parsing"))
    monkeypatch.setattr(TypeScriptParser, "parse", parser)

    warm = parse(str(typescript_project), language="typescript")

    assert warm == cold
    assert ("app.App", "target.Target", "import") in warm.to_edge_tuples()
    parser.assert_not_called()


@pytest.mark.parametrize("relation", ["extends", "references"])
@pytest.mark.parametrize("suffix", [".json", ".jsonc"])
def test_parse_cache_tracks_transitive_typescript_configuration(tmp_path, relation, suffix):
    for directory in ("a", "b"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "target.ts").write_text("export class Target {}\n")
    (tmp_path / "app.ts").write_text(
        'import { Target } from "@target"; export class App { value!: Target; }\n'
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    leaf = config_dir / f"base{suffix}"
    leaf.write_text(
        '{/* JSONC */"compilerOptions":{"baseUrl":"../a",'
        '"paths":{"@target":["target.ts"]},},}\n'
    )
    (tmp_path / "project.json").write_text(json.dumps({"extends": f"./config/base{suffix}"}))
    configuration = {"extends": ["./project"]} if relation == "extends" else {
        "files": [], "references": [{"path": "./project"}],
    }
    (tmp_path / "tsconfig.json").write_text(json.dumps(configuration))
    files = [str(path) for path in sorted(tmp_path.rglob("*.ts"))]
    first = parse(str(tmp_path), language="typescript", files=files)
    assert ("app.App", "a.target.Target", "import") in first.to_edge_tuples()

    leaf.write_text(leaf.read_text().replace('"../a"', '"../b"'))
    newer = leaf.stat().st_mtime + 2
    os.utime(leaf, (newer, newer))
    second = parse(str(tmp_path), language="typescript", files=files)

    assert ("app.App", "b.target.Target", "import") in second.to_edge_tuples()
    assert ("app.App", "a.target.Target", "import") not in second.to_edge_tuples()


def test_typescript_configuration_scan_is_bounded_and_contained(tmp_path, monkeypatch):
    from arcade_agent.parsers import typescript_resolution

    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text("{}")
    (tmp_path / "linked.json").symlink_to(outside)
    (tmp_path / "tsconfig.external.json").symlink_to(outside)
    (tmp_path / "tsconfig.json").write_text(json.dumps({
        "extends": ["./first", "./missing", str(outside), "./linked"],
    }))
    (tmp_path / "first.json").write_text(
        '{"references":[{"path":"./second"}],"extends":"./tsconfig"}'
    )
    (tmp_path / "second.json").write_text('{"extends":"./first"}')
    (tmp_path / "unrelated.json").write_text("{}")
    original = typescript_resolution._read_json_object
    reads = []

    def record(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(typescript_resolution, "_read_json_object", record)
    files = _typescript_configuration_files(tmp_path)

    assert files == {
        str(tmp_path / name)
        for name in ("tsconfig.json", "first.json", "second.json", "missing.json")
    }
    assert len(reads) == len(set(reads))
    assert outside not in reads
    assert tmp_path / "unrelated.json" not in reads


def test_typescript_configuration_scan_handles_deep_references(tmp_path):
    depth = sys.getrecursionlimit() + 10
    (tmp_path / "tsconfig.json").write_text('{"references":[{"path":"./0"}]}')
    for index in range(depth):
        target = str(index + 1) if index + 1 < depth else "tsconfig"
        (tmp_path / f"{index}.json").write_text(json.dumps({
            "references": [{"path": f"./{target}"}],
        }))

    files = _typescript_configuration_files(tmp_path)

    assert len(files) == depth + 1


def test_cache_key_ignores_unrelated_json(typescript_project):
    root = typescript_project
    unrelated = root / "unrelated.json"
    unrelated.write_text('{"before": true}')
    first = cache_key(str(root), "typescript", None)
    unrelated.write_text('{"after": true}')
    newer = unrelated.stat().st_mtime + 2
    os.utime(unrelated, (newer, newer))

    assert cache_key(str(root), "typescript", None) == first


def test_cache_key_tracks_declared_missing_configuration(typescript_project):
    root = typescript_project
    (root / "tsconfig.json").write_text('{"extends":"./custom"}')
    first = cache_key(str(root), "typescript", None)

    configuration = root / "custom.json"
    configuration.write_text('{"compilerOptions":{"baseUrl":"src"}}')
    second = cache_key(str(root), "typescript", None)
    configuration.unlink()
    third = cache_key(str(root), "typescript", None)

    assert first != second
    assert second != third


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
