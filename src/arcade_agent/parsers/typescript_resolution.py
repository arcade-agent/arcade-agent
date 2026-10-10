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
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeAlias

logger = logging.getLogger(__name__)

_SOURCE_EXTENSIONS = (".tsx", ".ts", ".mts", ".cts", ".jsx", ".js", ".mjs", ".cjs")
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
    path_mappings: tuple[_PathMapping, ...] | None = None
    files: tuple[Path, ...] | None = None
    include: tuple[tuple[Path, str], ...] | None = None
    exclude: tuple[tuple[Path, str], ...] | None = None
    references: tuple[Path, ...] = ()


@dataclass(frozen=True)
class _WorkspacePackage:
    name: str
    directory: Path
    manifest: dict[str, Any]


def is_source_import(specifier: str) -> bool:
    """Keep package names, but omit explicit asset paths from source coverage."""
    path = specifier.split("?", 1)[0].split("#", 1)[0]
    suffix = Path(path).suffix.lower()
    if not suffix or suffix in _SOURCE_EXTENSIONS:
        return True
    return suffix not in {
        ".css", ".scss", ".sass", ".less", ".json", ".svg", ".png", ".jpg",
        ".jpeg", ".gif", ".webp", ".avif", ".ico", ".woff", ".woff2", ".ttf",
        ".eot", ".wasm", ".html", ".txt", ".md", ".yaml", ".yml", ".mp3",
        ".wav", ".mp4",
        # Framework single-file components and documents are compiled by their
        # own toolchains; TypeScript only sees them through ambient shims.
        ".vue", ".svelte", ".astro", ".graphql", ".gql", ".mdx",
    }


def _is_non_source_file(target: Path) -> bool:
    """Whether an import names an existing file that is not a script module."""
    suffix = target.suffix.lower()
    if not suffix or suffix in _SOURCE_EXTENSIONS:
        return False
    try:
        return target.is_file()
    except OSError:
        return False


def _matches_source(path: Path, directory: Path, pattern: str) -> bool:
    try:
        relative = path.relative_to(directory).as_posix()
    except ValueError:
        return False
    return _source_pattern_regex(pattern).fullmatch(relative) is not None


@lru_cache(maxsize=4096)
def _source_pattern_regex(pattern: str) -> re.Pattern[str]:
    pattern = pattern.removeprefix("./").rstrip("/")
    if not any(char in pattern for char in "*?") and not Path(pattern).suffix:
        pattern += "/**/*"
    expression = re.escape(pattern)
    expression = expression.replace(r"\*\*/", "(?:.*/)?")
    expression = expression.replace(r"\*\*", ".*")
    expression = expression.replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.compile(expression)


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
    raw = _strip_trailing_commas(_strip_jsonc_comments(path.read_text(encoding="utf-8-sig")))
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("top-level JSON value must be an object")
    return value


