"""Tool: Detect architectural smells."""

from arcade_agent.algorithms.architecture import Architecture
from arcade_agent.algorithms.concern import (
    detect_concern_overload,
    detect_concerns_llm,
    detect_link_overload,
    detect_scattered_functionality,
)
from arcade_agent.algorithms.cycles import detect_dependency_cycles
from arcade_agent.algorithms.smells import SmellInstance, SmellType
from arcade_agent.parsers.graph import DependencyGraph
from arcade_agent.tools.registry import tool


@tool(
    name="detect_smells",
    description="Detect architectural anti-patterns: dependency cycles, concern overload, "
    "scattered functionality, and link overload.",
)
def detect_smells(
    architecture: Architecture,
    dep_graph: DependencyGraph,
    use_llm: bool = False,
) -> list[SmellInstance]:
    """Detect architectural smells in the recovered architecture.

    Detects four smell types:
    1. Dependency Cycle (BDC): SCC-based cycle detection (always algorithmic)
    2. Concern Overload (BCO): Component covers too many concerns
    3. Scattered Parasitic Functionality (SPF): Concern scattered across components
    4. Link/Upstream Overload (BUO): Heavily depended-on components (always algorithmic)

    When ``use_llm=True``, smell types 2 and 3 are detected by sending the
    architecture to Claude CLI for semantic analysis instead of using
    heuristic thresholds.  Requires the ``claude`` CLI to be installed and
    authenticated.  Set ``ARCADE_MOCK=1`` to skip LLM calls.

    Args:
        architecture: The recovered architecture.
        dep_graph: The dependency graph.
        use_llm: Use Claude CLI for concern-based smell detection (default: False).

    Returns:
        List of SmellInstance objects.
    """
    smells: list[SmellInstance] = []

    # 1. Dependency Cycles (always algorithmic)
    cycles = detect_dependency_cycles(architecture, dep_graph)
    for cycle in cycles:
        if len(cycle) >= 5:
            severity = "high"
        elif len(cycle) >= 3:
            severity = "medium"
        else:
            severity = "low"

        smells.append(SmellInstance(
            smell_type=SmellType.DEPENDENCY_CYCLE,
            severity=severity,
            affected_components=cycle,
            description=(
                f"Circular dependency among {len(cycle)} components: "
                f"{' <-> '.join(cycle)}"
            ),
            explanation=(
                "Dependency cycles make components tightly coupled — you cannot "
                "change, test, or deploy any component in the cycle independently. "
                "This hinders maintainability, increases build times, and makes "
                "the system harder to understand."
            ),
            suggestion=(
                "Break the cycle by introducing an interface/abstraction that "
                "one component depends on, inverting the dependency direction. "
                "Consider the Dependency Inversion Principle (DIP)."
            ),
        ))

    # 2 & 3. Concern Overload + Scattered Functionality
    if use_llm:
        llm_smells = detect_concerns_llm(architecture, dep_graph)
        for item in llm_smells:
            smell_type_str = item["smell_type"]
            # Map LLM response to SmellType enum
            if "Scatter" in smell_type_str:
                smell_type = SmellType.SCATTERED_FUNCTIONALITY
            else:
                smell_type = SmellType.CONCERN_OVERLOAD

            smells.append(SmellInstance(
                smell_type=smell_type,
                severity=item["severity"],
                affected_components=item["affected_components"],
                description=item["description"],
                explanation=item["explanation"],
                suggestion=item["suggestion"],
            ))
    else:
        # Heuristic: Concern Overload
        overloads = detect_concern_overload(architecture, dep_graph)
        for overload in overloads:
            if overload.get("coverage_status") == "insufficient":
                languages = ", ".join(overload["incomplete_call_languages"])
                smells.append(SmellInstance(
                    smell_type=SmellType.CONCERN_OVERLOAD,
                    severity=overload["severity"],
                    affected_components=[overload["component"]],
                    description=(
                        f"{overload['component']}: insufficient coverage for cohesion "
                        f"({languages} call relations are not fully collected). "
                        f"Observed {overload['entity_count']} entities and "
                        f"{overload['internal_edges']} internal dependencies."
                    ),
                    explanation=(
                        "Missing internal calls can make a cohesive component appear sparse. "
                        "This is an analysis limitation; multiple responsibilities have "
                        "not been established. Import-cycle detection remains available."
                    ),
                    suggestion=(
                        "Collect and validate call coverage or inspect source responsibilities "
                        "before deciding whether to split this component."
                    ),
                ))
                continue
            smells.append(SmellInstance(
                smell_type=SmellType.CONCERN_OVERLOAD,
                severity=overload["severity"],
                affected_components=[overload["component"]],
                description=(
                    f"{overload['component']} contains {overload['entity_count']} entities "
                    f"but only {overload['internal_edges']} internal dependencies "
                    f"({overload['internal_edges_per_entity']:.2f} per entity), "
                    "suggesting multiple responsibilities."
                ),
                explanation=(
                    "Large components are harder to understand, test, and maintain. "
                    "When their internals are also sparsely connected, that usually means "
                    "the component is acting as a bucket for unrelated concerns rather than "
                    "a cohesive module."
                ),
                suggestion=(
                    f"Consider splitting {overload['component']} into smaller, "
                    f"focused components with single responsibilities."
                ),
            ))

        # Heuristic: Scattered Parasitic Functionality
        scattered = detect_scattered_functionality(architecture, dep_graph)
        for item in scattered:
            smells.append(SmellInstance(
                smell_type=SmellType.SCATTERED_FUNCTIONALITY,
                severity=item["severity"],
                affected_components=item["components"],
                description=(
                    f"'{item['pattern']}' pattern is scattered across "
                    f"{item['count']} components: {', '.join(item['components'])}"
                ),
                explanation=(
                    "When a single concern is spread across many components, changes "
                    "to that concern require modifying multiple places, increasing "
                    "the risk of inconsistencies and bugs."
                ),
                suggestion=(
                    f"Centralize '{item['pattern']}'-related functionality into a "
                    f"dedicated component to reduce scattering."
                ),
            ))

    # 4. Link/Upstream Overload (always algorithmic)
    link_overloads = detect_link_overload(architecture, dep_graph)
    for item in link_overloads:
        smells.append(SmellInstance(
            smell_type=SmellType.LINK_OVERLOAD,
            severity=item["severity"],
            affected_components=[item["component"]],
            description=(
                f"{item['component']} is depended on by {item['dependent_count']} "
                f"other components ({item['ratio']*100:.0f}% of all components)."
            ),
            explanation=(
                "A component with too many dependents becomes a bottleneck. "
                "Any change to it can cascade across the entire system, making "
                "evolution risky and expensive."
            ),
            suggestion=(
                f"Consider splitting {item['component']} into smaller interfaces "
                f"so dependents only couple to what they actually use (ISP)."
            ),
        ))

    return smells
