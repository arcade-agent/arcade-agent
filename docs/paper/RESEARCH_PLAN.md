# ARCADE-Next: Research Plan for AI-Powered Architecture Recovery

**Date:** 2026-03-25
**Team:** PI, Postdoc, PhD Student
**Status:** Draft — For Discussion

---

## 1. Team Discussion: Understanding the Problem

### PI (Principal Investigator)

> ARCADE has served us well for over a decade — deployed at 20+ universities, used in industry, and has analyzed ~500 MSLOC across 50+ systems. But let's be honest about its limitations:
>
> 1. **Heavy manual setup** — Users must manually organize source code directories, configure version naming, tune Mallet parameters, select stopwords, and identify test code patterns before anything runs.
> 2. **Commercial dependency** — SciTools Understand requires an expensive license for dependency extraction.
> 3. **Outdated tech stack** — Java 11, Maven fat JAR, Mallet for topic modeling (LDA with fixed 50 topics), XStream/JXL for serialization, and Python 2-era orchestration scripts.
> 4. **No AI in the loop** — The clustering, smell detection, and metric computation are all rule-based or statistical. Modern LLMs understand code semantics far better than LDA ever could.
> 5. **Scalability ceiling** — 32GB+ heap for large systems, no distributed processing.
> 6. **Poor developer experience** — No API, no web UI, CLI-only with obscure argument formats.
>
> The research question is: **Can we reimagine ARCADE using LLMs and modern infrastructure to make architecture recovery faster, more accurate, and accessible — while enabling new capabilities that were previously impossible?**

### Postdoc

> I agree. Looking at the codebase, here's what I see as the core value we must preserve:
>
> - **5 subsystems**: Recovery, Decay Detection, Measurement, Visualization, Prediction
> - **Multi-algorithm comparison**: Running ACDC, ARC, WCA, Limbo, PKG in parallel and comparing results
> - **11 architectural smells** across 4 categories (interface, change, dependency, concern-based)
> - **Evolution metrics**: a2a, MojoFM, cvg, MQ for tracking architectural drift
> - **RecovAr**: Design decision extraction by correlating code changes with issues
>
> What I'd add with AI:
> - **LLM-based semantic understanding** replaces LDA topic modeling entirely
> - **Code embeddings** for similarity measurement instead of hand-crafted features
> - **Natural language explanations** of architectural smells (not just detection, but "why it matters" and "how to fix it")
> - **Conversational interface** — ask questions about your architecture in plain English
> - **Automated report generation** — produce architectural documentation that stays in sync with code

### PhD Student

> From a practical standpoint, the current pipeline has 7 Python scripts that must be run in sequence (`renamer.py` → `dir_cleaner.py` → `extract_facts.py` → `run_clustering.py` → `detect_smells.py` → `extract_metrics.py` → `plotter.py`). Each has its own CLI arguments and failure modes. I've spent weeks just getting the pipeline to run on new systems.
>
> What excites me about reimplementation:
> - **Git-native**: Point it at a repo URL and it figures out versions, branches, tags
> - **Language-agnostic dependency extraction**: Use tree-sitter instead of SciTools Understand
> - **LLM-powered clustering**: Let the model understand module boundaries based on code semantics, not just import graphs
> - **Real-time analysis**: Run as a GitHub Action or CI plugin, not a batch process
> - **Interactive exploration**: Web-based visualization with drill-down capability

---

## 2. Proposed System: ARCADE-Next

### 2.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    ARCADE-Next                          │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ Ingestion│→ │ Analysis │→ │   AI     │→ │ Output │ │
│  │ Layer    │  │ Engine   │  │  Engine  │  │ Layer  │ │
│  └──────────┘  └──────────┘  └──────────┘  └────────┘ │
│       ↕             ↕             ↕            ↕       │
│  ┌─────────────────────────────────────────────────┐   │
│  │              Shared Data Store                   │   │
│  │         (Knowledge Graph + Vector DB)            │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Tech Stack Decision