def _as_string_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _resolve_path(path: Path) -> Path:
    """Reject symlink loops while allowing missing module/config suffixes."""
    resolved = path.resolve()
    try:
        # Non-strict resolution suppresses symlink-loop errors in Python 3.13.
        # A single stat exposes those errors while allowing absent lookup suffixes.
        resolved.stat()
    except FileNotFoundError:
        pass
    return resolved


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
        self._config_for_file: dict[Path, tuple[_CompilerConfig, ...]] = {}
        self._config_candidates_for_directory: dict[Path, tuple[_CompilerConfig, ...]] = {}
        self._path_mapping_index: dict[
            Path, tuple[dict[str, _PathMapping], tuple[_PathMapping, ...]]
        ] = {}
        self._configuration_errors: set[str] = set()
        self._configuration_notes: set[str] = set()
        self._path_cache: dict[Path, str | None] = {}
        self._duplicate_workspace_names: set[str] = set()
        self._root_config_paths = self._discover_root_config_paths()
        self._workspace_packages = self._discover_workspace_packages()

    @property
    def configuration_errors(self) -> tuple[str, ...]:
        return tuple(sorted(self._configuration_errors))

    @property
    def configuration_notes(self) -> tuple[str, ...]:
        return tuple(sorted(self._configuration_notes))

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
            # Shared bases (@tsconfig/node20, @vue/tsconfig, ...) set compiler
            # flags rather than local aliases, so the gap is informational:
            # an alias that only the package defines still surfaces as an
            # external import, not as a fabricated local edge.
            note = (f"{self._relative_display(config_path)}: "
                    f"package config extends not followed: {specifier}")
            if note not in self._configuration_notes:
                logger.info("TypeScript configuration: %s", note)
                self._configuration_notes.add(note)
            return None
        return self._resolve_config_path(config_path, specifier)

    def _resolve_config_path(self, config_path: Path, specifier: str) -> Path | None:
        target = Path(specifier)
        if not target.is_absolute():
            target = config_path.parent / target
        try:
            target = _resolve_path(target)
        except (OSError, RuntimeError) as error:
            self._record_configuration_error(config_path, error)
            return None
        if not target.is_relative_to(self.root):
            self._record_configuration_error(config_path, ValueError("config outside project root"))
            return None
        if target.is_dir():
            target = target / "tsconfig.json"
        elif target.suffix not in (".json", ".jsonc"):
            target = target.with_suffix(".json")
        try:
            target = _resolve_path(target)
        except (OSError, RuntimeError) as error:
            self._record_configuration_error(config_path, error)
            return None
        if not target.is_relative_to(self.root):
            self._record_configuration_error(config_path, ValueError("config outside project root"))
            return None
        return target

    def _load_config(self, path: Path, ancestors: frozenset[Path]) -> _CompilerConfig:
        cached = self._config_cache.get(path)
        if cached is not None:
            return cached
        try:
            path = _resolve_path(path)
        except (OSError, RuntimeError) as error:
            self._record_configuration_error(path, error)
            return _CompilerConfig(path=path)
        if not path.is_relative_to(self.root):
            self._record_configuration_error(path, ValueError("config outside project root"))
            return _CompilerConfig(path=path)
        cached = self._config_cache.get(path)
        if cached is not None:
            return cached
        data_by_path: dict[Path, dict[str, Any]] = {}
        parents_by_path: dict[Path, list[Path]] = {}
        active = set(ancestors)
        stack = [(path, False)]
        while stack:
            current, complete = stack.pop()
            if current in self._config_cache:
                continue
            if complete:
                inherited = _CompilerConfig(path=current)
                for parent_path in parents_by_path[current]:
                    parent = self._config_cache.get(parent_path)
                    if parent is None:
                        continue
                    inherited = _CompilerConfig(
                        path=current,
                        base_url=(parent.base_url if parent.base_url is not None
                                  else inherited.base_url),
                        path_mappings=(parent.path_mappings if parent.path_mappings is not None
                                       else inherited.path_mappings),
                        files=parent.files if parent.files is not None else inherited.files,
                        include=parent.include if parent.include is not None else inherited.include,
                        exclude=parent.exclude if parent.exclude is not None else inherited.exclude,
                    )
                self._config_cache[current] = self._compile_config(
                    current, data_by_path.pop(current), inherited,
                )
                active.discard(current)
                continue
            if current in active:
                self._record_configuration_error(current, ValueError("cyclic extends"))
                continue
            try:
                data = _read_json_object(current)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
                self._record_configuration_error(current, error)
                self._config_cache[current] = _CompilerConfig(path=current)
                continue
            data_by_path[current] = data
            parents = []
            for specifier in _as_string_list(data.get("extends")):
                target = self._resolve_extends_path(current, specifier)
                if target is None:
                    continue
                if not target.is_file():
                    self._record_configuration_error(target, FileNotFoundError(target))
                    continue
                parents.append(target)
            parents_by_path[current] = parents
            active.add(current)
            stack.append((current, True))
            stack.extend((parent, False) for parent in reversed(parents))
        return self._config_cache.get(path, _CompilerConfig(path=path))

    def _compile_config(
        self, path: Path, data: dict[str, Any], inherited: _CompilerConfig,
    ) -> _CompilerConfig:

        compiler_options = data.get("compilerOptions")
        if not isinstance(compiler_options, dict):
            compiler_options = {}

        base_url = inherited.base_url
        raw_base_url = compiler_options.get("baseUrl")
        if isinstance(raw_base_url, str):
            if "${configDir}" in raw_base_url:
                self._record_configuration_error(path, ValueError("unsupported ${configDir}"))
            try:
                base_url = _resolve_path(path.parent / raw_base_url)
            except (OSError, RuntimeError) as error:
                self._record_configuration_error(path, error)
                return _CompilerConfig(path=path)

        mappings = inherited.path_mappings
        raw_paths = compiler_options.get("paths")
        if isinstance(raw_paths, dict):
            if any("${configDir}" in target for targets in raw_paths.values()
                   for target in _as_string_list(targets)):
                self._record_configuration_error(path, ValueError("unsupported ${configDir}"))
            mappings = tuple(
                sorted(
                    (
                        _PathMapping(
                            pattern=pattern,
                            targets=_as_string_list(targets),
                            base_directory=path.parent,
                        )
                        for pattern, targets in raw_paths.items()
                        if isinstance(pattern, str) and _as_string_list(targets)
                    ),
                    key=lambda mapping: mapping.specificity,
                )
            )

        files = inherited.files
        if "files" in data:
            resolved_files = []
            for value in _as_string_list(data["files"]):
                try:
                    resolved_files.append(_resolve_path(path.parent / value))
                except (OSError, RuntimeError) as error:
                    self._record_configuration_error(path, error)
            files = tuple(resolved_files)
        include = inherited.include
        if "include" in data:
            include = tuple((path.parent, value) for value in _as_string_list(data["include"]))
        exclude = inherited.exclude
        if "exclude" in data:
            exclude = tuple((path.parent, value) for value in _as_string_list(data["exclude"]))
        references = []
        raw_references = data.get("references", [])
        if isinstance(raw_references, list):
            for reference in raw_references:
                if isinstance(reference, dict) and isinstance(reference.get("path"), str):
                    target = self._resolve_config_path(path, reference["path"])
                    if target is not None:
                        references.append(target)
        config = _CompilerConfig(
            path=path, base_url=base_url, path_mappings=mappings,
            files=files, include=include, exclude=exclude, references=tuple(references),
        )
        return config

    def _configs_for(self, importing: Path) -> tuple[_CompilerConfig, ...]:
        cached = self._config_for_file.get(importing)
        if cached is not None:
            return cached
        configs = []
        for config in self._config_candidates_for(importing.parent):
            if config.files is not None and importing in config.files:
                configs.append(config)
                continue
            include = config.include
            if include is None:
                include = () if config.files is not None else ((config.path.parent, "**/*"),)
            excluded = any(
                _matches_source(importing, origin, pattern)
                for origin, pattern in config.exclude or ()
            )
            if not excluded and any(
                _matches_source(importing, origin, pattern) for origin, pattern in include
            ):
                configs.append(config)
        result = tuple(configs)
        self._config_for_file[importing] = result
        return result

    def _config_candidates_for(self, directory: Path) -> tuple[_CompilerConfig, ...]:
        cached = self._config_candidates_for_directory.get(directory)
        if cached is not None:
            return cached

        candidates: list[Path] = []
        current = directory
        while current == self.root or self.root in current.parents:
            found = False
            for name in _CONFIG_NAMES:
                candidate = current / name
                if candidate.is_file():
                    candidates.append(candidate)
                    found = True
                    break
            if found or current == self.root:
                break
            current = current.parent

        if not candidates:
            candidates.extend(self._root_config_paths)
        elif candidates[0].parent != self.root:
            nearest = self._load_config(candidates[0], frozenset())
            for name in _CONFIG_NAMES:
                candidate = self.root / name
                if (candidate.is_file() and nearest.base_url is None
                        and nearest.path_mappings is None and not nearest.references):
                    root_config = self._load_config(candidate, frozenset())
                    if root_config.references:
                        candidates.append(candidate)

        configs: list[_CompilerConfig] = []
        seen: set[Path] = set()
        stack = list(reversed(candidates))
        while stack:
            path = stack.pop()
            if path in seen:
                continue
            seen.add(path)
            config = self._load_config(path, frozenset())
            configs.append(config)
            stack.extend(reversed(config.references))
        result = tuple(configs)
        self._config_candidates_for_directory[directory] = result
        return result

    def _safe_glob(self, pattern: str) -> list[Path]:
        normalized = pattern.removeprefix("./")
        if not normalized or normalized.startswith(("!", "/")) or ".." in Path(normalized).parts:
            return []
        try:
            return sorted(path for path in self.root.glob(normalized)
                          if "node_modules" not in path.relative_to(self.root).parts
                          and path.resolve().is_relative_to(self.root))
        except (OSError, RuntimeError, ValueError) as error:
            self._record_configuration_error(self.root / "package.json", error)
            return []

    def _manifest_paths(self) -> list[Path]:
        manifests: set[Path] = set()
        root_manifest = self.root / "package.json"
        if root_manifest.is_file():
            if not root_manifest.resolve().is_relative_to(self.root):
                self._record_configuration_error(
                    root_manifest, ValueError("manifest outside project root"),
                )
                return []
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
                    if manifest.is_file() and manifest.resolve().is_relative_to(self.root):
                        manifests.add(manifest.resolve())

        return sorted(manifests)

    def _discover_workspace_packages(self) -> tuple[_WorkspacePackage, ...]:
        packages: list[_WorkspacePackage] = []
        seen_names: set[str] = set()
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
            if name in seen_names:
                self._duplicate_workspace_names.add(name)
                self._record_configuration_error(
                    manifest_path,
                    ValueError(f"duplicate workspace package name: {name}"),
                )
                continue
            seen_names.add(name)
            packages.append(_WorkspacePackage(name, directory, manifest))
        return tuple(sorted(
            (package for package in packages
             if package.name not in self._duplicate_workspace_names),
            key=lambda package: (-len(package.name), package.name),
        ))

    def _lookup_path(self, target: Path) -> str | None:
        if target in self._path_cache:
            return self._path_cache[target]
        original = target
        try:
            target = _resolve_path(target)
            relative = target.relative_to(self.root)
        except (OSError, RuntimeError, ValueError):
            self._path_cache[original] = None
            return None
        key = relative.as_posix().rstrip("/")
        suffix = target.suffix
        substitutions = {
            ".js": (".ts", ".tsx", ".d.ts", ".js", ".jsx"),
            ".jsx": (".tsx", ".d.ts", ".jsx"),
            ".mjs": (".mts", ".d.mts", ".mjs"),
            ".cjs": (".cts", ".d.cts", ".cjs"),
        }
        if suffix in _SOURCE_EXTENSIONS:
            base = key[:-len(suffix)]
            extensions = substitutions.get(suffix, (suffix,))
            candidates = [base + extension for extension in extensions]
        else:
            extensions = (".ts", ".tsx", ".d.ts", ".js", ".jsx")
            candidates = [key + extension for extension in extensions]
            candidates.extend(f"{key}/index{extension}" for extension in extensions)
        for candidate in candidates:
            module = self.module_by_pathkey.get(candidate)
            if module is not None:
                self._path_cache[original] = module
                return module
            source = self.root / candidate
            if source.is_symlink():
                module = self._lookup_path(source)
                if module is not None:
                    self._path_cache[original] = module
                    return module
        self._path_cache[original] = None
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
                    for mapping in config.path_mappings or ()
                    if "*" not in mapping.pattern
                }
                wildcard = tuple(
                    mapping for mapping in config.path_mappings or () if "*" in mapping.pattern
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
                    module = self._lookup_path((config.base_url or mapping.base_directory) / target)
                    if module is not None:
                        return module, True
                if mapping.pattern == "*":
                    # A bare catch-all with no source target falls back to
                    # node_modules in tsc, so it says nothing about locality.
                    break
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

        if exports is not None:
            return candidates

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
        if any(specifier == name or specifier.startswith(f"{name}/")
               for name in self._duplicate_workspace_names):
            return None, True
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
        if not is_source_import(specifier):
            return ExternalImport()
        if specifier.startswith("#"):
            self._record_configuration_error(
                importing, ValueError("unsupported package.json #imports"),
            )
            return UnresolvedLocal("package.json #imports are not supported")
        if specifier.startswith("."):
            target = importing.parent / specifier
            module = self._lookup_path(target)
            if module is not None:
                return ResolvedLocal(module, "relative")
            if _is_non_source_file(target):
                return ExternalImport()
            return UnresolvedLocal("relative import target is outside the parsed source set")
        if specifier.startswith("/"):
            module = self._lookup_path(Path(specifier))
            if module is not None:
                return ResolvedLocal(module, "absolute")
            if _is_non_source_file(Path(specifier)):
                return ExternalImport()
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
        metrics_qualified = bool(unresolved or configuration_incomplete)

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
            "configuration_notes": list(self.configuration_notes)[:_MAX_DIAGNOSTICS],
            "unresolved_local_imports": unresolved_details,
            "unresolved_local_imports_truncated": max(
                0, len(unresolved) - _MAX_DIAGNOSTICS
            ),
            "unlinked_local_imports": unlinked_details,
            "unlinked_local_imports_truncated": max(0, len(unlinked) - _MAX_DIAGNOSTICS),
            "metrics_qualified": metrics_qualified,
        }
