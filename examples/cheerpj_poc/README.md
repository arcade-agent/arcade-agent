# Minimal CheerpJ runtime PoC

Runtime smoke only: the same Java 17 JAR reflects a method, reads the existing
Kotlin fixture (1,754 characters), validates both results, and exits. It does NOT
parse Kotlin, load Kotlin Analysis API, or resolve the fixture's 40 calls.

Prerequisites: JDK 17, Node.js, Playwright (`npm install --no-save playwright` in
a separate dependency environment if needed), and an installed Chrome/Chromium.
From the repository root:

```sh
mkdir -p examples/cheerpj_poc/build
javac --release 17 -d examples/cheerpj_poc/build examples/cheerpj_poc/Probe.java
jar --create --file examples/cheerpj_poc/build/probe.jar -C examples/cheerpj_poc/build Probe.class
cp examples/cheerpj_poc/MainActivity.kt examples/cheerpj_poc/build/MainActivity.kt
CHROME_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  node examples/cheerpj_poc/run.cjs > examples/cheerpj_poc/results.json
```

For Linux CI, supply its Chrome executable in CHROME_PATH, or install the
Playwright Chromium executable and omit that variable. The harness uses an
isolated headless browser profile, a loopback-only HTTP server with Range support,
and the vendor's hosted CheerpJ 4.3 runtime. It closes browser/server on completion
and returns nonzero on timeout, nonzero Java exit, or missing success markers.
The runtime is not redistributed or self-hosted.

## Observed locally, 2026-09-14

macOS ARM64; installed Chrome 153; native Corretto 17.0.17; CheerpJ reports
17.0.19-internal. Two fresh-browser invocations, three Java launches each:

| Measurement | First | Repeat |
| --- | --- | --- |
| Native JVM process wall time, three runs | 65 / 59 / 62 ms | 62 / 62 / 86 ms |
| CheerpJ loader + initialization | 1,621 ms | 1,688 ms |
| First CheerpJ program run, after initialization | 4,157 ms | 3,735 ms |
| Further launches in same page | 747 / 707 ms | 728 / 701 ms |
| Browser launch through all three runs | 8,112 ms | 7,650 ms |
| Successful runs | 3/3 | 3/3 |

Raw measurements are in local ignored `results.json` and `results-repeat.json`.
The network counter observes only this page's CDP session (about 1.43 MB); it
MUST NOT be reported as total runtime download size: worker traffic may be absent.
Browser/JDK installation, compilation, OS cache effects and peak RAM are not
measured. This is NOT a cold CI benchmark or semantic throughput comparison.
Repeated CheerpJ launches share a browser/runtime context; native samples start
separate JVM processes. Java patch versions also differ.

An initial harness failure showed that CheerpJ requires HTTP Range support for
JAR reads. Adding it produced successful execution. Aborted `/etc/localtime`
requests were observed but did not prevent the probe passing.

Conclusion: basic Java reflection/file access works in headless CheerpJ. On this
smoke workload, there is no observed startup advantage over the installed JVM.
Stop at this runtime gate for the lightweight-CI hypothesis; Kotlin Analysis API
compatibility and correctness remain UNTESTED. A semantic PoC would need the same
extractor and classpath on both runtimes before making any semantic speed claim.

References:
- https://cheerpj.com/docs/getting-started/Java-app
- https://cheerpj.com/docs/reference/cheerpjInit

## GitHub Actions

`.github/workflows/cheerpj-poc.yml` runs three independent Ubuntu jobs on
pushes to `codex/cheerpj-ci-poc`. Each installs pinned Playwright/Chromium without
restoring a dependency cache, compiles the probe, compares native Java with
CheerpJ, and uploads raw JSON plus environment details. Setup time and probe
timing are reported separately. No application semantic analysis is performed.
