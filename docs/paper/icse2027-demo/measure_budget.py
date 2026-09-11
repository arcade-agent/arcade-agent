#!/usr/bin/env python3
"""Reproduce the token figures reported in Sections III and IV.

    python3 -m venv .venv && .venv/bin/pip install -e ".[languages]"
    .venv/bin/python docs/paper/icse2027-demo/measure_budget.py

Run from the arcade-agent repository root. Requires Python >= 3.12.
Every token count is the package's own estimate (~4 characters per token).
"""

import json
import pathlib
import sys

from arcade_agent.budget import estimate_tokens, truncate_result
from arcade_agent.serialization import architecture_to_dict, graph_to_dict
from arcade_agent.tools.context_for_task import context_for_task
from arcade_agent.tools.parse import parse
from arcade_agent.tools.recover import recover
from arcade_agent.tools.summarize import summarize

SRC = "src"
TASK = "add a Ruby language parser"


def main() -> int:
    if not pathlib.Path(SRC).is_dir():
        print(f"error: run from the repository root ({SRC}/ not found)", file=sys.stderr)
        return 1

    graph = parse(SRC, language="python")
    arch = recover(graph, algorithm="pkg")
    print(
        f"parsed: {graph.num_entities} entities, {graph.num_edges} edges, "
        f"{len(arch.components)} components"
    )

    # Section III: the unreduced payload and the budget ladder.
    full = {"graph": graph_to_dict(graph), "architecture": architecture_to_dict(arch)}
    base = estimate_tokens(full)
    print(f"\nunreduced graph + architecture: {base} tokens")
    for budget in (16000, 8000, 4000):
        reduced = truncate_result(full, budget)
        print(
            f"  max_tokens={budget:>6} -> {estimate_tokens(reduced):>6} tokens  "
            f"graph keys={sorted(reduced.get('graph', {}))}"
        )

    # Section IV: summarize vs. reading the source.
    summary = summarize(SRC, language="python")
    raw = sum(len(p.read_text(errors="ignore")) for p in pathlib.Path(SRC).rglob("*.py"))
    print(f"\nsummarize(): {estimate_tokens(summary)} tokens")
    print(f"reading {SRC}/ directly: ~{raw // 4} tokens")

    # Section IV: task-shaped context selection.
    ctx = context_for_task(graph, TASK, architecture=arch)
    print(f"\ncontext_for_task({TASK!r}): {estimate_tokens(ctx)} tokens, {ctx['num_files']} files")
    for entry in ctx["files"][:4]:
        print(f"  {entry['file_path']:<45} {entry['primary_role']:<14} score={entry['score']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
