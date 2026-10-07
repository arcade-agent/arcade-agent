#!/usr/bin/env python3
"""Generate results.tex: every number the paper quotes, from the raw records.

    python make_results.py            # reads eval/results/*.jsonl, inject.json,
                                      # and budget.json; writes results.tex

Nothing in paper.tex should be typed by hand that this script can compute.
"""

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "eval" / "results"
CONDITIONS = ["off", "spec", "advisory", "tools", "on"]
LABELS = {
    "off": r"\emph{off} (no spec)",
    "spec": r"spec",
    "advisory": r"spec + rules",
    "tools": r"spec + tools",
    "on": r"spec + rules + tools",
}
MODEL_SHORT = {
    "claude-haiku-4-5-20251001": "Haiku 4.5",
}


def short_model(model_id: str) -> str:
    if model_id in MODEL_SHORT:
        return MODEL_SHORT[model_id]
    parts = model_id.replace("claude-", "").split("-")
    name = parts[0].title()
    version = ".".join(p for p in parts[1:3] if p.isdigit())
    return f"{name} {version}".strip()


def fisher_one_sided(a: int, b: int, c: int, d: int) -> float:
    """P(X >= a) for the 2x2 table [[a, b], [c, d]] under the hypergeometric null."""
    n1, n2, k = a + b, c + d, a + c
    total = math.comb(n1 + n2, k)
    return sum(math.comb(n1, x) * math.comb(n2, k - x)
               for x in range(a, min(n1, k) + 1)) / total


def cmd(name: str, value: Any) -> str:
    return f"\\newcommand{{\\{name}}}{{{value}}}\n"


def num(n: int) -> str:
    return f"{n:,}".replace(",", "{,}")


def load_runs() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(RESULTS.glob("*.jsonl")):
        rows += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if r.get("verdict") not in ("HARNESS_ERROR",)]


