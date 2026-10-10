"""Tests for the TypeScript / JavaScript parser."""

import json
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


def test_asset_imports_do_not_qualify_source_resolution(tmp_path):
    app = tmp_path / "app.ts"
    app.write_text(
        'import "./styles.css"; import data from "./data.json";\n'
        'import logo from "./logo.svg?url"; import "@assets/theme.scss";\n'
        "export class App { render() { return [data, logo]; } }\n"
    )
    graph = TypeScriptParser().parse([app], tmp_path)
    summary = _resolution_summary(graph)
    assert summary["import_specifiers"] == 0
    assert summary["unresolved_local"] == 0
    assert summary["metrics_qualified"] is False
    assert "app.App" in graph.entities


def test_resolved_type_alias_and_top_level_imports_are_information_only(tmp_path):
    (tmp_path / "types.ts").write_text("export type Config = { value: number };\n")
    (tmp_path / "factory.ts").write_text("export function make() {}\n")
    (tmp_path / "use.ts").write_text(
        'import type { Config } from "./types"; import { make } from "./factory";\n'
        "make(); export class App { value!: Config; }\n"
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    summary = _resolution_summary(graph)
    assert summary["resolved_local"] == 2
    assert summary["unlinked_local"] == 2
    assert summary["metrics_qualified"] is False


def test_type_alias_import_never_links_to_an_unrelated_unique_class(tmp_path):
    (tmp_path / "types.ts").write_text("export type Config = { value: number };\n")
    (tmp_path / "unrelated.ts").write_text("export class Config {}\n")
    (tmp_path / "use.ts").write_text(
        'import type { Config } from "./types"; export class App { value!: Config; }\n'
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    assert ("use.App", "unrelated.Config", "import") not in set(graph.to_edge_tuples())
    assert _resolution_summary(graph)["unlinked_local"] == 1


@pytest.mark.parametrize("barrel", [
    'export { Model as PublicModel } from "./model";',
    'export * from "./named";',
])
def test_reexport_barrels_link_the_declared_module_without_name_fallback(tmp_path, barrel):
    (tmp_path / "model.ts").write_text("export class Model {}\n")
    (tmp_path / "other.ts").write_text("export class Model {}\n")
    (tmp_path / "named.ts").write_text('export { Model as PublicModel } from "./model";\n')
    (tmp_path / "index.ts").write_text(barrel)
    (tmp_path / "use.ts").write_text(
        'import { PublicModel } from "./index"; export class App { value!: PublicModel; }\n'
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    edges = set(graph.to_edge_tuples())
    assert ("use.App", "model.Model", "import") in edges
    assert ("use.App", "other.Model", "import") not in edges


def test_circular_reexports_terminate_and_keep_a_valid_sibling(tmp_path):
    (tmp_path / "a.ts").write_text('export * from "./b";\n')
    (tmp_path / "b.ts").write_text('export * from "./a"; export * from "./model";\n')
    (tmp_path / "model.ts").write_text("export class Model {}\n")
    (tmp_path / "use.ts").write_text(
        'import { Model } from "./a"; export class App { value!: Model; }\n'
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    assert ("use.App", "model.Model", "import") in set(graph.to_edge_tuples())


def test_local_export_alias_and_default_reexport_link_entities(tmp_path):
    (tmp_path / "model.ts").write_text(
        "class Model {}\nexport { Model as PublicModel }; export default Model;\n"
    )
    (tmp_path / "index.ts").write_text('export { default } from "./model";\n')
    (tmp_path / "use.ts").write_text(
        'import Model from "./index"; import { PublicModel } from "./model";\n'
        "export class App { first!: Model; second!: PublicModel; }\n"
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    assert ("use.App", "model.Model", "import") in set(graph.to_edge_tuples())


def test_imported_heritage_uses_its_module_when_names_are_duplicated(tmp_path):
    (tmp_path / "model.ts").write_text("export class Model {}\n")
    (tmp_path / "other.ts").write_text("export class Model {}\n")
    (tmp_path / "use.ts").write_text(
        'import { Model } from "./model"; export class App extends Model {}\n'
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    edges = set(graph.to_edge_tuples())
    assert ("use.App", "model.Model", "extends") in edges
    assert ("use.App", "other.Model", "extends") not in edges


def test_ambiguous_star_exports_do_not_choose_an_arbitrary_class(tmp_path):
    for name in ("a", "b"):
        (tmp_path / f"{name}.ts").write_text("export class Model {}\n")
    (tmp_path / "index.ts").write_text('export * from "./a"; export * from "./b";\n')
    (tmp_path / "use.ts").write_text(
        'import { Model } from "./index"; export class App { value!: Model; }\n'
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    assert not any(edge.source == "use.App" for edge in graph.edges)
    assert "use.App" in graph.entities


def test_star_export_does_not_reexport_a_default_or_private_class(tmp_path):
    (tmp_path / "model.ts").write_text("class Private {}\nexport default class Model {}\n")
    (tmp_path / "index.ts").write_text('export * from "./model";\n')
    (tmp_path / "use.ts").write_text(
        'import Model from "./index"; import { Private } from "./model";\n'
        "export class App { first!: Model; second!: Private; }\n"
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    assert not any(edge.source == "use.App" for edge in graph.edges)
    assert "use.App" in graph.entities


@pytest.mark.parametrize("kind", ["class", "interface"])
def test_default_declaration_is_not_implicitly_a_named_export(tmp_path, kind):
    (tmp_path / "model.ts").write_text(f"export default {kind} Model {{}}\n")
    (tmp_path / "use.ts").write_text(
        'import { Model } from "./model"; export class App { value!: Model; }\n'
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    assert not any(edge.source == "use.App" for edge in graph.edges)


def test_deep_reexports_are_iterative_and_preserve_a_valid_sibling(tmp_path):
    for index in range(1_200):
        (tmp_path / f"barrel{index}.ts").write_text(
            f'export * from "./barrel{index + 1}";\n'
        )
    (tmp_path / "barrel1200.ts").write_text("export class Model {}\n")
    (tmp_path / "use.ts").write_text(
        'import { Model } from "./barrel0"; export class App { value!: Model; }\n'
    )
    (tmp_path / "sibling.ts").write_text("export class Survives {}\n")
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    assert ("use.App", "barrel1200.Model", "import") in set(graph.to_edge_tuples())
    assert "sibling.Survives" in graph.entities
    assert all(edge.source in graph.entities and edge.target in graph.entities
               for edge in graph.edges)


@pytest.mark.parametrize("extension", [".mts", ".cts"])
def test_typescript_module_extensions_are_sources_instead_of_assets(tmp_path, extension):
    model = tmp_path / f"model{extension}"
    model.write_text("export class Model {}\n")
    app = tmp_path / "use.ts"
    app.write_text(
        f'import {{ Model }} from "./model{extension}"; export class App {{ value!: Model; }}\n'
    )
    graph = TypeScriptParser().parse([model, app], tmp_path)
    assert ("use.App", "model.Model", "import") in set(graph.to_edge_tuples())
    assert _resolution_summary(graph)["resolved_local"] == 1


@pytest.mark.parametrize("depth", [2, 1_100])
def test_deep_config_extends_preserves_source_and_a_valid_sibling(tmp_path, depth):
    (tmp_path / "tsconfig.json").write_text('{"extends":"./config0"}')
    for index in range(depth):
        (tmp_path / f"config{index}.json").write_text(
            f'{{"extends":"./config{index + 1}"}}'
        )
    (tmp_path / f"config{depth}.json").write_text(
        '{"compilerOptions":{"paths":{"@model":["model.ts"]}}}'
    )
    (tmp_path / "model.ts").write_text("export class Model {}\n")
    (tmp_path / "sibling.ts").write_text("export class Survives {}\n")
    (tmp_path / "use.ts").write_text(
        'import { Model } from "@model"; export class App { value!: Model; }\n'
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    assert ("use.App", "model.Model", "import") in set(graph.to_edge_tuples())
    assert "sibling.Survives" in graph.entities
    assert _resolution_summary(graph)["configuration_errors"] == []


@pytest.mark.parametrize("statement,expected", [
    ('export * from "../model";', False),
    ('export { default } from "../model";', True),
])
def test_nested_barrel_only_links_declared_default_export(tmp_path, statement, expected):
    (tmp_path / "barrel").mkdir()
    (tmp_path / "barrel/index.ts").write_text(statement)
    (tmp_path / "model.ts").write_text("export default class Model {}\n")
    (tmp_path / "use.ts").write_text(
        'import Model from "./barrel"; export class App { value!: Model; }\n'
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.rglob("*.ts")), tmp_path)
    edges = set(graph.to_edge_tuples())
    assert (("use.App", "model.Model", "import") in edges) is expected
    assert ("use.App", "barrel", "import") not in edges


def test_file_and_index_exports_keep_distinct_resolution_identities(tmp_path):
    (tmp_path / "foo").mkdir()
    (tmp_path / "foo.ts").write_text("export class Model {}\n")
    (tmp_path / "foo/index.ts").write_text("export class Other {}\n")
    (tmp_path / "explicit.ts").write_text(
        'import { Model, Other } from "./foo/index";\n'
        "export class App { first!: Model; second!: Other; }\n"
    )
    (tmp_path / "implicit.ts").write_text(
        'import { Model, Other } from "./foo";\n'
        "export class App { first!: Model; second!: Other; }\n"
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.rglob("*.ts")), tmp_path)
    edges = set(graph.to_edge_tuples())
    assert ("explicit.App", "foo.Other", "import") in edges
    assert ("explicit.App", "foo.Model", "import") not in edges
    assert ("implicit.App", "foo.Model", "import") in edges
    assert ("implicit.App", "foo.Other", "import") not in edges


@pytest.mark.parametrize("cycle", [False, True])
def test_symlink_import_does_not_erase_a_healthy_sibling(tmp_path, cycle):
    model = tmp_path / "model.ts"
    model.write_text("export class Model {}\n")
    linked = tmp_path / "linked.ts"
    linked.symlink_to(linked if cycle else model)
    use = tmp_path / "use.ts"
    use.write_text('import { Model } from "./linked"; export class App { value!: Model; }\n')
    sibling = tmp_path / "sibling.ts"
    sibling.write_text("export class Survives {}\n")
    graph = TypeScriptParser().parse([model, use, sibling], tmp_path)
    assert "sibling.Survives" in graph.entities
    assert (("use.App", "model.Model", "import") in set(graph.to_edge_tuples())) is not cycle
    assert _resolution_summary(graph)["metrics_qualified"] is cycle


def test_duplicate_file_and_index_symbol_never_links_to_the_wrong_source(tmp_path):
    (tmp_path / "foo").mkdir()
    (tmp_path / "foo.ts").write_text("export class Model {}\n")
    (tmp_path / "foo/index.ts").write_text("export class Model {}\n")
    (tmp_path / "use.ts").write_text(
        'import { Model } from "./foo"; export class App { value!: Model; }\n'
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.rglob("*.ts")), tmp_path)
    assert graph.entities["foo.Model"].file_path == "foo/index.ts"
    assert not any(edge.source == "use.App" for edge in graph.edges)
    assert graph.metadata["typescript_parser"]["duplicate_entity_fqns"] == 1


@pytest.mark.parametrize("base_url", ["loop", "loop/missing", "missing/../loop"])
@pytest.mark.parametrize("mutual", [False, True])
def test_symlink_loop_in_base_url_is_a_visible_config_error(tmp_path, base_url, mutual):
    loop = tmp_path / "loop"
    other = tmp_path / "other"
    loop.symlink_to(other if mutual else loop, target_is_directory=True)
    if mutual:
        other.symlink_to(loop, target_is_directory=True)
    (tmp_path / "tsconfig.json").write_text(json.dumps({
        "compilerOptions": {"baseUrl": base_url, "paths": {"@model": ["model.ts"]}},
    }))
    (tmp_path / "model.ts").write_text("export class Model {}\n")
    (tmp_path / "use.ts").write_text(
        'import { Model } from "@model"; export class App { value!: Model; }\n'
    )
    (tmp_path / "sibling.ts").write_text("export class Survives {}\n")
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    assert "model.Model" in graph.entities
    assert "use.App" in graph.entities
    assert "sibling.Survives" in graph.entities
    assert not graph.edges
    assert _resolution_summary(graph)["configuration_errors"]
    assert _resolution_summary(graph)["metrics_qualified"] is True


@pytest.mark.parametrize("specifier,target", [
    ("./foo", "foo.Model"), ("./foo.js", "foo.Model"), ("./foo.tsx", "foo.Other"),
])
def test_ts_and_tsx_resolution_keeps_the_selected_file_identity(tmp_path, specifier, target):
    (tmp_path / "foo.ts").write_text("export class Model {}\n")
    (tmp_path / "foo.tsx").write_text("export class Other {}\n")
    (tmp_path / "use.ts").write_text(
        f'import {{ Model, Other }} from "{specifier}";\n'
        "export class App { first!: Model; second!: Other; }\n"
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts*")), tmp_path)
    assert {edge.target for edge in graph.edges if edge.source == "use.App"} == {target}


def test_dotted_extensionless_module_name_is_not_filtered_as_an_asset(tmp_path):
    (tmp_path / "user.model.ts").write_text("export class Model {}\n")
    (tmp_path / "use.ts").write_text(
        'import { Model } from "./user.model"; export class App { value!: Model; }\n'
    )
    graph = TypeScriptParser().parse(sorted(tmp_path.glob("*.ts")), tmp_path)
    assert ("use.App", "user.model.Model", "import") in set(graph.to_edge_tuples())
    assert _resolution_summary(graph)["resolved_local"] == 1


def test_vue_project_with_shared_tsconfig_base_is_not_qualified(tmp_path):
    (tmp_path / "tsconfig.json").write_text(json.dumps({
        "extends": "@vue/tsconfig/tsconfig.dom.json",
        "compilerOptions": {"paths": {"*": ["src/types/*"]}},
    }))
    (tmp_path / "App.vue").write_text("<template><div /></template>\n")
    main = tmp_path / "main.ts"
    main.write_text(
        'import { createApp } from "vue"; import App from "./App.vue";\n'
        'import Widget from "./Widget.svelte";\n'
        "export class Boot { run() { return createApp(App); } }\n"
    )
    graph = TypeScriptParser().parse([main], tmp_path)
    summary = _resolution_summary(graph)
    assert summary["unresolved_local"] == 0
    assert summary["configuration_errors"] == []
    assert summary["metrics_qualified"] is False


def _write_js(root, files):
    paths = []
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        paths.append(path)
    return paths


def test_commonjs_require_and_module_exports_produce_edges(tmp_path):
    """Regression for #68: CommonJS modules were parsed with no edges."""
    paths = _write_js(tmp_path, {
        "repositories/userRepository.js": (
            "class UserRepository { find(id) { return id; } }\n"
            "module.exports = UserRepository;\n"
        ),
        "services/userService.js": (
            "const UserRepository = require('../repositories/userRepository');\n"
            "function getUser(id) { return new UserRepository().find(id); }\n"
            "module.exports = { getUser };\n"
        ),
        "routes/users.js": (
            "const { getUser } = require('../services/userService');\n"
            "function show(req) { return getUser(req.id); }\n"
            "module.exports = { show };\n"
        ),
    })

    edges = set(TypeScriptParser().parse(paths, tmp_path).to_edge_tuples())

    assert ("routes.users.show", "services.userService.getUser", "import") in edges
    assert ("services.userService.getUser",
            "repositories.userRepository.UserRepository", "import") in edges


def test_commonjs_namespace_member_and_exports_assignments(tmp_path):
    paths = _write_js(tmp_path, {
        "services/articles.js": (
            "function list() { return []; }\n"
            "const create = (data) => data;\n"
            "exports.list = list;\n"
            "module.exports.create = create;\n"
        ),
        "routes/articles.js": (
            "const articles = require('../services/articles');\n"
            "const create = require('../services/articles').create;\n"
            "function index() { return articles.list(); }\n"
            "function post(body) { return create(body); }\n"
            "module.exports = { index, post };\n"
        ),
    })

    edges = set(TypeScriptParser().parse(paths, tmp_path).to_edge_tuples())

    assert ("routes.articles.index", "services.articles.list", "import") in edges
    assert ("routes.articles.post", "services.articles.create", "import") in edges


def test_commonjs_index_reexports_and_external_requires(tmp_path):
    paths = _write_js(tmp_path, {
        "models/user.js": "class User {}\nmodule.exports = User;\n",
        "models/index.js": (
            "const User = require('./user');\n"
            "module.exports = { User };\n"
        ),
        "services/users.js": (
            "const express = require('express');\n"
            "const { User } = require('../models');\n"
            "function make() { return new User(); }\n"
            "module.exports = { make };\n"
        ),
    })

    edges = set(TypeScriptParser().parse(paths, tmp_path).to_edge_tuples())

    assert ("services.users.make", "models.user.User", "import") in edges
    assert not [edge for edge in edges if "express" in edge[1]]


def test_imports_of_exported_object_values_link_to_their_module(tmp_path):
    """Regression for #72: ``export const svc = { … }`` is a dependency target."""
    paths = _write_js(tmp_path, {
        "repositories/userRepository.ts": (
            "export const userRepository = { find(id: string) { return id; } };\n"
        ),
        "services/userService.ts": (
            "import { userRepository } from '../repositories/userRepository';\n"
            "export const userService = { get(id: string) { return userRepository.find(id); } };\n"
        ),
        "routes/users.ts": (
            "import { userService } from '../services/userService';\n"
            "export function show(id: string) { return userService.get(id); }\n"
        ),
    })

    edges = set(TypeScriptParser().parse(paths, tmp_path).to_edge_tuples())

    assert ("routes.users.show", "services.userService", "import") in edges
    assert ("services.userService", "repositories.userRepository", "import") in edges


def test_value_exports_in_files_with_declarations_and_commonjs(tmp_path):
    paths = _write_js(tmp_path, {
        "repositories/articles.js": (
            "class ArticleRepository { all() { return []; } }\n"
            "const articleRepository = new ArticleRepository();\n"
            "module.exports = { articleRepository };\n"
        ),
        "repositories/tags.ts": (
            "class TagStore {}\n"
            "const tagStore = new TagStore();\n"
            "export { tagStore };\n"
            "export default tagStore;\n"
        ),
        "routes/articles.js": (
            "const { articleRepository } = require('../repositories/articles');\n"
            "function index() { return articleRepository.all(); }\n"
            "module.exports = { index };\n"
        ),
        "routes/tags.ts": (
            "import tags, { tagStore } from '../repositories/tags';\n"
            "export function list() { return [tags, tagStore]; }\n"
        ),
    })

    edges = set(TypeScriptParser().parse(paths, tmp_path).to_edge_tuples())

    assert ("routes.articles.index", "repositories.articles.ArticleRepository", "import") in edges
    assert ("routes.tags.list", "repositories.tags.TagStore", "import") in edges
