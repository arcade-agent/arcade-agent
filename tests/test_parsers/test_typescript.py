"""Tests for the TypeScript / JavaScript parser."""

import os

import pytest

ts = pytest.importorskip("tree_sitter_typescript")
import arcade_agent.parsers.typescript as typescript_parser  # noqa: E402
from arcade_agent.parsers.typescript import TypeScriptParser  # noqa: E402
from arcade_agent.tools.parse import parse  # noqa: E402


def _project(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "services").mkdir()
    (tmp_path / "models" / "user.ts").write_text(
        "export class User { constructor(public id: number) {} }\n"
        "export interface IRepo { find(id: number): User; }\n"
    )
    (tmp_path / "services" / "userService.ts").write_text(
        'import { User, IRepo } from "../models/user";\n'
        "export class UserService implements IRepo {\n"
        "  find(id: number): User { return new User(id); }\n"
        "}\n"
        "export function makeUser(id: number): User { return new User(id); }\n"
    )
    return list(tmp_path.rglob("*.ts"))


def test_ts_parser_properties():
    parser = TypeScriptParser()
    assert parser.language == "typescript"
    assert ".ts" in parser.file_extensions
    assert ".tsx" in parser.file_extensions


def test_ts_parser_entities(tmp_path):
    parser = TypeScriptParser()
    graph = parser.parse(_project(tmp_path), tmp_path)
    names = {e.name for e in graph.entities.values()}
    assert {"User", "IRepo", "UserService", "makeUser"} <= names
    kinds = {e.name: e.kind for e in graph.entities.values()}
    assert kinds["User"] == "class"
    assert kinds["IRepo"] == "interface"
    assert kinds["makeUser"] == "function"


def test_ts_parser_cross_file_edges(tmp_path):
    parser = TypeScriptParser()
    graph = parser.parse(_project(tmp_path), tmp_path)
    rels = {(e.source.split(".")[-1], e.target.split(".")[-1], e.relation) for e in graph.edges}
    # import edge across files and an implements edge
    assert ("UserService", "User", "import") in rels
    assert ("UserService", "IRepo", "implements") in rels


def test_ts_parser_packages_by_directory(tmp_path):
    parser = TypeScriptParser()
    graph = parser.parse(_project(tmp_path), tmp_path)
    assert "models" in graph.packages
    assert "services" in graph.packages


def _resolution_summary(graph):
    return graph.metadata["dependency_resolution"]["typescript"]


def test_tsconfig_paths_resolve_local_workspace_imports_and_report_coverage(tmp_path):
    """Regression for #41, including JSONC and inherited compiler options."""
    (tmp_path / "tsconfig.base.json").write_text(
        """
        {
          // Paths are relative to the config that declares them.
          "compilerOptions": {
            "paths": {"@workspace/*": ["packages/*/src"],},
          },
        }
        """
    )
    package_a = tmp_path / "packages" / "a" / "src"
    package_b = tmp_path / "packages" / "b" / "src"
    package_a.mkdir(parents=True)
    package_b.mkdir(parents=True)
    (tmp_path / "packages" / "b" / "tsconfig.json").write_text(
        '{"extends": "../../tsconfig.base.json"}'
    )
    (package_a / "index.ts").write_text("export class A {}\n")
    (package_b / "service.ts").write_text(
        'import { A } from "@workspace/a";\n'
        "export class B { make(): A { return new A(); } }\n"
    )

    graph = TypeScriptParser().parse(sorted(tmp_path.rglob("*.ts")), tmp_path)
    assert (
        "packages.b.src.service.B",
        "packages.a.src.A",
        "import",
    ) in set(graph.to_edge_tuples())

    summary = _resolution_summary(graph)
    assert summary["import_specifiers"] == 1
    assert summary["resolved_local"] == 1
    assert summary["linked_local"] == 1
    assert summary["unresolved_local"] == 0
    assert summary["external"] == 0
    assert summary["resolved_by"] == {"tsconfig_paths": 1}
    assert summary["local_resolution_rate"] == 1.0
    assert summary["local_edge_rate"] == 1.0
    assert summary["metrics_qualified"] is False


def test_workspace_manifest_resolves_package_and_subpath_imports(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"name":"root","private":true,"workspaces":["packages/*"]}'
    )
    package_a = tmp_path / "packages" / "a"
    package_b = tmp_path / "packages" / "b"
    (package_a / "src").mkdir(parents=True)
    (package_b / "src").mkdir(parents=True)
    (package_a / "package.json").write_text(
        """{
          "name": "@workspace/a",
          "types": "src/index.ts",
          "exports": {".": "./src/index.ts", "./*": "./src/*.ts"}
        }"""
    )
    (package_b / "package.json").write_text('{"name":"@workspace/b"}')
    (package_a / "src" / "index.ts").write_text("export class A {}\n")
    (package_a / "src" / "client.ts").write_text("export class Client {}\n")
    (package_b / "src" / "service.ts").write_text(
        'import { A } from "@workspace/a";\n'
        'import { Client } from "@workspace/a/client";\n'
        "export class B { make(a: A): Client { return new Client(); } }\n"
    )

    graph = TypeScriptParser().parse(sorted(tmp_path.rglob("*.ts")), tmp_path)
    edges = set(graph.to_edge_tuples())
    assert (
        "packages.b.src.service.B",
        "packages.a.src.A",
        "import",
    ) in edges
    assert (
        "packages.b.src.service.B",
        "packages.a.src.client.Client",
        "import",
    ) in edges
    assert _resolution_summary(graph)["resolved_by"] == {"workspace": 2}


