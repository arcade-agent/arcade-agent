"""Incremental parsing: a content-hash-keyed cache for per-file extraction.

The expensive part of parsing a large repo is the per-file AST walk (Pass 1).
Pass 1 depends only on a file's content and its path (which fixes its module
name) — never on other files — so its result can be cached and reused as long as
the file is byte-identical. On a re-run, only changed files are re-extracted; the
link pass (Pass 2) always runs in full, so edges are recomputed every time and
can never go stale.

Keyed by (absolute path + content) hash — robust to mtime-only changes from
`git checkout` / `touch`, which the older mtime-based cache treated as changes.

Prototype: currently wired for the Python parser
(`PythonParser.parse_incremental`). The same FileFacts shape applies to the other
two-pass parsers, so this generalizes.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path

from arcade_agent.parsers.graph import Entity

logger = logging.getLogger(__name__)

_SKIP = {"__skip__": True}  # marker: this file contributes no entity

# Bump when per-file extraction changes, so cached facts from an older
# extractor are not reused for byte-identical files.
_EXTRACT_VERSION = "2"


def file_key(path: Path) -> str:
    """SHA-256 over the file's absolute path + content. Path is included because
    extraction (module name, package) depends on where the file lives, so two
    byte-identical files at different paths must not share a cache entry."""
    h = hashlib.sha256()
    h.update(f"extract:{_EXTRACT_VERSION}".encode())
    h.update(b"\0")
    h.update(str(path.resolve()).encode())
    h.update(b"\0")
    h.update(path.read_bytes())
    return h.hexdigest()


def _facts_to_json(ff) -> dict:
    return {
        "rel_path": ff.rel_path,
        "package": ff.package,
        "entities": {fqn: asdict(e) for fqn, e in ff.entities.items()},
        "file_imports": ff.file_imports,
        "refs": {fqn: sorted(r) for fqn, r in ff.refs.items()},
    }


def _facts_from_json(d: dict):
    from arcade_agent.parsers.python import FileFacts
    return FileFacts(
        rel_path=d["rel_path"],
        package=d["package"],
        entities={fqn: Entity(**ed) for fqn, ed in d["entities"].items()},
        file_imports=d["file_imports"],
        refs={fqn: set(r) for fqn, r in d["refs"].items()},
    )


class ExtractCache:
    """On-disk cache of per-file Pass-1 extraction, keyed by content hash.

    Stats (`reused` / `extracted`) let callers report the warm-cache hit rate.
    """

    def __init__(self, project_root: str | Path):
        self.dir = Path(project_root) / ".arcade-cache" / "extract"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.stats = {"reused": 0, "extracted": 0}

    def get_or_extract(self, path: Path, root: Path, extractor):
        """Return cached FileFacts for `path` (or None if it has no entity),
        re-extracting with `extractor(path, root)` only on a content change."""
        try:
            key = file_key(path)
        except OSError:
            return extractor(path, root)
        cache_file = self.dir / f"{key}.json"

        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                self.stats["reused"] += 1
                if data.get("__skip__"):
                    return None
                return _facts_from_json(data)
            except (json.JSONDecodeError, KeyError, TypeError):
                logger.warning("Corrupt extract cache %s, re-extracting", cache_file.name)
                cache_file.unlink(missing_ok=True)

        ff = extractor(path, root)
        self.stats["extracted"] += 1
        cache_file.write_text(json.dumps(_SKIP if ff is None else _facts_to_json(ff)))
        return ff
