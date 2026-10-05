# arcade-agent

[![CI](https://github.com/lemduc/arcade-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/lemduc/arcade-agent/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/arcade-agent)](https://pypi.org/project/arcade-agent/)
[![Python versions](https://img.shields.io/pypi/pyversions/arcade-agent)](https://pypi.org/project/arcade-agent/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP compatible](https://img.shields.io/badge/MCP-compatible-blue)](https://modelcontextprotocol.io/)
[![GitHub stars](https://img.shields.io/github/stars/lemduc/arcade-agent?style=social)](https://github.com/lemduc/arcade-agent/stargazers)

19 MCP tools · 4 task-shaped context tools · one git-versioned baseline · one pip install

Docs: https://arcade-agent.dev

Framework-agnostic tool library for software architecture analysis.

Provides composable tools for parsing source code, recovering architecture, detecting architectural smells, computing quality metrics, and comparing versions. Works standalone or plugs into MCP, LangChain, or Claude SDK.

## Install

```bash
pip install -e ".[dev]"

# With MCP and all optional language parsers (for polyglot AI agent integration)
pip install -e ".[mcp,languages,dev]"
```

## Quick Start

```python
from arcade_agent.tools.ingest import ingest
from arcade_agent.tools.parse import parse
from arcade_agent.tools.recover import recover
from arcade_agent.tools.detect_smells import detect_smells
from arcade_agent.tools.compute_metrics import compute_metrics
from arcade_agent.tools.visualize import visualize

# 1. Ingest a project
repo = ingest("/path/to/java/project")

# 2. Parse dependencies
graph = parse(repo.path, language="java")

# 3. Recover architecture
arch = recover(graph, algorithm="pkg")

# 4. Detect smells
smells = detect_smells(arch, graph)

# 5. Compute metrics
metrics = compute_metrics(arch, graph)

# 6. Generate report
visualize(repo.name, repo.version, graph, arch, smells, output="report.html")
```

For async applications, the one-call `analyze` tool keeps the event loop
responsive by running the sequential analysis stages in a worker thread
(other sync MCP tools still run inline on the event loop):

```python
from arcade_agent.tools.analyze import analyze

result = await analyze("/path/to/project", language="python", algorithm="pkg")
print(len(result.architecture.components), len(result.smells))
```

## Tools

| Tool | Description |
|------|-------------|
| `analyze` | One-call ingest → parse → recover → smells → metrics (async; offloads blocking work) |
| `ingest` | Clone/load source code, detect versions, discover files; `ref=` materialises a specific commit/tag/branch without touching the working tree |
| `parse` | Parse source → DependencyGraph via tree-sitter |
| `recover` | Recover architecture (PKG, WCA, ACDC, ARC, LIMBO) |
| `detect_smells` | Find dependency cycles, concern overload, scattered functionality, link overload (heuristic or LLM-powered) |
| `compute_metrics` | Calculate RCI, TurboMQ, connectivity metrics |
| `compare` | A2A architecture comparison across versions |
| `changelog_architecture` | Architectural changelog between two versions: components added/removed/renamed/rewritten/split/merged, entities that changed component, smell and metric deltas |
| `visualize` | Generate HTML reports, DOT, Mermaid, JSON, RSF |
| `query` | Explore recovered architecture interactively |
| `summarize` | Codebase overview with package tree, hotspots, entry points; drill-down via `focus` |
| `explain_component` | Component detail: API surface, dependencies, cohesion |
| `find_relevant` | Find entities relevant to a natural-language query |
| `api_surface` | Extract the public API surface (public types + members) without implementation detail |
| `diff_impact` | Map changed files to affected components, downstream dependents, and broken contracts |
| `dependency_cone` | Upstream/downstream dependency cone of an entity or file, with depth control |
| `context_for_task` | Rank the minimal set of files to read for a natural-language task |

Plus two session helpers — `get_full_result` and `list_sessions` — for a total of 19 MCP tools.

## Balanced Architecture Score

arcade-agent keeps the original ARCADE-style quality metrics (`RCI`, `TurboMQ`,
`BasicMQ`, `IntraConnectivity`, `InterConnectivity`, `TwoWayPairRatio`) and adds
an explainable derived score for reporting:

```text
BalancedArchitectureScore =
  0.50 * cohesion_family
+ 0.35 * PrincipleAlignmentScore
+ 0.15 * SmellDiscipline
```

This score is bounded to `[0, 1]` and higher is better. It is intended as a
readable summary for PR comments and self-analysis reports, not as a replacement
for the raw metrics. The original metrics are still emitted and shown so teams
can inspect the underlying cohesion and coupling values.

The balanced score exists because raw cohesion/coupling metrics can be hard to
interpret in isolation. A change can improve `RCI` while still creating an
unbalanced component, a dependency hub, or new architecture smells. The derived
score combines three views:

- **Cohesion family** — existing `RCI`, `TurboMQ`, and `BasicMQ` signals.
  `TurboMQ` is the Bunch-style *sum* of per-component cluster factors (range
  `[0, k]` for `k` components); `BasicMQ` is the normalized mean of the same
  cluster factors (range `[0, 1]`), so the balanced score uses the normalized
  form of `TurboMQ` internally.
- **Principle alignment** — acyclic dependencies, layering health,
  responsibility focus, interface segregation, component balance, hub balance,
  boundary clarity, and dependency distribution.
- **Smell discipline** — architectural smell burden weighted by severity and
  affected-component scope.

Reports also include `score_drivers`, which identify the strongest and weakest
signals behind the score. This makes the result actionable: reviewers can see
whether a score moved because of dependency concentration, component imbalance,
smell burden, or another architectural pressure.

## Supported Languages

- Java (full support)
- Python (full support)
- C/C++ (full support)
- TypeScript/JavaScript (full support)
- Go (full support)
- Kotlin (structural support via optional `[languages]` extra; import + inheritance graph)
- Rust (structural support via optional `[languages]` extra; structs, enums, unions, traits,
  type aliases, functions, methods, imports, qualified references, trait
  inheritance/implementations, and Cargo workspaces)

Rust unit tests live *inline* in production files, so path-based test exclusion
cannot see them. With `exclude_tests=True` (the default) the Rust parser also
drops `#[cfg(test)]` items — including `cfg(all(test, ...))` / `cfg(any(test, ...))`,
inner `#![cfg(test)]` files and module bodies, the file behind an out-of-line
`#[cfg(test)] mod helpers;`, and `#[cfg(test)] use ...` dev-dependency imports.
Pass `exclude_tests=False` to `ingest`/`parse`/`analyze` to keep them.

## Example: ARCADE Core

[ARCADE Core](https://github.com/usc-softarch/arcade_core) is a Java-based architecture recovery workbench from USC's Software Architecture Research Group. Running arcade-agent against it:

```bash
git clone https://github.com/usc-softarch/arcade_core.git
python examples/basic_analysis.py arcade_core --language java
```

Results (v1.2.0): 170 entities, 470 edges, 13 components recovered, 7 architectural smells detected (including a 7-component dependency cycle and concern overload in the Clustering module).

See [`examples/arcade_core_report.html`](https://lemduc.github.io/arcade-agent/examples/arcade_core_report.html) for the full interactive report.

### Algorithm Comparison

Compare PKG, ACDC, ARC, and LIMBO recovery algorithms side-by-side on the same project:

```bash
python examples/compare_algorithms.py arcade_core --language java --use-llm
```

See [`examples/comparison_report.html`](https://lemduc.github.io/arcade-agent/examples/comparison_report.html) for the full comparison report.

## MCP Server (AI Agent Integration)

arcade-agent exposes all tools via the [Model Context Protocol](https://modelcontextprotocol.io/) so AI agents (Claude Code, Cursor, etc.) can analyze codebases with minimal token usage.

### Start the server

```bash
arcade-mcp
```

### Configure in Claude Code

Add to your Claude Code MCP settings:

```json
{
  "mcpServers": {
    "arcade-agent": {
      "command": "arcade-mcp"
    }
  }
}
```

### How it works

1. **Async pipeline** — `analyze` runs the complete sequential pipeline in a worker thread so the MCP event loop stays responsive, and returns reusable session IDs for every artifact.
2. **Session store** — Tools like `parse` and `recover` return compact summaries with a `session_id`. Pass session IDs to downstream tools instead of full data objects.
3. **Token budget** — Every tool accepts an optional `max_tokens` parameter. Outputs are progressively truncated (entity details → edge summaries → component counts) to fit.
4. **Parse caching** — Parsed dependency graphs are cached to `.arcade-cache/` keyed by file modification times. Repeated analysis of the same codebase skips re-parsing.
5. **On-demand detail** — Call `get_full_result(session_id)` to retrieve complete data when the summary isn't enough.
6. **Production-source default** — `ingest`, `analyze`, and automatic `parse`
   discovery exclude test/vendor/build directories by default. Set
   `exclude_tests=false` to include them; an explicit `parse(files=[...])` list
   is always honored. For custom layouts, pass exact project-relative directory
   prefixes such as `exclude_dirs=["integrationTest", "src/e2e"]`. These values
   are not globs and apply only to the named directory and its descendants.

The built-in policy treats any directory segment named `test` or `tests` as
non-production, including a package such as `src/main/java/com/example/test`.
Use `exclude_tests=false` if that convention is production code, then provide
only the precise custom exclusions needed through `exclude_dirs`.

### Example agent workflow

```
Agent: call analyze(source="/path/to/project", language="python")
       → {graph: {session_id: "a1b2c3", ...}, architecture: {session_id: "d4e5f6", ...}, ...}

# Or compose individual stages:
Agent: call parse(source_path="/path/to/project")
       → {session_id: "a1b2c3", num_entities: 170, num_edges: 470, ...}

Agent: call recover(dep_graph="a1b2c3", algorithm="pkg")
       → {session_id: "d4e5f6", num_components: 13, components: [...], ...}

Agent: call detect_smells(architecture="d4e5f6", dep_graph="a1b2c3")
       → {num_smells: 7, smells: [...]}

Agent: call get_full_result(session_id="a1b2c3", max_tokens=2000)
       → full graph data, truncated to fit token budget
```

For a polyglot repository, let `ingest` preserve its file selection and pass
the returned session directly to `parse`:

```
Agent: call ingest(source="/path/to/project", languages=["java", "kotlin"])
       → {session_id: "p1q2r3", languages: ["java", "kotlin"], ...}

Agent: call parse(source_path="p1q2r3")
       → {session_id: "a1b2c3", num_entities: 420, num_edges: 960, ...}
```

An ingest session carries an already-selected explicit file list. Therefore,
`exclude_tests` and `exclude_dirs` on a subsequent `parse` call do not re-filter
that session. Configure exclusions on `ingest`, or pass your own authoritative
`files=[...]` list to `parse`.

For a repository with non-standard test locations:

```
Agent: call analyze(
  source="/path/to/project",
  language="multi",
  exclude_dirs=["integrationTest", "src/e2e", "modules/api/spec"]
)
```

Use `language="multi"` instead when every detected supported language should
be parsed automatically.

**Supported polyglot pairs (MVP).** Every requested language is parsed, but
cross-language edges are only created *within a language family*:

| Family | Languages | Cross-language relinking |
|--------|-----------|--------------------------|
| `jvm` | `java`, `kotlin` | Yes — validated pair (shared FQN space, extends/implements across languages) |
| every other language | `python`, `go`, `typescript`, `c`, ... | No — parsed and merged into one graph, but never linked to another family |

The relink heuristics are dotted-name tuned (packages, unique leaf names,
`import a.b.C`) and only the JVM pair is covered by tests, so a Python
`com.auth.service` module and a Java `com.auth.service` class are never
confused for each other. When such an FQN coincidence happens across families
both entities are kept — the later one re-keyed as `<fqn>#<language>` — and the
counts appear in the graph's `metadata` (`fqn_collisions`,
`fqn_collisions_cross_family`, …), so nothing is silently dropped.

## LLM-Powered Analysis

Pass `--use-llm` to enable Claude-powered concern detection. Requires the `claude` CLI installed and authenticated.

```bash
python examples/basic_analysis.py arcade_core --language java --use-llm
```

This replaces heuristic smell detection (entity count thresholds, suffix matching) with semantic analysis that identifies *what* concerns each component handles and *why* they are problematic. Set `ARCADE_MOCK=1` to skip LLM calls, or `ARCADE_MODEL=haiku` to use a faster model.

## CI/CD Integration

arcade-agent ships a GitHub Action that detects architecture drift on every PR
— like SonarQube for architecture. Consumer repositories add a short workflow
that calls `lemduc/arcade-agent/actions/analyze`, and the action runs released
arcade-agent tooling from PyPI without checking out this repository's live
source.

### How It Works

1. On **pull request**: parses the codebase, recovers architecture (PKG), compares against the latest stored baseline artifact, and posts a PR comment with a drift report.
2. On **push to the default branch**: stores a fresh baseline artifact so future PRs compare against the latest merged state.

### Recommended Setup

Create `.github/workflows/arcade-agent-analysis.yml` in the repository you want
to analyze:

```yaml
name: Arcade Architecture Analysis

on:
  pull_request:
  push:
  workflow_dispatch:

jobs:
  analyze:
    runs-on: ubuntu-latest
    permissions:
      actions: read
      contents: read
      issues: write
      pull-requests: write
    steps:
      - uses: lemduc/arcade-agent/actions/analyze@v0.3.0
        with:
          arcade-agent-version: "0.3.0"
```

Common optional inputs:

```yaml
      - uses: lemduc/arcade-agent/actions/analyze@v0.3.0
        with:
          arcade-agent-version: "0.3.0"
          source-path: "."
          language: ""
          exclude-tests: "true"
          exclude-dirs: "integrationTest,src/e2e"
          primary-algorithm: pkg
          run-secondary-analyses: "true"
          baseline-branch: ""
```

For reproducible CI, keep `arcade-agent-version` pinned to a released package
version such as `"0.3.0"`. Avoid `latest` in shared CI because a new package
release can change analyzer behavior without a workflow review.

The action stores the baseline as a GitHub Actions artifact on
successful pushes to the repository default branch and uses that artifact for
future PR comparisons. To use a different baseline branch, set
`baseline-branch`.

If you prefer copying a full standalone workflow instead of using an action,
copy `examples/workflows/arcade-agent-analysis.yml` from this repository. The
standalone workflow installs `arcade-agent` from PyPI and runs the same packaged
CLI commands directly.

### Local Usage

Run the drift detection script locally:

```bash
# Analyze without a baseline (first run)
arcade-arch-diff --source /path/to/project --language java

# Update the baseline
arcade-arch-diff --source /path/to/project --language java --update-baseline

# Compare against existing baseline
arcade-arch-diff --source /path/to/project --language java
```

### PR Comment Format

The action posts a comment with:
- **Drift table** — component count, similarity score, balanced score, principle alignment, RCI, TurboMQ, and supporting metric deltas
- **Changes** — added/removed components, entity movements, splits/merges
- **Smells** — dependency cycles, concern overload, scattered functionality

The comment is updated on each push to the PR (not duplicated).

## Roadmap

arcade-agent ports and extends the capabilities of the original [ARCADE](https://github.com/usc-softarch/arcade_core) Java workbench, and is evolving into a token-efficient codebase understanding layer for AI agents. See [ROADMAP.md](ROADMAP.md) for the full AI agent integration roadmap.

| Feature | Status | Details |
|---------|--------|---------|
| 5 recovery algorithms (PKG, WCA, ACDC, ARC, LIMBO) | Done | Package-based, weighted clustering, pattern-based, LLM concern-based, information-theoretic |
| 4 smell types (BDC, BCO, SPF, BUO) | Done | Heuristic + LLM-powered detection |
| 6 quality metrics | Done | RCI, TurboMQ, BasicMQ, IntraConnectivity, InterConnectivity, TwoWayPairRatio |
| Balanced architecture score | Done | Derived reporting score combining core metrics, principle signals, and smell burden |
| A2A architecture comparison | Done | Hungarian algorithm on Jaccard similarity |
| Multi-language parsing | Done | Java, Python, C/C++, TypeScript/JavaScript, Go (full); Kotlin, Rust (structural); polyglot merge+relink via `languages=[...]` / `language="multi"` (cross-language edges within the JVM family only) |
| 5 export formats | Done | HTML, DOT, JSON, RSF, Mermaid |
| LLM concern extraction | Done | Claude CLI for semantic BCO/SPF detection |
| MCP server | Done | Expose tools to AI agents via Model Context Protocol with session store |
| Token-budget truncation | Done | Progressive output reduction to fit agent context windows |
| Parse result caching | Done | Mtime-based cache avoids re-parsing unchanged codebases |
| Codebase summarization | Done | Token-efficient overview with package tree, hotspots, hierarchical drill-down |
| Component explanation | Done | API surface, dependencies, cohesion metrics for recovered components |
| Relevance search | Done | Keyword-based entity search with architecture-aware boosting |
| Multi-version evolution pipeline | Planned | Batch version history analysis, A2A cost trends, CVG over time |
| Flexible stopping criteria | Planned | `no_orphans`, `size_fraction` strategies for WCA/ARC/LIMBO |
| Additional similarity measures | Planned | UEMNM (normalized UEM) and InfoLoss |
| Architectural Stability metric | Planned | Fan-in/fan-out ratio |
| MCFP-based comparison | Planned | Minimum Cost Flow for accurate entity movement cost |
| Design decision recovery (RecovAr) | Planned | Link issue trackers to architectural changes |

## Comparison with ARCADE Core

arcade-agent is a Python successor to the original [ARCADE Core](https://github.com/usc-softarch/arcade_core) Java workbench. The table below compares capabilities across both projects.

### High Value

| Feature | ARCADE Core (Java) | arcade-agent (Python) | Notes |
|---------|--------------------|-----------------------|-------|
| LIMBO algorithm | Full | Done (LLM-powered) | Uses Claude CLI concern vectors + size-weighted JS divergence |
| ARC algorithm | Full (concern-based) | Done (LLM-powered) | Uses Claude CLI concern vectors + JS divergence instead of MALLET topics |
| Topic modeling (MALLET) | Full (50 topics, 250 iterations) | LLM-based | arcade-agent uses Claude CLI instead of MALLET for semantic concern analysis |
| Evolution metrics (A2A cost, CVG) | MCFP-based movement cost, coverage | Basic Jaccard comparison | Core computes actual entity movement costs and bidirectional coverage |
| Multi-version batch analysis | VersionMap, VersionTree, batch processing | Single-pair compare | Core can process entire version histories and track trends |
| Stopping/serialization criteria | 3 stopping + 4 serialization strategies | Hardcoded target cluster count | Flexible termination (no-orphans, size-fraction) would improve clustering |
| Similarity measures | 11 (UEMNM, InfoLoss, WeightedJS, ARC variants) | 3 (JS, UEM, SCM) | More measures = better tuning per project type |

### Medium Value

| Feature | ARCADE Core (Java) | arcade-agent (Python) | Notes |
|---------|--------------------|-----------------------|-------|
| Architectural Stability metric | Fan-in/fan-out ratio | Missing | Simple addition to existing 6 metrics |
| Concern-based smell detection | Topic distributions for BCO and SPF | LLM-powered (Claude CLI) | Heuristic fallback also available |
| Cluster matching (MCFP) | Minimum Cost Flow for movement cost | Hungarian algorithm on Jaccard | MCFP gives more accurate evolution cost |
| ODEM input format | XML-based dependency parsing | Missing | Academic interchange format, limited real-world use |
| SmellToIssuesCorrelation | Correlates smells with issue tracker data | Missing | Requires issue tracker integration |

### Lower Priority

| Feature | ARCADE Core (Java) | arcade-agent (Python) | Notes |
|---------|--------------------|-----------------------|-------|
| RecovAr (Design Decision Recovery) | Full engine (GitLab issues/commits) | Missing | Large scope research feature |
| Issue tracker integration | JIRA + GitLab REST clients | Missing | Needed for RecovAr or SmellToIssues |
| Swing GUI | Full desktop visualization | HTML reports + CLI | HTML/Mermaid is more modern |
| Classycle bytecode analysis | Java bytecode dependency extraction | tree-sitter source parsing | tree-sitter is arguably better (no compilation needed) |
| Make dependency / Understand CSV | C-specific input formats | Missing | Niche; tree-sitter C parser covers the core need |
