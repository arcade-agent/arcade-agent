"""Tool: Find entities relevant to a natural-language query."""

import re

from arcade_agent.algorithms.architecture import Architecture
from arcade_agent.parsers.graph import DependencyGraph
from arcade_agent.tools.registry import tool


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens, handling camelCase and snake_case."""
    # Split camelCase: "MyClassName" -> ["my", "class", "name"]
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Split on non-alphanumeric
    tokens = re.split(r"[^a-zA-Z0-9]+", text.lower())
    return [t for t in tokens if t and len(t) > 1]


# Words that carry no location signal in a query or task description. Left in,
# short ones such as "to" or "in" substring-match almost every file path.
_STOPWORDS = frozenset(
    """
    a an and are as at be by can do does for from has have how i if in into is it
    its me my of on or our should so that the their them then there these this
    those to was we what when where which while who why will with would you your
    add adds adding change create make new please support update use using
    """.split()
)


def _query_keywords(text: str) -> list[str]:
    """Tokenize a query or task, dropping stopwords and duplicates (order kept)."""
    seen: set[str] = set()
    keywords = []
    for token in _tokenize(text):
        if token not in _STOPWORDS and token not in seen:
            seen.add(token)
            keywords.append(token)
    return keywords


def _score_entity(
    fqn: str,
    name: str,
    package: str,
    file_path: str,
    keywords: list[str],
) -> float:
    """Score an entity against query keywords.

    Weighting:
    - Exact name match: 10 points
    - Name contains keyword: 5 points
    - Package contains keyword: 3 points
    - File path contains keyword: 1 point
    """
    score = 0.0
    name_lower = name.lower()
    name_tokens = set(_tokenize(name))
    pkg_lower = package.lower()
    fpath_lower = file_path.lower()

    for kw in keywords:
        if kw == name_lower:
            score += 10.0
        elif kw in name_tokens:
            score += 5.0
        elif kw in name_lower:
            score += 3.0

        if kw in pkg_lower:
            score += 3.0

        if kw in fpath_lower:
            score += 1.0

    return score


@tool(
    name="find_relevant",
    description="Find code entities relevant to a natural-language query. "
    "Searches entity names, packages, and file paths. "
    "Optionally uses recovered architecture for component context.",
)
def find_relevant(
    dep_graph: DependencyGraph,
    query: str,
    architecture: Architecture | None = None,
    top_k: int = 10,
) -> dict:
    """Find entities relevant to a natural-language query.

    Uses keyword matching against entity names, packages, and file paths.
    If an architecture is provided, also matches against component names
    and responsibilities.

    Args:
        dep_graph: Dependency graph to search.
        query: Natural-language query (e.g. "authentication login").
        architecture: Optional recovered architecture for component context.
        top_k: Maximum number of results to return.

    Returns:
        Dict with ranked list of relevant entities.
    """
    keywords = _query_keywords(query)
    if not keywords:
        return {"query": query, "results": [], "error": "No searchable keywords found"}

    # Score entities
    scored: list[tuple[float, str]] = []
    for fqn, entity in dep_graph.entities.items():
        score = _score_entity(fqn, entity.name, entity.package, entity.file_path, keywords)
        if score > 0:
            scored.append((score, fqn))

    # If architecture is provided, boost entities in matching components
    if architecture:
        comp_boosts: dict[str, float] = {}
        for comp in architecture.components:
            comp_score = 0.0
            comp_tokens = set(_tokenize(comp.name))
            resp_tokens = set(_tokenize(comp.responsibility))
            for kw in keywords:
                if kw in comp_tokens:
                    comp_score += 5.0
                if kw in resp_tokens:
                    comp_score += 3.0
            if comp_score > 0:
                for fqn in comp.entities:
                    comp_boosts[fqn] = comp_boosts.get(fqn, 0) + comp_score

        # Apply boosts
        boosted = []
        existing_fqns = {fqn for _, fqn in scored}
        for score, fqn in scored:
            boosted.append((score + comp_boosts.get(fqn, 0), fqn))

        # Add entities that only matched via component
        for fqn, boost in comp_boosts.items():
            if fqn not in existing_fqns:
                boosted.append((boost, fqn))

        scored = boosted

    # Rank and take top-k
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    results = []
    for score, fqn in top:
        entity = dep_graph.entities.get(fqn)
        if not entity:
            continue
        result: dict = {
            "fqn": fqn,
            "name": entity.name,
            "kind": entity.kind,
            "package": entity.package,
            "file_path": entity.file_path,
            "score": round(score, 1),
        }
        if architecture:
            result["component"] = architecture.component_of(fqn)
        results.append(result)

    return {
        "query": query,
        "keywords": keywords,
        "num_results": len(results),
        "results": results,
    }
