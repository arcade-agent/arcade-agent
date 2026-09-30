"""Concern overload and scattered functionality detection.

Provides both heuristic-based detection (default) and LLM-powered semantic
analysis via Claude CLI.  Set ``use_llm=True`` on the public helpers or call
the ``_llm`` variants directly to use Claude for concern analysis.
"""

from __future__ import annotations

import json
import logging
from typing import Literal, NotRequired, TypedDict

from arcade_agent.algorithms.architecture import Architecture
from arcade_agent.parsers.graph import DependencyGraph

log = logging.getLogger(__name__)


class ConcernOverloadFinding(TypedDict):
    component: str
    entity_count: int
    severity: Literal["high", "medium", "low"]
    internal_edges: int
    internal_edges_per_entity: float
    coverage_status: NotRequired[Literal["insufficient"]]
    incomplete_call_languages: NotRequired[list[str]]


def detect_concern_overload(
    architecture: Architecture,
    dep_graph: DependencyGraph,
    threshold: int = 20,
    high_threshold: int = 40,
    min_internal_edges_per_entity: float = 0.4,
) -> list[ConcernOverloadFinding]:
    """Detect components with too many responsibilities.

    A component is flagged only when it is both large and internally sparse.
    Size alone is too noisy for package-based recovery: a large library package
    may still be cohesive if its entities collaborate heavily.

    Args:
        architecture: The recovered architecture.
        dep_graph: Dependency graph used to measure internal cohesion.
        threshold: Minimum entity count to flag (default: 20).
        high_threshold: Entity count for high severity (default: 40).
        min_internal_edges_per_entity: Minimum internal edge density guard.
            Components at or above this ratio are treated as cohesive enough
            to avoid a concern-overload warning.

    Returns:
        List of dicts with component name, entity count, severity, and
        cohesion details.
    """
    results: list[ConcernOverloadFinding] = []
    coverage = dep_graph.relation_coverage()
    for comp in architecture.components:
        count = len(comp.entities)
        if count <= threshold:
            continue

        internal_edges = _count_internal_edges(comp.entities, dep_graph)
        internal_edges_per_entity = internal_edges / count if count else 0.0
        if internal_edges_per_entity >= min_internal_edges_per_entity:
            continue

        severity: Literal["high", "medium", "low"] = (
            "high" if count > high_threshold else "medium"
        )
        incomplete_languages = sorted({
            entity.language
            for fqn in comp.entities
            if (entity := dep_graph.entities.get(fqn)) is not None
            and entity.language in coverage
            and coverage[entity.language]["call_coverage"] != "complete"
        })
        finding: ConcernOverloadFinding = {
            "component": comp.name,
            "entity_count": count,
            "severity": severity,
            "internal_edges": internal_edges,
            "internal_edges_per_entity": round(internal_edges_per_entity, 2),
        }
        if incomplete_languages:
            finding.update({
                "severity": "low",
                "coverage_status": "insufficient",
                "incomplete_call_languages": incomplete_languages,
            })
        results.append(finding)
    return results


def _count_internal_edges(component_entities: list[str], dep_graph: DependencyGraph) -> int:
    """Count entity-level edges that stay within a component."""
    entity_set = set(component_entities)
    return sum(
        1
        for edge in dep_graph.edges
        if edge.source in entity_set and edge.target in entity_set
    )


