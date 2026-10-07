# Guardrail evaluation

`run.py` drives headless Claude Code agents on `tasks/gf-orders-write-py` under
five conditions and scores each result for conformance (error-severity
violations of the canonical spec) and function (the task's functional check).
`inject.py` is the deterministic detection check. Raw per-run records are in
`results/`; `../make_results.py` turns them into the numbers the paper quotes.

Evaluated version: arcade-agent at commit `29367ac` (guardrail port), run
2026-10-07 with Claude Code 2.1.292, models `claude-haiku-4-5-20251001` and
`claude-sonnet-5-5`. Agents ran with `--setting-sources project
--strict-mcp-config` in directories outside any CLAUDE.md.

    python run.py --task tasks/gf-orders-write-py --models haiku \
        --conditions off,spec,advisory,tools,on --reps 20 --out results/run.jsonl
    python run.py --summarize results/*.jsonl
    python inject.py

Each agent run spends model usage (about $0.06-0.15 per Haiku run).
