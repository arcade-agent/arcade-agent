# Roadmap: arcade-agent as an AI Agent Tool

Transform arcade-agent into a token-efficient codebase understanding layer for AI agents.

## Phase 1 — MCP Server (Foundation)

Make tools callable by any MCP-compatible agent (Claude Code, Cursor, etc.)

- [x] **1. Implement MCP server** — Expose tools via Model Context Protocol with session store for complex objects. Tools return compact summaries with session IDs; agents drill into details via `get_full_result`.
- [x] **2. Streaming responses** — Tools return summaries by default; full results retrievable on demand via `get_full_result` tool. Agents control how much data they consume.
- [x] **3. Token-budget parameter** — Every tool accepts an optional `max_tokens` hint. Progressive truncation (entity details → edge summaries → component counts) via `budget.py`.
- [x] **4. Cached parse results** — Cache `DependencyGraph` to JSON in `.arcade-cache/`. Keyed by file paths + mtimes. Auto-invalidates when source files change.

## Phase 2 — Token-Efficient Summaries

Give agents maximum understanding per token spent.

- [x] **5. `summarize` tool** — Returns structured codebase overview: package tree, dependency hotspots, entry points. One call replaces reading dozens of files.
- [x] **6. Hierarchical drill-down** — `summarize(focus="com.foo.auth")` drills into a specific package with entities, dependencies in/out, and key files.
- [x] **7. `explain_component` tool** — Shows responsibility, entities, public API surface, internal-only entities, component dependencies, and cohesion metric.
- [x] **8. `find_relevant` tool** — Keyword-based search across entity names, packages, and file paths. Architecture-aware boosting via component names and responsibilities.

## Phase 3 — Change-Aware Context

Help agents understand *what changed* and *what matters* without reading full diffs.

- [x] **9. `diff_impact` tool** — Given a set of changed files, map them to affected components, the downstream reverse-dependency closure, and potentially broken contracts (external callers of changed public entities).
- [x] **10. `changelog_architecture` tool** — Given two commits/tags, produce an architectural changelog: new/removed components, shifted responsibilities, new smells.
- [ ] **11. `blame_component` tool** — Component-level ownership via git blame aggregation.

## Phase 4 — Smart Context Selection

Let agents ask "what do I need to read?" instead of reading everything.

- [x] **12. `context_for_task` tool** — Input: natural-language task description. Output: minimal set of files the agent needs, ranked by relevance with per-file role (direct match / dependency / dependent / component sibling) and reason.
- [x] **13. `dependency_cone` tool** — Given an entity/file, return the upstream/downstream dependency cone with depth control and per-direction node caps. Shares the cycle-safe traversal helper with `diff_impact`.
- [x] **14. `api_surface` tool** — Extract public interfaces only (public top-level types + their public members) without implementation details. Public is derived by naming convention + external-dependent signal (parsers store no visibility field or param types).

## Phase 5 — Multi-Language & Scale

Handle real-world polyglot monorepos.

- [x] **15. TypeScript/JS parser** — Shipped in #8 (`parsers/typescript.py`).
- [x] **16a. Go parser** — Shipped alongside TS/JS in #8 (`parsers/go.py`).
- [x] **16a2. Kotlin parser** — Shipped (`parsers/kotlin.py`) for JVM/Kotlin-first repos (e.g. embabel-agent).
- [x] **16b. Rust parser** — Shipped (`parsers/rust.py`): modules, types, traits, functions,
  methods, imports, qualified references, trait relationships, Cargo workspaces, and inline
  `#[cfg(test)]` exclusion.
- [x] **17. Incremental parsing** — Content-hash extract cache shipped in #9 (`incremental.py`), wired for the Python parser only; extending to the other two-pass parsers is follow-up.
- [x] **18. Cross-language dependency tracking** — MVP: multi-language ingest/parse (`languages=[...]` / `language="multi"`) merges per-language graphs and relinks import/extends/implements across FQN space. Relinking is **family-scoped**: the `jvm` family (Java↔Kotlin) is the supported and validated pair; every other language is its own family and is merged without cross-language edges. Extending relinking to further families (and broader RPC/IDL bridges — gRPC stubs, OpenAPI) remains follow-up.

## Phase 6 — Agent Protocol Integration

Work everywhere agents work.

