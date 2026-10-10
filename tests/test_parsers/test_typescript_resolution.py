"""Tests for configuration-aware TypeScript module resolution."""

import json

import pytest

from arcade_agent.parsers.typescript_resolution import (
    ExternalImport,
    ResolvedLocal,
    TypeScriptModuleResolver,
    UnresolvedLocal,
    _strip_jsonc_comments,
    _strip_trailing_commas,
)


def _resolver(tmp_path, paths):
    files = []
    modules = {}
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("export class Model {}\n")
        files.append(path)
        key = name.removesuffix(".ts")
        modules[name] = key.replace("/", ".")
    return TypeScriptModuleResolver(tmp_path, modules, files)


def test_solution_references_use_only_the_config_containing_the_source(tmp_path):
    resolver = _resolver(tmp_path, ["app/use.ts", "app/model.ts", "tools/model.ts"])
    (tmp_path / "tsconfig.json").write_text(json.dumps({
        "files": [],
        "references": [{"path": "./tsconfig.tools.json"}, {"path": "./tsconfig.app.json"}],
    }))
    for directory in ("app", "tools"):
        (tmp_path / f"tsconfig.{directory}.json").write_text(json.dumps({
            "include": [f"{directory}/**/*"],
            "compilerOptions": {"paths": {"@model": [f"{directory}/model.ts"]}},
        }))
    assert resolver.resolve("@model", tmp_path / "app/use.ts") == ResolvedLocal(
        "app.model", "tsconfig_paths"
    )


def test_array_extends_preserves_options_not_overridden_by_later_parent(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "src/model.ts"])
    (tmp_path / "aliases.json").write_text(
        '{"compilerOptions":{"paths":{"@model":["src/model.ts"]}}}'
    )
    (tmp_path / "strict.json").write_text('{"compilerOptions":{"strict":true}}')
    (tmp_path / "tsconfig.json").write_text('{"extends":["./aliases", "./strict"]}')
    assert resolver.resolve("@model", tmp_path / "use.ts") == ResolvedLocal(
        "src.model", "tsconfig_paths"
    )


def test_later_array_extends_replaces_paths_instead_of_deep_merging(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "a.ts", "b.ts"])
    for name in ("a", "b"):
        (tmp_path / f"{name}.json").write_text(json.dumps({
            "compilerOptions": {"paths": {f"@{name}": [f"{name}.ts"]}},
        }))
    (tmp_path / "tsconfig.json").write_text('{"extends":["./a", "./b"]}')
    assert isinstance(resolver.resolve("@a", tmp_path / "use.ts"), ExternalImport)
    assert resolver.resolve("@b", tmp_path / "use.ts") == ResolvedLocal("b", "tsconfig_paths")


def test_child_base_url_applies_to_inherited_paths(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "old/model.ts", "new/model.ts"])
    (tmp_path / "base.json").write_text(
        '{"compilerOptions":{"baseUrl":"old","paths":{"@model":["model.ts"]}}}'
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"extends":"./base","compilerOptions":{"baseUrl":"new"}}'
    )
    assert resolver.resolve("@model", tmp_path / "use.ts") == ResolvedLocal(
        "new.model", "tsconfig_paths"
    )


@pytest.mark.parametrize("specifier", ["./model", "./model.js", "./linked"])
def test_relative_resolution_and_in_root_symlink(tmp_path, specifier):
    resolver = _resolver(tmp_path, ["use.ts", "model.ts"])
    (tmp_path / "linked.ts").symlink_to(tmp_path / "model.ts")
    assert resolver.resolve(specifier, tmp_path / "use.ts") == ResolvedLocal(
        "model", "relative"
    )


def test_missing_js_symlink_target_still_resolves_to_typescript_source(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "model.ts"])
    (tmp_path / "linked.js").symlink_to(tmp_path / "model.js")
    assert resolver.resolve("./linked.js", tmp_path / "use.ts") == ResolvedLocal(
        "model", "relative"
    )
    assert not resolver.configuration_errors


