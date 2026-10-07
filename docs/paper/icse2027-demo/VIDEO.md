# Demo video script (3–5 min, required by the track)

Upload to YouTube (unlisted is fine) and keep it reachable without login through
the review period (notification 11 Dec 2026). Paste the URL at the end of the
abstract in `paper.tex`. Record the real terminal; no mock-ups. Each beat matches
a paragraph of Sec. IV, in order, so a reviewer can follow paper and video
together.

Setup before recording: a clean venv, `pip install "arcade-agent[mcp,languages]"`,
Claude Code with `arcade-mcp` registered (README, "Configure in Claude Code"),
terminal font ≥ 16 pt, and the greenfield scaffold from
`eval/tasks/gf-orders-write-py` (empty `app/{api,service,store,domain}`).

| # | Time | Beat (Sec. IV) | On screen | Voice-over |
|---|------|----------------|-----------|------------|
| 1 | 0:00–0:25 | Problem | Title card, then the unaided Fig. 1 graph | "Coding agents write code that passes its tests and erodes its architecture. Type checkers, linters and tests run inside the agent's loop; architecture checks don't." |
| 2 | 0:25–0:50 | Setup | `pip install`, the `.mcp.json` entry, `arcade-mcp` listed in `/mcp` | "One pip install gives a library, an MCP server with 24 tools, and a CLI gate." |
| 3 | 0:50–1:30 | Understand | Agent calls `summarize` on arcade-agent itself; show the token count | "The agent's first question is what's here. One call, about a thousand tokens, instead of reading 140 thousand." |
| 4 | 1:30–2:05 | Target | `context_for_task("add a Ruby language parser")` | "Next: what should I read? Ranked files, each with a reason — the existing parsers come first." |
| 5 | 2:05–3:15 | Guard | Greenfield scaffold + spec. Agent runs `propose` for each piece, `preview api → store` → WOULD VIOLATE, then routes through the service | "Before writing, the agent asks where code belongs and whether an import is allowed. Here it's told the API may not touch the store — so it changes its plan, not its code." |
| 6 | 3:15–3:50 | Gate | Inject `from app.store… import` into the API; `arcade-guard check` → FAIL, exit 1; remove it → PASS | "The same engine is the gate. Same code, same verdict — in the agent's loop, at commit, and in CI." |
| 7 | 3:50–4:20 | Evidence | Table II | "In NN headless runs, an unaided agent took the shortcut every time; with the specification it never did. Stating the rules was enough on this task; the tool delivers those rules when nobody wrote them into the prompt." (Fill from results.) |
| 8 | 4:20–4:40 | Availability | README, PyPI page, repo URL | "MIT-licensed, pip-installable, harness and run records in the repository." |

Checklist before uploading

- [ ] Every number spoken matches `results.tex` (regenerate first).
- [ ] No employer or internal system names on screen (terminal prompt, paths, browser tabs).
- [ ] Captions on (YouTube auto-captions, then correct "arcade", "MCP", "pip").
- [ ] Length 3:00–5:00.
- [ ] URL opens in a private window.
