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

All five runs installed `arcade-agent[languages]==0.3.0` (the maintainer's
reproduction note records tree-sitter 0.26.0 and tree-sitter-java 0.23.5 for
those runs). Counts in the two left-hand columns below were produced by that
0.3.0 release. Later fixes (#52, #58, #59) change them substantially; see the
two right-hand columns, which @lemduc re-ran on 2026-10-07 at the same pinned
source commits with `main` plus #58 and #59 applied. #58 and #59 have since
merged into `main` (release 0.4.0), so the right-hand columns describe the
current analyzer on `main`.

| Testbed | Components (0.3.0) | Detected smells (0.3.0) | Components (after #52/#58/#59) | Detected smells (after #52/#58/#59) | Source snapshot | Successful analysis |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| [parallel-collectors](https://arcade-agent.github.io/parallel-collectors/) | 97 | 0 | 1 | 0 | [`dfa509f`](https://github.com/arcade-agent/parallel-collectors/tree/dfa509ffc54bae6da1db06b262b6877a97d75146) | [Run 36423978934](https://github.com/arcade-agent/parallel-collectors/actions/runs/36423978934) |
| [LMAX Disruptor](https://arcade-agent.github.io/disruptor/) | 294 | 5 | 3 | 2 | [`451115e`](https://github.com/arcade-agent/disruptor/tree/451115ed905b0784303118435eec1ffacf5be7fc) | [Run 36513625373](https://github.com/arcade-agent/disruptor/actions/runs/36513625373) |
| [HikariCP](https://arcade-agent.github.io/HikariCP/) | 10 | 7 | 5 | 4 | [`cb11fe4`](https://github.com/arcade-agent/HikariCP/tree/cb11fe41c3a91e92ad36027e1c1f3c29af1124bf) | [Run 36513574440](https://github.com/arcade-agent/HikariCP/actions/runs/36513574440) |
| [Caffeine](https://arcade-agent.github.io/caffeine/) | 766 | 4 | 2 | 1 | [`e7a32ff`](https://github.com/arcade-agent/caffeine/tree/e7a32ff3a5b8675fa623b3352f805a846ea538dd) | [Run 36513806260](https://github.com/arcade-agent/caffeine/actions/runs/36513806260) |
| [Dataverse](https://arcade-agent.github.io/dataverse/) | 189 | 86 | 48 | 29 | [`597dbac`](https://github.com/arcade-agent/dataverse/tree/597dbac363fc14b26a7cbe07bfa2280188621ac1) | [Run 36512459288](https://github.com/arcade-agent/dataverse/actions/runs/36512459288) |

The reviewed workflows pin `arcade-agent/analyze-action` to
[`4db79fe26878a80405020f293fa478d42737c200`](https://github.com/arcade-agent/analyze-action/tree/4db79fe26878a80405020f293fa478d42737c200)
(v1.3.0). This identifies the action wrapper; it is separate from the installed
`arcade-agent` package version named above. The live badges keep showing the
0.3.0 numbers until a newer release ships, because
`analyze-action@4db79fe` installs the latest PyPI version. Entity counts are
unchanged between the two builds; post-fix entity-edge counts are
parallel-collectors 30 → 80, Disruptor 809 → 505, HikariCP 1,370 → 556,
Caffeine 2,688 → 560 and Dataverse 178,506 → 20,307 (all as reported by
@lemduc's re-run). Disruptor, HikariCP and Dataverse explicitly
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

In the fixed-analyzer graph (@lemduc's re-run), the reported cycle also passes
through `util`: `util.Util` imports `EventProcessor` and `Sequence`, and both
core and DSL import `Util`. The core↔DSL claim itself is unchanged.

A possible design discussion is to expose the producer policy in the core API.
Moving an existing public enum changes signatures and can break clients. Java
enums cannot extend another enum, so an extending enum alias is not a migration
strategy. A proposal would need compatible overloads and explicit conversion
from the existing DSL enum, plus source/binary compatibility tests and agreement
from the maintainer. No upstream fix or performance improvement is claimed.

## HikariCP: a pool/root-package cycle surviving the attribution fix

Upstream: [brettwooldridge/HikariCP](https://github.com/brettwooldridge/HikariCP).

The pinned [HikariPool source](https://github.com/arcade-agent/HikariCP/blob/cb11fe41c3a91e92ad36027e1c1f3c29af1124bf/src/main/java/com/zaxxer/hikari/pool/HikariPool.java#L20)
imports `HikariDataSource`; its other occurrences are in comments, including a
Javadoc link. `HikariDataSource` also uses `HikariPool`. The 0.3.0 graph
therefore included an edge in both directions, but that evidence did not show
an executable call from the pool to the datasource.

arcade-agent 0.3.0 attributed every file import to each class and method in the
file. [#52](https://github.com/arcade-agent/arcade-agent/pull/52) (closing
[#47](https://github.com/arcade-agent/arcade-agent/issues/47)) now keeps an
import only where the code references it, which removes the
`HikariPool → HikariDataSource` edge. The pool↔root-package cycle persists
through executable references: `HikariPool` implements `HikariPoolMXBean` and
takes a `HikariConfig`, while `HikariDataSource` constructs `HikariPool`. This
supersedes the earlier reading in which import/documentation attribution had
to be distinguished from resolved usage before the cycle could be presented
as executable coupling; under 0.3.0 that distinction was missing.

Using a fully qualified Javadoc link can remove an import-attribution edge in
the 0.3.0 parser, but the documentation still references the same class. Such
an editorial change is not proof of removing a runtime architectural defect.

## Caffeine: documentation references in statistics types

Upstream: [ben-manes/caffeine](https://github.com/ben-manes/caffeine).

The pinned statistics sources
([StatsCounter](https://github.com/arcade-agent/caffeine/blob/e7a32ff3a5b8675fa623b3352f805a846ea538dd/caffeine/src/main/java/com/github/benmanes/caffeine/cache/stats/StatsCounter.java),
[CacheStats](https://github.com/arcade-agent/caffeine/blob/e7a32ff3a5b8675fa623b3352f805a846ea538dd/caffeine/src/main/java/com/github/benmanes/caffeine/cache/stats/CacheStats.java),
[ConcurrentStatsCounter](https://github.com/arcade-agent/caffeine/blob/e7a32ff3a5b8675fa623b3352f805a846ea538dd/caffeine/src/main/java/com/github/benmanes/caffeine/cache/stats/ConcurrentStatsCounter.java))
import `Cache` for documentation links. Under 0.3.0 these documentation imports
contributed to a `cache`/`stats` cycle. Since #52 they produce no edges, but
the cycle remains: `StatsCounter.recordEviction(int, RemovalCause)` and its
implementations reference `cache.RemovalCause`, and `cache` uses
`StatsCounter`/`CacheStats`. The remaining dependency is a genuine
signature-level one. Inspecting the relation kinds and actual references is
still necessary before interpreting it as executable coupling; this testbed is
what motivated #47.

Changing the spelling of a Javadoc reference does not prove that the statistics
package has zero dependencies or that its runtime design improved.

## Dataverse: a large structural cycle candidate

Upstream: [IQSS/dataverse](https://github.com/IQSS/dataverse).

The fixed analyzer reports 48 components and 29 smells (1 dependency cycle
spanning 40 components, 13 Concern Overload, 11 Scattered Parasitic
Functionality, 4 Link Overload); arcade-agent 0.3.0 reported 189 and 86. Note
that 40 of 48 components is a larger share of the cycle than the 0.3.0 build's
113 of 189. The large reported dependency cycle is an investigation target.
The Java parser in the released analysis path emits `import`, `extends` and
`implements` edges (plus same-package `uses` edges after #59); it does not
resolve method-call edges. The report therefore cannot establish mutual
service cross-calls or runtime dispatch from the graph alone. In 0.3.0, file
imports attributed to every method inflated entity-edge counts (178,506 edges
vs 20,307 after #52/#59). Imports are still recorded at both class and method
level.

Before proposing a refactor, inspect the cycle's source imports, relation kinds,
component membership and used types. Separate documentation-only attribution
from executable structural dependencies, and confirm call-level claims with
source or a compiler-backed analysis.

## Reading these reports

- Keep parser coverage and relation provenance beside architecture conclusions.
- Treat smell counts as investigation signals within a fixed configuration.
- Preserve source and analyzer versions when comparing baselines.
- Validate performance with a separate, reproducible measurement protocol.
