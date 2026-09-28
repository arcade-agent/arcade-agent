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

- [x] **15. TypeScript/JS parser** — Shipped in #8 (`parsers/typescript.py`), with
  configuration-aware local import resolution (`baseUrl`/`paths`, inherited JSONC configs,
  npm workspaces) and graph-quality coverage metadata.
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

## Priority Order

| Priority | Items | Rationale |
|----------|-------|-----------|
| **Done** | 1–10, 12, 13, 14, 15, 16a, 16a2, 16b, 17, 18 (MVP) | Phases 1–2 + TS/JS & Go & Kotlin & Rust parsers, incremental parsing (Python), `diff_impact`, `context_for_task`, `api_surface`, polyglot merge+relink, `changelog_architecture` |
| **Now** | 11 | Component ownership |
| **Then** | 19–22 | Ecosystem breadth (OpenAI / LangChain / Claude SDK / IDE) |
