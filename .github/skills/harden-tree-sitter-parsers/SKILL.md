---
name: harden-tree-sitter-parsers
description: Harden new or changed tree-sitter parsers against adversarial nesting, malformed files, partial-state leaks, and stale non-source cache inputs.
reliability: validated-2x
---

# Harden Tree-sitter Parsers

Use this skill for new parser implementations, parser reviews, recursion failures,
or changes to AST traversal and linking.

## Required workflow

1. Inventory every AST traversal helper and classify it as iterative or recursive.
2. Replace source-depth recursion with an explicit stack or queue. Preserve traversal
   order deliberately and avoid repeated tuple/list copying where practical.
3. Extract each file into isolated temporary state. Merge entities, edges, imports,
   packages, and pending links only after the file succeeds.
4. Log skipped files with the failure class; do not silently discard valid siblings.
5. Add adversarial fixtures deeper than `sys.getrecursionlimit()` for every distinct
   traversal shape. Each fixture must be parsed beside a valid sibling file.
6. Test cache invalidation for manifests or configuration that changes graph identity.
7. For configurable module resolution, model resolved-local, external, and
   unresolved-local as distinct outcomes; publish bounded coverage diagnostics and qualify
   graph-derived scores when local resolution or symbol linking is incomplete.
8. Time the parser on one large generated single-package file before declaring it done.
   A de-duplicated ordered collection guarded by `if x not in list` is quadratic and only
   shows up at scale (see `docs/BUG_CATALOG.md` #3).
9. Run, in order:
   - focused parser and cache tests;
   - Ruff and the full test suite;
   - a large real repository for the target language, reporting wall-clock time;
   - arcade-agent self-analysis before/after, reporting metric and smell deltas.
9. Record any newly discovered reusable failure class in `docs/BUG_CATALOG.md`.

## Acceptance invariants

- No `RecursionError` for valid tree-sitter AST depth within the configured file limit.
- One malformed or adversarial file cannot erase healthy sibling entities.
- No partial entities from a failed file enter the final graph.
- No dangling edges, missing method owners, or duplicate package membership.
- Parse time grows linearly, not quadratically, with entity count.
- Relevant non-source inputs invalidate cached graphs, and so do flags such as
  `exclude_tests` that change the graph for an identical file list.
- Test exclusion covers every syntactic shape the language offers, imports included.
- Correctness and explicit failure behavior take precedence over cosmetic metric gains.
- Bare imports that match local manifests or resolver configuration are never silently
  collapsed into the external-dependency bucket.

## Evidence

- Kotlin follow-up `b7effc5`: iterative deep-expression traversal and sibling survival.
- Rust PR #18: iterative path/use/module/type traversal, transactional file extraction,
  Cargo-aware cache invalidation, and adversarial regression matrix.
- Rust reland: linear package membership (70.2 s -> 3.0 s on a 5.2 MB generated file, with
  identical entity and edge counts) and full `#[cfg(test)]` shape coverage.
