# Parser Robustness Bug Catalog

Reliability: `validated-2x`

This is the living catalog for parser failure classes that can abort or distort
whole-repository analysis. Entries use reproducible fixtures and design-time
prevention rules so the same defect is not rediscovered language by language.

## Design-time checklist

- Traverse untrusted AST depth with explicit stacks or queues, never Python recursion.
- Put every file extraction behind a transactional boundary: publish its entities only
  after extraction succeeds.
- Test nesting deeper than `sys.getrecursionlimit()` for every traversal shape.
- Pair each poisoned input with a healthy sibling file and assert the sibling survives.
- Track non-source inputs such as manifests when they affect graph identity or cache keys.
- Do not add per-parser input caps (file size, node counts) as a stand-in for robustness:
  they drop real code silently and never fix the traversal or complexity defect they appear
  to mitigate. The per-file exception boundary is the backstop. Caps are a legitimate
  *performance* tool for input that is genuinely not human-authored — `parsers/go.py` and
  `parsers/typescript.py` both keep a 1 MB `_MAX_FILE_BYTES` for vendored and minified
  bundles — but adopt one only after the underlying algorithm is linear, and say so
  explicitly rather than claiming other parsers have no cap.
- Run focused tests, the full suite, a large real repository, and arcade-agent's own
  self-analysis before publishing parser changes.

## 1. Kotlin deep-expression traversal aborted repository analysis

- **Symptom:** A machine-generated expression with thousands of nested parentheses
  raised `RecursionError`; valid sibling files disappeared because parsing aborted.
- **Root cause:** Recursive AST descent treated source nesting as trusted call-stack depth.
- **Detection:** Parse a deeply nested Kotlin file beside a valid file and assert the
  valid entity remains in the graph.
- **Fix:** Replace recursive descent with explicit stacks and isolate failures per file.
- **Prevention:** Apply the parser hardening skill to every new or materially changed
  tree-sitter traversal.
- First encountered: Kotlin parser follow-up `b7effc5`.
- **Pattern note:** First confirmed instance of cross-language AST depth fragility.

## 2. Rust path/use/module/type traversals repeated the recursion defect

- **Symptom:** Roughly 1,000 nested path segments, use groups, inline modules, or type
  wrappers raised `RecursionError` and killed analysis for healthy sibling files.
- **Root cause:** Four helpers used recursive descent even though `_references` already
  demonstrated the safe iterative pattern; extraction also ran outside the file-level
  exception boundary.
- **Detection:** Parameterize all four AST shapes above the Python recursion limit and
  parse each beside a valid Rust file.
- **Fix:** Use explicit LIFO worklists, publish per-file extraction state transactionally,
  and log-and-skip unexpected file-level failures.
- **Prevention:** Require the shared adversarial matrix and self-dogfood before parser PRs.
- First encountered: Rust parser PR #18 review, 2026-07-21.
- **Pattern note:** Second confirmed cross-language instance. Keep the class on the
  design checklist; wait for a third instance before naming a broader meta-pattern.

## 3. Rust package membership index was quadratic in entities per package

- **Symptom:** A 5.2 MB generated `.rs` file (79,500 entities) took 70 s to parse. Parse
  time grew with the square of the entity count, so large real crates looked like hangs.
- **Root cause:** `add_entity` guarded the per-package entity list with `if fqn not in
  package_entities`, a linear scan of a list that grows to tens of thousands of entries;
  the cross-file merge repeated the same `not in` test inside a generator expression.
- **Detection:** Generate one large single-package source file and time the parse; the
  entity count is a fine proxy for the input size the cap was hiding.
- **Fix:** Keep a companion `set` next to each ordered list purely for membership, and
  reset it wherever the list it shadows is reset (the Rust parser clears per-file state
  each iteration — a stale set would leak entities across files).
- **Prevention:** For any de-duplicated *ordered* collection, pair the list with a set at
  the moment it is introduced. Treat "an input cap makes this fast enough" as a signal
  that a container is being scanned linearly.
- First encountered: Rust parser reland, 2026-08-10 (70.2 s → 3.0 s, 23x, identical
  entity and edge counts).
- **Pattern note:** The mirror image of class 1/2 — not a crash, a silent complexity cliff
  that an input cap conceals instead of fixing.

## 4. Conditional-compilation test gating leaks through its less common shapes

- **Symptom:** With `exclude_tests=True`, a Rust probe crate with 2 production structs and
  10 test-only entities still yielded 10 entities, 8 of them test-only. Every production
  entity also carried a phantom `mockall` import, inflating fan-out and inventing coupling
  to dev-only crates.
- **Root cause:** The exclusion matched one syntactic shape (`#[cfg(test)]` normalizing to
  exactly that text, on an outer `attribute_item`, attached to an item with a body). Four
  other shapes bypassed it: compound predicates (`cfg(all(test, ...))`, `cfg(any(test, ...))`),
  inner `#![cfg(test)]` on a file or module body, out-of-line `#[cfg(test)] mod x;` whose
  backing file was later parsed as independent production source, and `#[cfg(test)] use ...`
  which was collected by an import pass that ran before attributes were inspected.
- **Detection:** Build one probe crate containing *every* shape and assert the production
  entity set exactly, not just the absence of one fixture name. Assert on `entity.imports`
  too — import leaks are invisible in entity counts.
- **Fix:** Evaluate the cfg predicate tree rather than string-matching (while leaving
  `not(test)` alone, which marks production-only code), inspect inner attributes, make the
  import pass attribute-aware, and record out-of-line test module paths so their files are
  skipped — which requires visiting a module's declaring file before the module's own file.
- **Prevention:** When a language gates test code by annotation rather than by path,
  enumerate the annotation's full grammar before implementing the filter; a single
  normalized string comparison is a smell.
- First encountered: Rust parser reland, 2026-08-10.
- **Pattern note:** Applies to any annotation-gated exclusion (Go build tags,
  C/C++ `#ifdef`), not only Rust.
