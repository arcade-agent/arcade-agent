# Real-World Java Benchmarks & Architectural Case Studies

To validate `arcade-agent` in real-world environments, we benchmarked and deployed continuous architectural analysis across five prominent Java open-source repositories representing diverse design paradigms: from ultra-high-speed concurrency primitives to enterprise-scale monoliths.

All repositories are continuously monitored via [`arcade-agent/analyze-action`](https://github.com/arcade-agent/analyze-action) on GitHub Actions, with interactive architecture visualizers and dynamic shields badges deployed to GitHub Pages.

---

## 📊 Benchmark Summary Matrix

| Project | Domain | Scale (Files / Entities) | Components | Detected Smells | Live Architecture Report | Badge Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| [**parallel-collectors**](https://github.com/arcade-agent/parallel-collectors) | Concurrent Stream Collectors | 21 files / 185 entities | 97 | **0 smells** | [Interactive Report](https://arcade-agent.github.io/parallel-collectors/) | ![Clean](https://img.shields.io/endpoint?url=https://arcade-agent.github.io/parallel-collectors/badge.json) |
| [**LMAX Disruptor**](https://github.com/arcade-agent/disruptor) | Low-Latency Messaging | 71 files / 373 entities | 294 | **5 smells** | [Interactive Report](https://arcade-agent.github.io/disruptor/) | ![Disruptor](https://img.shields.io/endpoint?url=https://arcade-agent.github.io/disruptor/badge.json) |
| [**HikariCP**](https://github.com/arcade-agent/HikariCP) | Zero-Overhead JDBC Pool | 49 files / 566 entities | 10 | **7 smells** | [Interactive Report](https://arcade-agent.github.io/HikariCP/) | ![HikariCP](https://img.shields.io/endpoint?url=https://arcade-agent.github.io/HikariCP/badge.json) |
| [**Caffeine**](https://github.com/arcade-agent/caffeine) | Near-Optimal In-Memory Cache | 51 files / 818 entities | 766 | **4 smells** | [Interactive Report](https://arcade-agent.github.io/caffeine/) | ![Caffeine](https://img.shields.io/endpoint?url=https://arcade-agent.github.io/caffeine/badge.json) |
| [**Dataverse**](https://github.com/arcade-agent/dataverse) | Enterprise Research Data Platform | 1,028 files / 13,265 entities | 189 | **86 smells** | [Interactive Report](https://arcade-agent.github.io/dataverse/) | ![Dataverse](https://img.shields.io/endpoint?url=https://arcade-agent.github.io/dataverse/badge.json) |

---

## 🔍 Case Study 1: LMAX Disruptor — Real Structural Dependency Inversion

- **Upstream:** [LMAX-Exchange/disruptor](https://github.com/LMAX-Exchange/disruptor)
- **Live Fork:** [arcade-agent/disruptor](https://github.com/arcade-agent/disruptor)
- **Detected Smell:** Dependency Cycle between `com.lmax.disruptor.RingBuffer` and `com.lmax.disruptor.dsl.ProducerType`

### The Architectural Flaw
`LMAX Disruptor` is renowned for mechanical sympathy and lock-free concurrency. The project separates its core ring buffer engine (`com.lmax.disruptor`) from its ergonomic builder facade (`com.lmax.disruptor.dsl`).

However, `arcade-agent` detected a bidirectional dependency cycle between the core and the DSL:
1. `com.lmax.disruptor.dsl.Disruptor` creates and wires `RingBuffer` (expected: DSL depends on Core).
2. `com.lmax.disruptor.RingBuffer` and `com.lmax.disruptor.SingleProducerSequencer` directly import `com.lmax.disruptor.dsl.ProducerType`!

```
┌─────────────────────────────────┐
│     com.lmax.disruptor.dsl      │
│  (Disruptor, ProducerType)      │
└────────────┬──────────▲─────────┘
             │          │
    creates  │          │ imports ProducerType
             ▼          │
┌───────────────────────┴─────────┐
│       com.lmax.disruptor        │
│  (RingBuffer, Sequencer)        │
└─────────────────────────────────┘
```

Because `ProducerType` (an enum denoting single vs multi producer sequencing) was placed in the `.dsl` package, the core sequencer engine cannot compile or be extracted without depending on its high-level wrapper.

### Proposed Upstream Contribution
- Move `ProducerType` to `com.lmax.disruptor.ProducerType` (or maintain `com.lmax.disruptor.dsl.ProducerType` as a deprecated alias extending/referencing the core enum).
- Breaking this cycle restores true layered architecture: Core primitives remain completely agnostic of the DSL.

---

## 🔍 Case Study 2: HikariCP — Phantom Cycle from Unused Javadoc Imports

- **Upstream:** [brettwooldridge/HikariCP](https://github.com/brettwooldridge/HikariCP)
- **Live Fork:** [arcade-agent/HikariCP](https://github.com/arcade-agent/HikariCP)
- **Detected Smell:** Dependency Cycle `HikariDataSource <-> HikariPool` within a 7-component tangle

### The Architectural Flaw
HikariCP is an ultra-fast, zero-overhead JDBC connection pool. `HikariDataSource` acts as the public JDBC facade wrapping `HikariPool`.
Naturally, `HikariDataSource` imports and holds an instance of `com.zaxxer.hikari.pool.HikariPool`.

However, `arcade-agent` flagged `HikariPool` as depending backwards on `HikariDataSource`.
Inspecting `com/zaxxer/hikari/pool/HikariPool.java`:
```java
package com.zaxxer.hikari.pool;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource; // <-- Line 20
```
In the entire 912 lines of `HikariPool.java`, `HikariDataSource` is **never referenced in code**. It only appears inside Javadoc comments:
```java
* through {@link HikariDataSource#evictConnection(Connection)} then {@code owner} is {@code true}.
```
The developer added the import solely so their IDE wouldn't warn about an unresolved `@link` in Javadoc. However, this introduced a compile-level dependency edge in static AST parsers, coupling the internal pool back to the outer DataSource facade.

### Proposed Upstream Contribution
- Remove `import com.zaxxer.hikari.HikariDataSource;` and use the fully qualified `{@link com.zaxxer.hikari.HikariDataSource#evictConnection}` in the Javadoc tag.
- This immediately breaks the cyclic dependency between pool internals and the datasource facade.

---

## 🔍 Case Study 3: Caffeine — Javadoc Import Tangling

- **Upstream:** [ben-manes/caffeine](https://github.com/ben-manes/caffeine)
- **Live Fork:** [arcade-agent/caffeine](https://github.com/arcade-agent/caffeine)
- **Detected Smell:** Dependency Cycle `Cache <-> Stats`

### The Architectural Flaw
Caffeine separates its statistics collection into a standalone package: `com.github.benmanes.caffeine.cache.stats`.
The core `Cache` interface depends on `stats` (e.g. `Cache.stats()` returns `CacheStats`).

However, `StatsCounter.java`, `CacheStats.java`, and `ConcurrentStatsCounter.java` in the `stats` sub-package all import `com.github.benmanes.caffeine.cache.Cache`.
Just like HikariCP, these classes never execute any methods or declare any fields of type `Cache` — the import exists purely to satisfy `@link Cache#stats` in documentation!

### Proposed Upstream Contribution
- Decouple `com.github.benmanes.caffeine.cache.stats` by removing the unused `Cache` import and using fully-qualified links in Javadoc.
- Allows `stats` to serve as a pure, zero-dependency statistical telemetry package.

---

## 🔍 Case Study 4: Harvard Dataverse — Monolithic Architectural Tangle

- **Upstream:** [IQSS/dataverse](https://github.com/IQSS/dataverse)
- **Live Fork:** [arcade-agent/dataverse](https://github.com/arcade-agent/dataverse)
- **Scale:** 1,028 Java files, 13,265 entities, 178,506 dependency edges.
- **Detected Smells:** **86 smells**, including a massive **113-component circular dependency tangle**.

### The Architectural Flaw
Dataverse is an established Java EE enterprise monolith. Over a decade of organic development, business services (`DatasetServiceBean`, `DataverseServiceBean`, `AuthenticationServiceBean`) have developed mutual bidirectional cross-calls.
`arcade-agent` parsed all 178,506 edges and detected this 113-node mega-cycle in just **4.5 seconds**.

This demonstrates `arcade-agent`'s capacity to handle heavy enterprise architectures where manual cycle detection is impossible.

---

## 💡 Key Takeaways for Architectural Engineering

1. **Unused Imports are not Harmless:** In high-profile projects like HikariCP and Caffeine, unused imports kept for Javadoc create false-positive dependency edges and phantom architectural cycles. Clean architecture linters should enforce zero unused imports even for Javadoc.
2. **Layer Inversion Happens Naturally:** Even in pristine libraries like LMAX Disruptor, utility enums like `ProducerType` often end up placed in higher-level packages (`dsl`), accidentally dragging core engines into bidirectional cycles.
3. **Continuous Enforcement via GitHub Actions:** By integrating `arcade-agent/analyze-action` into CI with pinned commit hashes, teams can detect these architectural drifts before they merge into release branches.
