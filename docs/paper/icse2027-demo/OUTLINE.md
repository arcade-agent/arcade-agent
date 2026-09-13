# ICSE 2027 — Tool Demonstration paper outline

**Working title:** *arcade-agent: Architecture Recovery and Conformance Inside the
Coding Agent's Loop*

**Status:** outline only. No prose drafted. Written 2026-09-11.

---

## 1. Submission facts

| Item | Value | Confidence |
|---|---|---|
| Track | ICSE 2027 Tool Demonstration and Data Showcase | verified (track page exists) |
| Submission site | `https://icse27demos.hotcrp.com` | verified |
| Deadline | **Fri 23 Oct 2026** | **UNVERIFIED — from search snippets only** |
| Notification | Fri 11 Dec 2026 | **UNVERIFIED** |
| Camera-ready | Wed 20 Jan 2027 | **UNVERIFIED** |
| Page limit | **4 pages, inclusive of all references, figures, tables, appendices** | **UNVERIFIED** |
| Format | IEEEtran, `\documentclass[10pt,conference]{IEEEtran}`, no `compsoc` | **UNVERIFIED** |
| Review model | Single-anonymous — **authors named in the submission** | **UNVERIFIED** |
| Required in abstract | URL of the demonstration video, appended at the end of the abstract | **UNVERIFIED** |
| Required in paper | Link to the publicly available tool + usage instructions | **UNVERIFIED** |

> **Action before writing a word:** `conf.researchr.org` is blocked by this
> environment's egress proxy, so every row marked UNVERIFIED came from search-result
> snippets, not the official CFP. Open
> <https://conf.researchr.org/track/icse-2027/icse-2027-demonstrations> manually and
> confirm the deadline, page limit, and video requirement. **Four pages inclusive of
> references is the single most structurally important constraint** — if it is
> actually 4+1 or 6 pages, the section budget below changes materially.

---

## 2. The claim

One sentence, and every section serves it:

> **Coding agents write code that works and architectures that rot. arcade-agent puts
> architecture recovery and conformance checking inside the agent's loop — as MCP tools
> the agent calls before and while it writes — so intended structure is enforced by
> construction rather than audited after the fact.**

### What is genuinely novel (say this plainly, reviewers will look for it)