@pytest.mark.parametrize("entry", ["extends", "references", "files"])
def test_symlink_loop_in_config_paths_reports_error_and_preserves_local_source(tmp_path, entry):
    resolver = _resolver(tmp_path, ["use.ts", "model.ts"])
    loop = tmp_path / "loop"
    loop.symlink_to(loop, target_is_directory=True)
    data = {
        "extends": {"extends": "./loop/config.json"},
        "references": {"references": [{"path": "./loop/config.json"}]},
        "files": {"files": ["loop/model.ts", "use.ts"]},
    }[entry]
    (tmp_path / "tsconfig.json").write_text(json.dumps(data))
    resolver.resolve("react", tmp_path / "use.ts")
    assert resolver.configuration_errors
    assert resolver.resolve("./model", tmp_path / "use.ts") == ResolvedLocal(
        "model", "relative"
    )


def test_extends_rejects_outside_root_before_reading_it(tmp_path, monkeypatch):
    resolver = _resolver(tmp_path, ["use.ts", "model.ts"])
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text('{"compilerOptions":{"paths":{"@model":["model.ts"]}}}')
    (tmp_path / "tsconfig.json").write_text(json.dumps({"extends": str(outside)}))
    from arcade_agent.parsers import typescript_resolution

    original = typescript_resolution._read_json_object
    reads = []

    def record(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(typescript_resolution, "_read_json_object", record)
    resolver.resolve("@model", tmp_path / "use.ts")
    assert outside not in reads
    assert any("outside project root" in error for error in resolver.configuration_errors)


def test_circular_extends_keeps_source_resolution_and_reports_error(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "model.ts"])
    (tmp_path / "tsconfig.json").write_text('{"extends":"./base"}')
    (tmp_path / "base.json").write_text(
        '{"extends":"./tsconfig","compilerOptions":{"paths":{"@model":["model.ts"]}}}'
    )
    assert resolver.resolve("@model", tmp_path / "use.ts") == ResolvedLocal(
        "model", "tsconfig_paths"
    )
    assert any("cyclic extends" in error for error in resolver.configuration_errors)


def test_exact_paths_rule_does_not_fall_through_to_wildcard(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "model.ts", "other.ts"])
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"paths":{"@*": ["other.ts"], "@model": ["model.ts"]}}}'
    )
    assert resolver.resolve("@model", tmp_path / "use.ts") == ResolvedLocal(
        "model", "tsconfig_paths"
    )
    (tmp_path / "model.ts").unlink()
    missing = TypeScriptModuleResolver(tmp_path, {"other.ts": "other"}, [tmp_path / "use.ts"])
    assert isinstance(missing.resolve("@model", tmp_path / "use.ts"), UnresolvedLocal)


def test_undeclared_fixture_package_cannot_capture_an_external_import(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "fixtures/react/index.ts"])
    (tmp_path / "fixtures/react/package.json").write_text('{"name":"react"}')
    resolver = TypeScriptModuleResolver(
        tmp_path, resolver.module_by_pathkey, list(resolver.source_files)
    )
    assert isinstance(resolver.resolve("react", tmp_path / "use.ts"), ExternalImport)


def test_workspace_glob_ignores_node_modules_and_duplicate_names(tmp_path):
    resolver = _resolver(tmp_path, [
        "use.ts", "packages/a/index.ts", "packages/b/index.ts",
        "packages/node_modules/fake/index.ts",
    ])
    (tmp_path / "package.json").write_text('{"workspaces":["packages/**"]}')
    for directory in ("packages/a", "packages/b"):
        (tmp_path / directory / "package.json").write_text('{"name":"shared"}')
    (tmp_path / "packages/node_modules/fake/package.json").write_text('{"name":"fake"}')
    resolver = TypeScriptModuleResolver(
        tmp_path, resolver.module_by_pathkey, list(resolver.source_files)
    )
    assert isinstance(resolver.resolve("fake", tmp_path / "use.ts"), ExternalImport)
    assert isinstance(resolver.resolve("shared", tmp_path / "use.ts"), UnresolvedLocal)
    assert any("duplicate workspace" in error for error in resolver.configuration_errors)


