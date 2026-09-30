# Kotlin relation coverage regression

This synthetic fixture isolates missing-call coverage from component recovery.
`MainActivity` is a single-computation chain, not an Android Activity. Its methods
cross the default HIGH entity-count threshold, although the Kotlin parser only
collects imports and inheritance. Sparse recorded edges do not establish that
there are multiple responsibilities.

Run after installing `.[languages,dev]`:

```sh
PYTHONPATH=src python tests/fixtures/kotlin_dependency_coverage/reproduce.py
python -m pytest -q tests/test_tools/test_kotlin_dependency_coverage.py
```

Both paths use `language=kotlin`, `algorithm=pkg`, `exclude_tests=true`,
`use_cache=false`, and `use_llm=false`. The script prints graph capabilities,
detector evidence, smells and metrics. Through MCP, analyze the fixture's `src`
directory and retrieve graph and smell sessions with `get_full_result`.

## Before and after

The reproduction PR originally used main baseline
`1b3532bcf5ce20d6553dda5ffdd5e23f0bfdabac` (package 0.3.0). The old guard test
failed: three tests passed and the fourth reported an unqualified HIGH concern
overload for Ui. Historical counts were 51 entities / 12 import edges; the three
renderer imports were attributed to nine class/method edges. These are snapshot
observations, not contracts enforced by the regression tests.

The fix declares `relation_coverage` in graph metadata. A sparse candidate with
incomplete Kotlin/Rust call coverage remains visible as a LOW **insufficient
coverage** finding. Its explanation does not claim multiple responsibilities or
recommend splitting the component before inspecting source or collecting calls.
Graph merge, filtering, cache and serialization retain the coverage declaration;
the parse-cache schema changes so old entries cannot hide it.

The regression runs normally without `xfail`. It permits suppression, downgrade
or visible qualification, but rejects an unqualified HIGH conclusion. Relational
assertions avoid pinning exact parser entity/import counts. The controlled
counterfactual still adds the 40 calls explicitly present in source and removes
the sparse candidate; it is not a Kotlin call resolver.

`TimelineFrameRenderer.close()` is empty. Import edges assigned to it represent
file attribution, not resolved usages. Import attribution and semantic call
resolution remain separate follow-ups in [#43](https://github.com/arcade-agent/arcade-agent/issues/43).
The independent Data → Export → View → Data import cycle remains detectable.

Real Android classpaths and compiler semantic integration are outside this
fixture. See #43 for the separately scoped project observations and PoC limits.
