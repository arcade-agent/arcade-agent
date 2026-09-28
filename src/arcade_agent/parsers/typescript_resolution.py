"""Configuration-aware TypeScript/JavaScript module resolution.

The tree-sitter parser extracts import syntax, while this module decides whether
an import points at a parsed local source file.  It intentionally models the
three outcomes separately: a resolved local module, a known local import whose
target is missing from the graph, and an external dependency.

This is not a replacement for the TypeScript compiler's complete resolver.  It
covers the source-level contracts that materially affect architecture graphs:
relative imports, ``compilerOptions.baseUrl``/``paths`` (including inherited
JSONC configs), and npm-compatible workspace package manifests.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

logger = logging.getLogger(__name__)

_SOURCE_EXTENSIONS = (".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs")
_CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
_MAX_DIAGNOSTICS = 100


@dataclass(frozen=True)
class ResolvedLocal:
    """An import resolved to a module present in the parsed source set."""

    module: str
    method: str


@dataclass(frozen=True)
class UnresolvedLocal:
    """An import is known to be local but its target is absent or invalid."""

    reason: str


@dataclass(frozen=True)
class ExternalImport:
    """An import has no repository-local resolution contract."""


ImportResolution: TypeAlias = ResolvedLocal | UnresolvedLocal | ExternalImport


@dataclass(frozen=True)
class _PathMapping:
    pattern: str
    targets: tuple[str, ...]
    base_directory: Path

    @property
    def specificity(self) -> tuple[bool, int, int, str]:
        """Sort exact rules first, then wildcard rules by longest prefix."""
        prefix = self.pattern.split("*", 1)[0]
        return (
            "*" in self.pattern,
            -len(prefix),
            -len(self.pattern.replace("*", "")),
            self.pattern,
        )

    def capture(self, specifier: str) -> str | None:
        """Return the wildcard capture, or an empty string for an exact match."""
        if "*" not in self.pattern:
            return "" if specifier == self.pattern else None
        prefix, suffix = self.pattern.split("*", 1)
        if not specifier.startswith(prefix) or not specifier.endswith(suffix):
            return None
        end = len(specifier) - len(suffix) if suffix else len(specifier)
        return specifier[len(prefix):end]


@dataclass(frozen=True)
class _CompilerConfig:
    path: Path
    base_url: Path | None = None
    path_mappings: tuple[_PathMapping, ...] = ()


@dataclass(frozen=True)
class _WorkspacePackage:
    name: str
    directory: Path
    manifest: dict[str, Any]


def _strip_jsonc_comments(text: str) -> str:
    """Strip JavaScript comments while preserving strings and line structure."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < len(text):
            marker = text[index + 1]
            if marker == "/":
                index += 2
                while index < len(text) and text[index] not in "\r\n":
                    index += 1
                continue
            if marker == "*":
                index += 2
                while index + 1 < len(text) and text[index:index + 2] != "*/":
                    if text[index] in "\r\n":
                        output.append(text[index])
                    index += 1
                index = min(len(text), index + 2)
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _strip_trailing_commas(text: str) -> str:
    """Remove JSONC trailing commas without altering comma-like string data."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _read_json_object(path: Path) -> dict[str, Any]:
    raw = _strip_trailing_commas(_strip_jsonc_comments(path.read_text(encoding="utf-8")))
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("top-level JSON value must be an object")
    return value


def _strip_source_extension(path_key: str) -> str:
    for extension in _SOURCE_EXTENSIONS:
        if path_key.endswith(extension):
            path_key = path_key[:-len(extension)]
            break
    if path_key.endswith(".d"):
        path_key = path_key[:-2]
    return path_key


def _as_string_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _export_targets(value: object) -> tuple[str, ...]:
    """Flatten package export conditions in deterministic preference order."""
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(target for item in value for target in _export_targets(item))
    if not isinstance(value, dict):
        return ()

    preferred = ("types", "source", "import", "default", "require")
    ordered_keys = [key for key in preferred if key in value]
    ordered_keys.extend(sorted(key for key in value if key not in ordered_keys))
    return tuple(
        target
        for key in ordered_keys
        for target in _export_targets(value[key])
    )


class TypeScriptModuleResolver:
    """Resolve imports against the exact source files represented in a graph."""

    def __init__(
        self,
        root: Path,
        module_by_pathkey: dict[str, str],
        source_files: list[Path],
    ) -> None:
        self.root = root.resolve()
        self.module_by_pathkey = module_by_pathkey
        self.source_files = tuple(source_files)
        self._config_cache: dict[Path, _CompilerConfig] = {}
        self._config_for_directory: dict[Path, tuple[_CompilerConfig, ...]] = {}
        self._path_mapping_index: dict[
            Path, tuple[dict[str, _PathMapping], tuple[_PathMapping, ...]]
        ] = {}
        self._configuration_errors: set[str] = set()
        self._root_config_paths = self._discover_root_config_paths()
        self._workspace_packages = self._discover_workspace_packages()

    @property
    def configuration_errors(self) -> tuple[str, ...]:
        return tuple(sorted(self._configuration_errors))

    def _relative_display(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def _record_configuration_error(self, path: Path, error: BaseException) -> None:
        message = " ".join(str(error).split())
        detail = f"{self._relative_display(path)}: {type(error).__name__}"
        if message:
            detail = f"{detail}: {message}"
        if detail not in self._configuration_errors:
            logger.warning("Ignoring TypeScript configuration: %s", detail)
            self._configuration_errors.add(detail)

    def _discover_root_config_paths(self) -> tuple[Path, ...]:
        candidates: list[Path] = []
        for name in (*_CONFIG_NAMES, "tsconfig.base.json"):
            candidate = self.root / name
            if candidate.is_file():
                candidates.append(candidate)
        for candidate in sorted(self.root.glob("tsconfig*.json")):
            if candidate not in candidates:
                candidates.append(candidate)
        return tuple(candidates)

    def _resolve_extends_path(self, config_path: Path, specifier: str) -> Path | None:
        if not specifier.startswith((".", "/")):
            # Package-based config inheritance needs Node's package resolver.
            self._record_configuration_error(
                config_path,
                ValueError(f"unsupported package config extends: {specifier}"),
            )
            return None
        target = Path(specifier)
        if not target.is_absolute():
            target = config_path.parent / target
        if target.is_dir():
            target = target / "tsconfig.json"
        elif target.suffix != ".json":
            target = target.with_suffix(".json")
        return target.resolve()

    def _load_config(self, path: Path, ancestors: frozenset[Path]) -> _CompilerConfig:
        path = path.resolve()
        cached = self._config_cache.get(path)
        if cached is not None:
            return cached
        if path in ancestors:
            self._record_configuration_error(path, ValueError("cyclic extends"))
            return _CompilerConfig(path=path)

        try:
            data = _read_json_object(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            self._record_configuration_error(path, error)
            config = _CompilerConfig(path=path)
            self._config_cache[path] = config
            return config

        inherited = _CompilerConfig(path=path)
        extends_values = _as_string_list(data.get("extends"))
        for extends_value in extends_values:
            parent_path = self._resolve_extends_path(path, extends_value)
            if parent_path is None:
                continue
            if not parent_path.is_file():
                self._record_configuration_error(parent_path, FileNotFoundError(parent_path))
                continue
            inherited = self._load_config(parent_path, ancestors | {path})

        compiler_options = data.get("compilerOptions")
        if not isinstance(compiler_options, dict):
            compiler_options = {}

        base_url = inherited.base_url
        raw_base_url = compiler_options.get("baseUrl")
        if isinstance(raw_base_url, str):
            base_url = (path.parent / raw_base_url).resolve()

        mappings = inherited.path_mappings
        raw_paths = compiler_options.get("paths")
        if isinstance(raw_paths, dict):
            mapping_base = base_url or path.parent
            mappings = tuple(
                sorted(
                    (
                        _PathMapping(
                            pattern=pattern,
                            targets=_as_string_list(targets),
                            base_directory=mapping_base,
                        )
                        for pattern, targets in raw_paths.items()
                        if isinstance(pattern, str) and _as_string_list(targets)
                    ),
                    key=lambda mapping: mapping.specificity,
                )
            )

        config = _CompilerConfig(path=path, base_url=base_url, path_mappings=mappings)
        self._config_cache[path] = config
        return config

    def _configs_for(self, importing: Path) -> tuple[_CompilerConfig, ...]:
        directory = importing.parent
        cached = self._config_for_directory.get(directory)
        if cached is not None:
            return cached

        configs: list[_CompilerConfig] = []
        current = directory
        while current == self.root or self.root in current.parents:
            found = False
            for name in _CONFIG_NAMES:
                candidate = current / name
                if candidate.is_file():
                    configs.append(self._load_config(candidate, frozenset()))
                    found = True
                    break
            if found or current == self.root:
                break
            current = current.parent

        if not configs:
            configs.extend(
                self._load_config(path, frozenset()) for path in self._root_config_paths
            )
        result = tuple(configs)
        self._config_for_directory[directory] = result
        return result

    def _safe_glob(self, pattern: str) -> list[Path]:
        normalized = pattern.removeprefix("./")
        if not normalized or normalized.startswith(("!", "/")):
            return []
        try:
            return sorted(self.root.glob(normalized))
        except (OSError, ValueError) as error:
            self._record_configuration_error(self.root / "package.json", error)
            return []

    def _manifest_paths(self) -> list[Path]:
        manifests: set[Path] = set()
        root_manifest = self.root / "package.json"
        if root_manifest.is_file():
            manifests.add(root_manifest)
            try:
                data = _read_json_object(root_manifest)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
                self._record_configuration_error(root_manifest, error)
                data = {}
            raw_workspaces = data.get("workspaces")
            if isinstance(raw_workspaces, dict):
                raw_workspaces = raw_workspaces.get("packages")
            for pattern in _as_string_list(raw_workspaces):
                for match in self._safe_glob(pattern):
                    manifest = match if match.name == "package.json" else match / "package.json"
                    if manifest.is_file():
                        manifests.add(manifest.resolve())

        # Explicit file lists may represent a package without a root workspace
        # declaration.  Its nearest manifest is still an authoritative local
        # package boundary.
        for source_file in self.source_files:
            current = source_file.parent
            while current == self.root or self.root in current.parents:
                manifest = current / "package.json"
                if manifest.is_file():
                    manifests.add(manifest.resolve())
                    break
                if current == self.root:
                    break
                current = current.parent
        return sorted(manifests)

    def _discover_workspace_packages(self) -> tuple[_WorkspacePackage, ...]:
        packages: list[_WorkspacePackage] = []
        seen_names: set[str] = set()
        source_files = set(self.source_files)
        for manifest_path in self._manifest_paths():
            try:
                manifest = _read_json_object(manifest_path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
                self._record_configuration_error(manifest_path, error)
                continue
            name = manifest.get("name")
            if not isinstance(name, str) or not name:
                continue
            directory = manifest_path.parent.resolve()
            if directory != self.root and not any(
                directory in source.parents for source in source_files
            ):
                continue
            if name in seen_names:
                self._record_configuration_error(
                    manifest_path,
                    ValueError(f"duplicate workspace package name: {name}"),
                )
                continue
            seen_names.add(name)
            packages.append(_WorkspacePackage(name, directory, manifest))
        return tuple(sorted(packages, key=lambda package: (-len(package.name), package.name)))

    def _lookup_path(self, target: Path) -> str | None:
        try:
            target = target.resolve()
            relative = target.relative_to(self.root)
        except (OSError, ValueError):
            return None
        key = _strip_source_extension(relative.as_posix()).rstrip("/")
        for candidate in (key, f"{key}/index"):
            module = self.module_by_pathkey.get(candidate)
            if module is not None:
                return module
        return None

    def _resolve_path_mappings(
        self,
        specifier: str,
        configs: tuple[_CompilerConfig, ...],
    ) -> tuple[str | None, bool]:
        for config in configs:
            indexed = self._path_mapping_index.get(config.path)
            if indexed is None:
                exact = {
                    mapping.pattern: mapping
                    for mapping in config.path_mappings
                    if "*" not in mapping.pattern
                }
                wildcard = tuple(
                    mapping for mapping in config.path_mappings if "*" in mapping.pattern
                )
                indexed = (exact, wildcard)
                self._path_mapping_index[config.path] = indexed
            exact, wildcard = indexed
            exact_mapping = exact.get(specifier)
            candidates = (exact_mapping,) if exact_mapping is not None else wildcard
            for mapping in candidates:
                capture = mapping.capture(specifier)
                if capture is None:
                    continue
                for target_pattern in mapping.targets:
                    target = target_pattern.replace("*", capture)
                    module = self._lookup_path(mapping.base_directory / target)
                    if module is not None:
                        return module, True
                # TypeScript chooses the most specific matching paths rule.  A
                # stale exact rule must stay visible rather than silently falling
                # through to a broader wildcard and fabricating confidence.
                return None, True
        return None, False

    def _workspace_export_candidates(
        self,
        package: _WorkspacePackage,
        subpath: str,
    ) -> list[Path]:
        candidates: list[Path] = []
        exports = package.manifest.get("exports")
        export_key = "." if not subpath else f"./{subpath}"
        export_value: object | None = None
        if isinstance(exports, dict) and any(str(key).startswith(".") for key in exports):
            if export_key in exports:
                export_value = exports[export_key]
            else:
                for key, value in exports.items():
                    if not isinstance(key, str) or "*" not in key:
                        continue
                    prefix, suffix = key.split("*", 1)
                    if export_key.startswith(prefix) and export_key.endswith(suffix):
                        end = len(export_key) - len(suffix) if suffix else len(export_key)
                        capture = export_key[len(prefix):end]
                        export_value = tuple(
                            target.replace("*", capture) for target in _export_targets(value)
                        )
                        break
        elif exports is not None and not subpath:
            export_value = exports

        for target in _export_targets(export_value):
            if target.startswith("."):
                candidates.append(package.directory / target)

        if not subpath:
            for field in ("types", "typings", "source", "module", "main"):
                manifest_target = package.manifest.get(field)
                if isinstance(manifest_target, str):
                    candidates.append(package.directory / manifest_target)
            candidates.extend((package.directory / "src" / "index", package.directory / "index"))
        else:
            candidates.extend(
                (package.directory / "src" / subpath, package.directory / subpath)
            )
        return candidates

    def _resolve_workspace(self, specifier: str) -> tuple[str | None, bool]:
        for package in self._workspace_packages:
            if specifier != package.name and not specifier.startswith(f"{package.name}/"):
                continue
            subpath = specifier[len(package.name):].removeprefix("/")
            for candidate in self._workspace_export_candidates(package, subpath):
                module = self._lookup_path(candidate)
                if module is not None:
                    return module, True
            return None, True
        return None, False

    def resolve(self, specifier: str, importing: Path) -> ImportResolution:
        """Classify and, when possible, resolve one import specifier."""
        if specifier.startswith("."):
            module = self._lookup_path(importing.parent / specifier)
            if module is not None:
                return ResolvedLocal(module, "relative")
            return UnresolvedLocal("relative import target is outside the parsed source set")
        if specifier.startswith("/"):
            module = self._lookup_path(Path(specifier))
            if module is not None:
                return ResolvedLocal(module, "absolute")
            return UnresolvedLocal("absolute import target is outside the parsed source set")

        configs = self._configs_for(importing)
        module, matched_paths = self._resolve_path_mappings(specifier, configs)
        if module is not None:
            return ResolvedLocal(module, "tsconfig_paths")

        # baseUrl is a lookup candidate, not proof that every bare dependency is
        # local.  If no source matches, Node/package resolution may still make it
        # a perfectly valid external dependency.
        for config in configs:
            if config.base_url is None:
                continue
            module = self._lookup_path(config.base_url / specifier)
            if module is not None:
                return ResolvedLocal(module, "base_url")

        workspace_module, matched_workspace = self._resolve_workspace(specifier)
        if workspace_module is not None:
            return ResolvedLocal(workspace_module, "workspace")
        if matched_paths:
            return UnresolvedLocal("matched tsconfig paths but no parsed source target exists")
        if matched_workspace:
            return UnresolvedLocal("matched workspace package but no parsed source target exists")
        return ExternalImport()

    def summary(
        self,
        resolutions: dict[Path, dict[str, ImportResolution]],
        linked_local: set[tuple[Path, str]],
    ) -> dict[str, Any]:
        """Build deterministic graph/report metadata for discovered imports."""
        resolved: list[tuple[Path, str, ResolvedLocal]] = []
        unresolved: list[tuple[Path, str, UnresolvedLocal]] = []
        external = 0
        resolved_by: dict[str, int] = {}
        for importing, by_specifier in resolutions.items():
            for specifier, result in by_specifier.items():
                if isinstance(result, ResolvedLocal):
                    resolved.append((importing, specifier, result))
                    resolved_by[result.method] = resolved_by.get(result.method, 0) + 1
                elif isinstance(result, UnresolvedLocal):
                    unresolved.append((importing, specifier, result))
                else:
                    external += 1

        resolved_keys = {(path, specifier) for path, specifier, _ in resolved}
        linked = len(resolved_keys & linked_local)
        unlinked = sorted(
            resolved_keys - linked_local,
            key=lambda item: (self._relative_display(item[0]), item[1]),
        )
        local_total = len(resolved) + len(unresolved)
        resolution_rate = len(resolved) / local_total if local_total else 1.0
        edge_rate = linked / len(resolved) if resolved else 1.0
        configuration_errors = list(self.configuration_errors)
        has_config_sensitive_import = any(
            not specifier.startswith((".", "/"))
            for by_specifier in resolutions.values()
            for specifier in by_specifier
        )
        configuration_incomplete = bool(configuration_errors and has_config_sensitive_import)
        metrics_qualified = bool(unresolved or unlinked or configuration_incomplete)

        unresolved_details = [
            {
                "file_path": self._relative_display(path),
                "specifier": specifier,
                "reason": result.reason,
            }
            for path, specifier, result in sorted(
                unresolved,
                key=lambda item: (self._relative_display(item[0]), item[1]),
            )[:_MAX_DIAGNOSTICS]
        ]
        unlinked_details = [
            {
                "file_path": self._relative_display(path),
                "specifier": specifier,
                "reason": "module resolved but no imported local symbol edge was emitted",
            }
            for path, specifier in unlinked[:_MAX_DIAGNOSTICS]
        ]

        return {
            "import_specifiers": len(resolved) + len(unresolved) + external,
            "resolved_local": len(resolved),
            "external": external,
            "unresolved_local": len(unresolved),
            "linked_local": linked,
            "unlinked_local": len(unlinked),
            "local_resolution_rate": round(resolution_rate, 4),
            "local_edge_rate": round(edge_rate, 4),
            "resolved_by": dict(sorted(resolved_by.items())),
            "configuration_errors": configuration_errors[:_MAX_DIAGNOSTICS],
            "configuration_errors_truncated": max(
                0, len(configuration_errors) - _MAX_DIAGNOSTICS
            ),
            "configuration_errors_affect_resolution": configuration_incomplete,
            "unresolved_local_imports": unresolved_details,
            "unresolved_local_imports_truncated": max(
                0, len(unresolved) - _MAX_DIAGNOSTICS
            ),
            "unlinked_local_imports": unlinked_details,
            "unlinked_local_imports_truncated": max(0, len(unlinked) - _MAX_DIAGNOSTICS),
            "metrics_qualified": metrics_qualified,
        }