def test_workspace_conditional_exports_and_private_subpaths(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "packages/a/source.ts", "packages/a/private.ts"])
    (tmp_path / "package.json").write_text('{"workspaces":["packages/*"]}')
    (tmp_path / "packages/a/package.json").write_text(json.dumps({
        "name": "a",
        "exports": {".": {"types": "./missing.d.ts", "import": "./source.ts"}},
    }))
    resolver = TypeScriptModuleResolver(
        tmp_path, resolver.module_by_pathkey, list(resolver.source_files)
    )
    assert resolver.resolve("a", tmp_path / "use.ts") == ResolvedLocal(
        "packages.a.source", "workspace"
    )
    assert isinstance(resolver.resolve("a/private", tmp_path / "use.ts"), UnresolvedLocal)


def test_jsonc_stripping_preserves_comment_markers_and_escaped_quotes_in_strings():
    text = '{/*comment*/ "url":"https://example.test/*keep*/", "quote":"a\\\"//b", //line\n}'
    assert json.loads(_strip_trailing_commas(_strip_jsonc_comments(text))) == {
        "url": "https://example.test/*keep*/", "quote": 'a"//b',
    }


def test_declared_workspace_without_parsed_sources_is_unresolved_local(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts"])
    (tmp_path / "packages/missing").mkdir(parents=True)
    (tmp_path / "package.json").write_text('{"workspaces":["packages/*"]}')
    (tmp_path / "packages/missing/package.json").write_text('{"name":"missing"}')
    resolver = TypeScriptModuleResolver(
        tmp_path, resolver.module_by_pathkey, list(resolver.source_files)
    )
    assert isinstance(resolver.resolve("missing", tmp_path / "use.ts"), UnresolvedLocal)


def test_longest_wildcard_prefix_wins(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "short.ts", "long.ts"])
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"paths":{"@*":["short.ts"],"@local/*":["long.ts"]}}}'
    )
    assert resolver.resolve("@local/model", tmp_path / "use.ts") == ResolvedLocal(
        "long", "tsconfig_paths"
    )


@pytest.mark.parametrize("entry", ["extends", "references"])
def test_symlink_config_cannot_escape_root(tmp_path, monkeypatch, entry):
    resolver = _resolver(tmp_path, ["use.ts"])
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text('{}')
    (tmp_path / "outside.json").symlink_to(outside)
    data = {"extends": "./outside"} if entry == "extends" else {
        "references": [{"path": "./outside.json"}],
    }
    (tmp_path / "tsconfig.json").write_text(json.dumps(data))
    from arcade_agent.parsers import typescript_resolution

    reads = []
    original = typescript_resolution._read_json_object

    def record(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(typescript_resolution, "_read_json_object", record)
    resolver.resolve("react", tmp_path / "use.ts")
    assert outside not in reads
    assert any("outside project root" in error for error in resolver.configuration_errors)


def test_config_scope_preserves_inherited_include_and_explicit_files(tmp_path):
    resolver = _resolver(tmp_path, ["app/use.ts", "app/model.ts", "tools/model.ts"])
    (tmp_path / "base.json").write_text('{"include":["app/**/*"]}')
    (tmp_path / "tsconfig.app.json").write_text(
        '{"extends":"./base","compilerOptions":{"paths":{"@model":["app/model.ts"]}}}'
    )
    (tmp_path / "tsconfig.tools.json").write_text(
        '{"files":["tools/model.ts"],"compilerOptions":{"paths":{"@model":["tools/model.ts"]}}}'
    )
    (tmp_path / "tsconfig.json").write_text(json.dumps({
        "files": [], "references": [
            {"path": "./tsconfig.tools.json"}, {"path": "./tsconfig.app.json"},
        ],
    }))
    assert resolver.resolve("@model", tmp_path / "app/use.ts") == ResolvedLocal(
        "app.model", "tsconfig_paths"
    )


def test_unsupported_config_interpolation_and_package_imports_report_limits(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts"])
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"paths":{"@local":["${configDir}/model.ts"]}}}'
    )
    assert isinstance(resolver.resolve("@local", tmp_path / "use.ts"), UnresolvedLocal)
    assert isinstance(resolver.resolve("#local", tmp_path / "use.ts"), UnresolvedLocal)
    assert any("${configDir}" in error for error in resolver.configuration_errors)
    assert any("#imports" in error for error in resolver.configuration_errors)


