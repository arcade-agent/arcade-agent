#!/usr/bin/env python3
"""Guardrail evaluation harness for the ICSE 2027 demo paper.

For each (task x model x condition x rep) the harness creates an empty
greenfield scaffold, drives a headless Claude Code agent on the task, and
scores the result three ways:

- conformance: ``arcade_agent.tools.guard.check_architecture`` against the
  task's canonical spec (only error-severity findings count as violations);
- function: the task's functional check, run in a fresh interpreter;
- process: how often the agent actually invoked ``arcade-guard``.

Conditions form a 2x2 over {rules stated in the prompt} x {guard tools}, with
the spec file present in all four, plus a no-spec baseline:

  off       feature + folder roles only; no spec file
  spec      the spec file, "keep the code conformant to it"; no rules, no tools
  advisory  spec + the rules stated in the prompt; no tools
  tools     spec + the arcade-guard propose / preview / check workflow; no rules
  on        spec + rules + tools

Agents run with user settings, plugins, hooks and MCP servers disabled
(``--setting-sources project --strict-mcp-config``) in a directory outside
any CLAUDE.md, so only the prompt differs between conditions.

Usage:
    python run.py --task tasks/gf-orders-write-py --models haiku \\
        --conditions off,advisory,on --reps 20 --workers 4 --out results/haiku.jsonl
    python run.py --summarize results/*.jsonl
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GUARD_BIN = Path(sys.executable).parent / "arcade-guard"
DISALLOWED = ["WebSearch", "WebFetch", "Task", "Workflow", "Skill", "ToolSearch",
              "CronCreate", "RemoteTrigger", "ScheduleWakeup", "PushNotification"]

FIND_AND_RUN = """
import importlib, pathlib, sys
sys.path.insert(0, ".")
mods = [p for p in pathlib.Path("app/api").rglob("*.py")]
cls = None
for p in mods:
    name = ".".join(p.with_suffix("").parts)
    try:
        m = importlib.import_module(name)
    except Exception:
        continue
    if hasattr(m, "OrdersApi"):
        cls = m.OrdersApi
        break
