#!/usr/bin/env python3
"""Reproduce the token figures in Sections III and IV.

    pip install "arcade-agent[languages]" tiktoken
    python docs/paper/icse2027-demo/measure_budget.py --repos <dir-with-clones>

Run from the arcade-agent repository root. ``--repos`` holds shallow clones of
the pinned external repositories listed in REPOS (clone commands are printed
when one is missing).

Token counts use tiktoken's ``o200k_base`` encoding, a real BPE tokenizer.
Claude's tokenizer is not public; ``--calibrate`` sends samples of each payload
kind through ``claude -p`` and reports Claude's input-token count relative to
o200k_base, which is what the paper's footnote quotes.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import tiktoken

from arcade_agent.budget import truncate_result
from arcade_agent.serialization import architecture_to_dict, graph_to_dict
from arcade_agent.tools.context_for_task import context_for_task
from arcade_agent.tools.parse import parse
from arcade_agent.tools.recover import recover
from arcade_agent.tools.summarize import summarize

ENC = tiktoken.get_encoding("o200k_base")

# name -> (git URL, tag, source root inside the clone, language, file suffix)
REPOS: dict[str, tuple[str, str, str, str, str]] = {
    "click": ("https://github.com/pallets/click.git", "8.1.7", "src/click", "python", ".py"),
    "HikariCP": ("https://github.com/brettwooldridge/HikariCP.git", "HikariCP-6.3.0",
                 "src/main/java", "java", ".java"),
    "disruptor": ("https://github.com/LMAX-Exchange/disruptor.git", "4.0.0",
                  "src/main/java", "java", ".java"),
    "caffeine": ("https://github.com/ben-manes/caffeine.git", "v3.2.0",
                 "caffeine/src/main/java", "java", ".java"),
}
SELF_TASK = "add a Ruby language parser"
BUDGET = 8000


def tokens(obj: Any) -> int:
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return len(ENC.encode(text, disallowed_special=()))


def source_text(root: pathlib.Path, suffix: str) -> str:
    return "\n".join(p.read_text(errors="ignore") for p in sorted(root.rglob(f"*{suffix}")))


def measure(name: str, root: pathlib.Path, language: str, suffix: str) -> dict[str, Any]:
    graph = parse(str(root), language=language, use_cache=False)
    arch = recover(graph, algorithm="pkg")
    full = {"graph": graph_to_dict(graph), "architecture": architecture_to_dict(arch)}
    return {
        "repo": name,
        "language": language,
        "files": len(list(root.rglob(f"*{suffix}"))),
        "entities": graph.num_entities,
        "edges": graph.num_edges,
        "components": len(arch.components),
        "source_tokens": tokens(source_text(root, suffix)),
        "full_tokens": tokens(full),
        "budget_tokens": tokens(truncate_result(full, BUDGET)),
        "summarize_tokens": tokens(summarize(str(root), language=language)),
    }


def calibrate(samples: dict[str, str]) -> dict[str, float]:
    """Claude input tokens / o200k tokens for each sample, via `claude -p` usage."""

    def claude_tokens(text: str) -> int:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE",)}
        out = subprocess.run(
            ["claude", "-p", "Reply with OK.", "--model", "haiku", "--output-format", "json",
             "--setting-sources", "project", "--strict-mcp-config",
             "--no-session-persistence", "--tools", ""],
            input=text, capture_output=True, text=True, env=env, check=True)
        usage = json.loads(out.stdout)["usage"]
        return int(usage["input_tokens"] + usage.get("cache_creation_input_tokens", 0)
                   + usage.get("cache_read_input_tokens", 0))

    empty = claude_tokens("")
    return {kind: round((claude_tokens(text) - empty) / tokens(text), 3)
            for kind, text in samples.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", type=pathlib.Path, required=True)
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()
    if not pathlib.Path("src/arcade_agent").is_dir():
        print("error: run from the arcade-agent repository root", file=sys.stderr)
        return 1

    rows = [measure("arcade-agent", pathlib.Path("src"), "python", ".py")]
    for name, (url, tag, sub, language, suffix) in REPOS.items():
        root = args.repos / name / sub
        if not root.is_dir():
            print(f"missing {root}: git clone --depth 1 --branch {tag} {url} "
                  f"{args.repos / name}", file=sys.stderr)
            return 1
        rows.append(measure(name, root, language, suffix))

    graph = parse("src", language="python", use_cache=False)
    arch = recover(graph, algorithm="pkg")
    ctx = context_for_task(graph, SELF_TASK, architecture=arch)
    result: dict[str, Any] = {
        "tokenizer": "tiktoken o200k_base",
        "budget": BUDGET,
        "rows": rows,
        "context_for_task": {
            "task": SELF_TASK,
            "num_files": ctx["num_files"],
            "tokens": tokens(ctx),
            "top": [f["file_path"] for f in ctx["files"][:4]],
        },
    }
    if args.calibrate:
        full = {"graph": graph_to_dict(graph), "architecture": architecture_to_dict(arch)}
        samples = {
            "json": json.dumps(full)[:60000],
            "python": source_text(pathlib.Path("src"), ".py")[:60000],
            "java": source_text(args.repos / "HikariCP" / "src/main/java", ".java")[:60000],
        }
        result["claude_over_o200k"] = calibrate(samples)

    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