def test_config_selection_is_per_file_even_when_siblings_share_a_directory(tmp_path):
    resolver = _resolver(tmp_path, ["use-a.ts", "use-b.ts", "a.ts", "b.ts"])
    (tmp_path / "tsconfig.json").write_text(json.dumps({
        "files": [], "references": [{"path": "./config-a.json"}, {"path": "./config-b.json"}],
    }))
    for name in ("a", "b"):
        (tmp_path / f"config-{name}.json").write_text(json.dumps({
            "files": [f"use-{name}.ts"],
            "compilerOptions": {"paths": {"@model": [f"{name}.ts"]}},
        }))
    assert resolver.resolve("@model", tmp_path / "use-a.ts") == ResolvedLocal(
        "a", "tsconfig_paths"
    )
    assert resolver.resolve("@model", tmp_path / "use-b.ts") == ResolvedLocal(
        "b", "tsconfig_paths"
    )


def test_explicit_jsonc_extends_is_read_without_changing_the_extension(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "model.ts"])
    (tmp_path / "base.jsonc").write_text(
        '{/*comment*/"compilerOptions":{"paths":{"@model":["model.ts",],},},}'
    )
    (tmp_path / "tsconfig.json").write_text('{"extends":"./base.jsonc"}')
    assert resolver.resolve("@model", tmp_path / "use.ts") == ResolvedLocal(
        "model", "tsconfig_paths"
    )


def test_catch_all_paths_rule_without_a_source_target_is_external(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "src/types/model.ts"])
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"paths":{"*":["src/types/*"]}}}'
    )
    assert resolver.resolve("src/types/model", tmp_path / "use.ts") == ExternalImport()
    assert resolver.resolve("model", tmp_path / "use.ts") == ResolvedLocal(
        "src.types.model", "tsconfig_paths"
    )
    assert resolver.resolve("react", tmp_path / "use.ts") == ExternalImport()


def test_package_config_extends_is_a_note_not_a_configuration_error(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "model.ts"])
    (tmp_path / "tsconfig.json").write_text(json.dumps({
        "extends": ["@tsconfig/node20/tsconfig.json", "./aliases"],
    }))
    (tmp_path / "aliases.json").write_text(
        '{"compilerOptions":{"paths":{"@model":["model.ts"]}}}'
    )
    assert resolver.resolve("@model", tmp_path / "use.ts") == ResolvedLocal(
        "model", "tsconfig_paths"
    )
    assert resolver.resolve("react", tmp_path / "use.ts") == ExternalImport()
    assert resolver.configuration_errors == ()
    assert any("@tsconfig/node20" in note for note in resolver.configuration_notes)
    summary = resolver.summary(
        {tmp_path / "use.ts": {"react": ExternalImport()}}, set(),
    )
    assert summary["metrics_qualified"] is False
    assert summary["configuration_notes"]


@pytest.mark.parametrize("name", ["App.vue", "Card.svelte", "Page.astro", "query.graphql"])
def test_framework_component_imports_are_not_unresolved_sources(tmp_path, name):
    resolver = _resolver(tmp_path, ["use.ts"])
    (tmp_path / name).write_text("")
    assert resolver.resolve(f"./{name}", tmp_path / "use.ts") == ExternalImport()


def test_existing_non_script_file_is_external_but_missing_module_is_not(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts"])
    (tmp_path / "shader.wgsl").write_text("")
    assert resolver.resolve("./shader.wgsl", tmp_path / "use.ts") == ExternalImport()
    assert isinstance(resolver.resolve("./missing.service", tmp_path / "use.ts"),
                      UnresolvedLocal)


def test_tsconfig_with_utf8_bom_keeps_its_aliases(tmp_path):
    resolver = _resolver(tmp_path, ["use.ts", "model.ts"])
    (tmp_path / "tsconfig.json").write_bytes(
        b'\xef\xbb\xbf{"compilerOptions":{"paths":{"@model":["model.ts"]}}}'
    )
    assert resolver.resolve("@model", tmp_path / "use.ts") == ResolvedLocal(
        "model", "tsconfig_paths"
    )
    assert resolver.configuration_errors == ()