def detect_scattered_functionality(
    architecture: Architecture,
    dep_graph: DependencyGraph,
    min_components: int = 3,
) -> list[dict]:
    """Detect functionality scattered across too many components.

    Looks for naming patterns (suffixes like Service, Controller, Repository)
    that appear in multiple components, suggesting a concern is scattered.

    Args:
        architecture: The recovered architecture.
        dep_graph: The dependency graph.
        min_components: Minimum number of components a pattern must appear in.

    Returns:
        List of dicts with pattern, components, and count.
    """
    common_suffixes = [
        "Service", "Controller", "Repository", "Manager", "Handler",
        "Factory", "Listener", "Provider", "Adapter", "Helper",
        "Util", "Utils", "Config", "Configuration", "Exception",
        "Test", "Spec",
    ]

    # Track which components contain each suffix pattern
    suffix_components: dict[str, set[str]] = {}

    for comp in architecture.components:
        for fqn in comp.entities:
            entity = dep_graph.entities.get(fqn)
            if not entity:
                continue
            for suffix in common_suffixes:
                if entity.name.endswith(suffix):
                    suffix_components.setdefault(suffix, set()).add(comp.name)
                    break

    results = []
    for suffix, components in sorted(suffix_components.items()):
        if len(components) >= min_components:
            results.append({
                "pattern": suffix,
                "components": sorted(components),
                "count": len(components),
                "severity": "high" if len(components) >= 5 else "medium",
            })

    return results


def detect_link_overload(
    architecture: Architecture,
    dep_graph: DependencyGraph,
    threshold_ratio: float = 0.5,
) -> list[dict]:
    """Detect components that are depended upon by too many other components.

    A component has link overload if more than threshold_ratio of all other
    components depend on it.

    Args:
        architecture: The recovered architecture.
        dep_graph: The dependency graph.
        threshold_ratio: Fraction of components that must depend on a component (default: 0.5).

    Returns:
        List of dicts with component name, dependents count, and severity.
    """
    if len(architecture.components) <= 2:
        return []

    # Count incoming dependencies per component
    incoming: dict[str, set[str]] = {comp.name: set() for comp in architecture.components}
    for src, tgt in architecture.component_dependencies(dep_graph):
        incoming[tgt].add(src)

    total_other = len(architecture.components) - 1
    results = []
    for comp_name, dependents in incoming.items():
        ratio = len(dependents) / total_other if total_other > 0 else 0
        if ratio >= threshold_ratio and len(dependents) >= 3:
            severity = "high" if ratio >= 0.75 else "medium"
            results.append({
                "component": comp_name,
                "dependents": sorted(dependents),
                "dependent_count": len(dependents),
                "ratio": round(ratio, 2),
                "severity": severity,
            })

    return results


# ---------------------------------------------------------------------------
# LLM-based concern detection
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a software architecture expert specializing in architectural "
    "smell detection. You analyze component structures and dependencies to "
    "identify concern overload and scattered parasitic functionality."
)


def _build_component_summary(
    architecture: Architecture,
    dep_graph: DependencyGraph,
    max_entities_per_component: int = 30,
) -> list[dict]:
    """Build a concise component summary suitable for an LLM prompt.

    Args:
        architecture: The recovered architecture.
        dep_graph: The dependency graph.
        max_entities_per_component: Cap entity names sent per component.

    Returns:
        List of component summary dicts.
    """
    comp_summary = []
    for comp in architecture.components:
        comp_deps = sorted({
            tgt for src, tgt in architecture.component_dependencies(dep_graph)
            if src == comp.name
        })
        # Collect entity simple names for the LLM (more readable than FQNs)
        entity_names = []
        for fqn in comp.entities[:max_entities_per_component]:
            entity = dep_graph.entities.get(fqn)
            if entity:
                entity_names.append(entity.name)
            else:
                entity_names.append(fqn.rsplit(".", 1)[-1])

        comp_summary.append({
            "name": comp.name,
            "responsibility": comp.responsibility,
            "num_entities": len(comp.entities),
            "entities": entity_names,
            "depends_on": comp_deps,
        })
    return comp_summary