- [ ] **19. OpenAI function-calling schema** — Auto-generate from existing registry type hints.
- [ ] **20. LangChain/LangGraph tool wrappers** — Thin adapter using the registry.
- [ ] **21. Claude Agent SDK integration** — Native tool definitions for the Agent SDK.
- [ ] **22. VS Code / IDE extension** — Expose tools as code actions or inline hints.

## Phase 7 — Decay research into the tool

Bring the thesis findings behind the "When Architecture Decays" guest lecture
(VinUni COMP5320, October 2026) into arcade-agent and arcade-guard: drift vs
erosion, smelly-file hotspots, architecture evolution across versions, the full
ECSA 2017 smell catalog, and the audit trail that student and consulting
assessments need. Items are in priority order. Each has a tracking issue; none
is implemented yet.

- [ ] **23. Drift vs erosion in arcade-guard** — `algorithms/conformance.py` maps every entity no spec glob matches to `UNMAPPED` and drops its edges, so `check_architecture` only ever counts them. Report spec coverage (% of entities mapped), list new unmapped modules/directories as *drift* warnings (Perry & Wolf: changes the intent does not cover) and keep forbid/layer violations as *erosion*. Add a `min_coverage` budget next to `no_cycles` / `max_fan_in`.
- [ ] **24. Architectural hotspot list** — `SmellInstance` carries affected components only. Add file/entity membership so `detect_smells` can emit a ranked list of smelly files (thesis: ~18% of files carry smells and see 1.2–2.4× more issues), and have `context_for_task` and `diff_impact` warn when a task touches one.
- [ ] **25. `evolution` tool (architecture timeline)** — Run recover + compare across a list of tags/commits and emit per-version a2a, c2c, cvg, smell counts, RCI and TurboMQ as a series (JSON + HTML chart). Reuses `ingest(ref=)` and `compare`. Backs thesis H1 (minor releases change architecture more than major ones), a trended Drift Index for executives, and the data the D1 "does AI accelerate decay" study needs.
- [ ] **26. Complete the ECSA 2017 smell catalog** — 4 of 11 implemented (Dependency Cycle, Concern Overload, Scattered Parasitic Functionality, Link Overload). Add Unused Component and Unused Interface first (cheap, agent-relevant), then Co-change Coupling (needs git-log mining; `src/` has no git-history code yet), then Duplicate Functionality, Sloppy Delegation, Functionality Overload and Lego Syndrome.
- [ ] **27. Guard audit log** — Append one JSONL event per `propose_placement` / `preview_impact` / `check_architecture` / `remediate` call (timestamp, agent, inputs, verdict, violations). COMP5320 M5 asks students to log what the agent tried and what the guard caught; ArchAgentBench and consulting assessments need the same record.
- [ ] **28. `init_spec --from-recovery`** — Draft `architecture.spec.json` from a recovered architecture: components, observed allowed dependencies, suggested forbids from layer order, and optional LLM-proposed component names (`tools/recover.py` derives names from package keys only). Closes the "guard checks hand-written globs, not the recovered architecture" critique and is D3's "flip it" study.
- [ ] **29. Cycle classification** — In `remediate` and `detect_smells`, label each component cycle as code-level, architecture-level or both (entity SCCs vs component SCCs; `algorithms/cycles.py` already uses `strongly_connected_components`) and say when refactoring code alone cannot break it (Le et al., ICSA 2018).
- [ ] **30. Housekeeping before COMP5320 M3** — Cut a release carrying the Unreleased Python type-annotation `uses` edges and nested-import fixes (#80, #81) before students install arcade-agent for M3. Reconcile the lecture deck, which still cites the 14+14 pilot (21% → 0%), with the ICSE 2027 demo paper's 130 runs (20/20 → 5/20 → 0/20).

## Priority Order

| Priority | Items | Rationale |
|----------|-------|-----------|
| **Done** | 1–10, 12, 13, 14, 15, 16a, 16a2, 16b, 17, 18 (MVP) | Phases 1–2 + TS/JS & Go & Kotlin & Rust parsers, incremental parsing (Python), `diff_impact`, `context_for_task`, `api_surface`, polyglot merge+relink, `changelog_architecture` |
| **Now** | 30, 11 | Release the pending Python parser fixes before COMP5320 M3; component ownership |
| **Then** | 23–29 | Phase 7 in listed order: drift vs erosion, hotspots, evolution series, smell catalog, audit log, spec from recovery, cycle classification |
| **Later** | 19–22 | Ecosystem breadth (OpenAI / LangChain / Claude SDK / IDE) |