| Layer | Current (ARCADE) | Proposed (ARCADE-Next) | Rationale |
|-------|-------------------|------------------------|-----------|
| **Language** | Java 11 + Python scripts | Python 3.12+ (core) + TypeScript (UI) | Faster prototyping, richer ML/AI ecosystem |
| **Build** | Maven + manual scripts | uv (Python), pnpm (TS), Docker Compose | Modern dependency management |
| **Dependency Extraction** | SciTools Understand ($$), Classycle | tree-sitter (multi-lang, free) | No license cost, supports 100+ languages |
| **Semantic Analysis** | Mallet LDA (50 topics) | Code embeddings (CodeBERT/StarCoder) + LLM | Far richer semantic understanding |
| **Clustering** | Custom agglomerative (ARC/WCA/Limbo/ACDC) | Hybrid: traditional + LLM-guided clustering | Best of both worlds |
| **Smell Detection** | Rule-based (11 smells, 4 categories) | Rule-based + LLM explanation/validation | Keep rigor, add interpretability |
| **Metrics** | Custom (a2a, MojoFM, MQ, cvg) | Same metrics + new LLM-based quality scores | Backward compatibility |
| **Visualization** | GraphViz DOT → SVG | D3.js / Cytoscape.js interactive web UI | Exploration, not just static images |
| **Data Store** | RSF text files, JSON | Neo4j (graph) + ChromaDB (vectors) + SQLite (metrics) | Queryable, relational, semantic search |
| **API** | None (CLI only) | FastAPI REST + WebSocket | Integration, automation, real-time |
| **CI Integration** | None | GitHub Actions plugin | Continuous architecture monitoring |
| **LLM Integration** | None | Claude API (via Anthropic SDK) | Explanations, analysis, Q&A |

---

## 3. Research Plan: Phases and Milestones

### Phase 1: Foundation (Months 1-3)
**Goal:** Core infrastructure and dependency extraction

| Task | Owner | Description | Deliverable |
|------|-------|-------------|-------------|
| 1.1 | PhD | **Git-native ingestion** — Given a repo URL, auto-detect versions from tags/releases, clone, and organize | `arcade_next.ingestion` module |
| 1.2 | PhD | **Tree-sitter dependency extraction** — Parse source code in Java, C/C++, Python, C#, JS/TS and extract import/call/inheritance dependencies | `arcade_next.facts` module producing dependency graphs |
| 1.3 | Postdoc | **Data model design** — Define the knowledge graph schema (Neo4j) for entities, dependencies, clusters, smells, metrics | Schema + migration scripts |
| 1.4 | Postdoc | **Code embedding pipeline** — Generate embeddings for source files using CodeBERT or StarCoder | `arcade_next.embeddings` module + ChromaDB integration |
| 1.5 | PI | **Evaluation framework** — Define ground truth datasets, metrics, and comparison methodology against original ARCADE | Evaluation plan document |

**Milestone 1:** Given a Git repo URL, produce a dependency graph and code embeddings for all versions — fully automated.

**Validation:** Run on 5 subject systems from original ARCADE paper. Compare dependency graphs against SciTools Understand output. Target: ≥95% recall on dependencies.

---

### Phase 2: Architecture Recovery (Months 3-6)
**Goal:** Implement and validate clustering algorithms with AI enhancement

| Task | Owner | Description | Deliverable |
|------|-------|-------------|-------------|
| 2.1 | PhD | **Port classical algorithms** — Reimplement ACDC, ARC, WCA, Limbo in Python using the same algorithmic approach | `arcade_next.clustering` module |
| 2.2 | PhD | **LLM-guided clustering** — New algorithm: use Claude API to suggest module boundaries based on code semantics, then validate with structural constraints | `arcade_next.clustering.llm_guided` |
| 2.3 | Postdoc | **Embedding-based clustering** — Use code embeddings + HDBSCAN/spectral clustering as a new recovery technique | `arcade_next.clustering.embedding_based` |
| 2.4 | Postdoc | **Consensus clustering** — Combine results from multiple algorithms (classical + AI) into a consensus architecture | `arcade_next.clustering.consensus` |
| 2.5 | PI+PhD | **Validation study** — Compare recovered architectures against ground truth for 10+ systems | Conference paper draft (Section: Recovery) |

