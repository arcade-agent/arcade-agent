# Kotlin semantic analysis and compilation on native JVM / CheerpJ

This PoC compiles real Kotlin source using the pinned K2 compiler 2.0.21.
A compiler plugin visits **resolved IR before JVM lowering** to collect caller,
callee, Kotlin signatures, source file and line. The driver separately inspects
compiled bytecode for JVM signatures and calls. This is a compiler-plugin
backend, not the standalone Kotlin Analysis API and not a name-matching parser.

Acceptance gates:
- MainActivity.kt: exactly 40 compiler-resolved internal calls.
- Overloads.kt: `choose(Int)` and `choose(Long)` resolve to distinct signatures.
- Invalid.kt: compilation fails with unresolved `missingSymbol` diagnostics.
- Native and CheerpJ resolved source-call arrays must match exactly.
- Tree-sitter alone records zero calls in MainActivity. Adding the actual
  verified compiler edges removes the HIGH concern-overload finding for the
  fixed Ui component (42 entities). Fixing component membership isolates the
  detector from recovery heuristics. No production graph/metric code is changed.

## Run

Requires JDK **17**, Maven, Node, Python 3.12+ and arcade-agent with tree-sitter-kotlin.
The compiler/plugin are version-coupled; do not upgrade independently.

```sh
examples/kotlin_semantic_poc/build.sh
npm ci --prefix examples/kotlin_semantic_poc
# Install Playwright Chromium, or set CHROME_PATH to an installed Chrome binary:
(cd examples/kotlin_semantic_poc && npx playwright install --with-deps chromium)
python examples/kotlin_semantic_poc/measure.py
python examples/kotlin_semantic_poc/verify.py
```

The workflow `kotlin-semantic-poc.yml` runs two independent fresh Ubuntu jobs.
Artifacts include raw runtime output, source/bytecode edges, diagnostics,
verification, JAR sizes, timings and sampled process-tree RSS sum. No dependency
cache is restored. The RSS metric sums Node and all descendants; shared pages
may be double-counted and short peaks between samples may be missed. It covers
the harness, not Maven/browser installation. Page-only network bytes are not
whole-runtime download bytes. Initialization, first compilation and end-to-end
harness wall time are separate measurements.

## Scope and limitations

Fixtures use Kotlin builtins only, with explicit stdlib and `-no-jdk` on both
runtimes. This is not Android/project-classpath compatibility proof. Source
compiler plugin calls cover `IrCall`; constructor calls, callable references,
dynamic dispatch target sets, generated code and complete lambda/inline semantics
are outside this fixture's validated contract. Bytecode is retained as a separate
view, not substituted for source semantics. Real projects require source-set/JDK/
Android dependencies and stable symbol IDs, especially for overload graph merging.
Only MainActivity's uniquely named methods are mapped to tree-sitter FQNs here;
overloads are verified independently and are not silently merged by name.

CheerpJ uses the vendor-hosted 4.3 runtime, Java 17; dependencies are fetched from
Maven. No CheerpJ runtime is bundled. A loopback HTTP server serves the input JARs
with Range support. Compiler outputs go to CheerpJ's writable `/files` filesystem.
Compilation diagnostics are retained, including expected invalid-fixture errors.

Local Java 17 validation on macOS ARM64 produced 42 matching source calls,
40 internal calls, correct overloads and expected compilation-error diagnostics.
The measured successful compile was ~1.91 s native / ~12.77 s CheerpJ, excluding
backend initialization. These are local samples, not CI results.
