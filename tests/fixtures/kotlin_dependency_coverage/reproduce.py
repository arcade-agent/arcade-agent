"""Run from the repository root with PYTHONPATH=src python tests/fixtures/.../reproduce.py."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from arcade_agent.algorithms.concern import detect_concern_overload
from arcade_agent.tools.analyze import analyze


async def main() -> None:
    result = await analyze(
        str(Path(__file__).parent / "src"),
        language="kotlin",
        algorithm="pkg",
        exclude_tests=True,
        use_cache=False,
        use_llm=False,
    )
    try:
        graph = result.graph
        renderer_edges = [
            edge for edge in graph.edges
            if graph.entities[edge.source].file_path.endswith("TimelineFrameRenderer.kt")
            and edge.relation == "import"
        ]
        print(json.dumps({
            "entities": graph.num_entities,
            "edges_by_relation": dict(Counter(edge.relation for edge in graph.edges)),
            "renderer_entity_import_edges": len(renderer_edges),
            "renderer_file_target_pairs": len({
                (graph.entities[edge.source].file_path, edge.target)
                for edge in renderer_edges
            }),
            "graph_metadata": graph.metadata,
            "concern_overload": detect_concern_overload(result.architecture, graph),
            "smells": [asdict(smell) for smell in result.smells],
            "metrics": [asdict(metric) for metric in result.metrics],
        }, indent=2))
    finally:
        result.repository.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