**Milestone 2:** Recover architectures for 10+ systems using 6+ algorithms (4 classical + 2 AI-based). Demonstrate that AI-based approaches achieve comparable or better accuracy.

**Key Research Questions:**
- RQ1: Does LLM-guided clustering produce architectures that better match expert ground truth?
- RQ2: Does embedding-based similarity outperform LDA-based similarity for concern identification?
- RQ3: Does consensus clustering reduce false positives compared to any single algorithm?

---

### Phase 3: Smell Detection & Explanation (Months 6-9)
**Goal:** Detect architectural smells and provide AI-powered explanations

| Task | Owner | Description | Deliverable |
|------|-------|-------------|-------------|
| 3.1 | PhD | **Port smell detectors** — Reimplement all 11 smell detectors from original ARCADE | `arcade_next.smells` module |
| 3.2 | Postdoc | **LLM smell validation** — Use Claude to validate detected smells: is this a true positive? What's the severity? | `arcade_next.smells.validator` |
| 3.3 | Postdoc | **Natural language explanations** — For each detected smell, generate: what it is, why it matters, affected components, and suggested refactoring | `arcade_next.smells.explainer` |
| 3.4 | PhD | **New smell discovery** — Use LLM to identify architectural issues beyond the 11 known smells | Taxonomy extension paper section |
| 3.5 | PI | **Developer study** — Survey developers: are LLM explanations more actionable than raw smell reports? | User study protocol + results |

**Milestone 3:** Detect smells with ≥90% precision (validated by LLM + human review). Generate actionable explanations rated "useful" by ≥80% of developers in user study.

---

### Phase 4: Evolution Analysis & Prediction (Months 9-12)
**Goal:** Track architectural evolution and predict decay

| Task | Owner | Description | Deliverable |
|------|-------|-------------|-------------|
| 4.1 | PhD | **Port evolution metrics** — a2a, MojoFM, cvg, c2c, MQ | `arcade_next.metrics` module |
| 4.2 | PhD | **Git-aware evolution** — Automatically analyze all tagged versions, track component birth/death/merge/split | `arcade_next.evolution` module |
| 4.3 | Postdoc | **AI-powered RecovAr** — Use LLM to correlate commits, PRs, and issues with architectural decisions (replacing manual JIRA/GitLab parsing) | `arcade_next.decisions` module |
| 4.4 | Postdoc | **Decay prediction model** — Train ML model to predict which components will decay, using historical data + LLM features | `arcade_next.prediction` module |
| 4.5 | PI+All | **Longitudinal study** — Run on 20+ systems, compare evolution patterns | Journal paper draft |

**Milestone 4:** Fully automated evolution analysis from Git history. Predict decay-prone components with ≥75% accuracy (AUC-ROC).

---

### Phase 5: Platform & Dissemination (Months 12-15)
**Goal:** Production-quality platform and publications

| Task | Owner | Description | Deliverable |
|------|-------|-------------|-------------|
| 5.1 | PhD | **Web UI** — Interactive visualization with D3.js: architecture graphs, smell heatmaps, evolution timelines, drill-down to code | Web application |
| 5.2 | PhD | **Conversational interface** — "Ask about your architecture" — natural language queries powered by Claude | Chat feature in web UI |
| 5.3 | Postdoc | **GitHub Action** — Run ARCADE-Next in CI/CD: architecture checks on PRs, decay alerts | `arcade-next-action` |
| 5.4 | Postdoc | **API documentation & SDK** — REST API docs, Python SDK for programmatic access | API docs + `arcade-next` PyPI package |
| 5.5 | PI | **Tool paper** — ARCADE-Next tool demonstration paper for ICSE/FSE/ASE | Submitted paper |
| 5.6 | All | **Open-source release** — GitHub repo, documentation, example notebooks | Public repository |

