#!/usr/bin/env python3
"""Run arcade-agent self-analysis and save results as JSON + HTML.

Usage:
    arcade-self-analysis [--output-json results.json] [--output-html report.html]
"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arcade_agent.algorithms.coupling import compute_balanced_scores
from arcade_agent.algorithms.smells import SmellInstance
from arcade_agent.ci.graph_filter import _filter_non_architectural_entities
from arcade_agent.exporters.json import build_component_summary, build_graph_summary
from arcade_agent.tools.compute_metrics import compute_metrics
from arcade_agent.tools.detect_smells import detect_smells
from arcade_agent.tools.ingest import ingest
from arcade_agent.tools.parse import parse
from arcade_agent.tools.recover import recover
from arcade_agent.tools.visualize import visualize


def _smell_to_dict(smell: SmellInstance) -> dict[str, Any]:
    """Serialize a SmellInstance to a plain dict."""
    d = asdict(smell)
    # SmellType enum → string
    if hasattr(d.get("smell_type"), "value"):
        d["smell_type"] = d["smell_type"].value
    elif not isinstance(d.get("smell_type"), str):
        d["smell_type"] = str(d["smell_type"])
    return d


def main() -> None:
    parser = argparse.ArgumentParser(description="Run architecture self-analysis on a source tree")
    parser.add_argument(
        "--source",
        default=".",
        help="Path to the source repository or project root",
    )
    parser.add_argument(
        "--language",
        default="",
        help=(
            "Optional language override (java, python, typescript, c, go, kotlin, rust, "
            "or multi for every detected language)"
        ),
    )
    parser.add_argument(
        "--languages",
        default="",
        help="Comma-separated polyglot languages (e.g. java,kotlin)",
    )
    parser.add_argument(
        "--exclude-dirs",
        default="",
        help=(
            "Comma-separated project-relative directories to exclude "
            "(e.g. integrationTest,src/e2e)"
        ),
    )
    parser.add_argument(
        "--exclude-tests",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply the built-in test/vendor/build exclusion policy (default: true)",
    )
    parser.add_argument(
        "--repo-name",
        default="",
        help="Optional project name override for reports",
    )
    parser.add_argument(
        "--output-json",
        default="arcade_analysis_results.json",
        help="Path to save JSON results",
    )
    parser.add_argument(
        "--output-html",
        default="arcade_analysis_report.html",
        help="Path to save HTML report",
    )
    parser.add_argument(
        "--algorithm",
        default="pkg",
        help="Architecture recovery algorithm (pkg, wca, acdc)",
    )
    parser.add_argument(
        "--num-clusters",
        type=int,
        default=None,
        help="Target cluster count for clustering algorithms such as wca.",
    )
    parser.add_argument(
        "--filter-non-architectural-helpers",
        action="store_true",
        help=(
            "Apply arcade-agent self-dogfooding filters for private helpers and "
            "registration decorators. Leave disabled for generic repositories."
        ),
    )
    args = parser.parse_args()

    source = str(Path(args.source).resolve())
    language = args.language or None
    languages = [part.strip() for part in args.languages.split(",") if part.strip()] or None
    exclude_dirs = [
        part.strip()
        for part in args.exclude_dirs.split(",")
        if part.strip()
    ] or None

    print(f"[1/5] Ingesting {source}...")
    repo = ingest(
        source,
        language=language,
        languages=languages,
        exclude_tests=args.exclude_tests,
        exclude_dirs=exclude_dirs,
    )
    print(
        f"  Found {len(repo.source_files)} source files "
        f"(languages={repo.languages or [repo.language]})"
    )

    if not repo.source_files:
        print("  No source files found. Exiting.")
        sys.exit(1)

    print("[2/5] Parsing dependencies...")
    parse_files = [str(f) for f in repo.source_files]
    if len(repo.languages) > 1:
        raw_graph = parse(
            str(repo.path),
            languages=repo.languages,
            files=parse_files,
        )
    else:
        raw_graph = parse(
            str(repo.path),
            language=repo.language or language or "python",
            files=parse_files,
        )
    graph = (
        _filter_non_architectural_entities(raw_graph)
        if args.filter_non_architectural_helpers
        else raw_graph
    )
    print(f"  {graph.num_entities} entities, {graph.num_edges} edges")

    print(f"[3/5] Recovering architecture ({args.algorithm})...")
    arch = recover(graph, algorithm=args.algorithm, num_clusters=args.num_clusters)
    print(f"  {len(arch.components)} components recovered")

    print("[4/5] Detecting smells and computing metrics...")
    smells = detect_smells(arch, graph)
    metrics = compute_metrics(arch, graph)
    derived_metrics, principle_signals, score_drivers = compute_balanced_scores(
        arch,
        graph,
        smells,
        metrics=metrics,
    )

    print("[5/5] Saving results...")
    source_summary = build_graph_summary(raw_graph)
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repo_name": args.repo_name or repo.name,
        "language": repo.language or language,
        "languages": repo.languages,
        "commit_sha": os.environ.get("GITHUB_SHA", "local"),
        "ref": os.environ.get("GITHUB_REF", "local"),
        "algorithm": args.algorithm,
        "num_components": len(arch.components),
        "num_entities": graph.num_entities,
        "num_edges": graph.num_edges,
        "source_num_entities": source_summary["num_entities"],
        "class_count": source_summary["class_count"],
        "function_count": source_summary["function_count"],
        "method_count": source_summary["method_count"],
        "entity_kind_counts": source_summary["entity_kind_counts"],
        "component_dependencies": [
            {"source": source, "target": target}
            for source, target in arch.component_dependencies(graph)
        ],
        "components": [
            build_component_summary(c, graph, stats_graph=raw_graph)
            for c in arch.components
        ],
        "metrics": {m.name: round(m.value, 4) for m in metrics},
        "derived_metrics": {m.name: round(m.value, 4) for m in derived_metrics},
        "principle_signals": principle_signals,
        "score_drivers": score_drivers,
        "smells": [_smell_to_dict(s) for s in smells],
    }

    output_json = Path(args.output_json)
    output_json.write_text(json.dumps(results, indent=2, default=str))
    print(f"  JSON results → {output_json}")

    html_out = visualize(
        args.repo_name or repo.name,
        repo.version,
        graph,
        arch,
        smells,
        metrics + derived_metrics,
        output=args.output_html,
    )
    print(f"  HTML report  → {html_out}")
    print("Done.")


if __name__ == "__main__":
    main()
