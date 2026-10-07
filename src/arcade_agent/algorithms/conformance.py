"""Architecture conformance: check a dependency graph against an intended architecture.

The intended architecture is an author-written ``architecture.spec.json``::

    {
      "intent": "...",
      "components": [{"name": "api", "match": "**/api/**", "layer": "presentation"}],
      "layers": ["presentation", "application", "domain", "infrastructure"],
      "allow":  [{"from": "presentation", "to": "application"}],
      "forbid": [{"from": "presentation", "to": "infrastructure", "why": "..."}],
      "budgets": {"no_cycles": true, "max_fan_in": 8, "max_component_entities": 200,
                  "min_turbomq": 0.4, "max_new_smells": 0}
    }

Conformance is deterministic by construction: entities are assigned to
components by matching their file paths against the spec's ``match`` globs, and
rules are evaluated over the resulting component-level edges. No clustering is
involved, so the same code always yields the same verdict.

Every dependency rule is evaluated by one function, :func:`dependency_problems`,
which both the whole-codebase check and the hypothetical-edge preview call. The
answer an agent gets before writing an import therefore cannot disagree with the
verdict it gets after.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx

from arcade_agent.parsers.graph import DependencyGraph

UNMAPPED = "(unmapped)"

ERROR = "error"
WARN = "warn"


@dataclass
class ComponentSpec:
    """One component of the intended architecture."""

    name: str
    match: str = ""
    layer: str | None = None
    keywords: list[str] = field(default_factory=list)


@dataclass
class DependencyRule:
    """An allow or forbid rule between components or layers (names may be globs)."""

    from_: str
    to: str
    why: str = ""


@dataclass
class ArchitectureSpec:
    """The intended architecture an implementation is checked against."""

    intent: str = ""
    components: list[ComponentSpec] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    allow: list[DependencyRule] = field(default_factory=list)
    forbid: list[DependencyRule] = field(default_factory=list)
    budgets: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArchitectureSpec":
        """Build a spec from its JSON form, rejecting malformed entries."""
        components = []
        for raw in data.get("components", []):
            if not isinstance(raw, dict) or not raw.get("name"):
                raise ValueError(f"Spec component needs a 'name': {raw!r}")
            components.append(
                ComponentSpec(
                    name=raw["name"],
                    match=raw.get("match", ""),
                    layer=raw.get("layer"),
                    keywords=list(raw.get("keywords", [])),
                )
            )
        return cls(
            intent=data.get("intent", ""),
            components=components,
            layers=list(data.get("layers", [])),
            allow=[_rule_from_dict(r) for r in data.get("allow", [])],
            forbid=[_rule_from_dict(r) for r in data.get("forbid", [])],
            budgets=dict(data.get("budgets", {})),
        )

    def layer_of(self, component: str) -> str | None:
        """Return the layer a component belongs to, if the spec assigns one."""
        for comp in self.components:
            if comp.name == component:
                return comp.layer
        return None

    def component_names(self) -> list[str]:
        """Return component names in spec order."""
        return [c.name for c in self.components]


@dataclass
class Violation:
    """A single way the implementation departs from the intended architecture."""

    id: str
    rule: str
    severity: str
    message: str
    fix: str = ""
    source: str | None = None
    target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form, omitting unset endpoints."""
        out: dict[str, Any] = {
            "id": self.id,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "fix": self.fix,
        }
        if self.source is not None:
            out["from"] = self.source
        if self.target is not None:
            out["to"] = self.target
        return out


def _rule_from_dict(raw: dict[str, Any]) -> DependencyRule:
    if not isinstance(raw, dict) or "from" not in raw or "to" not in raw:
        raise ValueError(f"Spec rule needs 'from' and 'to': {raw!r}")
    return DependencyRule(from_=raw["from"], to=raw["to"], why=raw.get("why", ""))


