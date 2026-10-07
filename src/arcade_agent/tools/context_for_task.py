"""Tool: Assemble the minimal set of files an agent should read for a task."""

from typing import Any

from arcade_agent.algorithms.architecture import Architecture
from arcade_agent.parsers.graph import DependencyGraph
from arcade_agent.tools.find_relevant import _query_keywords, _score_entity, _tokenize
from arcade_agent.tools.registry import tool

# Roles, ordered from strongest (kept when an entity qualifies for several).
ROLE_DIRECT = "direct match"
ROLE_DEPENDENT = "dependent of match"
ROLE_DEPENDENCY = "dependency of match"
ROLE_SIBLING = "component sibling"

_ROLE_RANK: dict[str, int] = {
    ROLE_DIRECT: 3,
    ROLE_DEPENDENT: 2,
    ROLE_DEPENDENCY: 1,
    ROLE_SIBLING: 0,
}

# How many top seed entities we expand the neighbourhood from. Keeps the
# returned context minimal instead of pulling in every one-hop neighbour.
_EXPAND_SEED_LIMIT = 10

# Fraction of a seed's relevance propagated to its neighbours.
_NEIGHBOUR_DECAY = 0.5


def _matched_keywords(name: str, package: str, file_path: str, keywords: list[str]) -> list[str]:
    """Return the keywords that directly match an entity's identifiers.

    Args:
        name: Entity simple name.
        package: Entity package.
        file_path: Entity file path.
        keywords: Tokenized task keywords.

    Returns:
        Sorted list of keywords that appear in the name, package, or file path.
    """
    name_lower = name.lower()
    name_tokens = set(_tokenize(name))
    pkg_lower = package.lower()
    fpath_lower = file_path.lower()

    hits: set[str] = set()
    for kw in keywords:
        if (
            kw == name_lower
            or kw in name_tokens
            or kw in name_lower
            or kw in pkg_lower
            or kw in fpath_lower
        ):
            hits.add(kw)
    return sorted(hits)


def _assign(
    info: dict[str, dict[str, Any]],
    fqn: str,
    role: str,
    relevance: float,
) -> dict[str, Any]:
    """Record or upgrade an entity's inclusion in the context.

    Relevance accumulates across signals; the strongest role (by rank) wins.

    Args:
        info: Accumulator mapping fqn -> inclusion metadata.
        fqn: Entity fully-qualified name.
        role: Candidate role for this entity.
        relevance: Relevance contribution to add.

    Returns:
        The (possibly newly created) metadata entry for the entity.
    """
    entry = info.get(fqn)
    if entry is None:
        entry = {"role": role, "relevance": 0.0, "keywords": set(), "notes": []}
        info[fqn] = entry
    entry["relevance"] += relevance
    if _ROLE_RANK[role] > _ROLE_RANK[entry["role"]]:
        entry["role"] = role
    return entry


def _add_note(entry: dict[str, Any], note: str) -> None:
    """Append a de-duplicated relationship note to an entity entry.

    Args:
        entry: Entity metadata entry.
        note: Human-readable note describing why the entity is relevant.
    """
    if note not in entry["notes"]:
        entry["notes"].append(note)