**Milestone 5:** Public release with documentation, example analyses of 20+ systems, and submitted tool paper.

---

## 4. Key Technical Decisions

### 4.1 Why Claude API for LLM Integration?

| Consideration | Decision |
|---------------|----------|
| **Code understanding** | Claude has strong code comprehension across languages |
| **Long context** | 200K token window allows analyzing large modules in a single call |
| **Tool use** | Claude's tool-use capability enables structured output (JSON architectures) |
| **Cost** | Haiku for bulk operations (embeddings, validation), Sonnet for analysis, Opus for complex reasoning |
| **Reproducibility** | Temperature=0 for deterministic results in experiments |

### 4.2 Hybrid AI Approach (Not Pure LLM)

> **PI:** We must NOT make this a "just throw it at GPT" paper. The value is in the hybrid approach:
> - **Classical algorithms** provide reproducible, well-understood baselines
> - **LLMs** add semantic understanding, explanations, and novel capabilities
> - **Embeddings** provide continuous representations for similarity
> - **Graph algorithms** (from JGraphT, now NetworkX) handle structural analysis
>
> Every LLM-enhanced step must be validated against classical baselines.

### 4.3 Replacing Mallet LDA with Modern Alternatives

| Current (Mallet LDA) | Proposed Alternatives |
|----------------------|----------------------|
| 50 fixed topics | Dynamic topic count via BERTopic |
| Bag-of-words features | Contextual embeddings (CodeBERT) |
| Manual stopword lists (5 languages) | Learned tokenization (no stopwords needed) |
| 250 iterations, sensitive to initialization | Deterministic transformer inference |
| No code structure awareness | Tree-sitter AST-aware parsing |

### 4.4 Replacing SciTools Understand

```
Current:  Source Code → SciTools Understand ($$$) → CSV → RSF
Proposed: Source Code → tree-sitter (free, 100+ langs) → Dependency Graph → Neo4j
```

tree-sitter advantages:
- Free and open source
- Supports 100+ programming languages via community grammars
- Incremental parsing (fast re-analysis on code changes)
- AST-level access (richer than just dependencies)
- Active community and maintenance

---

## 5. Evaluation Plan

### 5.1 Ground Truth Datasets

| System | Language | Versions | SLOC | Ground Truth Source |
|--------|----------|----------|------|---------------------|
| Hadoop | Java | 20+ | ~2M | Apache architect validation |
| Struts | Java | 15+ | ~200K | Expert clustering |
| Chromium | C++ | 10+ | ~10M | Module owners files |
| Linux Kernel | C | 20+ | ~25M | MAINTAINERS file |
| Android Framework | Java | 10+ | ~5M | AOSP module structure |
| *New: VSCode* | TypeScript | 20+ | ~1M | Extension host boundaries |
| *New: Kubernetes* | Go | 15+ | ~3M | SIG ownership |
| *New: React* | JavaScript | 10+ | ~200K | Package boundaries |

### 5.2 Metrics for Comparison

| Metric | Purpose | Target |
|--------|---------|--------|
| MojoFM | Distance from ground truth | ≥ current ARCADE |
| a2a | Architecture-to-architecture similarity | Validated on known pairs |
| Precision/Recall | Smell detection accuracy | ≥90% precision |
| Developer survey | Explanation usefulness | ≥80% "useful" rating |
| Pipeline runtime | End-to-end analysis time | ≤50% of current ARCADE |
| Setup time | Time from repo URL to first results | < 5 minutes (vs. hours) |

---

## 6. Publication Strategy

