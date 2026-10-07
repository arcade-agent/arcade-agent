#!/usr/bin/env python3
"""Summarize how agents used the guardrail, from the saved agent streams.

    python process_stats.py <keep-dir> [--out results/process.json]

For each tools/on run: did any ``arcade-guard check`` report FAIL (the agent
wrote a violation, was told, and had to repair it), and did any ``preview``
answer WOULD VIOLATE (the agent asked before writing and was warned). The
streams themselves are not committed: they contain local paths and session
metadata.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def scan(stream: Path) -> dict[str, bool]:
    pending: dict[str, str] = {}
    seen = {"check_failed": False, "preview_warned": False}
    for line in stream.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = event.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                command = str(block.get("input", {}).get("command", ""))
                if "arcade-guard" in command:
                    pending[block["id"]] = command
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
                command = pending.pop(block["tool_use_id"])
                body = block.get("content")
                text = body if isinstance(body, str) else json.dumps(body)
                if "arcade-guard check" in command and "FAIL:" in text:
                    seen["check_failed"] = True
                if "WOULD VIOLATE" in text:
                    seen["preview_warned"] = True
    return seen


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("keep", type=Path)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "results" / "process.json")
    args = ap.parse_args()

    cells: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for stream in sorted(args.keep.glob("*.stream.jsonl")):
        parts = stream.name.removesuffix(".stream.jsonl").split("-")
        model, condition = parts[-3], parts[-2]
        if condition not in ("tools", "on"):
            continue
        seen = scan(stream)
        cell = cells[f"{model}/{condition}"]
        cell["runs"] += 1
        cell["check_failed_then_repaired"] += seen["check_failed"]
        cell["preview_warned"] += seen["preview_warned"]

    result = {k: dict(v) for k, v in sorted(cells.items())}
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
