"""Tools: an architecture guardrail an agent consults while it writes code.

The guardrail answers three questions against an author-written
``architecture.spec.json`` (see :mod:`arcade_agent.algorithms.conformance`):

- before writing — *where does this belong, and what may it use?*
  (:func:`propose_placement`)
- before adding an import — *would this dependency be allowed?*
  (:func:`preview_impact`)
- after the change — *does the code still conform?* (:func:`check_architecture`)

All three are deterministic and read only the spec plus the parsed dependency
graph, so they are cheap enough to call on every edit.
"""

import copy
import json
from pathlib import Path
from typing import Any

from arcade_agent.algorithms import conformance as c
from arcade_agent.parsers.graph import DependencyGraph
from arcade_agent.tools.registry import tool

SPEC_FILENAME = "architecture.spec.json"

_BUDGETS: dict[str, Any] = {
    "no_cycles": True,
    "max_component_entities": 200,
    "max_fan_in": 8,
}

SPEC_TEMPLATES: dict[str, dict[str, Any]] = {
    "layered": {
        "intent": "Layered: presentation -> application -> domain; infrastructure "
        "is reached only through the application layer.",
        "components": [
            {"name": "presentation", "match": "**/{api,web,controller,ui}/**",
             "layer": "presentation"},
            {"name": "service", "match": "**/{service,application}/**",
             "layer": "application"},
            {"name": "model", "match": "**/{model,domain,entity}/**", "layer": "domain"},
            {"name": "repository", "match": "**/{repository,dao,store}/**",
             "layer": "infrastructure"},
        ],
        "layers": ["presentation", "application", "domain", "infrastructure"],
        "allow": [
            {"from": "presentation", "to": "application"},
            {"from": "application", "to": "domain"},
            {"from": "application", "to": "infrastructure"},
            {"from": "infrastructure", "to": "domain"},
        ],
        "forbid": [
            {"from": "presentation", "to": "infrastructure",
             "why": "presentation must go through the application layer"},
        ],
        "budgets": _BUDGETS,
    },
    "hexagonal": {
        "intent": "Hexagonal (ports and adapters): the domain core is pure and "
        "adapters depend inward.",
        "components": [
            {"name": "api", "match": "**/api/**", "layer": "presentation"},
            {"name": "application", "match": "**/application/**", "layer": "application"},
            {"name": "domain", "match": "**/domain/**", "layer": "domain"},
            {"name": "adapters", "match": "**/adapters/**", "layer": "infrastructure"},
        ],
        "layers": ["presentation", "application", "domain", "infrastructure"],
        "allow": [
            {"from": "presentation", "to": "application"},
            {"from": "application", "to": "domain"},
            {"from": "infrastructure", "to": "domain"},
        ],
        "forbid": [
            {"from": "presentation", "to": "infrastructure",
             "why": "the API must not reach adapters or persistence directly"},
            {"from": "domain", "to": "infrastructure", "why": "the domain must stay pure"},
        ],
        "budgets": _BUDGETS,
    },
    "clean": {
        "intent": "Clean architecture: every dependency points inward toward entities.",
        "components": [
            {"name": "frameworks", "match": "**/{frameworks,infra,external}/**",
             "layer": "frameworks"},
            {"name": "interface-adapters", "match": "**/adapters/**",
             "layer": "interface-adapters"},
            {"name": "usecases", "match": "**/usecases/**", "layer": "usecases"},
            {"name": "entities", "match": "**/entities/**", "layer": "entities"},
        ],
        "layers": ["frameworks", "interface-adapters", "usecases", "entities"],
        "allow": [],
        "forbid": [
            {"from": "entities", "to": "*", "why": "entities depend on nothing outside"},
        ],
        "budgets": _BUDGETS,
    },
    "mvc": {
        "intent": "MVC: controllers orchestrate; models never depend on views or "
        "controllers.",
        "components": [
            {"name": "controllers", "match": "**/controllers/**", "layer": "presentation"},
            {"name": "views", "match": "**/views/**", "layer": "presentation"},
            {"name": "models", "match": "**/models/**", "layer": "domain"},
        ],
        "layers": ["presentation", "domain"],
        "allow": [{"from": "presentation", "to": "domain"}],
        "forbid": [
            {"from": "models", "to": "{controllers,views}",
             "why": "models must not depend on controllers or views"},
        ],
        "budgets": {"no_cycles": True, "max_component_entities": 200},
    },
}