| # | Title (Working) | Venue | Timeline | Focus |
|---|-----------------|-------|----------|-------|
| 1 | "LLM-Guided Architecture Recovery: A Hybrid Approach" | ICSE 2027 | Month 6-8 | Recovery algorithms (Phase 2) |
| 2 | "Explaining Architectural Smells with Large Language Models" | FSE 2027 | Month 9-11 | Smell detection + explanations (Phase 3) |
| 3 | "ARCADE-Next: An AI-Powered Platform for Continuous Architecture Analysis" | ASE 2027 Tool Track | Month 12-14 | Tool paper (Phase 5) |
| 4 | "Predicting Architectural Decay with Code Embeddings and LLMs" | TSE Journal | Month 14-18 | Evolution + prediction (Phase 4) |

---

## 7. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LLM hallucinations in architecture recovery | High | High | Always validate against structural constraints; hybrid approach with classical baselines |
| tree-sitter grammar gaps for some languages | Medium | Medium | Prioritize Java/C++/Python; fall back to regex-based extraction |
| LLM API costs for large-scale experiments | Medium | Medium | Use Haiku for bulk ops; cache embeddings; batch API calls |
| Reproducibility concerns with LLM-based results | High | High | Pin model versions; temperature=0; seed parameters; publish all prompts |
| Comparison unfairness (new data vs old baselines) | Medium | High | Run original ARCADE on same datasets; apple-to-apple comparison |
| Scope creep (too many features) | High | Medium | Strict phase gates; MVP for each phase before moving on |

---

## 8. Resource Requirements

| Resource | Details | Cost Estimate |
|----------|---------|---------------|
| **Compute** | GPU server for embedding generation (A100/H100) | University cluster (free) |
| **Claude API** | Haiku + Sonnet + Opus usage | ~$500-2000/month during experiments |
| **Storage** | Neo4j + ChromaDB for 50+ systems | ~100GB, university servers |
| **Subject systems** | Git repos (all open source) | Free |
| **Developer study** | 30+ participants for evaluation | IRB approval needed, ~$1500 in incentives |

---

## 9. Immediate Next Steps (Week 1-2)

- [ ] **PI:** Secure IRB approval process for developer study
- [ ] **PI:** Identify 3 subject systems for initial prototyping (suggest: Hadoop, Struts, a TypeScript project)
- [ ] **Postdoc:** Set up project repository with Python 3.12, uv, pre-commit hooks, CI
- [ ] **Postdoc:** Prototype tree-sitter dependency extraction for Java (compare against ARCADE's Classycle output)
- [ ] **PhD:** Prototype LLM-based module boundary suggestion on a small system (~50 classes)
- [ ] **PhD:** Review BERTopic and CodeBERT literature for code clustering
- [ ] **All:** Read and discuss 5 key related papers on LLM-based code analysis (list below)

### Required Reading

1. Wan et al. "Large Language Models for Software Engineering: A Systematic Literature Review" (2024)
2. Hou et al. "Large Language Models for Software Engineering: Survey and Open Problems" (2024)
3. Zhang et al. "CodeBERTScore: Evaluating Code Generation with Pretrained Models of Code" (2023)
4. Grootendorst "BERTopic: Neural topic modeling with a class-based TF-IDF procedure" (2022)
5. Garcia et al. "A Comparative Analysis of Software Architecture Recovery Techniques" (ICSE 2013) — *ARCADE's own foundational paper*

---

## 10. Decision Log

| Date | Decision | Rationale | Decided By |
|------|----------|-----------|------------|
| 2026-03-25 | Use Python as primary language | AI/ML ecosystem, faster prototyping | All |
| 2026-03-25 | Keep classical algorithms as baselines | Scientific rigor, reproducibility | PI |
| 2026-03-25 | Claude API as primary LLM | Code comprehension, tool use, long context | Postdoc |
| 2026-03-25 | tree-sitter for dependency extraction | Free, multi-language, maintained | PhD |
| 2026-03-25 | Hybrid approach (not pure LLM) | Avoid hallucination risks, maintain rigor | PI |

---

*This document is a living research plan. Update after each team meeting.*
