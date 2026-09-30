"""Parse result caching based on file modification times."""

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from arcade_agent.parsers.graph import DependencyGraph
from arcade_agent.serialization import dict_to_graph, graph_to_dict

logger = logging.getLogger(__name__)

_CACHE_DIR = ".arcade-cache"
_GRAPH_CACHE_SCHEMA_VERSION = "3"  # Java coverage and conservative unknown declarations.


def _cache_dir(project_root: Path) -> Path:
    """Return the cache directory for a project."""
    return project_root / _CACHE_DIR


_SOURCE_SUFFIXES = {
    ".java", ".py", ".c", ".cpp", ".h", ".hpp", ".ts", ".tsx", ".js", ".jsx",
    ".go", ".kt", ".kts", ".rs",
}


def cache_key(
    source_path: str,
    language: str | None,
    files: list[str] | None,
    exclude_tests: bool = True,
) -> str:
    """Compute a cache key from source path, language, and file mtimes.

    The key is a SHA-256 hash of the sorted file paths and their modification
    times, ensuring the cache is automatically invalidated when any source file
    changes.

    Args:
        source_path: Root directory of the project.
        language: Language being parsed (or None for auto-detect).
        files: Specific files to parse, or None to discover all.
        exclude_tests: Whether inline test code is excluded. Parsers that honor
            it (Rust) produce a different graph for the same file list, so it
            must take part in the key.

    Returns:
        A hex digest string usable as a cache filename.
    """
    root = Path(source_path).resolve()
    hasher = hashlib.sha256()
    hasher.update(f"graph-schema:{_GRAPH_CACHE_SCHEMA_VERSION}".encode())
    hasher.update(str(root).encode())
    hasher.update((language or "auto").encode())
    hasher.update(b"tests:excluded" if exclude_tests else b"tests:included")

    if files:
        file_paths = set(files)
    else:
        # Hash all source-like files under root
        file_paths = {
            str(f) for f in root.rglob("*") if f.is_file() and f.suffix in _SOURCE_SUFFIXES
        }

    # Rust module layout and crate names come from Cargo manifests, so editing
    # one changes the graph without touching any .rs file.
    tracks_rust = language is None or "rust" in language or any(
        fp.endswith(".rs") for fp in file_paths
    )
    if tracks_rust:
        file_paths.update(
            str(manifest) for manifest in root.rglob("Cargo.toml") if manifest.is_file()
        )

    for fp in sorted(file_paths):
        p = Path(fp)
        hasher.update(fp.encode())
        if p.exists():
            hasher.update(str(p.stat().st_mtime_ns).encode())

    return hasher.hexdigest()


def get_cached_graph(project_root: str, key: str) -> DependencyGraph | None:
    """Retrieve a cached DependencyGraph if it exists.

    Args:
        project_root: Root directory of the project (cache lives here).
        key: Cache key from cache_key().

    Returns:
        The cached DependencyGraph, or None on cache miss.
    """
    cache_file = _cache_dir(Path(project_root)) / f"{key}.json"
    if not cache_file.exists():
        return None

    try:
        data = json.loads(cache_file.read_text())
        graph = dict_to_graph(data)
        logger.info("Cache hit for %s (%d entities)", key[:12], graph.num_entities)
        return graph
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("Corrupt cache file %s, removing", cache_file)
        cache_file.unlink(missing_ok=True)
        return None


def put_cached_graph(project_root: str, key: str, graph: DependencyGraph) -> None:
    """Store a DependencyGraph in the cache.

    Args:
        project_root: Root directory of the project.
        key: Cache key from cache_key().
        graph: The graph to cache.
    """
    cache_dir = _cache_dir(Path(project_root))
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{key}.json"
    payload = json.dumps(graph_to_dict(graph), indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=cache_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(payload)
        os.replace(tmp_name, cache_file)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    logger.info("Cached parse result %s (%d entities)", key[:12], graph.num_entities)


def invalidate_cache(project_root: str) -> int:
    """Remove all cached parse results for a project.

    Args:
        project_root: Root directory of the project.

    Returns:
        Number of cache files removed.
    """
    cache_dir = _cache_dir(Path(project_root))
    if not cache_dir.exists():
        return 0
    removed = 0
    for f in cache_dir.glob("*.json"):
        f.unlink()
        removed += 1
    logger.info("Invalidated %d cache entries for %s", removed, project_root)
    return removed