def find_spec(source_path: str, spec_path: str | None = None) -> Path:
    """Locate the spec: an explicit path, else ``<source_path>/architecture.spec.json``.

    Args:
        source_path: Project root.
        spec_path: Optional explicit spec path.

    Returns:
        Path to an existing spec file.

    Raises:
        FileNotFoundError: If no spec exists.
    """
    path = Path(spec_path).expanduser() if spec_path else Path(source_path) / SPEC_FILENAME
    if not path.is_file():
        raise FileNotFoundError(
            f"No architecture spec at {path}. Create one with init_spec "
            f"(templates: {', '.join(SPEC_TEMPLATES)})."
        )
    return path


@tool(
    name="init_spec",
    description="Scaffold an architecture.spec.json from a template (layered, "
    "hexagonal, clean, mvc) for the guardrail tools to check against.",
)
def init_spec(
    source_path: str,
    template: str = "layered",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a starter ``architecture.spec.json`` into a project.

    Args:
        source_path: Project root to write the spec into.
        template: One of ``layered``, ``hexagonal``, ``clean``, ``mvc``.
        overwrite: Replace an existing spec.

    Returns:
        The written path and the spec contents.
    """
    if template not in SPEC_TEMPLATES:
        raise ValueError(f"Unknown template {template!r}; choose from {list(SPEC_TEMPLATES)}")
    path = Path(source_path) / SPEC_FILENAME
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists; pass overwrite=True to replace it")
    spec = copy.deepcopy(SPEC_TEMPLATES[template])
    path.write_text(json.dumps(spec, indent=2) + "\n")
    return {"spec_path": str(path), "template": template, "spec": spec}


def _evaluate(
    source_path: str,
    spec: c.ArchitectureSpec,
    dep_graph: DependencyGraph | None,
    language: str | None,
    use_cache: bool,
    baseline_num_smells: int | None,
) -> tuple[DependencyGraph, dict[str, str], dict[tuple[str, str], int], list[c.Violation],
           list[str]]:
    if dep_graph is None:
        from arcade_agent.tools.parse import parse

        dep_graph = parse(source_path, language=language, use_cache=use_cache)
    mapping = c.map_entities(dep_graph, spec)
    edges = c.component_edges(dep_graph, mapping)

    budgets = spec.budgets
    turbomq: float | None = None
    num_smells: int | None = None
    not_evaluated: list[str] = []
    wants_smells = "max_new_smells" in budgets and baseline_num_smells is not None
    if "max_new_smells" in budgets and baseline_num_smells is None:
        not_evaluated.append("max_new_smells (no baseline smell count given)")
    if "min_turbomq" in budgets or wants_smells:
        # Package-based recovery is deterministic, so the verdict stays stable.
        from arcade_agent.tools.recover import recover

        arch = recover(dep_graph, algorithm="pkg")
        if "min_turbomq" in budgets:
            from arcade_agent.tools.compute_metrics import compute_metrics

            metrics = compute_metrics(arch, dep_graph)
            turbomq = next((m.value for m in metrics if m.name == "TurboMQ"), None)
        if wants_smells:
            from arcade_agent.tools.detect_smells import detect_smells

            num_smells = len(detect_smells(arch, dep_graph))

    violations = c.check_conformance(
        spec,
        mapping,
        edges,
        turbomq=turbomq,
        num_smells=num_smells,
        baseline_num_smells=baseline_num_smells,
    )
    return dep_graph, mapping, edges, violations, not_evaluated


@tool(
    name="check_architecture",
    description="Check a codebase against its architecture.spec.json. Returns "
    "PASS/WARN/FAIL with every violation, the rule it breaks, and a fix. "
    "Deterministic; call after each change and fix every error.",
)
def check_architecture(
    source_path: str,
    spec_path: str | None = None,
    dep_graph: DependencyGraph | None = None,
    language: str | None = None,
    use_cache: bool = True,
    baseline_num_smells: int | None = None,
) -> dict[str, Any]:
    """Check an implementation against its intended architecture.

    Args:
        source_path: Project root.
        spec_path: Spec location (default ``<source_path>/architecture.spec.json``).
        dep_graph: An already-parsed graph; parsed from ``source_path`` if omitted.
        language: Language to parse when ``dep_graph`` is omitted (auto if None).
        use_cache: Reuse the parse cache when parsing.
        baseline_num_smells: Smell count of the accepted baseline, which enables
            the ``max_new_smells`` budget.

    Returns:
        Verdict, error/warning counts, violations, component edges, and the
        number of entities no component glob matched.
    """
    spec = c.load_spec(find_spec(source_path, spec_path))
    graph, mapping, edges, violations, not_evaluated = _evaluate(
        source_path, spec, dep_graph, language, use_cache, baseline_num_smells
    )
    errors = sum(1 for v in violations if v.severity == c.ERROR)
    sizes: dict[str, int] = {}
    for comp in mapping.values():
        sizes[comp] = sizes.get(comp, 0) + 1
    return {
        "verdict": c.verdict(violations),
        "errors": errors,
        "warnings": len(violations) - errors,
        "violations": [v.to_dict() for v in violations],
        "component_edges": [
            {"from": s, "to": t, "count": n} for (s, t), n in sorted(edges.items())
        ],
        "component_sizes": {k: sizes[k] for k in sorted(sizes)},
        "num_entities": graph.num_entities,
        "unmapped_entities": sizes.get(c.UNMAPPED, 0),
        "budgets_not_evaluated": not_evaluated,
    }


@tool(
    name="propose_placement",
    description="Before writing new code: given a plain-language description of "
    "what you are about to add, return the component it belongs in, its path "
    "convention, and which components it may and must not depend on.",
)
def propose_placement(
    intent: str,
    source_path: str = ".",
    spec_path: str | None = None,
) -> dict[str, Any]:
    """Suggest where new code belongs according to the spec.

    Args:
        intent: What is about to be added, in words.
        source_path: Project root (used to find the spec).
        spec_path: Explicit spec location.

    Returns:
        Suggested component, layer, path convention, and dependency guidance.
    """
    spec = c.load_spec(find_spec(source_path, spec_path))
    return c.propose_placement(spec, intent)


@tool(
    name="preview_impact",
    description="Before adding a cross-component import: would a dependency from "
    "one component to another be allowed by the architecture spec? Uses the same "
    "rules as check_architecture, so the answers cannot disagree.",
)
def preview_impact(
    from_component: str,
    to_component: str,
    source_path: str = ".",
    spec_path: str | None = None,
) -> dict[str, Any]:
    """Evaluate a hypothetical component-level dependency.

    Args:
        from_component: Component that would gain the dependency.
        to_component: Component it would depend on.
        source_path: Project root (used to find the spec).
        spec_path: Explicit spec location.

    Returns:
        Whether the edge is allowed and, if not, each violation with its fix.
    """
    spec = c.load_spec(find_spec(source_path, spec_path))
    known = spec.component_names()
    unknown = [n for n in (from_component, to_component) if n not in known]
    if unknown:
        raise ValueError(f"Unknown component(s) {unknown}; the spec defines {known}")
    problems = c.dependency_problems(spec, from_component, to_component)
    return {
        "from": from_component,
        "to": to_component,
        "allowed": not problems,
        "problems": [
            {"rule": p.rule, "message": p.message, "fix": p.fix} for p in problems
        ],
    }


@tool(
    name="remediate",
    description="Return the ranked fixes (errors first) that would bring a "
    "codebase back into conformance with its architecture spec.",
)
def remediate(
    source_path: str,
    spec_path: str | None = None,
    dep_graph: DependencyGraph | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """List violations as an ordered fix plan.

    Args:
        source_path: Project root.
        spec_path: Spec location.
        dep_graph: An already-parsed graph; parsed from ``source_path`` if omitted.
        language: Language to parse when ``dep_graph`` is omitted.

    Returns:
        Verdict and the ordered list of actions.
    """
    result = check_architecture(source_path, spec_path, dep_graph, language)
    rank = {c.ERROR: 0, c.WARN: 1}
    actions = sorted(result["violations"], key=lambda v: rank.get(v["severity"], 2))
    return {"verdict": result["verdict"], "actions": actions}
