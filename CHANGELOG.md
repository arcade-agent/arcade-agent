# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

### Fixed

- TypeScript/JavaScript local bare imports are no longer silently treated as external.
  The parser resolves inherited JSONC `baseUrl`/`paths` aliases and npm workspace package
  manifests, tracks their non-source files in the parse cache key, and reports
  resolved/external/unresolved plus linked/unlinked coverage. Architecture metric details
  now carry a visible graph-quality qualifier when discovered local dependencies remain
  incomplete, without changing the numeric formulas.

## 0.3.0 — 2026-08-21

Also in this release: **Rust** parser support, **polyglot multi-language
parsing** (`--language multi`), and TurboMQ redefined as the raw sum of
cluster factors with BasicMQ as the normalized variant — see the linked PRs
on the release page for details.

### Added

- `changelog_architecture` tool: an architectural changelog between two
  recovered versions of a codebase — components added, removed, renamed,
  split or merged; entities that changed component ("responsibility
  shifts"); smells gained or resolved; and metric deltas.
- `algorithms/provenance.py`: entity-provenance-based classification of
  structural change (`classify_structural_changes`), used by both `compare`
  and `changelog_architecture` so the two tools never disagree about which
  components were genuinely added, removed, split or merged.
- `ingest(ref=...)`: materialise a specific commit, tag or branch into a
  temporary tree via `git archive`, without touching the caller's working
  tree, index or HEAD.
- `arcade-arch-diff` now renders the architectural changelog inside its PR
  comment when a baseline exists.

### Breaking

- `compare`'s `summary.possible_splits` / `summary.possible_merges` keys are
  **removed**. Use `summary.splits` / `summary.merges` (counts) and the new
  `structural.split` / `structural.merged` (detail) instead.

  The old keys were derived from the Hungarian algorithm's 1:1 component
  matching (`similarity < 0.5` plus which side's entity count grew), which
  is structurally incapable of representing a split (one source component →
  multiple targets) or a merge (multiple sources → one target): a 1:1
  match can only ever report *one* target or *one* source per component. In
  practice this mislabelled ordinary renames as splits/merges by whichever
  way the entity count happened to move, and double-counted genuine splits
  as both a low-similarity match *and* a separate "added" component.

  `compare`'s return also gains an additive `structural` key — the same
  provenance-derived classification `changelog_architecture` uses — carrying
  the accurate `added` / `removed` / `renamed` / `split` / `merged` /
  `rewritten` / `stable` component names. `matches` (the raw Hungarian 1:1
  view) is unchanged. `summary.components_added` /
  `summary.components_removed` keep their keys but are now
  provenance-derived: a split product, a merge source or a rewritten
  component no longer counts as added/removed, so consumers asserting on the
  old Hungarian-unmatched counts will see different numbers for those
  scenarios. `summary` also gains `components_rewritten`.

- **Structural change output gains a `rewritten` bucket.** `added` and
  `removed` are now about component *names*: a name is `removed` only when
  the later architecture has none by that name, and `added` only when the
  earlier one has none. `structural.rewritten` carries
  `{name, before, after, retained}` entries for components whose name
  survived while their entities churned entirely. Consumers that enumerate
  the structural buckets — or that assume `added`/`removed` cover every
  changed component — must handle the new key.

### Fixed

- `_clone_and_ingest` no longer leaks the cloned repository (including
  `.git`) when `_build_ingested_repo` narrows the ingested path to a
  detected source root (e.g. `src/main/java`); `cleanup()` now removes the
  whole clone.
- `render_changelog_markdown`'s embedded heading no longer collides with the
  drift report's own heading level (`## Architectural changes` inside a
  `## Architecture Drift Report` re-parented the report's `### Smells`
  section); it now accepts `heading_level` and `arcade-arch-diff` passes
  `heading_level=3`.
- A component rewritten in place is no longer reported as **both** added and
  removed. Such a component keeps its name while every entity it holds
  churns, so it has no significant entity flow in either direction and fell
  into `added` *and* `removed` at once: `arcade-arch-diff` printed
  "added `PkgAuth`" directly above "removed `PkgAuth`" above a table showing
  it unchanged, and the `arcade-compare-baseline` PR comment reported
  `Matched Components: 2` alongside `Components Added: 1` /
  `Components Removed: 1` for two components. It is now a single `rewritten`
  entry (`12 → 12 entities, none in common`), counted in neither bucket. The
  same fix covers a component present in both architectures with zero
  entities, which is now `stable`, and a surviving name that is the
  destination of a rename or merge, which is no longer also called removed.
- `ingest(ref=...)` archives the `git rev-parse`-resolved SHA rather than
  the raw `ref` string, closing a TOCTOU window and a narrow argument-
  injection surface for refs beginning with `-`.

## 0.2.0 and earlier

See GitHub releases: https://github.com/lemduc/arcade-agent/releases