def load_spec(path: str | Path) -> ArchitectureSpec:
    """Load an ``architecture.spec.json`` file.

    Args:
        path: Path to the spec file.

    Returns:
        The parsed spec.
    """
    return ArchitectureSpec.from_dict(json.loads(Path(path).read_text()))


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path glob to an anchored regex.

    Supports ``**`` (any number of directories, including none when followed by
    ``/``), ``*`` and ``?`` within one segment, and ``{a,b}`` alternation.

    Args:
        pattern: The glob pattern.

    Returns:
        Compiled regex matching the whole string.
    """
    out: list[str] = []
    i = 0
    depth = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**", i):
            i += 2
            if i < len(pattern) and pattern[i] == "/":
                out.append("(?:.*/)?")
                i += 1
            else:
                out.append(".*")
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "{":
            out.append("(?:")
            depth += 1
        elif c == "}" and depth:
            out.append(")")
            depth -= 1
        elif c == "," and depth:
            out.append("|")
        else:
            out.append(re.escape(c))
        i += 1
    if depth:
        raise ValueError(f"Unbalanced '{{' in glob: {pattern!r}")
    return re.compile("^" + "".join(out) + "$")


def map_entities(dep_graph: DependencyGraph, spec: ArchitectureSpec) -> dict[str, str]:
    """Assign each entity to a spec component by file path (first match wins).

    Args:
        dep_graph: Parsed dependency graph.
        spec: Intended architecture.

    Returns:
        Map of entity FQN to component name, or ``UNMAPPED``.
    """
    matchers = [(c.name, glob_to_regex(c.match)) for c in spec.components if c.match]
    mapping: dict[str, str] = {}
    for fqn, entity in dep_graph.entities.items():
        path = entity.file_path.replace("\\", "/")
        mapping[fqn] = next((name for name, rx in matchers if rx.match(path)), UNMAPPED)
    return mapping


def component_edges(
    dep_graph: DependencyGraph, entity_components: dict[str, str]
) -> dict[tuple[str, str], int]:
    """Aggregate entity edges into weighted cross-component edges.

    Edges touching ``UNMAPPED`` are dropped: the contract governs only the
    components it names.

    Args:
        dep_graph: Parsed dependency graph.
        entity_components: Output of :func:`map_entities`.

    Returns:
        Map of ``(from_component, to_component)`` to edge count.
    """
    edges: dict[tuple[str, str], int] = {}
    for edge in dep_graph.edges:
        src = entity_components.get(edge.source)
        dst = entity_components.get(edge.target)
        if not src or not dst or src == dst or UNMAPPED in (src, dst):
            continue
        edges[(src, dst)] = edges.get((src, dst), 0) + 1
    return edges


def _matches(spec: ArchitectureSpec, component: str, pattern: str) -> bool:
    """A rule endpoint matches a component by name glob or by layer name."""
    if glob_to_regex(pattern).match(component):
        return True
    return spec.layer_of(component) == pattern


def _is_allowed(spec: ArchitectureSpec, src: str, dst: str) -> bool:
    return any(_matches(spec, src, r.from_) and _matches(spec, dst, r.to) for r in spec.allow)


def dependency_problems(spec: ArchitectureSpec, src: str, dst: str) -> list[Violation]:
    """Evaluate every dependency rule for one component-level edge.

    This is the single source of truth for whether ``src -> dst`` is permitted.
    Violation ids are left empty; callers number them.

    Args:
        spec: Intended architecture.
        src: Depending component.
        dst: Depended-on component.

    Returns:
        The violations the edge would cause (empty when allowed).
    """
    problems: list[Violation] = []
    for rule in spec.forbid:
        if _matches(spec, src, rule.from_) and _matches(spec, dst, rule.to):
            reason = f": {rule.why}" if rule.why else ""
            problems.append(
                Violation(
                    id="",
                    rule="forbidden-dependency",
                    severity=ERROR,
                    message=f"{src} -> {dst} is forbidden{reason}",
                    fix=f"Remove the dependency from {src} to {dst}, or route it "
                    "through a component both may use.",
                    source=src,
                    target=dst,
                )
            )

    rank = {layer: i for i, layer in enumerate(spec.layers)}
    src_layer, dst_layer = spec.layer_of(src), spec.layer_of(dst)
    if (
        src_layer in rank
        and dst_layer in rank
        and rank[src_layer] > rank[dst_layer]
        and not _is_allowed(spec, src, dst)
    ):
        problems.append(
            Violation(
                id="",
                rule="layer-violation",
                severity=ERROR,
                message=f"{src} ({src_layer}) -> {dst} ({dst_layer}) points against "
                "the layer order",
                fix=f"Invert the dependency (depend on an abstraction owned by "
                f"{src_layer}) or move the code.",
                source=src,
                target=dst,
            )
        )
    return problems


def _cycles(edges: dict[tuple[str, str], int]) -> list[list[str]]:
    """One sorted member list per strongly connected group of >1 component."""
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_edges_from(edges)
    groups = [sorted(scc) for scc in nx.strongly_connected_components(graph) if len(scc) > 1]
    return sorted(groups)


def check_conformance(
    spec: ArchitectureSpec,
    entity_components: dict[str, str],
    edges: dict[tuple[str, str], int],
    turbomq: float | None = None,
    num_smells: int | None = None,
    baseline_num_smells: int | None = None,
) -> list[Violation]:
    """Check an implementation against its intended architecture.

    Args:
        spec: Intended architecture.
        entity_components: Output of :func:`map_entities`.
        edges: Output of :func:`component_edges`.
        turbomq: TurboMQ of the package-based recovery, for ``min_turbomq``.
        num_smells: Current smell count, for ``max_new_smells``.
        baseline_num_smells: Baseline smell count, for ``max_new_smells``.

    Returns:
        Violations in a stable order (rule evaluation order, then edge order).
    """
    found: list[Violation] = []
    budgets = spec.budgets

    for src, dst in sorted(edges):
        found.extend(dependency_problems(spec, src, dst))

    if budgets.get("no_cycles"):
        for group in _cycles(edges):
            found.append(
                Violation(
                    id="",
                    rule="dependency-cycle",
                    severity=ERROR,
                    message="Mutually dependent components: " + ", ".join(group),
                    fix="Break the cycle: introduce an interface or invert one edge.",
                )
            )

    if "max_fan_in" in budgets:
        fan_in: dict[str, int] = {}
        for _src, dst in edges:
            fan_in[dst] = fan_in.get(dst, 0) + 1
        for comp in sorted(fan_in):
            if fan_in[comp] > budgets["max_fan_in"]:
                found.append(
                    Violation(
                        id="",
                        rule="max-fan-in",
                        severity=WARN,
                        message=f"{comp} is depended on by {fan_in[comp]} components "
                        f"(> {budgets['max_fan_in']})",
                        fix=f"Split {comp} or hide it behind a narrower interface.",
                    )
                )

    if "max_component_entities" in budgets:
        sizes: dict[str, int] = {}
        for comp in entity_components.values():
            if comp != UNMAPPED:
                sizes[comp] = sizes.get(comp, 0) + 1
        for comp in sorted(sizes):
            if sizes[comp] > budgets["max_component_entities"]:
                found.append(
                    Violation(
                        id="",
                        rule="max-component-size",
                        severity=WARN,
                        message=f"{comp} has {sizes[comp]} entities "
                        f"(> {budgets['max_component_entities']})",
                        fix=f"Split {comp} along its sub-responsibilities.",
                    )
                )

    if turbomq is not None and "min_turbomq" in budgets and turbomq < budgets["min_turbomq"]:
        found.append(
            Violation(
                id="",
                rule="metric-budget",
                severity=ERROR,
                message=f"TurboMQ {turbomq:.2f} is below the floor {budgets['min_turbomq']}",
                fix="Improve cohesion or reduce coupling between components.",
            )
        )

    if (
        num_smells is not None
        and baseline_num_smells is not None
        and "max_new_smells" in budgets
        and num_smells - baseline_num_smells > budgets["max_new_smells"]
    ):
        found.append(
            Violation(
                id="",
                rule="smell-budget",
                severity=ERROR,
                message=f"{num_smells - baseline_num_smells} new smell(s) since the "
                f"baseline (budget {budgets['max_new_smells']})",
                fix="Resolve the newly introduced smells before committing.",
            )
        )

    for i, violation in enumerate(found):
        violation.id = f"{violation.rule}:{i}"
    return found


def verdict(violations: list[Violation]) -> str:
    """Return ``FAIL`` if any error, ``WARN`` if only warnings, else ``PASS``."""
    if any(v.severity == ERROR for v in violations):
        return "FAIL"
    return "WARN" if violations else "PASS"


_WORD = re.compile(r"[a-z0-9]+")

# Words that signal a role, keyed by conventional layer names. Used only when a
# component's layer is one of these; spec authors can add per-component
# ``keywords`` for anything else.
ROLE_VOCABULARY: dict[str, tuple[str, ...]] = {
    "presentation": ("api", "http", "endpoint", "route", "controller", "handler",
                     "request", "response", "rest", "view", "page", "cli", "web"),
    "application": ("service", "usecase", "business", "logic", "workflow",
                    "orchestrate", "command", "application"),
    "domain": ("entity", "model", "domain", "value", "aggregate", "rule",
               "dataclass"),
    "infrastructure": ("store", "storage", "database", "repository", "persist",
                       "persistence", "cache", "sql", "table", "queue", "client",
                       "adapter", "file", "memory"),
}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2}


def _related(a: str, b: str) -> bool:
    """Words match when one is a prefix of the other (``store``/``stores``)."""
    return a.startswith(b) or b.startswith(a)


def propose_placement(spec: ArchitectureSpec, intent: str) -> dict[str, Any]:
    """Suggest where new code described by ``intent`` belongs, and what it may use.

    Components are ranked by how many words of their name, layer, path glob,
    ``keywords``, and the role vocabulary of their layer relate to words of the
    intent. Ties keep spec order, so the answer is stable.

    Args:
        spec: Intended architecture.
        intent: Plain-language description of what is about to be added.

    Returns:
        Suggested component and layer, its path convention, and the components
        it may and must not depend on.
    """
    intent_words = _words(intent)

    def score(comp: ComponentSpec) -> int:
        role = " ".join(ROLE_VOCABULARY.get(comp.layer or "", ()))
        comp_words = _words(
            f"{comp.name} {comp.layer or ''} {comp.match} {' '.join(comp.keywords)} {role}"
        )
        return sum(1 for cw in comp_words if any(_related(cw, iw) for iw in intent_words))

    scored = [(score(c), -i, c) for i, c in enumerate(spec.components)]
    best_score, _, best = max(scored, key=lambda t: (t[0], t[1]), default=(0, 0, None))
    if best is None or best_score == 0:
        return {
            "intent": intent,
            "suggested_component": None,
            "suggested_layer": None,
            "path_convention": None,
            "may_depend_on": [],
            "must_not_depend_on": [],
            "note": "No component clearly matches; choose by layer: "
            + ", ".join(spec.component_names()),
        }

    # "May depend on" is exactly what dependency_problems() permits, so the
    # guidance can never contradict preview_impact or check_architecture.
    others = [c.name for c in spec.components if c.name != best.name]
    may, must_not = [], []
    for other in others:
        problems = dependency_problems(spec, best.name, other)
        if problems:
            must_not.append({"component": other, "why": problems[0].message})
        else:
            may.append(other)
    return {
        "intent": intent,
        "suggested_component": best.name,
        "suggested_layer": best.layer,
        "path_convention": best.match or None,
        "may_depend_on": may,
        "must_not_depend_on": must_not,
        "note": None,
    }
