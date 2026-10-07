"""MCP server adapter — exposes arcade-agent tools via Model Context Protocol."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from arcade_agent.budget import enforce_budget, truncate_result
from arcade_agent.serialization import (
    dict_to_architecture,
    dict_to_graph,
    serialize_result,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session store — holds intermediate results so agents can reference by ID
# ---------------------------------------------------------------------------

_session: dict[str, Any] = {}


def _store(obj: Any, label: str = "") -> str:
    """Store an object in the session and return its ID."""
    sid = uuid.uuid4().hex[:12]
    _session[sid] = {"label": label, "value": obj}
    return sid


def _resolve(ref: str | dict | Any, expected_type: str = "") -> Any:
    """Resolve a session reference or pass-through a dict.

    Accepts either a session ID string or an inline dict representation.
    """
    if isinstance(ref, str) and ref in _session:
        return _session[ref]["value"]
    if isinstance(ref, dict):
        if expected_type == "DependencyGraph":
            return dict_to_graph(ref)
        if expected_type == "Architecture":
            return dict_to_architecture(ref)
        return ref
    raise ValueError(
        f"Invalid reference: {ref!r}. Provide a session ID from a previous tool call "
        f"or an inline JSON object."
    )


# ---------------------------------------------------------------------------
# Tool wrappers — each MCP tool maps to an arcade-agent tool
# ---------------------------------------------------------------------------


def _make_summary(obj: Any, label: str) -> dict:
    """Create a compact summary dict with a session ID for later retrieval."""
    sid = _store(obj, label)
    summary: dict[str, Any] = {"session_id": sid, "type": label}

    if hasattr(obj, "num_entities"):
        summary["num_entities"] = obj.num_entities
    if hasattr(obj, "num_edges"):
        summary["num_edges"] = obj.num_edges
    if hasattr(obj, "packages"):
        summary["num_packages"] = len(obj.packages)
    if hasattr(obj, "components"):
        summary["num_components"] = len(obj.components)
        summary["components"] = [
            {"name": c.name, "num_entities": len(c.entities)}
            for c in obj.components
        ]
    if hasattr(obj, "source_files"):
        summary["num_files"] = len(obj.source_files)
    if hasattr(obj, "language"):
        summary["language"] = obj.language
    if hasattr(obj, "languages"):
        langs = getattr(obj, "languages")
        if langs:
            summary["languages"] = list(langs)
    if hasattr(obj, "exclude_tests"):
        summary["exclude_tests"] = bool(obj.exclude_tests)
    if hasattr(obj, "exclude_dirs"):
        summary["exclude_dirs"] = list(obj.exclude_dirs)
    metadata = getattr(obj, "metadata", None) if hasattr(obj, "num_entities") else None
    if isinstance(metadata, dict) and metadata:
        # Agents never see log lines; surface parse notes such as polyglot
        # FQN-collision counts directly in the summary.
        summary["metadata"] = metadata
    if hasattr(obj, "name") and isinstance(getattr(obj, "name", None), str):
        summary["name"] = obj.name
    if hasattr(obj, "version"):
        summary["version"] = obj.version
    if hasattr(obj, "algorithm"):
        summary["algorithm"] = obj.algorithm

    return summary


def _apply_budget(data: Any, max_tokens: int | None) -> Any:
    """Apply token budget truncation if requested.

    - Dicts with ``graph``/``architecture`` keys are handled by the
      domain-aware ``truncate_result``.
    - All other dicts (e.g. MCP summary dicts with ``session_id``) are
      handled by the generic ``enforce_budget``.
    - Non-dict values pass through unchanged.
    """
    if max_tokens is None or not isinstance(data, dict):
        return data
    if "graph" in data or "architecture" in data:
        return truncate_result(data, max_tokens)
    return enforce_budget(data, max_tokens)


def _resolve_parse_source(
    source_path: str,
    language: str | None,
    languages: list[str] | None,
    files: list[str] | None,
) -> tuple[str, str | None, list[str] | None, list[str] | None]:
    """Resolve an ingest session into the concrete inputs required by parse.

    MCP clients should not need to discover a temporary clone path or repeat the
    language/file selection already made by ``ingest``. The ingest session's
    selected files are explicit and therefore are not re-filtered by parse-level
    exclusion options. Explicit parse arguments still take precedence when a
    caller intentionally wants a narrower parse.
    """
    if source_path not in _session:
        return source_path, language, languages, files

    from arcade_agent.tools.ingest import IngestedRepo

    ingested = _session[source_path]["value"]
    if not isinstance(ingested, IngestedRepo):
        label = _session[source_path]["label"]
        raise ValueError(
            f"source_path session {source_path!r} contains {label}, not IngestedRepo"
        )

    if language is None and languages is None:
        if ingested.languages:
            languages = list(ingested.languages)
        elif ingested.language:
            language = ingested.language
    if files is None:
        files = [str(path) for path in ingested.source_files]

    return str(ingested.path), language, languages, files


def _build_server():  # type: ignore[no-untyped-def]
    """Build and return the FastMCP server instance.

    Separated from module-level so the mcp dependency is only required
    at runtime when actually starting the server.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "MCP support requires the 'mcp' package. "
            "Install with: pip install arcade-agent[mcp]"
        )

    server = FastMCP(
        "arcade-agent",
        instructions=(
            "Software architecture analysis toolkit. "
            "Tools produce complex results stored in a session. "
            "Use 'analyze' for a one-call end-to-end pipeline (offloaded from the event loop), "
            "or compose individual tools. "
            "Use session IDs from previous tool outputs as inputs to subsequent tools. "
            "For example: call 'ingest' and pass its session_id as source_path to "
            "'parse', then pass the parse session_id as dep_graph to 'recover'. "
            "For polyglot repositories, pass languages such as ['java', 'kotlin'] "
            "to ingest; parse inherits that selection from the ingest session. "
            "Source discovery excludes test/vendor/build directories by default; "
            "set exclude_tests=false when those files are intentionally in scope, "
            "or pass project-relative exclude_dirs for custom layouts. "
            "Configure exclusions on ingest when chaining ingest to parse because "
            "the ingest session carries an already-selected explicit file list. "
            "Cross-language edges are only linked within a language family "
            "(java+kotlin today); other language pairs are parsed and merged "
            "but never linked to each other."
        ),
    )

    # -- ingest ----------------------------------------------------------------

    @server.tool()
    def ingest(
        source: str,
        language: str | None = None,
        languages: list[str] | None = None,
        work_dir: str | None = None,
        exclude_tests: bool = True,
        source_root: str | None = None,
        exclude_dirs: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Prepare source code for analysis.

        Accepts a git URL or local path. Auto-detects source roots and filters
        out test/vendored code.

        Args:
            source: Git repo URL or local directory path.
            language: Override language detection (java, python, c, typescript,
                go, kotlin, rust, or "multi" for every detected language).
            languages: Explicit polyglot language list (e.g. ["java", "kotlin"]).
                Mutually exclusive with language.
            work_dir: Directory to clone into. Uses temp dir if None.
            exclude_tests: Exclude test/vendor/build directories (default True).
            source_root: Override source root (e.g. 'src/main/java').
            exclude_dirs: Additional exact project-relative directories to exclude
                (e.g. ["integrationTest", "src/e2e"]).
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.ingest import ingest as _ingest

        result = _ingest(
            source=source,
            language=language,
            languages=languages,
            work_dir=work_dir,
            exclude_tests=exclude_tests,
            source_root=source_root,
            exclude_dirs=exclude_dirs,
        )
        summary = _make_summary(result, "IngestedRepo")
        return json.dumps(_apply_budget(summary, max_tokens), indent=2)

    # -- parse -----------------------------------------------------------------

    @server.tool()
    def parse(
        source_path: str,
        language: str | None = None,
        languages: list[str] | None = None,
        files: list[str] | None = None,
        exclude_tests: bool = True,
        exclude_dirs: list[str] | None = None,
        use_cache: bool = True,
        max_tokens: int | None = None,
    ) -> str:
        """Parse source code and extract a dependency graph.

        Returns a session_id referencing the parsed DependencyGraph. Pass this
        session_id as the dep_graph argument to recover, detect_smells, etc.

        Cross-language relinking is family-scoped: java+kotlin is the supported
        (and validated) pair. Any other combination is parsed and merged into
        one graph without edges between the two languages. FQN collisions
        across families keep both entities (the later one re-keyed as
        "<fqn>#<language>") and are counted in the summary's metadata.

        Args:
            source_path: Root directory of the project, or a session ID returned by
                ingest. An ingest session carries its selected files and languages
                into this parse call unless explicitly overridden. Its files are
                not re-filtered by parse-level exclusion options.
            language: Language to parse (java, python, c, typescript, go, kotlin, rust),
                or "multi" to parse every detected language and relink
                cross-language edges.
            languages: Explicit polyglot language list (e.g. ["java", "kotlin"]).
                Mutually exclusive with language.
            files: Specific files to parse. An explicit list is authoritative.
                Discovers automatically only when neither files nor an ingest
                session supplies a list.
            exclude_tests: Exclude test/vendor/build directories during automatic
                discovery (default True). When source_path is an ingest session,
                configure this on ingest instead.
            exclude_dirs: Additional exact project-relative directories to exclude
                during automatic discovery. When source_path is an ingest session,
                configure these on ingest instead.
            use_cache: Return cached results when source files haven't changed.
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.parse import parse as _parse

        source_path, language, languages, files = _resolve_parse_source(
            source_path, language, languages, files
        )
        graph = _parse(
            source_path=source_path,
            language=language,
            languages=languages,
            files=files,
            exclude_tests=exclude_tests,
            exclude_dirs=exclude_dirs,
            use_cache=use_cache,
        )
        summary = _make_summary(graph, "DependencyGraph")
        return json.dumps(_apply_budget(summary, max_tokens), indent=2)

    # -- analyze ---------------------------------------------------------------

    @server.tool()
    async def analyze(
        source: str,
        language: str | None = None,
        source_root: str | None = None,
        work_dir: str | None = None,
        exclude_tests: bool = True,
        exclude_dirs: list[str] | None = None,
        algorithm: str = "pkg",
        num_clusters: int | None = None,
        similarity_measure: str = "uem",
        pkg_depth: int | None = None,
        hybrid_weight: float = 0.5,
        use_cache: bool = True,
        use_llm: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        """Run the complete architecture analysis pipeline asynchronously.

        Blocking stages run sequentially in a worker thread so the MCP event
        loop stays responsive. Per-stage session IDs are stored as each stage
        completes, so a later failure still leaves earlier artifacts reusable.
        Session IDs match the conventions of the fine-grained MCP tools.

        Args:
            source: Git repo URL or local directory path.
            language: Optional language override, including "multi".
            source_root: Optional source-root override.
            work_dir: Directory used for remote clones.
            exclude_tests: Exclude test/vendor/build directories (default True).
            exclude_dirs: Additional exact project-relative directories to exclude.
            algorithm: Architecture recovery algorithm.
            num_clusters: Optional target cluster count.
            similarity_measure: Similarity measure for supported algorithms.
            pkg_depth: Optional package depth.
            hybrid_weight: Hybrid recovery weight.
            use_cache: Reuse parse caches when valid.
            use_llm: Enable LLM-assisted smell explanations.
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.analyze import (
            PartialAnalysisError,
        )
        from arcade_agent.tools.analyze import (
            analyze as _analyze,
        )

        stage_summaries: dict[str, Any] = {}

        def _on_stage(stage: str, value: Any) -> None:
            if stage == "repository":
                stage_summaries["repository"] = _make_summary(value, "IngestedRepo")
            elif stage == "graph":
                stage_summaries["graph"] = _make_summary(value, "DependencyGraph")
            elif stage == "architecture":
                stage_summaries["architecture"] = _make_summary(value, "Architecture")
            elif stage == "smells":
                stage_summaries["smells"] = {
                    "session_id": _store(value, "SmellList"),
                    "num_smells": len(value),
                }
            elif stage == "metrics":
                stage_summaries["metrics"] = {
                    "session_id": _store(value, "MetricList"),
                    "num_metrics": len(value),
                }

        try:
            result = await _analyze(
                source=source,
                language=language,
                source_root=source_root,
                work_dir=work_dir,
                exclude_tests=exclude_tests,
                exclude_dirs=exclude_dirs,
                algorithm=algorithm,
                num_clusters=num_clusters,
                similarity_measure=similarity_measure,
                pkg_depth=pkg_depth,
                hybrid_weight=hybrid_weight,
                use_cache=use_cache,
                use_llm=use_llm,
                on_stage=_on_stage,
            )
        except PartialAnalysisError as exc:
            error_payload: dict[str, Any] = {
                "type": "PartialAnalysisError",
                "error": str(exc),
                "failed_stage": exc.stage,
                **stage_summaries,
            }
            if max_tokens is not None:
                error_payload = enforce_budget(error_payload, max_tokens)
            return json.dumps(error_payload, indent=2)

        summary: dict[str, Any] = {
            "type": "AnalysisResult",
            "repository": stage_summaries["repository"],
            "graph": stage_summaries["graph"],
            "architecture": stage_summaries["architecture"],
            "smells": stage_summaries["smells"],
            "metrics": stage_summaries["metrics"],
            "num_smells": len(result.smells),
            "num_metrics": len(result.metrics),
        }
        # Composite summaries include nested graph/architecture keys, so the
        # domain-aware truncate_result path is a no-op. Use enforce_budget.
        if max_tokens is not None:
            summary = enforce_budget(summary, max_tokens)
        return json.dumps(summary, indent=2)

    # -- recover ---------------------------------------------------------------

    @server.tool()
    def recover(
        dep_graph: str,
        algorithm: str = "pkg",
        num_clusters: int | None = None,
        similarity_measure: str = "uem",
        pkg_depth: int | None = None,
        hybrid_weight: float = 0.5,
        max_tokens: int | None = None,
    ) -> str:
        """Recover software architecture from a dependency graph.

        Args:
            dep_graph: Session ID from a previous 'parse' call.
            algorithm: Recovery algorithm (pkg, wca, acdc, arc, limbo).
            num_clusters: Target number of clusters (for wca/arc/limbo).
            similarity_measure: Similarity measure for wca (js, uem, scm).
            pkg_depth: Package depth for pkg algorithm.
            hybrid_weight: Semantic/structural blend for arc (0-1).
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.recover import recover as _recover

        graph_obj = _resolve(dep_graph, "DependencyGraph")
        arch = _recover(
            dep_graph=graph_obj,
            algorithm=algorithm,
            num_clusters=num_clusters,
            similarity_measure=similarity_measure,
            pkg_depth=pkg_depth,
            hybrid_weight=hybrid_weight,
        )
        summary = _make_summary(arch, "Architecture")
        return json.dumps(_apply_budget(summary, max_tokens), indent=2)

    # -- detect_smells ---------------------------------------------------------

    @server.tool()
    def detect_smells(
        architecture: str,
        dep_graph: str,
        use_llm: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        """Detect architectural anti-patterns.

        Finds dependency cycles, concern overload, scattered functionality,
        and link overload.

        Args:
            architecture: Session ID from a previous 'recover' call.
            dep_graph: Session ID from a previous 'parse' call.
            use_llm: Use LLM for deeper semantic analysis (default False).
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.detect_smells import detect_smells as _detect

        arch_obj = _resolve(architecture, "Architecture")
        graph_obj = _resolve(dep_graph, "DependencyGraph")
        smells = _detect(
            architecture=arch_obj,
            dep_graph=graph_obj,
            use_llm=use_llm,
        )
        result = serialize_result(smells)
        sid = _store(smells, "SmellList")
        output = {"session_id": sid, "num_smells": len(smells), "smells": result}
        return json.dumps(_apply_budget(output, max_tokens), indent=2)

    # -- compute_metrics -------------------------------------------------------

    @server.tool()
    def compute_metrics(
        architecture: str,
        dep_graph: str,
        max_tokens: int | None = None,
    ) -> str:
        """Calculate architecture quality metrics.

        Computes RCI, TurboMQ, BasicMQ, connectivity, and coupling ratios.

        Args:
            architecture: Session ID from a previous 'recover' call.
            dep_graph: Session ID from a previous 'parse' call.
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.compute_metrics import compute_metrics as _metrics

        arch_obj = _resolve(architecture, "Architecture")
        graph_obj = _resolve(dep_graph, "DependencyGraph")
        metrics = _metrics(architecture=arch_obj, dep_graph=graph_obj)
        result = serialize_result(metrics)
        sid = _store(metrics, "MetricList")
        output = {"session_id": sid, "num_metrics": len(metrics), "metrics": result}
        return json.dumps(_apply_budget(output, max_tokens), indent=2)

    # -- compare ---------------------------------------------------------------

    @server.tool()
    def compare(
        arch_a: str,
        arch_b: str,
        max_tokens: int | None = None,
    ) -> str:
        """Compare two architectures (A2A analysis).

        Matches components using the Hungarian algorithm and tracks additions,
        removals, splits, and merges.

        Args:
            arch_a: Session ID for the first architecture.
            arch_b: Session ID for the second architecture.
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.compare import compare as _compare

        a = _resolve(arch_a, "Architecture")
        b = _resolve(arch_b, "Architecture")
        result = _compare(arch_a=a, arch_b=b)
        serialized = serialize_result(result)
        return json.dumps(_apply_budget(serialized, max_tokens), indent=2)

    # -- query -----------------------------------------------------------------

    @server.tool()
    def query(
        architecture: str,
        dep_graph: str,
        question: str,
        entity: str | None = None,
        component: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Query and explore a recovered architecture.

        Supported questions: component_of, dependencies, dependents, entities,
        most_coupled, summary, largest.

        Args:
            architecture: Session ID from a previous 'recover' call.
            dep_graph: Session ID from a previous 'parse' call.
            question: Query type (component_of, dependencies, dependents, entities,
                most_coupled, summary, largest).
            entity: Entity FQN for entity-specific queries.
            component: Component name for component-specific queries.
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.query import query as _query

        arch_obj = _resolve(architecture, "Architecture")
        graph_obj = _resolve(dep_graph, "DependencyGraph")
        result = _query(
            architecture=arch_obj,
            dep_graph=graph_obj,
            question=question,
            entity=entity,
            component=component,
        )
        serialized = serialize_result(result)
        return json.dumps(_apply_budget(serialized, max_tokens), indent=2)

    # -- visualize -------------------------------------------------------------

    @server.tool()
    def visualize(
        repo_name: str,
        version: str,
        dep_graph: str,
        architecture: str,
        smells: str | None = None,
        metrics: str | None = None,
        output: str = "report.html",
        format: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Generate architecture reports and diagrams.

        Supports HTML, DOT, JSON, RSF, and Mermaid output formats.

        Args:
            repo_name: Name of the repository.
            version: Version/commit identifier.
            dep_graph: Session ID from a previous 'parse' call.
            architecture: Session ID from a previous 'recover' call.
            smells: Session ID from a previous 'detect_smells' call (optional).
            metrics: Session ID from a previous 'compute_metrics' call (optional).
            output: Output file path (default: report.html).
            format: Output format (html, dot, json, rsf, mermaid). Auto-detected from extension.
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.visualize import visualize as _visualize

        graph_obj = _resolve(dep_graph, "DependencyGraph")
        arch_obj = _resolve(architecture, "Architecture")
        smells_obj = _resolve(smells, "SmellList") if smells else None
        metrics_obj = _resolve(metrics, "MetricList") if metrics else None

        result_path = _visualize(
            repo_name=repo_name,
            version=version,
            dep_graph=graph_obj,
            architecture=arch_obj,
            smells=smells_obj,
            metrics=metrics_obj,
            output=output,
            format=format,
        )
        return json.dumps({"output_path": result_path}, indent=2)

    # -- summarize -------------------------------------------------------------

    @server.tool()
    def summarize(
        source_path: str,
        language: str | None = None,
        focus: str | None = None,
        use_cache: bool = True,
        exclude_tests: bool = True,
        exclude_dirs: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Summarize a codebase for quick understanding.

        Returns package structure, dependency hotspots, and entry points.
        Use the focus parameter to drill into a specific package.

        Args:
            source_path: Root directory of the project.
            language: Language to parse (auto-detected if None).
            focus: Package name to drill into (e.g. "com.example.auth").
            use_cache: Use cached parse results when available.
            exclude_tests: Exclude test/vendor/build directories (default True).
            exclude_dirs: Additional exact project-relative directories to exclude.
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.summarize import summarize as _summarize

        result = _summarize(
            source_path=source_path,
            language=language,
            focus=focus,
            use_cache=use_cache,
            exclude_tests=exclude_tests,
            exclude_dirs=exclude_dirs,
        )
        serialized = serialize_result(result)
        return json.dumps(_apply_budget(serialized, max_tokens), indent=2)

    # -- explain_component -----------------------------------------------------

    @server.tool()
    def explain_component(
        architecture: str,
        dep_graph: str,
        component: str,
        max_tokens: int | None = None,
    ) -> str:
        """Explain a component from a recovered architecture.

        Shows responsibility, entities, public API surface, dependencies,
        and cohesion metrics.

        Args:
            architecture: Session ID from a previous 'recover' call.
            dep_graph: Session ID from a previous 'parse' call.
            component: Name of the component to explain.
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.explain_component import explain_component as _explain

        arch_obj = _resolve(architecture, "Architecture")
        graph_obj = _resolve(dep_graph, "DependencyGraph")
        result = _explain(
            architecture=arch_obj,
            dep_graph=graph_obj,
            component=component,
        )
        serialized = serialize_result(result)
        return json.dumps(_apply_budget(serialized, max_tokens), indent=2)

    # -- find_relevant ---------------------------------------------------------

    @server.tool()
    def find_relevant(
        dep_graph: str,
        query: str,
        architecture: str | None = None,
        top_k: int = 10,
        max_tokens: int | None = None,
    ) -> str:
        """Find code entities relevant to a natural-language query.

        Searches entity names, packages, and file paths using keyword matching.
        Optionally uses recovered architecture for component context.

        Args:
            dep_graph: Session ID from a previous 'parse' call.
            query: Natural-language query (e.g. "authentication login").
            architecture: Optional session ID from a previous 'recover' call.
            top_k: Maximum number of results to return.
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.find_relevant import find_relevant as _find

        graph_obj = _resolve(dep_graph, "DependencyGraph")
        arch_obj = _resolve(architecture, "Architecture") if architecture else None
        result = _find(
            dep_graph=graph_obj,
            query=query,
            architecture=arch_obj,
            top_k=top_k,
        )
        serialized = serialize_result(result)
        return json.dumps(_apply_budget(serialized, max_tokens), indent=2)

    # -- api_surface -----------------------------------------------------------

    @server.tool()
    def api_surface(
        dep_graph: str,
        scope: str | None = None,
        include_members: bool = True,
        max_tokens: int | None = None,
    ) -> str:
        """Extract the public API surface of a codebase.

        Returns public top-level types and their public members grouped by
        package (the 'what can I call' view). Implementation bodies, private
        (underscore-prefixed) entities, and parameter/return types are omitted.

        Args:
            dep_graph: Session ID from a previous 'parse' call.
            scope: Optional package prefix; only entities whose package starts
                with it are included.
            include_members: Nest public members under their owner type (default True).
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.api_surface import api_surface as _api_surface

        graph_obj = _resolve(dep_graph, "DependencyGraph")
        result = _api_surface(
            dep_graph=graph_obj,
            scope=scope,
            include_members=include_members,
        )
        serialized = serialize_result(result)
        return json.dumps(_apply_budget(serialized, max_tokens), indent=2)

    # -- diff_impact -----------------------------------------------------------

    @server.tool()
    def diff_impact(
        dep_graph: str,
        changed_files: list[str],
        architecture: str | None = None,
        max_depth: int = 3,
        max_tokens: int | None = None,
    ) -> str:
        """Assess the architectural blast radius of a set of changed files.

        Maps changed file paths to affected entities and components, the
        downstream reverse-dependency closure (who depends on them), and the
        subset of changed entities that form a public contract for external
        callers — a diff impact analysis without reading the diff.

        Args:
            dep_graph: Session ID from a previous 'parse' call.
            changed_files: Changed file paths (e.g. from 'git diff --name-only').
            architecture: Optional session ID from a previous 'recover' call.
            max_depth: Max reverse-dependency hops to walk (1 = direct callers).
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.diff_impact import diff_impact as _diff_impact

        graph_obj = _resolve(dep_graph, "DependencyGraph")
        arch_obj = _resolve(architecture, "Architecture") if architecture else None
        result = _diff_impact(
            dep_graph=graph_obj,
            changed_files=changed_files,
            architecture=arch_obj,
            max_depth=max_depth,
        )
        serialized = serialize_result(result)
        return json.dumps(_apply_budget(serialized, max_tokens), indent=2)

    # -- context_for_task ------------------------------------------------------

    @server.tool()
    def context_for_task(
        dep_graph: str,
        task: str,
        architecture: str | None = None,
        max_files: int = 15,
        max_tokens: int | None = None,
    ) -> str:
        """Assemble the minimal ranked set of files to read for a task.

        Given a natural-language task, returns the smallest useful set of files
        an agent should read, each with a role ("direct match", "dependency of
        match", "dependent of match", "component sibling") and a concise reason.
        Seeds on keyword matches, then expands to the direct dependencies and
        dependents of the top seeds so the agent gets the neighbourhood it must
        understand instead of reading everything.

        Args:
            dep_graph: Session ID from a previous 'parse' call.
            task: Natural-language task (e.g. "add rate limiting to login").
            architecture: Optional session ID from a previous 'recover' call.
            max_files: Maximum number of files to return.
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.context_for_task import (
            context_for_task as _context_for_task,
        )

        graph_obj = _resolve(dep_graph, "DependencyGraph")
        arch_obj = _resolve(architecture, "Architecture") if architecture else None
        result = _context_for_task(
            dep_graph=graph_obj,
            task=task,
            architecture=arch_obj,
            max_files=max_files,
        )
        serialized = serialize_result(result)
        return json.dumps(_apply_budget(serialized, max_tokens), indent=2)

    # -- dependency_cone -------------------------------------------------------

    @server.tool()
    def dependency_cone(
        dep_graph: str,
        target: str,
        direction: str = "both",
        max_depth: int = 3,
        max_nodes: int | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Return the upstream/downstream dependency cone of an entity or file.

        Args:
            dep_graph: Session ID from a previous 'parse' call.
            target: An entity FQN or a file path to seed the cone from.
            direction: "upstream", "downstream", or "both".
            max_depth: Maximum hops to walk (1 = direct neighbors).
            max_nodes: Optional per-direction cap on returned nodes.
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.dependency_cone import (
            dependency_cone as _dependency_cone,
        )

        graph_obj = _resolve(dep_graph, "DependencyGraph")
        result = _dependency_cone(
            dep_graph=graph_obj,
            target=target,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        serialized = serialize_result(result)
        return json.dumps(_apply_budget(serialized, max_tokens), indent=2)

    # -- changelog_architecture ------------------------------------------------

    @server.tool()
    def changelog_architecture(
        arch_a: str,
        graph_a: str,
        arch_b: str,
        graph_b: str,
        ref_a: str | None = None,
        ref_b: str | None = None,
        min_entities: int = 8,
        min_share: float = 0.20,
        max_tokens: int | None = None,
    ) -> str:
        """Architectural changelog between two analysed versions of a codebase.

        Args:
            arch_a: Session ID from a 'recover' call on the earlier version.
            graph_a: Session ID from a 'parse' call on the earlier version.
            arch_b: Session ID from a 'recover' call on the later version.
            graph_b: Session ID from a 'parse' call on the later version.
            ref_a: Optional label for the earlier version (e.g. a tag).
            ref_b: Optional label for the later version.
            min_entities: Absolute significance threshold for a component flow.
                ``min_share`` already catches proportionally-large moves in
                small components, so this exists only to catch large absolute
                moves in large ones; see
                ``algorithms.provenance.classify_structural_changes`` for the
                measured datapoint behind the default.
            min_share: Fractional significance threshold in (0, 1].
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.changelog_architecture import (
            changelog_architecture as _changelog,
        )

        result = _changelog(
            _resolve(arch_a, "Architecture"),
            _resolve(graph_a, "DependencyGraph"),
            _resolve(arch_b, "Architecture"),
            _resolve(graph_b, "DependencyGraph"),
            ref_a=ref_a,
            ref_b=ref_b,
            min_entities=min_entities,
            min_share=min_share,
        )
        serialized = serialize_result(result)
        return json.dumps(_apply_budget(serialized, max_tokens), indent=2)

    # -- architecture guardrail --------------------------------------------------
    # Spec problems come back as {"error": ...} rather than exceptions, so an
    # agent can read the message and act on it (e.g. call init_spec).

    def _guarded(fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            result: dict[str, Any] = fn(*args, **kwargs)
        except (FileNotFoundError, FileExistsError, ValueError) as exc:
            return {"error": str(exc)}
        return result

    @server.tool()
    def init_spec(
        source_path: str,
        template: str = "layered",
        overwrite: bool = False,
    ) -> str:
        """Scaffold an architecture.spec.json for the guardrail tools.

        Args:
            source_path: Project root to write the spec into.
            template: One of "layered", "hexagonal", "clean", "mvc".
            overwrite: Replace an existing spec.
        """
        from arcade_agent.tools.guard import init_spec as _init_spec

        return json.dumps(_guarded(_init_spec, source_path, template, overwrite), indent=2)

    @server.tool()
    def propose_placement(
        intent: str,
        source_path: str = ".",
        spec_path: str | None = None,
    ) -> str:
        """Call BEFORE writing new code: where does it belong, and what may it use?

        Returns the component and layer the described code belongs in, its path
        convention, and the components it may and must not depend on, according
        to the project's architecture.spec.json.

        Args:
            intent: What you are about to add, in plain words.
            source_path: Project root (the spec is looked up there).
            spec_path: Explicit spec location.
        """
        from arcade_agent.tools.guard import propose_placement as _propose

        return json.dumps(_guarded(_propose, intent, source_path, spec_path), indent=2)

    @server.tool()
    def preview_impact(
        from_component: str,
        to_component: str,
        source_path: str = ".",
        spec_path: str | None = None,
    ) -> str:
        """Call BEFORE adding a cross-component import: would it be allowed?

        Uses the same rules as check_architecture, so a dependency this tool
        allows will not later fail the check.

        Args:
            from_component: Component that would gain the dependency.
            to_component: Component it would depend on.
            source_path: Project root (the spec is looked up there).
            spec_path: Explicit spec location.
        """
        from arcade_agent.tools.guard import preview_impact as _preview

        return json.dumps(
            _guarded(_preview, from_component, to_component, source_path, spec_path),
            indent=2,
        )

    @server.tool()
    def check_architecture(
        source_path: str,
        spec_path: str | None = None,
        dep_graph: str | None = None,
        language: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Call AFTER a change: does the code still conform to its architecture?

        Returns PASS/WARN/FAIL with each violation, the rule it breaks, and a
        concrete fix. Deterministic: the same code always gets the same verdict.
        Fix every error before committing.

        Args:
            source_path: Project root.
            spec_path: Spec location (default <source_path>/architecture.spec.json).
            dep_graph: Optional session ID from 'parse' to skip re-parsing.
            language: Language to parse when dep_graph is omitted (auto if None).
            max_tokens: Optional token budget for the response.
        """
        from arcade_agent.tools.guard import check_architecture as _check

        graph_obj = _resolve(dep_graph, "DependencyGraph") if dep_graph else None
        result = _guarded(
            _check, source_path, spec_path, dep_graph=graph_obj, language=language
        )
        return json.dumps(_apply_budget(result, max_tokens), indent=2)

    @server.tool()
    def remediate(
        source_path: str,
        spec_path: str | None = None,
        language: str | None = None,
    ) -> str:
        """Return the ranked fixes (errors first) that restore conformance.

        Args:
            source_path: Project root.
            spec_path: Spec location.
            language: Language to parse (auto if None).
        """
        from arcade_agent.tools.guard import remediate as _remediate

        return json.dumps(
            _guarded(_remediate, source_path, spec_path, language=language), indent=2
        )

    # -- get_full_result -------------------------------------------------------

    @server.tool()
    def get_full_result(
        session_id: str,
        max_tokens: int | None = None,
    ) -> str:
        """Retrieve the full result of a previous tool call by session ID.

        Use this when you need detailed data beyond the summary returned by
        other tools. For example, to get the full entity list from a parse result.

        Args:
            session_id: Session ID from a previous tool call.
            max_tokens: Optional token budget for the response.
        """
        if session_id not in _session:
            available = list(_session.keys())
            return json.dumps({
                "error": f"Session ID '{session_id}' not found",
                "available_sessions": available,
            })
        entry = _session[session_id]
        serialized = serialize_result(entry["value"])
        result = {"session_id": session_id, "type": entry["label"], "data": serialized}
        return json.dumps(_apply_budget(result, max_tokens), indent=2)

    # -- list_sessions ---------------------------------------------------------

    @server.tool()
    def list_sessions() -> str:
        """List all objects stored in the current session.

        Returns session IDs and their types so you can reference them in
        subsequent tool calls.
        """
        entries = [
            {"session_id": sid, "type": entry["label"]}
            for sid, entry in _session.items()
        ]
        return json.dumps({"sessions": entries}, indent=2)

    return server


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_server = None


def get_server():  # type: ignore[no-untyped-def]
    """Get or create the MCP server singleton."""
    global _server
    if _server is None:
        _server = _build_server()
    return _server


def main() -> None:
    """Run the MCP server on stdio transport."""
    server = get_server()
    server.run()


if __name__ == "__main__":
    main()