@tool(
    name="context_for_task",
    description="Given a natural-language task, return the minimal ranked set of files an "
    "agent should read, each with a short role explanation. Answers 'what do I need to "
    "read?' by seeding on keyword matches and expanding to their direct dependencies and "
    "dependents. Optionally uses recovered architecture for component context.",
)
def context_for_task(
    dep_graph: DependencyGraph,
    task: str,
    architecture: Architecture | None = None,
    max_files: int = 15,
) -> dict[str, Any]:
    """Assemble the minimal ranked set of files to read for a task.

    Seeds relevance by keyword-matching entity names, packages, and file paths,
    then expands the neighbourhood of the top seeds with their direct
    dependencies (forward edges) and dependents (reverse edges) so the agent
    understands the code it must touch. Entities are aggregated into files,
    ranked by relevance, and capped at ``max_files``.

    Args:
        dep_graph: Dependency graph to search.
        task: Natural-language task (e.g. "add rate limiting to login").
        architecture: Optional recovered architecture for component context.
        max_files: Maximum number of files to return.

    Returns:
        Dict with the task, extracted keywords, and a ranked list of files.
        Each file entry carries its score, primary role, contributing entities
        with per-entity roles, and a concise reason string. If the task yields
        no searchable keywords, an empty result with an ``error`` note is
        returned rather than raising.
    """
    keywords = _query_keywords(task)
    if not keywords:
        return {
            "task": task,
            "keywords": [],
            "num_files": 0,
            "files": [],
            "error": "No searchable keywords found",
        }

    entities = dep_graph.entities
    info: dict[str, dict[str, Any]] = {}

    # -- 1. Seed on direct keyword matches -----------------------------------
    for fqn, entity in entities.items():
        score = _score_entity(fqn, entity.name, entity.package, entity.file_path, keywords)
        if score > 0:
            entry = _assign(info, fqn, ROLE_DIRECT, score)
            hits = _matched_keywords(entity.name, entity.package, entity.file_path, keywords)
            entry["keywords"].update(hits)

    # -- 2. Component context (siblings + boost) -----------------------------
    if architecture:
        for comp in architecture.components:
            comp_score = 0.0
            comp_tokens = set(_tokenize(comp.name))
            resp_tokens = set(_tokenize(comp.responsibility))
            for kw in keywords:
                if kw in comp_tokens:
                    comp_score += 5.0
                if kw in resp_tokens:
                    comp_score += 3.0
            if comp_score <= 0:
                continue
            for fqn in comp.entities:
                if fqn not in entities:
                    continue
                if fqn in info and info[fqn]["role"] == ROLE_DIRECT:
                    # Boost an already-matched entity's relevance.
                    info[fqn]["relevance"] += comp_score
                else:
                    entry = _assign(info, fqn, ROLE_SIBLING, comp_score)
                    _add_note(entry, f"in component '{comp.name}' matching task")

    # -- 3. Expand neighbourhood of the top seeds ----------------------------
    forward = dep_graph.to_adjacency()
    reverse: dict[str, list[str]] = {fqn: [] for fqn in entities}
    for edge in dep_graph.edges:
        if edge.target in reverse and edge.source in entities:
            reverse[edge.target].append(edge.source)

    direct_seeds = [fqn for fqn, e in info.items() if e["role"] == ROLE_DIRECT]
    direct_seeds.sort(key=lambda f: info[f]["relevance"], reverse=True)
    top_seeds = direct_seeds[:_EXPAND_SEED_LIMIT]

    visited: set[str] = set()
    for seed in top_seeds:
        if seed in visited:
            continue
        visited.add(seed)
        seed_name = entities[seed].name
        seed_relevance = info[seed]["relevance"]
        propagated = seed_relevance * _NEIGHBOUR_DECAY

        for dep in forward.get(seed, []):
            if dep not in entities or dep == seed:
                continue
            entry = _assign(info, dep, ROLE_DEPENDENCY, propagated)
            _add_note(entry, f"dependency of {seed_name}")
            _add_note(info[seed], f"depends on {entities[dep].name}")

        for dependent in reverse.get(seed, []):
            if dependent == seed:
                continue
            entry = _assign(info, dependent, ROLE_DEPENDENT, propagated)
            _add_note(entry, f"depends on matched {seed_name}")
            _add_note(info[seed], f"depended on by {entities[dependent].name}")

    # -- 4. Aggregate entities into files ------------------------------------
    files: dict[str, dict[str, Any]] = {}
    for fqn, entry in info.items():
        entity = entities[fqn]
        fpath = entity.file_path
        agg = files.get(fpath)
        if agg is None:
            agg = {
                "file_path": fpath,
                "score": 0.0,
                "top_relevance": -1.0,
                "primary_role": entry["role"],
                "entities": [],
                "keywords": set(),
                "notes": [],
            }
            files[fpath] = agg
        agg["score"] += entry["relevance"]
        agg["entities"].append({"fqn": fqn, "role": entry["role"]})
        agg["keywords"].update(entry["keywords"])
        for note in entry["notes"]:
            if note not in agg["notes"]:
                agg["notes"].append(note)
        # Primary role = role of the highest-relevance entity in the file.
        rank = (entry["relevance"], _ROLE_RANK[entry["role"]])
        if rank > (agg["top_relevance"], _ROLE_RANK[agg["primary_role"]]):
            agg["top_relevance"] = entry["relevance"]
            agg["primary_role"] = entry["role"]

    ranked = sorted(files.values(), key=lambda f: f["score"], reverse=True)[:max_files]

    results = []
    for agg in ranked:
        reason_parts: list[str] = []
        kws = sorted(agg["keywords"])
        if kws:
            reason_parts.append("matches " + ", ".join(f"'{k}'" for k in kws))
        reason_parts.extend(agg["notes"][:2])
        reason = "; ".join(reason_parts) if reason_parts else agg["primary_role"]

        results.append({
            "file_path": agg["file_path"],
            "score": round(agg["score"], 1),
            "primary_role": agg["primary_role"],
            "entities": sorted(agg["entities"], key=lambda e: e["fqn"]),
            "reason": reason,
        })

    return {
        "task": task,
        "keywords": keywords,
        "num_files": len(results),
        "files": results,
    }
