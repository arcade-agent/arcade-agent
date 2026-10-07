#!/usr/bin/env python3
"""Print the arcade-guard calls and their outputs from a recorded agent stream.

    python transcript.py <run>.stream.jsonl [--max-lines 6]

The stream is the ``--output-format stream-json`` log that run.py saves next to
each kept workdir. Output is the raw command and the first lines of its result,
in order, for choosing and abridging the paper's transcript figure.
"""

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stream", type=Path)
    ap.add_argument("--max-lines", type=int, default=6)
    args = ap.parse_args()

    pending: dict[str, str] = {}
    for line in args.stream.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = event.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                name = block.get("name")
                inp = block.get("input", {})
                if name == "Bash" and "arcade-guard" in str(inp.get("command", "")):
                    pending[block["id"]] = inp["command"]
                elif name in ("Write", "Edit"):
                    print(f"[{name.lower()}] {inp.get('file_path', '')}")
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
                print(f"$ {pending.pop(block['tool_use_id'])}")
                body = block.get("content")
                if isinstance(body, list):
                    body = "\n".join(b.get("text", "") for b in body if isinstance(b, dict))
                for out_line in str(body).splitlines()[: args.max_lines]:
                    print(f"  {out_line}")


if __name__ == "__main__":
    main()