The recovery algorithms are not new — ACDC, ARC, LIMBO, WCA and the decay metrics are
prior work from the ARCADE line (cite them; several are the authors' own). **The
contribution is the delivery vehicle:** these analyses have always been batch,
post-hoc, research-prototype tools run by a human on a snapshot. arcade-agent makes
them (a) an installable package, (b) callable by any MCP agent, (c) token-budgeted so
an agent can afford to call them, and (d) *proactive* — `propose_placement` and
`preview_impact` answer "where should this go, and may it depend on that?" **before**
the code exists.

### What NOT to claim

- Do not claim new recovery algorithms. Reviewers of this track know ARCADE.
- Do not claim the guardrail study is a controlled experiment. N is small (see §6).
- Do not claim cross-language edge recovery in general. Relinking is **family-scoped**;
  only the JVM family (Java↔Kotlin) is validated. Overclaiming here is a cheap reject.

---

## 3. Page budget

4 pages, IEEEtran two-column ≈ 8 columns. Allocate:

| § | Section | Columns | Notes |
|---|---|---:|---|
| — | Title, authors, abstract, index terms | 0.5 | Abstract ≤150 words; **append video URL** |
| I | Introduction | 0.8 | Problem + claim + contributions list |
| II | Motivating scenario | 0.8 | One concrete agent session. Fig. 1 |
| III | Architecture & implementation | 1.6 | Fig. 2 (system diagram) |
| IV | The tool in action | 1.8 | The demo walkthrough. Fig. 3 + Table I |
| V | Evidence it works | 1.2 | Table II (guardrail results) |
| VI | Related work | 0.6 | Dense, citation-heavy |
| VII | Availability, limitations, conclusion | 0.4 | Links, install, video |
| — | References | 0.7 | ~18–22 refs, IEEE abbreviated |
| | **Total** | **8.4** | **Over by 0.4 — see cut list in §8** |

---

## 3a. Drafting status (updated 2026-09-13)

**Drafted:** §I, §III, §IV. **Scaffolding only:** §II, §V, §VI, §VII.

§I is written from the author's own framing: software engineering rests on an
unstated assumption that artifacts are ultimately produced by people; the
man-month and lines-per-day are that assumption showing through, and
tokens-consumed is its current form; coding agents have broken the assumption,
so what was built on it needs re-examination. The paper then narrows to one
instance — nothing guards architecture while code is being written. Keep this
frame: it is what makes the paper an argument rather than a feature tour.

### Space: the paper is about one column over

| § | Budget | Prose | Floats | Actual |
|---|---:|---:|---:|---:|
| Front matter | 0.5 | — | — | 0.5 |
| I | 0.8 | ~312 w + list | — | **~0.95** |
| II | 0.8 | not written | Fig. 1 | ~0.9 |
| III | 1.6 | ~662 w | Fig. 2 + budget table | **~2.35** |
| IV | 1.8 | ~355 w | Fig. 3 + tool table | ~1.75 |
| V | 1.2 | not written | eval table | ~0.85 |
| VI | 0.6 | not written | — | 0.6 |
| VII | 0.4 | not written | — | 0.4 |
| Refs | 0.7 | 15 entries | — | 0.7 |
| | **8.0** | | | **~9.0** |

**Roughly one column has to go**, and §III is where most of the overage is.
Cut in this order, before touching any argument:

1. The `\todo`-marked paragraph on truncation coarseness in §III (optional by
   design).
2. The 19-tool inventory table in §IV — the measured budget table earns its
   space, an inventory does not. Collapse to a running list in the text.
3. The caching paragraph in §III, down to one sentence.
4. Figure 1, folding the scenario into §IV's walkthrough.

Do not pay for the overage out of §V. The threats paragraph and the null result
are what make the evaluation credible.

### Measured, not estimated

`measure_budget.py` reproduces every token figure in §III and §IV against
v0.3.0: 63,586 tokens unreduced; 12,644 at a 16k budget; 6,468 at 8k; 216 at 4k;
`summarize()` 911; `context_for_task` 3,615 over 14 files; reading `src/`
directly ~137,746. Re-run it if the code changes — these numbers are
load-bearing, and one of them is now in the contributions list.

### Material deliberately left out of §I

The source draft also covered identity and access management, and opened on a
topical exchange between two public figures about mathematics and AI. Both were
dropped: IAM is a different paper and would blur the contribution at the moment
reviewers are deciding what it is, and the topical framing carries a personal
register a tool paper cannot hold. One further claim — that software
architecture venues have recently grown unusually active — is omitted only for
want of a citation. With venue or submission counts to back it, it belongs in
the first paragraph.

---

## 4. Section-by-section

### I. Introduction (0.8 col)

- **Hook:** Agents now write a large and growing share of code. Type checkers guard
  types, linters guard style, tests guard behavior — *nothing guards architecture while
  the code is being written.* (This framing is already written in
  `arcade-analyze-skill/GUARDRAIL_PLAN.md`; reuse it, tighten it.)
- **Why it matters:** architectural decay is a measured, costly phenomenon — cite the
  decay/change-proneness line of work (self-cites available, see §7).
- **Why existing tools don't fit the agent loop:** batch, slow, human-facing, output
  measured in megabytes of HTML. An agent has a token budget and a latency budget.
- **The gap:** post-hoc CI conformance tells the agent it was wrong *after* it was
  wrong, and a red X with no fix makes an agent thrash.
- **Contributions** (explicit bulleted list — demo reviewers scan for this):
  1. An installable, framework-agnostic architecture-analysis library (`pip install
     arcade-agent`) with 7 language parsers and 5 recovery algorithms.
  2. An MCP server exposing 19 tools with per-call `max_tokens` budgeting and a
     session-handle protocol, so agents consume summaries not dumps.
  3. Four *task-shaped* context tools (`context_for_task`, `diff_impact`,
     `dependency_cone`, `api_surface`) that answer an agent's actual question instead
     of returning a graph.
  4. An architecture contract (`architecture.spec.json`) + proactive guardrail tools,
     with evidence that it eliminates a violation class an unaided agent produces ~1
     run in 5 on greenfield code.

### II. Motivating scenario (0.8 col) — **Figure 1**

One concrete, honest vignette. Recommended: the greenfield orders service from the
evaluation, because it doubles as the evaluated case.

> An agent is told "add order listing and totals" in a repo whose API layer must not
> touch the store. Unaided, it imports `app.store` from `app.api` — the shortest path
> to a working feature. With the guardrail, it calls `propose_placement`, is told the
> logic belongs in `service`, calls `preview_impact` on the `api → store` edge, is told
> it would violate, and routes through the service layer instead.

**Figure 1:** side-by-side of the two resulting import graphs — one with the forbidden
edge in red, one clean. Small, 1-column. This is the whole paper in one picture.

### III. Architecture and implementation (1.6 col) — **Figure 2**

Pipeline: **ingest → parse → recover → detect_smells → compute_metrics → compare**,
plus the conformance core reading `architecture.spec.json` and `.arcade/baseline.json`.

- **Parsing.** tree-sitter, one `LanguageParser` per language behind a
  `@register_parser` registry. 7 languages (Java, Python always available; C, TypeScript,
  Go, Kotlin, Rust via the `[languages]` extra). Polyglot merge with family-scoped
  relinking — **state the JVM-only validation limit here, in the body, not in a
  footnote.**
- **Recovery.** 5 algorithms: `pkg` (default, deterministic), `wca`, `acdc`, and the
  LLM-powered `arc` and `limbo`. Say why the guardrail path uses `pkg`/glob mapping
  only: *determinism* — same code must yield the same verdict, every time — and speed.
  This design rationale is the most defensible engineering claim in the paper.
- **Agent-facing delivery — the part reviewers should remember:**
  - *Session handles.* Tools return a compact summary plus a `session_id`; the agent
    calls `get_full_result` only if it actually needs the graph. Named types:
    `IngestedRepo`, `DependencyGraph`, `Architecture`, `SmellList`, `MetricList`.
  - *Token budgeting.* Every tool takes `max_tokens`. Two strategies: `truncate_result`
    does 7-level domain-aware progressive reduction on graph/architecture payloads
    (component entity lists → FQN→kind map → edge-relation counts → drop edges → drop
    entities → minimize components → drop packages); `enforce_budget` does generic
    largest-key dropping for everything else, flagging `_budget_truncated`.
    **This is the most cite-worthy implementation detail — give it real space and a
    concrete before/after token count.**
  - *Caching.* `DependencyGraph` cached to `.arcade-cache/`, keyed on file paths +
    mtimes, auto-invalidated.
- **Enforcement tiers.** Advisory (MCP tools + a Claude Code `PostToolUse` hook that
  injects findings into agent context) → blocking (pre-commit hook, CI gate,
  `--fail-on error` exits non-zero).

**Figure 2:** the system diagram. An ASCII draft already exists in
`arcade-analyze-skill/GUARDRAIL_PLAN.md` ("System shape") — redraw in TikZ.

### IV. The tool in action (1.8 col) — **Figure 3, Table I**

This is a *demonstration* paper; this section is the product. Mirror the video beat
for beat so a reviewer can follow both.

1. `pip install arcade-agent[mcp,languages]`, register `arcade-mcp` with the agent.
2. **Understand:** `summarize` on an unfamiliar repo → package tree, hotspots, entry
   points in one call. Contrast with the token cost of reading the files.
3. **Target:** `context_for_task("add rate limiting to the public API")` → ranked
   minimal file set with a per-file role (direct match / dependency / dependent /
   component sibling) and a reason.
4. **Guard:** `propose_placement` before writing; `preview_impact` before a
   cross-component import; `check_architecture` after the edit.
5. **Gate:** the same check as a pre-commit hook and in CI, with the PR comment.
6. **Self-application (do this — it is cheap, honest, and memorable):** arcade-agent
   analyses itself in its own CI. At v0.3.0: **76 source files, 407 entities, 228
   edges, 11 components, 4 smells**, with `.arcade/baseline.json` committed on every
   default-branch push so drift is caught against the last good state.

**Table I:** the 19 MCP tools grouped by purpose — *core pipeline* (ingest, parse,
analyze, recover, detect_smells, compute_metrics, compare, query, visualize),
*comprehension* (summarize, explain_component, find_relevant, api_surface),
*change-aware* (diff_impact, dependency_cone, changelog_architecture,
context_for_task), *session* (get_full_result, list_sessions). One line each. This
table is the densest information-per-column in the paper.

**Figure 3:** a real transcript excerpt of the agent calling `preview_impact` and
changing course. Use a real captured session, not a mock-up — reviewers can tell.

### V. Evidence it works (1.2 col) — **Table II**

Keep it short, concrete, and *scrupulously* honest about scale. A demo paper does not
need a full study; it needs credible evidence the tool does what it claims.

Three results, from `arcade-analyze-skill/evals/EVAL_REPORT.md`:

1. **Detection is sound and deterministic.** 3/3 injected violations caught, each with
   the right rule and a fix; gate exits non-zero every time.
2. **Zero false positives.** Across all runs, every PASS was genuinely conformant.
3. **Behavior change where theory predicts it.** Greenfield, 14 runs per condition: the
   unaided condition introduced the forbidden `api → store` edge in **3/14 (21%)**; the
   guardrail condition in **0/14 (0%)**. On a *pre-structured* codebase (12 runs) there
   was no difference — agents imitate existing layering. **Report the null result.** It
   is the single most credibility-building sentence available, and it sharpens the
   claim: the guardrail matters most on new code, where there is no structure to copy.

**Table II:** condition × runs × violations × rate, for the structured and greenfield
settings.

**Threats — 3 sentences, do not skip:** small N and agent stochasticity (treat
percentages as directional); both conditions were told folder *roles*, so 21% is likely
a floor; the capable model complied in every condition, so the measured benefit
concentrates on smaller/faster agents; conformance is structural, not functional
correctness. All four are already written honestly in `EVAL_REPORT.md` — compress, do
not soften.

### VI. Related work (0.6 col)

Four clusters, one or two sentences each, heavily cited:

1. **Architecture recovery.** ACDC, ARC, LIMBO, WCA, and ARCADE as a workbench.
   Position: arcade-agent *reimplements and repackages* this line — say so.
2. **Architectural decay and conformance.** Decay studies, change/issue-proneness,
   reflexion models, Lattix/Structure101/ArchUnit/Sonargraph as industrial conformance.
   Position: all post-hoc, human-invoked, CI-time.
3. **Agent context and repository comprehension.** Repo maps, retrieval for code agents,
   SWE-bench-style harnesses. Position: these optimize *what to read*; none carry an
   architectural contract.
4. **Guardrails for coding agents.** Linters/type-checkers/tests in the agent loop.
   Position: the missing rung is architecture.

### VII. Availability, limitations, conclusion (0.4 col)

- `pip install arcade-agent[mcp,languages]`; Python ≥3.12; MIT.
- Repo `github.com/lemduc/arcade-agent`; docs `arcade-agent.dev`; PyPI `arcade-agent`
  v0.3.0; video URL.
- Limitations in one honest sentence each: cross-language relinking validated on the
  JVM family only; incremental parsing wired for Python only, so the sub-second
  guarantee on very large repos is not yet general; MCP sessions are in-process and do
  not survive a server restart; conformance is structural, not behavioral.

---

## 5. Verified facts to draw on

Every number below is verified against `origin/main` at `1b3532b` (v0.3.0). The
authoritative fact base with per-line citations is
`arcade-agent-site/docs/content-facts.md` — **cite that file's sources when writing,
and never restate a number from memory.** Its own rule: read the released state via
`git show origin/main:<path>`, not a feature branch.

| Fact | Value |
|---|---|
| Version / license / Python | 0.3.0 on PyPI · MIT · ≥3.12 |
| Languages | 7 — Java, Python (core); C, TypeScript, Go, Kotlin, Rust (`[languages]` extra) |
| Recovery algorithms | 5 — `pkg`, `wca`, `acdc`, `arc`, `limbo` (`arc`/`limbo` LLM-powered) |
| MCP tools | 19, registered via `@server.tool()`, stdio transport, FastMCP |
| Registered `@tool` functions | 15 (`ingest`/`parse` are plain functions, exposed via MCP) |
| Decay metrics | 6 — RCI, TurboMQ, BasicMQ, IntraConnectivity, InterConnectivity, TwoWayPairRatio |
| Balanced/derived scores | 8, via `compute_balanced_scores` (self-analysis only) |
| Smell types | 4 — Dependency Cycle, Concern Overload, Scattered Parasitic Functionality, Link/Upstream Overload |
| Budget levels | 7 (`truncate_result`) + generic largest-key dropping (`enforce_budget`) |
| Self-analysis at v0.3.0 | 76 files, 407 entities, 228 edges, 11 components, 4 smells |
| Guardrail: injected | 3/3 caught, gate exit 1 |
| Guardrail: greenfield | off 3/14 (21%) violations · on 0/14 (0%) |
| Guardrail: structured | 12 runs, no difference, 0 false positives |
| Test files | 61 under `tests/` |

**Careful:** the README's "19 MCP tools · 4 task-shaped context tools" is accurate, but
19 MCP tools ≠ 15 `@tool`-registered functions. Do not write "19 tools" next to a count
derived from `list_tools()` — a reviewer who pip-installs and counts will find the
mismatch. State which surface you are counting.

---

## 6. The demo video

The track requires a video URL appended to the abstract. Plan it as a deliverable with
its own deadline, not an afterthought.

- **Length:** aim 3–5 minutes (confirm any stated cap in the CFP).
- **Content:** exactly the §IV walkthrough, unedited terminal, real agent session.
- **Reusable assets:** the site already has a recorded terminal cast
  (`arcade-agent-site/public/casts/self-analysis.cast`) and the published self-analysis
  report under `public/self/`. The narrative beats are drafted in the site's landing copy.
- **The money shot:** the agent calling `preview_impact`, being told the edge would
  violate, and *changing its plan*. Capture a real one.
- **Host:** YouTube unlisted or Zenodo. Must be reachable by reviewers without login.

---

## 7. Citation set to assemble

Self-citations available (verified from `arcade-agent-site/src/pages/research.astro`) —
note single-anonymous review, so self-citation is unproblematic:

- Schmitt Laser, Medvidović, Le, Garcia. *ARCADE: An Extensible Workbench for
  Architecture Recovery, Change, and Decay Evaluation.* ESEC/FSE 2020.
- Le, Behnamghader, Garcia, Link, Shahbazian, Medvidović. *An Empirical Study of
  Architectural Change in Open-Source Software Systems.* MSR 2015 (Best Paper).
- Le, Link, Shahbazian, Medvidović. *An Empirical Study of Architectural Decay in
  Open-Source Software Systems.* ICSA 2018.
- Le, Karthik, Laser, Medvidović. *Architectural Decay as Predictor of Issue- and
  Change-Proneness.* ICSA 2021.
- Behnamghader, Le, Garcia, Link, Shahbazian, Medvidović. *A Large-Scale Study of
  Architectural Evolution in Open-Source Software Systems.* EMSE 2017.
- Le. *Architectural Evolution and Decay in Software Systems.* PhD thesis, USC, 2018.

Still to gather: original ACDC / ARC / LIMBO / WCA papers; a reflexion-model reference;
ArchUnit or an industrial conformance tool; 2–3 current coding-agent/context references;
the MCP specification.

---

## 8. Risks, and the cut list

**Risks**

1. **"This is ARCADE with an MCP wrapper."** The likeliest reject reason. Mitigation:
   lead §III with the agent-delivery mechanics (token budgeting, session handles,
   determinism-by-design) and §V with the behavior-change result. The novelty is that
   an agent can *afford* to call this, and that it is consulted *before* the code exists.
2. **Thin evaluation.** Mitigation: frame §V as evidence, not a study; report the null
   result and all four threats; keep percentages labelled directional.
3. **Two tools, one paper.** arcade-agent and arcade-guard must read as one system, not
   a bundle. Mitigation: single pipeline figure; guardrail presented as a *consumer* of
   the analysis core, never as a separate contribution.
4. **Four pages is brutal.** The budget in §3 is already 0.4 columns over.

**Cut list, in order** (spend this before cutting §V):

1. Merge §VII into §VI's tail; move availability links into a footnote on page 1.
2. Drop Table I to a compressed 2-column list of tool names by group.
3. Cut Figure 1 and fold the scenario into §IV's walkthrough.
4. Compress §III's caching and enforcement-tier paragraphs to one sentence each.

---

## 9. Schedule to 23 Oct 2026 (6 weeks)

| Week | Dates | Deliverable |
|---|---|---|
| 0 | Sep 11–14 | **Verify the CFP**: deadline, page limit, video requirement, format. Confirm author list and order. Resolve whether `publising/arcade-guard-paper/` (referenced at `arcade-analyze-skill/bench/README.md:6`) holds existing draft material — that path exists in no repo here. |
| 1 | Sep 15–21 | IEEEtran skeleton + all figure/table stubs. Assemble the full BibTeX. Write §III and §IV — the sections only the authors can write. |
| 2 | Sep 22–28 | Write §I, §II, §V. Draw Fig. 1–3 in TikZ. Capture the real agent transcript for Fig. 3. |
| 3 | Sep 29–Oct 5 | Write §VI, §VII, abstract. **Record the video.** First full pass at 4 pages — expect to be over; apply the cut list. |
| 4 | Oct 6–12 | Internal review pass. Re-verify every number against `content-facts.md`. Fresh-machine install test: does `pip install arcade-agent[mcp,languages]` + the documented usage instructions actually work from clean? |
| 5 | Oct 13–19 | Polish, tighten, final figure quality pass. Publish and link the video. Register usage instructions at a stable URL. |
| 6 | Oct 20–23 | Buffer. Submit no later than Oct 22 — never on deadline day. |

**Hard dependency:** the demo video and the public usage-instructions link are
*submission requirements*, not nice-to-haves. Both must be live and reachable before
the abstract is finalized, because the video URL goes in the abstract.