def detect_concerns_llm(
    architecture: Architecture,
    dep_graph: DependencyGraph,
) -> list[dict]:
    """Use Claude to detect concern overload and scattered functionality.

    Sends a structured summary of the architecture to Claude and asks it to
    identify components with too many mixed responsibilities (BCO) and
    concerns scattered across multiple components (SPF).

    Args:
        architecture: The recovered architecture.
        dep_graph: The dependency graph.

    Returns:
        List of smell dicts with keys: smell_type, severity,
        affected_components, description, explanation, suggestion.
    """
    from arcade_agent.algorithms.llm import MOCK_MODE, ask_claude_json

    if MOCK_MODE:
        log.info("Mock mode — skipping LLM concern detection")
        return []

    comp_summary = _build_component_summary(architecture, dep_graph)

    prompt = f"""Analyze this software architecture for architectural smells.

## Architecture Components
{json.dumps(comp_summary, indent=2)}

Look for these specific smell types:

1. **Concern Overload** (BCO): A component has too many responsibilities or
   contains entities that serve unrelated purposes.  Signs: large entity count,
   vague responsibility, entities with diverse naming patterns suggesting
   multiple concerns mixed together.

2. **Scattered Parasitic Functionality** (SPF): A single concern (e.g.,
   logging, security, validation, persistence, configuration) is spread across
   many unrelated components instead of being centralized.  Signs: similar
   entity names, suffixes, or functional patterns appearing across multiple
   components.

Respond with ONLY valid JSON:
{{
    "smells": [
        {{
            "smell_type": "Concern Overload" or "Scattered Parasitic Functionality",
            "severity": "high" or "medium" or "low",
            "affected_components": ["ComponentA", "ComponentB"],
            "description": "What the smell is",
            "explanation": "Why this is a problem for maintainability and evolution",
            "suggestion": "Concrete refactoring action to fix it"
        }}
    ]
}}

If no smells are found, return {{"smells": []}}.
Be conservative — only report clear, actionable smells, not speculative ones."""

    result = ask_claude_json(prompt, system=_SYSTEM_PROMPT)

    smells: list[dict] = []
    for s in result.get("smells", []):
        # Validate that affected_components actually exist
        valid_names = {c.name for c in architecture.components}
        affected = [c for c in s.get("affected_components", []) if c in valid_names]
        if not affected:
            continue

        smells.append({
            "smell_type": s.get("smell_type", "Concern Overload"),
            "severity": s.get("severity", "medium"),
            "affected_components": affected,
            "description": s.get("description", ""),
            "explanation": s.get("explanation", ""),
            "suggestion": s.get("suggestion", ""),
        })
    return smells


def extract_concerns_llm(
    architecture: Architecture,
    dep_graph: DependencyGraph,
) -> dict[str, list[str]]:
    """Use Claude to identify the concerns handled by each component.

    Args:
        architecture: The recovered architecture.
        dep_graph: The dependency graph.

    Returns:
        Dict mapping component name to a list of concern labels
        (e.g. ``{"Clustering": ["agglomerative clustering", "similarity measures"]}``).
    """
    from arcade_agent.algorithms.llm import MOCK_MODE, ask_claude_json

    if MOCK_MODE:
        log.info("Mock mode — skipping LLM concern extraction")
        return {}

    comp_summary = _build_component_summary(architecture, dep_graph)

    prompt = f"""Analyze this software architecture and identify the concerns
(responsibilities / topics) that each component handles.

## Architecture Components
{json.dumps(comp_summary, indent=2)}

For each component, list 1-5 short concern labels that describe what
functional or cross-cutting concerns it addresses.  Be specific and concise
(2-4 words per concern).

Respond with ONLY valid JSON:
{{
    "concerns": {{
        "ComponentName": ["concern 1", "concern 2"],
        "AnotherComponent": ["concern A"]
    }}
}}"""

    system = (
        "You are a software architecture expert. Identify the distinct "
        "functional and cross-cutting concerns each component addresses."
    )

    result = ask_claude_json(prompt, system=system)

    valid_names = {c.name for c in architecture.components}
    concerns: dict[str, list[str]] = {}
    for name, labels in result.get("concerns", {}).items():
        if name in valid_names and isinstance(labels, list):
            concerns[name] = [str(label) for label in labels[:5]]
    return concerns
