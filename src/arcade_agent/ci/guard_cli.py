#!/usr/bin/env python3
"""Architecture guardrail CLI: the same checks the MCP guard tools run.

Usage:
    arcade-guard init . --template layered
    arcade-guard propose . --intent "an endpoint that lists orders"
    arcade-guard preview . --from api --to store
    arcade-guard check . --fail-on error      # pre-commit / CI gate
    arcade-guard remediate .

Every sub-command accepts ``--json`` to print the raw tool result instead of
the human-readable summary. ``check`` exits 1 on FAIL unless ``--fail-on never``.
"""

import argparse
import json
import sys
from typing import Any

from arcade_agent.tools import guard

_ICON = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL"}


def _print_violations(violations: list[dict[str, Any]]) -> None:
    for v in violations:
        tag = "ERROR" if v["severity"] == "error" else "warn "
        print(f"  [{tag}] {v['rule']}: {v['message']}")
        if v.get("fix"):
            print(f"          fix: {v['fix']}")


def _render(cmd: str, result: dict[str, Any]) -> None:
    if cmd == "init":
        print(f"Wrote the {result['template']} template to {result['spec_path']}.")
        print("Adjust the component `match` globs to your layout, then run "
              "`arcade-guard check`.")
    elif cmd == "propose":
        if result["suggested_component"] is None:
            print(result["note"])
            return
        layer = f" (layer: {result['suggested_layer']})" if result["suggested_layer"] else ""
        print(f"Place it in: {result['suggested_component']}{layer}")
        if result["path_convention"]:
            print(f"  path convention: {result['path_convention']}")
        print("  may depend on: " + (", ".join(result["may_depend_on"]) or "(nothing)"))
        for item in result["must_not_depend_on"]:
            print(f"  must NOT depend on {item['component']}: {item['why']}")
    elif cmd == "preview":
        word = "ALLOWED" if result["allowed"] else "WOULD VIOLATE"
        print(f"{word}: {result['from']} -> {result['to']}")
        for p in result["problems"]:
            print(f"  - {p['rule']}: {p['message']}")
            print(f"    fix: {p['fix']}")
    elif cmd == "check":
        print(f"{_ICON[result['verdict']]}: {result['errors']} error(s), "
              f"{result['warnings']} warning(s), {result['num_entities']} entities "
              f"({result['unmapped_entities']} unmapped)")
        _print_violations(result["violations"])
        for note in result["budgets_not_evaluated"]:
            print(f"  (not evaluated: {note})")
    elif cmd == "remediate":
        if not result["actions"]:
            print("Nothing to fix: the code conforms to the spec.")
        for i, v in enumerate(result["actions"], 1):
            print(f"  {i}. [{v['severity']}] {v['rule']}: {v['message']}")
            print(f"     -> {v['fix']}")


def main(argv: list[str] | None = None) -> int:
    """Run the guardrail CLI.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        prog="arcade-guard", description="Architecture guardrail for coding agents"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("source", nargs="?", default=".", help="Project root")
        sp.add_argument("--spec", default=None, help="Spec path (default: <source>/"
                        f"{guard.SPEC_FILENAME})")
        sp.add_argument("--json", action="store_true", help="Print the raw JSON result")
        return sp

    sp = add("init", "Scaffold an architecture spec from a template")
    sp.add_argument("--template", choices=list(guard.SPEC_TEMPLATES), default="layered")
    sp.add_argument("--force", action="store_true", help="Overwrite an existing spec")

    sp = add("propose", "Where should new code live, and what may it use?")
    sp.add_argument("--intent", required=True, help="What you are about to add")

    sp = add("preview", "Would a component dependency be allowed?")
    sp.add_argument("--from", dest="frm", required=True, help="Depending component")
    sp.add_argument("--to", required=True, help="Depended-on component")

    sp = add("check", "Conformance verdict; exits 1 on FAIL (gate)")
    sp.add_argument("--language", default=None, help="Language (auto-detect if omitted)")
    sp.add_argument("--fail-on", choices=["error", "never"], default="error")
    sp.add_argument("--no-cache", action="store_true", help="Ignore the parse cache")

    sp = add("remediate", "Ranked fixes to restore conformance")
    sp.add_argument("--language", default=None)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "init":
            result = guard.init_spec(args.source, args.template, overwrite=args.force)
        elif args.cmd == "propose":
            result = guard.propose_placement(args.intent, args.source, args.spec)
        elif args.cmd == "preview":
            result = guard.preview_impact(args.frm, args.to, args.source, args.spec)
        elif args.cmd == "check":
            result = guard.check_architecture(
                args.source, args.spec, language=args.language, use_cache=not args.no_cache
            )
        else:
            result = guard.remediate(args.source, args.spec, language=args.language)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"arcade-guard: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _render(args.cmd, result)
    if args.cmd == "check" and result["verdict"] == "FAIL" and args.fail_on == "error":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