def eval_section(rows: list[dict[str, Any]]) -> str:
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        cells[(short_model(r.get("model_id") or r["model"]), r["condition"])].append(r)
    models = sorted({m for m, _ in cells}, key=lambda m: (m != "Haiku 4.5", m))
    out = ""
    present = [c for c in CONDITIONS if any((m, c) in cells for m in models)]

    header = " & ".join(rf"\multicolumn{{2}}{{c}}{{{m}}}" for m in models)
    sub = " & ".join(r"viol. & func." for _ in models)
    lines = [rf"\begin{{tabular}}{{@{{}}l{'rr' * len(models)}@{{}}}}", r"\toprule",
             rf"Condition & {header} \\", rf" & {sub} \\", r"\midrule"]
    for cond in present:
        vals = []
        for m in models:
            rs = [r for r in cells.get((m, cond), []) if r["verdict"] != "NO_CODE"]
            if not rs:
                vals += ["--", "--"]
                continue
            viol = sum(r["violated"] for r in rs)
            func = sum(r["functional"] for r in rs)
            vals += [f"{viol}/{len(rs)}", f"{func}/{len(rs)}"]
            key = f"{m.split()[0]}{cond.title()}"
            out += cmd(f"{key}Viol", viol) + cmd(f"{key}N", len(rs)) + cmd(f"{key}Func", func)
            used = sum(1 for r in rs if r.get("guard_calls"))
            out += cmd(f"{key}Used", used)
            calls = defaultdict(int)
            for r in rs:
                for k, v in (r.get("guard_calls") or {}).items():
                    calls[k] += v
            for sub_cmd in ("propose", "preview", "check"):
                out += cmd(f"{key}{sub_cmd.title()}Calls", calls.get(sub_cmd, 0))
                out += cmd(f"{key}{sub_cmd.title()}Avg", f"{calls.get(sub_cmd, 0) / len(rs):.1f}")
            # Violations where the API still used the service: it wired the
            # store in itself (composition root) rather than skipping the layer.
            roots = sum(1 for r in rs if r["violated"] and any(
                e["from"] == "api" and e["to"] == "service" for e in r.get("component_edges", [])))
            out += cmd(f"{key}CompRoot", roots)
            costs = [r.get("cost_usd") or 0 for r in rs]
            out += cmd(f"{key}Cost", f"{sum(costs) / len(costs):.2f}")
            walls = sorted(r["wall_s"] for r in rs)
            out += cmd(f"{key}Wall", f"{walls[len(walls) // 2]:.0f}")
        lines.append(f"{LABELS[cond]} & " + " & ".join(vals) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    out += "\\newcommand{\\EvalTable}{%\n" + "\n".join(lines) + "}\n"
    out += cmd("ModelNames", " and ".join(f"Claude {m}" for m in models))
    out += cmd("NumRuns", len(rows))

    haiku = {c: [r for r in cells.get(("Haiku 4.5", c), []) if r["verdict"] != "NO_CODE"]
             for c in present}
    if haiku.get("off") and haiku.get("spec"):
        o, s = haiku["off"], haiku["spec"]
        ov, sv = sum(r["violated"] for r in o), sum(r["violated"] for r in s)
        out += cmd("OffViol", ov) + cmd("OffN", len(o))
        for other in ("tools", "advisory", "on"):
            if haiku.get(other):
                t = haiku[other]
                tv = sum(r["violated"] for r in t)
                p = fisher_one_sided(sv, len(s) - sv, tv, len(t) - tv)
                out += cmd(f"PSpecVs{other.title()}", f"{p:.2g}" if p >= 0.001 else r"<0.001")
    elif haiku.get("off"):
        o = haiku["off"]
        out += cmd("OffViol", sum(r["violated"] for r in o)) + cmd("OffN", len(o))
    return out


def inject_section() -> str:
    path = RESULTS / "inject.json"
    if not path.exists():
        return cmd("InjectRules", r"\todo{run eval/inject.py}")
    rec = json.loads(path.read_text())
    cases = [r for r in rec if r["case"] != "conformant"]
    rules = "; ".join(f"{r['case']}: " + ", ".join(r["rules"]) for r in cases)
    caught = sum(1 for r in cases if r["exit"] == 1)
    base = next(r for r in rec if r["case"] == "conformant")
    return (cmd("InjectRules", rules.replace("_", r"\_"))
            + cmd("InjectCaught", caught) + cmd("InjectN", len(cases))
            + cmd("InjectBaseline", base["verdict"]))


def process_section() -> str:
    path = RESULTS / "process.json"
    if not path.exists():
        return ""
    out = ""
    for key, cell in json.loads(path.read_text()).items():
        model, cond = key.split("/")
        name = f"{model.title()}{cond.title()}"
        out += cmd(f"{name}Repaired", cell["check_failed_then_repaired"])
        out += cmd(f"{name}Warned", cell["preview_warned"])
    return out


def budget_section() -> str:
    path = HERE / "budget.json"
    if not path.exists():
        return (cmd("BudgetTable", r"\todo{run measure\_budget.py}")
                + "".join(cmd(n, r"\todo{n}") for n in
                          ("SelfFullTok", "SelfSummTok", "SelfRawTok", "CtxFiles", "CtxTok"))
                + cmd("TokenFootnote", r"\todo{tokenizer footnote}"))
    data = json.loads(path.read_text())
    rows = data["rows"]
    self_row = next(r for r in rows if r["repo"] == "arcade-agent")
    lines = [r"\setlength{\tabcolsep}{3.5pt}", r"\begin{tabular}{@{}lrrrrr@{}}", r"\toprule",
             r"Repository & Entities & Source & Full & "
             rf"{data['budget'] // 1000}k & Summary \\", r"\midrule"]
    for r in rows:
        name = r["repo"].replace("_", r"\_")
        lang = {"python": "Py", "java": "Java"}.get(r["language"], r["language"])
        lines.append(f"{name} ({lang}) & {num(r['entities'])} & "
                     f"{num(r['source_tokens'])} & {num(r['full_tokens'])} & "
                     f"{num(r['budget_tokens'])} & {num(r['summarize_tokens'])} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    out = "\\newcommand{\\BudgetTable}{%\n" + "\n".join(lines) + "}\n"
    out += cmd("SelfFullTok", num(self_row["full_tokens"]))
    out += cmd("SelfSummTok", num(self_row["summarize_tokens"]))
    out += cmd("SelfRawTok", num(self_row["source_tokens"]))
    ctx = data["context_for_task"]
    out += cmd("CtxFiles", ctx["num_files"]) + cmd("CtxTok", num(ctx["tokens"]))
    cal = data.get("claude_over_o200k")
    note = (f"Counted with the \\tool{{o200k\\_base}} BPE tokenizer. Claude's "
            f"tokenizer counted {min(cal.values()):.2f}--{max(cal.values()):.2f}$\\times$ "
            "as many tokens on samples of each payload kind."
            if cal else r"Counted with the \tool{o200k\_base} BPE tokenizer.")
    out += cmd("TokenFootnote", note + r" Reproduce with \tool{measure\_budget.py}.")
    return out


def main() -> None:
    rows = load_runs()
    text = "% Generated by make_results.py -- do not edit.\n"
    text += eval_section(rows) + inject_section() + process_section() + budget_section()
    for name in ("ResultSentence", "EvidenceProse", "TranscriptListing"):
        if f"\\newcommand{{\\{name}}}" not in text:
            text += cmd(name, r"\todo{" + name + "}")
    (HERE / "results.tex").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
