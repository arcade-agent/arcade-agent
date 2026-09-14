"""Verify compiler evidence and a fixture-scoped tree-sitter graph enrichment."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TypedDict, cast

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.algorithms.concern import detect_concern_overload
from arcade_agent.parsers.graph import Edge
from arcade_agent.parsers.kotlin import KotlinParser


class Call(TypedDict):
    caller: str
    callee: str
    callerSignature: str
    calleeSignature: str
    file: str
    line: int
    provenance: str


class SemanticResult(TypedDict):
    compileExit: str
    diagnostics: str
    invalidCompileExit: str
    invalidDiagnostics: str
    sourceCalls: list[Call]
    calls: list[Call]
    compileMs: float


def decode(text: str) -> SemanticResult:
    marker = "SEMANTIC_RESULT "
    start = text.index(marker) + len(marker)
    result, _ = json.JSONDecoder().raw_decode(text[start:])
    # JSON from our pinned Java driver is the boundary; assertions below validate evidence.
    if not isinstance(result, dict):
        raise ValueError("Compiler result must be an object")
    return cast(SemanticResult, result)


def main() -> None:
    root = Path(__file__).resolve().parent
    data = json.loads((root / "results.json").read_text())
    native = decode(data["native"][0]["stdout"])
    browser = decode("".join(data["logs"]))
    expected = {("ui.MainActivity.render", "ui.MainActivity.step01")}
    expected.update(
        (f"ui.MainActivity.step{i:02}", f"ui.MainActivity.step{i+1:02}")
        for i in range(1, 40)
    )
    for result in (native, browser):
        assert result["compileExit"] == "OK", result["diagnostics"]
        assert result["invalidCompileExit"] == "COMPILATION_ERROR"
        assert "missingSymbol" in result["invalidDiagnostics"]
        calls = result["sourceCalls"]
        internal = [c for c in calls if c["caller"].startswith("ui.MainActivity.")]
        assert len(internal) == 40
        assert {(c["caller"], c["callee"]) for c in internal} == expected
        source = (root / "MainActivity.kt").read_text().splitlines()
        for call in internal:
            assert call["provenance"] == "kotlin-2.0.21-resolved-ir"
            assert call["callee"].rsplit(".", 1)[1] + "()" in source[call["line"] - 1]
        assert {
            (c["caller"], c["calleeSignature"]) for c in calls
            if c["callee"] == "cases.Overloads.choose"
        } == {
            ("cases.Overloads.useInt", "(kotlin.Int):kotlin.Int"),
            ("cases.Overloads.useLong", "(kotlin.Long):kotlin.Long"),
        }
        assert {
            (c["caller"], c["callee"]) for c in result["calls"]
            if c["caller"].startswith("ui.MainActivity.") and "<init>" not in c["caller"]
        } == expected
    assert native["sourceCalls"] == browser["sourceCalls"]
    graph = KotlinParser().parse([root / "MainActivity.kt"], root)
    arch = Architecture(components=[Component("Ui", "Fixture computation", list(graph.entities))])
    before = detect_concern_overload(arch, graph)
    assert len(before) == 1 and before[0]["severity"] == "high"
    assert before[0]["internal_edges"] == 0
    # This fixture has unique method names. Do not use name-only mapping for overloads.
    # Use the verified compiler output, not the oracle, to enrich the graph.
    edges = [Edge(c["caller"], c["callee"], "calls") for c in native["sourceCalls"]
             if c["caller"] in graph.entities and c["callee"] in graph.entities]
    assert len(edges) == 40
    enriched = replace(graph, edges=[*graph.edges, *edges])
    assert detect_concern_overload(arch, enriched) == []
    print(json.dumps({"source_calls": len(native["sourceCalls"]), "internal_calls": 40,
                      "compile": "OK", "negative_compile": "COMPILATION_ERROR",
                      "native_cheerpj_source_graph_equal": True,
                      "overload_resolution": "PASS", "concern_before": before,
                      "concern_after": [], "native_compile_ms": native["compileMs"],
                      "cheerpj_compile_ms": browser["compileMs"]}, indent=2))


if __name__ == "__main__":
    main()