assert cls is not None, "no OrdersApi under app/api"
OrdersApi = cls
"""


def prompt_for(task: dict[str, Any], workdir: Path, condition: str) -> str:
    """Build the prompt; each condition strictly extends the previous one."""
    parts = [
        f"Build a feature from scratch in the Python project at {workdir} "
        "(currently empty package folders).",
        task["folder_roles"],
        f"Feature: {task['feature']}",
    ]
    if condition in ("advisory", "on"):
        parts.append(
            f"This project has an architecture contract at {workdir}/architecture.spec.json. "
            f"Contract: {task['rules_summary']} Keep the code conformant to it."
        )
    if condition in ("spec", "tools"):
        parts.append(
            f"This project has an architecture contract at {workdir}/architecture.spec.json. "
            "Keep the code conformant to it."
        )
    if condition in ("on", "tools"):
        parts.append(
            "The contract is enforced by the `arcade-guard` command (on PATH). "
            f"(1) Before writing each piece, run `arcade-guard propose {workdir} --intent "
            "\"<what you are about to add>\"`. "
            f"(2) Before importing from one of {', '.join(task['packages'])} into another, run "
            f"`arcade-guard preview {workdir} --from <component> --to <component>`. "
            f"(3) When done, run `arcade-guard check {workdir}` and fix every ERROR until it "
            "reports PASS."
        )
    parts.append("Make it work.")
    return " ".join(parts)


def scaffold(task: dict[str, Any], workdir: Path, condition: str, spec: Path) -> None:
    (workdir / "app").mkdir(parents=True)
    (workdir / "app" / "__init__.py").write_text("")
    for pkg in task["packages"]:
        (workdir / "app" / pkg).mkdir()
        (workdir / "app" / pkg / "__init__.py").write_text("")
    if condition != "off":
        shutil.copy(spec, workdir / "architecture.spec.json")


def run_agent(prompt: str, workdir: Path, model: str, condition: str,
              timeout_s: int) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    if condition in ("on", "tools"):
        env["PATH"] = f"{GUARD_BIN.parent}{os.pathsep}{env.get('PATH', '')}"
    cmd = ["claude", "-p", prompt, "--model", model,
           "--permission-mode", "bypassPermissions",
           "--setting-sources", "project", "--strict-mcp-config",
           "--no-session-persistence", "--output-format", "stream-json", "--verbose",
           "--disallowedTools", *DISALLOWED]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout_s)
        stdout, rc = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        rc = -1
    wall = round(time.perf_counter() - t0, 1)

    model_id, cost, turns = None, None, None
    guard_calls: dict[str, int] = defaultdict(int)
    bash_calls = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            model_id = event.get("model")
        elif event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    bash_calls += 1
                    command = str(block.get("input", {}).get("command", ""))
                    for sub in ("propose", "preview", "check", "remediate", "init"):
                        if f"arcade-guard {sub}" in command:
                            guard_calls[sub] += 1
        elif event.get("type") == "result":
            cost = event.get("total_cost_usd")
            turns = event.get("num_turns")
    return {"agent_rc": rc, "wall_s": wall, "model_id": model_id, "cost_usd": cost,
            "num_turns": turns, "bash_calls": bash_calls, "guard_calls": dict(guard_calls),
            "_stream": stdout}


def score(task: dict[str, Any], workdir: Path, spec: Path) -> dict[str, Any]:
    from arcade_agent.tools.guard import check_architecture

    py_files = [p for p in (workdir / "app").rglob("*.py") if p.stat().st_size > 0]
    if not py_files:
        return {"verdict": "NO_CODE", "errors": 0, "violations": [], "functional": False,
                "functional_error": "no code"}
    result = check_architecture(str(workdir), spec_path=str(spec), language="python",
                                use_cache=False)
    errors = [v for v in result["violations"] if v["severity"] == "error"]
    shutil.rmtree(workdir / ".arcade-cache", ignore_errors=True)

    check = FIND_AND_RUN + task["functional_check"].replace(
        "from app.api.orders_api import OrdersApi\n", "")
    try:
        proc = subprocess.run([sys.executable, "-I", "-c", check], cwd=workdir,
                              capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)
        functional, ferr = proc.returncode == 0, proc.stderr.strip()[-300:]
    except subprocess.TimeoutExpired:
        functional, ferr = False, "timeout"
    return {"verdict": result["verdict"], "errors": len(errors),
            "violations": [{"rule": v["rule"], "message": v["message"]} for v in errors],
            "component_edges": result["component_edges"],
            "unmapped_entities": result["unmapped_entities"],
            "functional": functional, "functional_error": None if functional else ferr}


def one_run(task: dict[str, Any], spec: Path, root: Path, model: str, condition: str,
            rep: int, timeout_s: int, keep: Path | None) -> dict[str, Any]:
    workdir = root / f"{task['id']}-{model}-{condition}-{rep:02d}"
    scaffold(task, workdir, condition, spec)
    agent = run_agent(prompt_for(task, workdir, condition), workdir, model, condition,
                      timeout_s)
    stream = agent.pop("_stream")
    sc = score(task, workdir, spec)
    if keep is not None:
        shutil.copytree(workdir, keep / workdir.name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", ".arcade-cache"))
        (keep / f"{workdir.name}.stream.jsonl").write_text(stream)
    shutil.rmtree(workdir, ignore_errors=True)
    return {"task": task["id"], "model": model, "condition": condition, "rep": rep,
            "violated": sc["errors"] > 0, **sc, **agent}


def summarize(paths: list[Path]) -> None:
    rows = [json.loads(line) for p in paths for line in p.read_text().splitlines() if line]
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(r["task"], r.get("model_id") or r["model"], r["condition"])].append(r)
    order = {"off": 0, "spec": 1, "advisory": 2, "tools": 3, "on": 4}
    print(f"{'task':20} {'model':28} {'cond':9} {'n':>3} {'viol':>5} {'func':>5} "
          f"{'guard':>6} {'cost$':>7} {'wall_s':>7}")
    for key in sorted(groups, key=lambda k: (k[0], k[1], order.get(k[2], 9))):
        rs = [r for r in groups[key] if r["verdict"] != "NO_CODE"]
        n = len(rs)
        viol = sum(r["violated"] for r in rs)
        func = sum(r["functional"] for r in rs)
        used = sum(1 for r in rs if r.get("guard_calls"))
        cost = sum(r.get("cost_usd") or 0 for r in groups[key])
        wall = sum(r["wall_s"] for r in rs) / n if n else 0
        no_code = len(groups[key]) - n
        print(f"{key[0]:20} {key[1]:28} {key[2]:9} {n:>3} {viol:>5} {func:>5} {used:>6} "
              f"{cost:>7.2f} {wall:>7.0f}" + (f"  (+{no_code} no code)" if no_code else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--task", type=Path)
    ap.add_argument("--models", default="haiku")
    ap.add_argument("--conditions", default="off,spec,advisory,tools,on")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--first-rep", type=int, default=1)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=900, help="Per-run agent timeout (s)")
    ap.add_argument("--out", type=Path, default=HERE / "results" / "run.jsonl")
    ap.add_argument("--keep", type=Path, default=None, help="Copy each workdir here")
    ap.add_argument("--summarize", nargs="+", type=Path, default=None)
    args = ap.parse_args()

    if args.summarize:
        summarize(args.summarize)
        return
    if args.task is None:
        ap.error("--task is required unless --summarize is given")
    if not GUARD_BIN.exists():
        sys.exit(f"arcade-guard not found next to {sys.executable}; run with the "
                 "interpreter that has arcade-agent installed.")

    task_dir = args.task.resolve()
    task = json.loads((task_dir / "task.json").read_text())
    spec = task_dir / "architecture.spec.json"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.keep:
        args.keep.mkdir(parents=True, exist_ok=True)

    jobs = [(m.strip(), c.strip(), rep)
            for rep in range(args.first_rep, args.first_rep + args.reps)
            for m in args.models.split(",") if m.strip()
            for c in args.conditions.split(",") if c.strip()]
    lock = threading.Lock()
    root = Path(tempfile.mkdtemp(prefix="guard-eval-"))

    def work(job: tuple[str, str, int]) -> None:
        model, cond, rep = job
        try:
            rec = one_run(task, spec, root, model, cond, rep, args.timeout, args.keep)
        except Exception as exc:  # keep the batch going; record the failure
            rec = {"task": task["id"], "model": model, "condition": cond, "rep": rep,
                   "verdict": "HARNESS_ERROR", "error": repr(exc), "violated": False,
                   "functional": False, "wall_s": 0}
        with lock:
            with args.out.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"{model:6} {cond:8} rep {rep:2}: {rec['verdict']:13} "
                  f"func={rec.get('functional')} guard={rec.get('guard_calls')} "
                  f"{rec.get('wall_s')}s ${rec.get('cost_usd')}", flush=True)

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(work, jobs))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    summarize([args.out])


if __name__ == "__main__":
    main()
