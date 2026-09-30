# Java Structural Analysis Case Studies

These five Java repositories are living testbeds for `arcade-agent`. Their
GitHub Actions workflows publish architecture dashboards and badges on pushes,
manual runs and a monthly schedule. The findings below describe the recorded
structural graph and identify places where source inspection is needed.

## Report snapshot and provenance

The badge counts were checked on 2026-09-29 (America/Chicago). Live reports can
change; the source commits and successful analysis runs below identify the
reviewed snapshots. Counts from different recovery configurations are not a
ranking of project quality. Zero detected smells does not establish complete
coverage or absence of architectural problems.

| Testbed | Components | Detected smells | Source snapshot | Successful analysis |
| --- | ---: | ---: | --- | --- |
| [parallel-collectors](https://arcade-agent.github.io/parallel-collectors/) | 97 | 0 | [`dfa509f`](https://github.com/arcade-agent/parallel-collectors/tree/dfa509ffc54bae6da1db06b262b6877a97d75146) | [Run 36423978934](https://github.com/arcade-agent/parallel-collectors/actions/runs/36423978934) |
| [LMAX Disruptor](https://arcade-agent.github.io/disruptor/) | 294 | 5 | [`451115e`](https://github.com/arcade-agent/disruptor/tree/451115ed905b0784303118435eec1ffacf5be7fc) | [Run 36513625373](https://github.com/arcade-agent/disruptor/actions/runs/36513625373) |
| [HikariCP](https://arcade-agent.github.io/HikariCP/) | 10 | 7 | [`cb11fe4`](https://github.com/arcade-agent/HikariCP/tree/cb11fe41c3a91e92ad36027e1c1f3c29af1124bf) | [Run 36513574440](https://github.com/arcade-agent/HikariCP/actions/runs/36513574440) |
| [Caffeine](https://arcade-agent.github.io/caffeine/) | 766 | 4 | [`e7a32ff`](https://github.com/arcade-agent/caffeine/tree/e7a32ff3a5b8675fa623b3352f805a846ea538dd) | [Run 36513806260](https://github.com/arcade-agent/caffeine/actions/runs/36513806260) |
| [Dataverse](https://arcade-agent.github.io/dataverse/) | 189 | 86 | [`597dbac`](https://github.com/arcade-agent/dataverse/tree/597dbac363fc14b26a7cbe07bfa2280188621ac1) | [Run 36512459288](https://github.com/arcade-agent/dataverse/actions/runs/36512459288) |

The reviewed workflows pin `arcade-agent/analyze-action` to
[`4db79fe26878a80405020f293fa478d42737c200`](https://github.com/arcade-agent/analyze-action/tree/4db79fe26878a80405020f293fa478d42737c200)
(v1.3.0). This identifies the action wrapper; it is separate from the installed
`arcade-agent` package version. Disruptor, HikariCP and Dataverse explicitly
analyze `src/main/java` with `language: java`; Caffeine uses
`caffeine/src/main/java`. The parallel-collectors workflow uses action defaults.
Reproduction must retain the full workflow inputs, package version, source
scope, recovery algorithm and exclusions from the corresponding run.

This is a case-study snapshot, not a controlled performance benchmark. The
previous 4.5-second Dataverse claim is omitted because its hardware, measured
stages, cache state and repeated timings were not recorded here. A speed claim
requires those details, the exact analyzer version and comparable workloads.

## Disruptor: a core-to-DSL type dependency

Upstream: [LMAX-Exchange/disruptor](https://github.com/LMAX-Exchange/disruptor).

The pinned [RingBuffer source](https://github.com/arcade-agent/disruptor/blob/451115ed905b0784303118435eec1ffacf5be7fc/src/main/java/com/lmax/disruptor/RingBuffer.java#L19)
imports `com.lmax.disruptor.dsl.ProducerType` and uses that enum in a factory
signature. The [DSL Disruptor source](https://github.com/arcade-agent/disruptor/blob/451115ed905b0784303118435eec1ffacf5be7fc/src/main/java/com/lmax/disruptor/dsl/Disruptor.java)
uses `RingBuffer`. Together these establish a bidirectional **package** dependency
between core and DSL. They do not establish a class cycle between `RingBuffer`
and the enum, or a runtime performance defect.

[SingleProducerSequencer](https://github.com/arcade-agent/disruptor/blob/451115ed905b0784303118435eec1ffacf5be7fc/src/main/java/com/lmax/disruptor/SingleProducerSequencer.java)
does not import `ProducerType`; its occurrences are in a diagnostic string and
Javadoc. It should not be cited as evidence for the executable dependency.

A possible design discussion is to expose the producer policy in the core API.
Moving an existing public enum changes signatures and can break clients. Java
enums cannot extend another enum, so an extending enum alias is not a migration
strategy. A proposal would need compatible overloads and explicit conversion
from the existing DSL enum, plus source/binary compatibility tests and agreement
from the maintainer. No upstream fix or performance improvement is claimed.

## HikariCP: documentation attribution in the import graph

Upstream: [brettwooldridge/HikariCP](https://github.com/brettwooldridge/HikariCP).

The pinned [HikariPool source](https://github.com/arcade-agent/HikariCP/blob/cb11fe41c3a91e92ad36027e1c1f3c29af1124bf/src/main/java/com/zaxxer/hikari/pool/HikariPool.java#L20)
imports `HikariDataSource`; its other occurrences are in comments, including a
Javadoc link. `HikariDataSource` also uses `HikariPool`. An import-based graph
therefore includes an edge in both directions, but this evidence does not show
an executable call from the pool to the datasource.

The analyzer currently attributes file imports to class and method entities
without checking whether each entity references the imported type. This is
tracked in [arcade-agent #47](https://github.com/arcade-agent/arcade-agent/issues/47).
The report should distinguish import/documentation attribution from resolved
usage before presenting the cycle as executable coupling.

Using a fully qualified Javadoc link can remove an import-attribution edge in
this parser, but the documentation still references the same class. Such an
editorial change is not proof of removing a runtime architectural defect.

## Caffeine: documentation references in statistics types

Upstream: [ben-manes/caffeine](https://github.com/ben-manes/caffeine).

The pinned statistics sources
([StatsCounter](https://github.com/arcade-agent/caffeine/blob/e7a32ff3a5b8675fa623b3352f805a846ea538dd/caffeine/src/main/java/com/github/benmanes/caffeine/cache/stats/StatsCounter.java),
[CacheStats](https://github.com/arcade-agent/caffeine/blob/e7a32ff3a5b8675fa623b3352f805a846ea538dd/caffeine/src/main/java/com/github/benmanes/caffeine/cache/stats/CacheStats.java),
[ConcurrentStatsCounter](https://github.com/arcade-agent/caffeine/blob/e7a32ff3a5b8675fa623b3352f805a846ea538dd/caffeine/src/main/java/com/github/benmanes/caffeine/cache/stats/ConcurrentStatsCounter.java))
import `Cache` for documentation links. `Cache.stats()` exposes `CacheStats`, so
an import-based report can show a `cache`/`stats` package cycle. Inspecting the
relation kinds and actual references is necessary before interpreting it as
executable coupling. This is another useful testbed for #47.

Changing the spelling of a Javadoc reference does not prove that the statistics
package has zero dependencies or that its runtime design improved.

## Dataverse: a large structural cycle candidate

Upstream: [IQSS/dataverse](https://github.com/IQSS/dataverse).

The snapshot reports 189 components and 86 smells. The large reported dependency
cycle is an investigation target. The Java parser in the released analysis path
emits `import`, `extends` and `implements` edges; it does not resolve method-call
edges. The report therefore cannot establish mutual service cross-calls or
runtime dispatch from the graph alone. File imports attributed to methods can
also inflate entity-edge counts.

Before proposing a refactor, inspect the cycle's source imports, relation kinds,
component membership and used types. Separate documentation-only attribution
from executable structural dependencies, and confirm call-level claims with
source or a compiler-backed analysis.

## Reading these reports

- Keep parser coverage and relation provenance beside architecture conclusions.
- Treat smell counts as investigation signals within a fixed configuration.
- Preserve source and analyzer versions when comparing baselines.
- Validate performance with a separate, reproducible measurement protocol.