def test_base_url_resolves_bare_local_import(tmp_path):
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"baseUrl":"."}}'
    )
    source = tmp_path / "src"
    source.mkdir()
    (source / "model.ts").write_text("export class Model {}\n")
    (source / "service.ts").write_text(
        'import { Model } from "src/model";\n'
        "export class Service { value!: Model; }\n"
    )

    graph = TypeScriptParser().parse(sorted(source.glob("*.ts")), tmp_path)

    assert ("src.service.Service", "src.model.Model", "import") in set(
        graph.to_edge_tuples()
    )
    assert _resolution_summary(graph)["resolved_by"] == {"base_url": 1}


def test_resolution_distinguishes_external_from_unresolved_local(tmp_path):
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"paths":{"@local/*":["packages/*/src"]}}}'
    )
    source = tmp_path / "src"
    source.mkdir()
    (source / "service.ts").write_text(
        'import React from "react";\n'
        'import { Missing } from "@local/missing";\n'
        "export class Service { value!: Missing; render() { return React; } }\n"
    )

    graph = TypeScriptParser().parse([source / "service.ts"], tmp_path)
    summary = _resolution_summary(graph)
    assert summary["external"] == 1
    assert summary["unresolved_local"] == 1
    assert summary["resolved_local"] == 0
    assert summary["local_resolution_rate"] == 0.0
    assert summary["metrics_qualified"] is True
    assert summary["unresolved_local_imports"] == [
        {
            "file_path": "src/service.ts",
            "specifier": "@local/missing",
            "reason": "matched tsconfig paths but no parsed source target exists",
        }
    ]


def test_invalid_config_keeps_source_and_qualifies_bare_imports(tmp_path, caplog):
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions": invalid}')
    source = tmp_path / "app.ts"
    source.write_text(
        'import React from "react";\n'
        "export class App { render() { return React; } }\n"
    )

    graph = TypeScriptParser().parse([source], tmp_path)
    summary = _resolution_summary(graph)

    assert "app.App" in graph.entities
    assert summary["external"] == 1
    assert summary["configuration_errors_affect_resolution"] is True
    assert summary["metrics_qualified"] is True
    assert summary["configuration_errors"] == [
        "tsconfig.json: JSONDecodeError: Expecting value: line 1 column 21 (char 20)"
    ]
    assert "JSONDecodeError" in caplog.text


def test_default_and_namespace_imports_link_local_symbols(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "library.ts").write_text(
        "export default class Library {}\n"
        "export function makeLibrary(): Library { return new Library(); }\n"
    )
    (source / "service.ts").write_text(
        'import Library from "./library";\n'
        'import * as library from "./library";\n'
        "export class Service { make(): Library { return library.makeLibrary(); } }\n"
    )

    graph = TypeScriptParser().parse(sorted(source.glob("*.ts")), tmp_path)
    edges = set(graph.to_edge_tuples())
    assert ("src.service.Service", "src.library.Library", "import") in edges
    assert ("src.service.Service", "src.library.makeLibrary", "import") in edges
    assert _resolution_summary(graph)["unlinked_local"] == 0


def test_parser_discards_partial_file_state_and_preserves_valid_sibling(
    tmp_path, monkeypatch, caplog
):
    poisoned = tmp_path / "poisoned.ts"
    valid = tmp_path / "valid.ts"
    poisoned.write_text("export class Poisoned {}\n")
    valid.write_text("export class Survives {}\n")
    original = typescript_parser._extract_imports

    def fail_for_poisoned_file(root_node):
        if root_node.text and b"Poisoned" in root_node.text:
            raise RuntimeError("synthetic extraction failure")
        return original(root_node)

    monkeypatch.setattr(typescript_parser, "_extract_imports", fail_for_poisoned_file)
    graph = TypeScriptParser().parse([poisoned, valid], tmp_path)

    assert "poisoned.Poisoned" not in graph.entities
    assert "valid.Survives" in graph.entities
    assert "RuntimeError" in caplog.text


def test_parser_handles_deep_ast_without_losing_valid_sibling(tmp_path):
    deep = tmp_path / "deep.ts"
    valid = tmp_path / "valid.ts"
    deep.write_text("export const value = " + "(" * 1_200 + "1" + ")" * 1_200 + ";\n")
    valid.write_text("export class Survives {}\n")

    graph = TypeScriptParser().parse([deep, valid], tmp_path)

    assert "valid.Survives" in graph.entities
    assert all(len(fqns) == len(set(fqns)) for fqns in graph.packages.values())


def test_parse_cache_invalidates_when_tsconfig_changes_resolution(tmp_path):
    packages = tmp_path / "packages"
    for name in ("a", "c"):
        target = packages / name / "src"
        target.mkdir(parents=True)
        (target / "index.ts").write_text("export class Target {}\n")
    app = tmp_path / "app.ts"
    app.write_text(
        'import { Target } from "@local/target";\n'
        "export class App { value!: Target; }\n"
    )
    config = tmp_path / "tsconfig.json"
    config.write_text(
        '{"compilerOptions":{"paths":{"@local/target":["packages/a/src"]}}}'
    )
    files = [str(path) for path in sorted(tmp_path.rglob("*.ts"))]

    first = parse(str(tmp_path), language="typescript", files=files, use_cache=True)
    assert ("app.App", "packages.a.src.Target", "import") in set(first.to_edge_tuples())

    config.write_text(
        '{"compilerOptions":{"paths":{"@local/target":["packages/c/src"]}}}'
    )
    newer = config.stat().st_mtime + 2
    os.utime(config, (newer, newer))

    second = parse(str(tmp_path), language="typescript", files=files, use_cache=True)
    edges = set(second.to_edge_tuples())
    assert ("app.App", "packages.c.src.Target", "import") in edges
    assert ("app.App", "packages.a.src.Target", "import") not in edges
