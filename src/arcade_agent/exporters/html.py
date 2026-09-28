"""HTML report generation using Jinja2 and Mermaid.js."""

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from jinja2 import Template

from arcade_agent.algorithms.architecture import Architecture
from arcade_agent.algorithms.coupling import graph_quality_context
from arcade_agent.algorithms.metrics import MetricResult
from arcade_agent.algorithms.smells import SmellInstance
from arcade_agent.exporters.mermaid import (
    build_mermaid_diagram,
    mermaid_label_text,
    mermaid_node_id,
)
from arcade_agent.parsers.graph import DependencyGraph

REPORT_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>arcade-agent: {{ repo_name }} ({{ version }})</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6; color: #1a1a2e; background: #f8f9fa;
            max-width: 1200px; margin: 0 auto; padding: 2rem; padding-top: 4rem;
        }
        nav {
            position: fixed; top: 0; left: 0; right: 0; z-index: 100;
            background: #1e293b; padding: 0.6rem 2rem;
            display: flex; align-items: center; gap: 1.5rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15);
        }
        nav .nav-title {
            font-weight: 700; color: #f8fafc; font-size: 0.85rem;
            white-space: nowrap; margin-right: 0.5rem;
        }
        nav a {
            color: #94a3b8; text-decoration: none; font-size: 0.8rem;
            padding: 0.3rem 0.7rem; border-radius: 4px; transition: all 0.15s;
            white-space: nowrap;
        }
        nav a:hover { color: #f8fafc; background: #334155; }
        h1 { font-size: 1.8rem; margin-bottom: 0.25rem; }
        h2 { font-size: 1.3rem; margin: 2rem 0 1rem; border-bottom: 2px solid #e0e0e0; padding-bottom: 0.5rem; scroll-margin-top: 3.5rem; }
        h3 { font-size: 1.1rem; margin: 1rem 0 0.5rem; }
        .subtitle { color: #666; margin-bottom: 2rem; }
        .stats {
            display: flex; gap: 1.5rem; margin-bottom: 2rem; flex-wrap: wrap;
        }
        .stat-card {
            background: white; border-radius: 8px; padding: 1rem 1.5rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); min-width: 140px;
        }
        .stat-card .number { font-size: 1.8rem; font-weight: 700; color: #2563eb; }
        .stat-card .label { font-size: 0.85rem; color: #666; }
        .card {
            background: white; border-radius: 8px; padding: 1.5rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 1rem;
        }
        .mermaid { text-align: center; margin: 1rem 0; }
        table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
        th, td { text-align: left; padding: 0.75rem; border-bottom: 1px solid #e0e0e0; }
        th { background: #f1f5f9; font-weight: 600; font-size: 0.85rem; text-transform: uppercase; }
        td { font-size: 0.9rem; }
        .entity-list { font-family: monospace; font-size: 0.8rem; color: #555; max-width: 500px; }
        .smell {
            border-left: 4px solid #ccc; padding: 1rem 1.5rem; margin-bottom: 1rem;
            background: white; border-radius: 0 8px 8px 0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .smell.high { border-left-color: #ef4444; }
        .smell.medium { border-left-color: #f59e0b; }
        .smell.low { border-left-color: #3b82f6; }
        .smell-header { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; }
        .badge {
            font-size: 0.7rem; font-weight: 600; padding: 0.2rem 0.6rem;
            border-radius: 99px; text-transform: uppercase;
        }
        .badge.high { background: #fef2f2; color: #dc2626; }
        .badge.medium { background: #fffbeb; color: #d97706; }
        .badge.low { background: #eff6ff; color: #2563eb; }
        .smell-detail { margin: 0.5rem 0; font-size: 0.9rem; }
        .smell-detail strong { display: inline-block; width: 100px; color: #555; }
        .rationale { background: #f8fafc; padding: 1rem; border-radius: 6px; font-style: italic; color: #555; }
        .metric-card {
            display: inline-block; background: white; border-radius: 8px; padding: 1rem 1.5rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin: 0.5rem; min-width: 160px;
        }
        .metric-card .value { font-size: 1.4rem; font-weight: 700; color: #059669; }
        .metric-card .name { font-size: 0.8rem; color: #666; }
        .quality-warning {
            border-left: 4px solid #d97706; padding: 1rem 1.25rem; margin: 1rem 0 2rem;
            background: #fffbeb; border-radius: 0 8px 8px 0; color: #78350f;
        }
        .quality-warning code { color: #92400e; }
        .concern-tag {
            display: inline-block; background: #e0e7ff; color: #3730a3;
            font-size: 0.75rem; padding: 0.2rem 0.6rem; border-radius: 99px;
            margin: 0.15rem; font-weight: 500;
        }
        footer { margin-top: 3rem; text-align: center; color: #999; font-size: 0.8rem; }
    </style>
</head>
<body>
    <nav>
        <span class="nav-title">arcade-agent</span>
        <a href="#overview">Overview</a>
        {% if metric_groups %}<a href="#metrics">Metrics</a>{% endif %}
        <a href="#diagram">Diagram</a>
        <a href="#components">Components</a>
        <a href="#smells">Smells ({{ num_smells }})</a>
        <a href="#dependencies">Dependencies</a>
    </nav>

    <h1 id="overview">Architecture Report</h1>
    <p class="subtitle">{{ repo_name }} &mdash; version {{ version }} &mdash; recovered with {{ algorithm }}</p>

    <div class="stats">
        <div class="stat-card">
            <div class="number">{{ num_entities }}</div>
            <div class="label">Entities</div>
        </div>
        <div class="stat-card">
            <div class="number">{{ num_edges }}</div>
            <div class="label">Dependencies</div>
        </div>
        <div class="stat-card">
            <div class="number">{{ num_components }}</div>
            <div class="label">Components</div>
        </div>
        <div class="stat-card">
            <div class="number">{{ num_smells }}</div>
            <div class="label">Smells</div>
        </div>
    </div>

    {% if graph_quality and graph_quality.status == "qualified" %}
    <div class="quality-warning">
        <strong>Qualified dependency-graph metrics.</strong>
        Values below were computed with incomplete discovered local-import coverage;
        interpret them as directional signals, not a complete architecture assessment.
        {% for language, summary in graph_quality.dependency_resolution.items() %}
        <div><code>{{ language }}</code>:
            {{ summary.resolved_local | default(0) }} resolved,
            {{ summary.unresolved_local | default(0) }} unresolved,
            {{ summary.linked_local | default(0) }} linked,
            {{ summary.unlinked_local | default(0) }} unlinked.
        </div>
        {% endfor %}
    </div>
    {% endif %}

    {% if metric_groups %}
    <h2 id="metrics">Quality Metrics</h2>
    {% for group in metric_groups %}
    <h3>{{ group.title }}</h3>
    <div>
    {% for metric in group.metrics %}
        <div class="metric-card">
            <div class="value">{{ "%.3f"|format(metric.value) }}</div>
            <div class="name">{{ metric.name }}</div>
        </div>
    {% endfor %}
    </div>
    {% endfor %}
    {% endif %}

    <h2 id="diagram">Architecture Diagram</h2>
    <div class="card">
        <pre class="mermaid">
{{ mermaid_diagram }}
        </pre>
    </div>

    {% if rationale %}
    <div class="rationale">{{ rationale }}</div>
    {% endif %}

    <h2 id="components">Components</h2>
    <table>
        <thead>
            <tr>
                <th>Component</th>
                <th>Responsibility</th>
                <th>#</th>
                {% if concerns %}<th>Concerns</th>{% endif %}
                <th>Entities</th>
            </tr>
        </thead>
        <tbody>
        {% for comp in components %}
            <tr>
                <td><strong>{{ comp.name }}</strong></td>
                <td>{{ comp.responsibility }}</td>
                <td>{{ comp.entities | length }}</td>
                {% if concerns %}
                <td>{% for c in concerns.get(comp.name, []) %}<span class="concern-tag">{{ c }}</span>{% endfor %}</td>
                {% endif %}
                <td class="entity-list">{{ comp.entities[:8] | join(', ') }}{% if comp.entities | length > 8 %}, ... (+{{ comp.entities | length - 8 }} more){% endif %}</td>
            </tr>
        {% endfor %}
        </tbody>
    </table>

    <h2 id="smells">Architectural Smells ({{ num_smells }})</h2>
    {% if smells %}
    {% for smell in smells %}
    <div class="smell {{ smell.severity }}">
        <div class="smell-header">
            <span class="badge {{ smell.severity }}">{{ smell.severity }}</span>
            <strong>{{ smell.smell_type }}</strong>
        </div>
        <p class="smell-detail">{{ smell.description }}</p>
        <p class="smell-detail"><strong>Why:</strong> {{ smell.explanation }}</p>
        <p class="smell-detail"><strong>Fix:</strong> {{ smell.suggestion }}</p>
        <p class="smell-detail"><strong>Affects:</strong> {{ smell.affected_components | join(', ') }}</p>
    </div>
    {% endfor %}
    {% else %}
    <div class="card">
        <p>No architectural smells detected. The architecture appears well-structured.</p>
    </div>
    {% endif %}

    <h2 id="dependencies">Dependency Summary</h2>
    <div class="card">
        <table>
            <thead><tr><th>Package</th><th>Entities</th></tr></thead>
            <tbody>
            {% for pkg, entities in packages %}
                <tr>
                    <td><code>{{ pkg or '(default)' }}</code></td>
                    <td>{{ entities | length }}</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <footer>
        Generated by arcade-agent &mdash; software architecture analysis toolkit
    </footer>

    <script>mermaid.initialize({ startOnLoad: true, theme: 'neutral', securityLevel: 'loose' });</script>
</body>
</html>
""")


def export_html(
    repo_name: str,
    version: str,
    dep_graph: DependencyGraph,
    architecture: Architecture,
    smells: list[SmellInstance],
    metrics: list[MetricResult],
    output_path: Path,
    concerns: dict[str, list[str]] | None = None,
) -> Path:
    """Generate an HTML architecture report.

    Args:
        repo_name: Repository name.
        version: Version string.
        dep_graph: The dependency graph.
        architecture: The recovered architecture.
        smells: Detected architectural smells.
        metrics: Computed quality metrics.
        output_path: Where to write the HTML file.
        concerns: Optional dict mapping component name to concern labels.

    Returns:
        Path to the generated HTML file.
    """
    mermaid = build_mermaid_diagram(architecture, dep_graph)
    packages = sorted(dep_graph.packages.items(), key=lambda x: -len(x[1]))
    metric_groups: list[dict[str, object]] = []
    grouped_metrics: dict[str, list[MetricResult]] = {}
    for metric in metrics:
        details = metric.details or {}
        group_name = str(details.get("group", "Core ARCADE Metrics"))
        grouped_metrics.setdefault(group_name, []).append(metric)
    for title, grouped in grouped_metrics.items():
        metric_groups.append({"title": title, "metrics": grouped})
    graph_quality = graph_quality_context(dep_graph)

    html = REPORT_TEMPLATE.render(
        repo_name=repo_name,
        version=version,
        algorithm=architecture.algorithm or "unknown",
        num_entities=dep_graph.num_entities,
        num_edges=dep_graph.num_edges,
        num_components=len(architecture.components),
        num_smells=len(smells),
        mermaid_diagram=mermaid,
        rationale=architecture.rationale,
        components=architecture.components,
        smells=smells,
        metric_groups=metric_groups,
        graph_quality=graph_quality,
        packages=packages,
        concerns=concerns or {},
    )

    output_path.write_text(html)
    return output_path


# ---------------------------------------------------------------------------
# Multi-algorithm comparison report
# ---------------------------------------------------------------------------

@dataclass
class AlgorithmResult:
    """Results from one recovery algorithm for comparison reports."""

    algorithm: str
    architecture: Architecture
    smells: list[SmellInstance]
    metrics: list[MetricResult]
    concerns: dict[str, list[str]]


class _EnrichedAlgorithmResult(TypedDict):
    algorithm: str
    architecture: Architecture
    smells: list[SmellInstance]
    metrics: list[MetricResult]
    metrics_dict: dict[str, float]
    concerns: dict[str, list[str]]
    mermaid: str


COMPARISON_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>arcade-agent: {{ repo_name }} — Algorithm Comparison</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6; color: #1a1a2e; background: #f8f9fa;
            max-width: 1400px; margin: 0 auto; padding: 2rem; padding-top: 4rem;
        }
        nav {
            position: fixed; top: 0; left: 0; right: 0; z-index: 100;
            background: #1e293b; padding: 0.6rem 2rem;
            display: flex; align-items: center; gap: 1.5rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15); flex-wrap: wrap;
        }
        nav .nav-title {
            font-weight: 700; color: #f8fafc; font-size: 0.85rem;
            white-space: nowrap; margin-right: 0.5rem;
        }
        nav a {
            color: #94a3b8; text-decoration: none; font-size: 0.8rem;
            padding: 0.3rem 0.7rem; border-radius: 4px; transition: all 0.15s;
            white-space: nowrap;
        }
        nav a:hover { color: #f8fafc; background: #334155; }
        nav .nav-sep { color: #475569; font-size: 0.7rem; }
        h1 { font-size: 1.8rem; margin-bottom: 0.25rem; }
        h2 { font-size: 1.3rem; margin: 2rem 0 1rem; border-bottom: 2px solid #e0e0e0; padding-bottom: 0.5rem; scroll-margin-top: 3.5rem; }
        h3 { font-size: 1.1rem; margin: 1rem 0 0.5rem; scroll-margin-top: 3.5rem; }
        .subtitle { color: #666; margin-bottom: 2rem; }
        .stats {
            display: flex; gap: 1.5rem; margin-bottom: 2rem; flex-wrap: wrap;
        }
        .stat-card {
            background: white; border-radius: 8px; padding: 1rem 1.5rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); min-width: 140px;
        }
        .stat-card .number { font-size: 1.8rem; font-weight: 700; color: #2563eb; }
        .stat-card .label { font-size: 0.85rem; color: #666; }
        .card {
            background: white; border-radius: 8px; padding: 1.5rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 1rem;
        }
        .mermaid { text-align: center; margin: 1rem 0; }
        table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
        th, td { text-align: left; padding: 0.75rem; border-bottom: 1px solid #e0e0e0; }
        th { background: #f1f5f9; font-weight: 600; font-size: 0.85rem; text-transform: uppercase; }
        td { font-size: 0.9rem; }
        .entity-list { font-family: monospace; font-size: 0.8rem; color: #555; max-width: 400px; }
        .smell {
            border-left: 4px solid #ccc; padding: 1rem 1.5rem; margin-bottom: 1rem;
            background: white; border-radius: 0 8px 8px 0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .smell.high { border-left-color: #ef4444; }
        .smell.medium { border-left-color: #f59e0b; }
        .smell.low { border-left-color: #3b82f6; }
        .smell-header { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; }
        .badge {
            font-size: 0.7rem; font-weight: 600; padding: 0.2rem 0.6rem;
            border-radius: 99px; text-transform: uppercase;
        }
        .badge.high { background: #fef2f2; color: #dc2626; }
        .badge.medium { background: #fffbeb; color: #d97706; }
        .badge.low { background: #eff6ff; color: #2563eb; }
        .smell-detail { margin: 0.5rem 0; font-size: 0.9rem; }
        .smell-detail strong { display: inline-block; width: 100px; color: #555; }
        .rationale { background: #f8fafc; padding: 1rem; border-radius: 6px; font-style: italic; color: #555; margin-bottom: 1rem; }
        .metric-card {
            display: inline-block; background: white; border-radius: 8px; padding: 1rem 1.5rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin: 0.5rem; min-width: 160px;
        }
        .metric-card .value { font-size: 1.4rem; font-weight: 700; color: #059669; }
        .metric-card .name { font-size: 0.8rem; color: #666; }
        .quality-warning {
            border-left: 4px solid #d97706; padding: 1rem 1.25rem; margin: 1rem 0 2rem;
            background: #fffbeb; border-radius: 0 8px 8px 0; color: #78350f;
        }
        .quality-warning code { color: #92400e; }
        .concern-tag {
            display: inline-block; background: #e0e7ff; color: #3730a3;
            font-size: 0.75rem; padding: 0.2rem 0.6rem; border-radius: 99px;
            margin: 0.15rem; font-weight: 500;
        }
        .algo-section {
            border: 1px solid #e2e8f0; border-radius: 12px; padding: 1.5rem;
            margin-bottom: 2rem; background: white;
        }
        .algo-header {
            font-size: 1.2rem; font-weight: 700; margin-bottom: 1rem;
            padding-bottom: 0.5rem; border-bottom: 2px solid #e0e0e0;
        }
        .metrics-compare {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem; margin: 1rem 0;
        }
        .metrics-compare .metric-row {
            background: #f8fafc; border-radius: 8px; padding: 0.75rem 1rem;
        }
        .metrics-compare .metric-name { font-size: 0.8rem; color: #666; }
        .metrics-compare .metric-values { display: flex; gap: 1rem; margin-top: 0.25rem; }
        .metrics-compare .mv { font-size: 0.85rem; font-weight: 600; }
        footer { margin-top: 3rem; text-align: center; color: #999; font-size: 0.8rem; }
    </style>
</head>
<body>
    <nav>
        <span class="nav-title">arcade-agent</span>
        <a href="#overview">Overview</a>
        <a href="#metrics-compare">Metrics</a>
        <span class="nav-sep">|</span>
        {% for r in results %}
        <a href="#algo-{{ r.algorithm }}">{{ r.algorithm | upper }}</a>
        {% endfor %}
        <span class="nav-sep">|</span>
        <a href="#dependencies">Dependencies</a>
    </nav>

    <h1 id="overview">Algorithm Comparison</h1>
    <p class="subtitle">{{ repo_name }} &mdash; version {{ version }} &mdash; {{ results | length }} recovery algorithms compared</p>

    <div class="stats">
        <div class="stat-card">
            <div class="number">{{ num_entities }}</div>
            <div class="label">Entities</div>
        </div>
        <div class="stat-card">
            <div class="number">{{ num_edges }}</div>
            <div class="label">Dependencies</div>
        </div>
        {% for r in results %}
        <div class="stat-card">
            <div class="number">{{ r.architecture.components | length }}</div>
            <div class="label">{{ r.algorithm | upper }} Components</div>
        </div>
        {% endfor %}
    </div>

    {% if graph_quality and graph_quality.status == "qualified" %}
    <div class="quality-warning">
        <strong>Qualified dependency-graph metrics.</strong>
        Values below were computed with incomplete discovered local-import coverage;
        interpret them as directional signals, not a complete architecture assessment.
        {% for language, summary in graph_quality.dependency_resolution.items() %}
        <div><code>{{ language }}</code>:
            {{ summary.resolved_local | default(0) }} resolved,
            {{ summary.unresolved_local | default(0) }} unresolved,
            {{ summary.linked_local | default(0) }} linked,
            {{ summary.unlinked_local | default(0) }} unlinked.
        </div>
        {% endfor %}
    </div>
    {% endif %}

    <h2 id="metrics-compare">Metrics Comparison</h2>
    <table>
        <thead>
            <tr>
                <th>Metric</th>
                {% for r in results %}
                <th>{{ r.algorithm | upper }}</th>
                {% endfor %}
            </tr>
        </thead>
        <tbody>
        {% for metric_name in metric_names %}
            <tr>
                <td><strong>{{ metric_name }}</strong></td>
                {% for r in results %}
                <td>{{ "%.4f"|format(r.metrics_dict.get(metric_name, 0)) }}</td>
                {% endfor %}
            </tr>
        {% endfor %}
        </tbody>
    </table>

    {% for r in results %}
    <h2 id="algo-{{ r.algorithm }}">{{ r.algorithm | upper }} — {{ r.architecture.components | length }} components</h2>
    <div class="algo-section">
        <div class="card">
            <pre class="mermaid">
{{ r.mermaid }}
            </pre>
        </div>

        {% if r.architecture.rationale %}
        <div class="rationale">{{ r.architecture.rationale }}</div>
        {% endif %}

        <h3>Components</h3>
        <table>
            <thead>
                <tr>
                    <th>Component</th>
                    <th>Responsibility</th>
                    <th>#</th>
                    {% if r.concerns %}<th>Concerns</th>{% endif %}
                    <th>Entities</th>
                </tr>
            </thead>
            <tbody>
            {% for comp in r.architecture.components %}
                <tr>
                    <td><strong>{{ comp.name }}</strong></td>
                    <td>{{ comp.responsibility }}</td>
                    <td>{{ comp.entities | length }}</td>
                    {% if r.concerns %}
                    <td>{% for c in r.concerns.get(comp.name, []) %}<span class="concern-tag">{{ c }}</span>{% endfor %}</td>
                    {% endif %}
                    <td class="entity-list">{{ comp.entities[:6] | join(', ') }}{% if comp.entities | length > 6 %}, ... (+{{ comp.entities | length - 6 }} more){% endif %}</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>

        <h3>Smells ({{ r.smells | length }})</h3>
        {% if r.smells %}
        {% for smell in r.smells %}
        <div class="smell {{ smell.severity }}">
            <div class="smell-header">
                <span class="badge {{ smell.severity }}">{{ smell.severity }}</span>
                <strong>{{ smell.smell_type }}</strong>
            </div>
            <p class="smell-detail">{{ smell.description }}</p>
            <p class="smell-detail"><strong>Why:</strong> {{ smell.explanation }}</p>
            <p class="smell-detail"><strong>Fix:</strong> {{ smell.suggestion }}</p>
            <p class="smell-detail"><strong>Affects:</strong> {{ smell.affected_components | join(', ') }}</p>
        </div>
        {% endfor %}
        {% else %}
        <div class="card"><p>No architectural smells detected.</p></div>
        {% endif %}
    </div>
    {% endfor %}

    <h2 id="dependencies">Dependency Summary</h2>
    <div class="card">
        <table>
            <thead><tr><th>Package</th><th>Entities</th></tr></thead>
            <tbody>
            {% for pkg, entities in packages %}
                <tr>
                    <td><code>{{ pkg or '(default)' }}</code></td>
                    <td>{{ entities | length }}</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>

    <footer>
        Generated by arcade-agent &mdash; software architecture analysis toolkit
    </footer>

    <script>mermaid.initialize({ startOnLoad: true, theme: 'neutral', securityLevel: 'loose' });</script>
</body>
</html>
""")


def export_comparison_html(
    repo_name: str,
    version: str,
    dep_graph: DependencyGraph,
    results: list[AlgorithmResult],
    output_path: Path,
) -> Path:
    """Generate an HTML report comparing multiple recovery algorithms.

    Args:
        repo_name: Repository name.
        version: Version string.
        dep_graph: The dependency graph (shared across algorithms).
        results: List of AlgorithmResult, one per algorithm.
        output_path: Where to write the HTML file.

    Returns:
        Path to the generated HTML file.
    """
    # Build mermaid diagrams and metrics dicts for each result
    enriched: list[_EnrichedAlgorithmResult] = []
    for result in results:
        mermaid = build_mermaid_diagram(result.architecture, dep_graph)
        metrics_dict = {metric.name: metric.value for metric in result.metrics}
        enriched.append({
            "algorithm": result.algorithm,
            "architecture": result.architecture,
            "smells": result.smells,
            "metrics": result.metrics,
            "metrics_dict": metrics_dict,
            "concerns": result.concerns,
            "mermaid": mermaid,
        })

    # Collect all metric names in consistent order
    metric_names: list[str] = []
    for r in enriched:
        for m in r["metrics"]:
            if m.name not in metric_names:
                metric_names.append(m.name)

    packages = sorted(dep_graph.packages.items(), key=lambda x: -len(x[1]))

    html = COMPARISON_TEMPLATE.render(
        repo_name=repo_name,
        version=version,
        num_entities=dep_graph.num_entities,
        num_edges=dep_graph.num_edges,
        results=enriched,
        metric_names=metric_names,
        graph_quality=graph_quality_context(dep_graph),
        packages=packages,
    )

    output_path.write_text(html)
    return output_path


def build_snapshot_mermaid(snapshot: dict | None) -> str:
    """Build a Mermaid diagram from stored component snapshots."""
    if not snapshot:
        return "graph TD\n    Empty[\"No baseline snapshot\"]"

    node_id = mermaid_node_id

    lines = ["graph TD"]
    components = snapshot.get("components", [])
    dependencies = snapshot.get("component_dependencies", [])

    for component in components:
        comparison_name = component.get("comparison_name", component["name"])
        label = (
            f"{mermaid_label_text(comparison_name)}\\n"
            f"{component.get('num_entities', len(component.get('entities', [])))} entities\\n"
            f"{component.get('class_count', 0)} classes / {component.get('method_count', 0)} methods"
        )
        lines.append(f"    {node_id(comparison_name)}[\"{label}\"]")

    for dep in dependencies:
        lines.append(
            f"    {node_id(dep['source'])} --> {node_id(dep['target'])}"
        )

    return "\n".join(lines)


EVOLUTION_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>arcade-agent: {{ repo_name }} — Architecture Evolution</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6; color: #1f2937; background: #f8fafc;
            max-width: 1500px; margin: 0 auto; padding: 2rem; padding-top: 4rem;
        }
        nav {
            position: fixed; top: 0; left: 0; right: 0; z-index: 100;
            background: #0f172a; padding: 0.7rem 2rem; display: flex;
            align-items: center; gap: 1rem; box-shadow: 0 2px 12px rgba(0,0,0,0.15);
            flex-wrap: wrap;
        }
        nav .nav-title { color: #f8fafc; font-weight: 700; margin-right: 0.5rem; }
        nav a {
            color: #94a3b8; text-decoration: none; font-size: 0.85rem;
            padding: 0.25rem 0.6rem; border-radius: 6px;
        }
        nav a:hover { color: #fff; background: #1e293b; }
        h1 { font-size: 1.9rem; margin-bottom: 0.35rem; }
        h2 { font-size: 1.35rem; margin: 2rem 0 1rem; border-bottom: 2px solid #e2e8f0; padding-bottom: 0.45rem; }
        .subtitle { color: #64748b; margin-bottom: 1.5rem; }
        .stats { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 2rem; }
        .stat-card {
            background: white; border-radius: 12px; padding: 1rem 1.25rem; min-width: 170px;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
        }
        .stat-card .number { font-size: 1.6rem; font-weight: 700; color: #0f766e; }
        .stat-card .label { font-size: 0.8rem; color: #64748b; }
        .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1.25rem; }
        .card {
            background: white; border-radius: 12px; padding: 1.25rem;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
            margin-bottom: 1rem;
        }
        .section-label { font-size: 0.82rem; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; }
        table { width: 100%; border-collapse: collapse; margin-top: 0.75rem; }
        th, td { text-align: left; padding: 0.7rem; border-bottom: 1px solid #e2e8f0; font-size: 0.9rem; }
        th { background: #f8fafc; font-size: 0.8rem; text-transform: uppercase; color: #475569; }
        .delta-positive { color: #047857; font-weight: 600; }
        .delta-negative { color: #b91c1c; font-weight: 600; }
        .delta-neutral { color: #475569; font-weight: 600; }
        .badge {
            display: inline-block; border-radius: 999px; padding: 0.2rem 0.55rem;
            font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
        }
        .badge.matched { background: #dbeafe; color: #1d4ed8; }
        .badge.added { background: #dcfce7; color: #15803d; }
        .badge.removed { background: #fee2e2; color: #b91c1c; }
        .muted { color: #64748b; }
        .full-width { grid-column: 1 / -1; }
        .mermaid { text-align: center; }
        .link { margin-top: 0.75rem; }
        .quality-warning {
            border-left: 4px solid #d97706; padding: 1rem 1.25rem; margin: 1rem 0 2rem;
            background: #fffbeb; border-radius: 0 8px 8px 0; color: #78350f;
        }
        .quality-warning code { color: #92400e; }
        @media (max-width: 960px) {
            .grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <nav>
        <span class="nav-title">arcade-agent</span>
        <a href="#overview">Overview</a>
        <a href="#diagrams">Diagrams</a>
        <a href="#components">Components</a>
        <a href="#dependencies">Dependencies</a>
        <a href="#metrics">Metrics</a>
    </nav>

    <h1 id="overview">Architecture Evolution Report</h1>
    <p class="subtitle">{{ repo_name }} — before/after comparison of high-level components and source statistics</p>

    <div class="stats">
        {% for card in overview_cards %}
        <div class="stat-card">
            <div class="number">{{ card.value }}</div>
            <div class="label">{{ card.label }}</div>
        </div>
        {% endfor %}
    </div>

    {% if graph_quality and graph_quality.status == "qualified" %}
    <div class="quality-warning">
        <strong>Qualified dependency-graph metrics.</strong>
        Current values were computed with incomplete discovered local-import coverage;
        interpret them as directional signals, not a complete architecture assessment.
        {% for language, summary in graph_quality.dependency_resolution.items() %}
        <div><code>{{ language }}</code>:
            {{ summary.resolved_local | default(0) }} resolved,
            {{ summary.unresolved_local | default(0) }} unresolved,
            {{ summary.linked_local | default(0) }} linked,
            {{ summary.unlinked_local | default(0) }} unlinked.
        </div>
        {% endfor %}
    </div>
    {% endif %}

    <div class="grid" id="diagrams">
        <div class="card">
            <div class="section-label">Baseline</div>
            <div class="muted">Commit {{ baseline_commit }}</div>
            <pre class="mermaid">{{ baseline_mermaid }}</pre>
        </div>
        <div class="card">
            <div class="section-label">Current</div>
            <div class="muted">Commit {{ current_commit }}</div>
            <pre class="mermaid">{{ current_mermaid }}</pre>
        </div>
    </div>

    <h2 id="metrics">Metric And Source Statistics</h2>
    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Metric</th>
                    <th>Baseline</th>
                    <th>Current</th>
                    <th>Delta</th>
                </tr>
            </thead>
            <tbody>
                {% for row in metric_rows %}
                <tr>
                    <td>{{ row.name }}</td>
                    <td>{{ row.baseline }}</td>
                    <td>{{ row.current }}</td>
                    <td class="{{ row.delta_class }}">{{ row.delta }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% if run_url %}
        <p class="link"><a href="{{ run_url }}">Open GitHub Actions run for artifacts</a></p>
        {% endif %}
    </div>

    <h2 id="components">Component Changes</h2>
    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Status</th>
                    <th>Baseline</th>
                    <th>Current</th>
                    <th>Similarity</th>
                    <th>Entities</th>
                    <th>Classes</th>
                    <th>Methods</th>
                </tr>
            </thead>
            <tbody>
                {% for row in component_rows %}
                <tr>
                    <td><span class="badge {{ row.status }}">{{ row.status }}</span></td>
                    <td>{{ row.baseline_name }}</td>
                    <td>{{ row.current_name }}</td>
                    <td>{{ row.similarity }}</td>
                    <td>{{ row.entities }}</td>
                    <td>{{ row.classes }}</td>
                    <td>{{ row.methods }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <h2 id="dependencies">Component Dependency Changes</h2>
    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Status</th>
                    <th>Source</th>
                    <th>Target</th>
                </tr>
            </thead>
            <tbody>
                {% for row in dependency_rows %}
                <tr>
                    <td><span class="badge {{ row.status }}">{{ row.status }}</span></td>
                    <td>{{ row.source }}</td>
                    <td>{{ row.target }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <script>mermaid.initialize({ startOnLoad: true, theme: 'neutral', securityLevel: 'loose' });</script>
</body>
</html>
""")


def export_evolution_html(report: dict, output_path: Path) -> Path:
    """Generate a before/after HTML report from stored analysis snapshots."""
    html = EVOLUTION_TEMPLATE.render(
        repo_name=report["repo_name"],
        baseline_commit=report["baseline_commit"],
        current_commit=report["current_commit"],
        overview_cards=report["overview_cards"],
        metric_rows=report["metric_rows"],
        component_rows=report["component_rows"],
        dependency_rows=report["dependency_rows"],
        baseline_mermaid=build_snapshot_mermaid(report.get("baseline")),
        current_mermaid=build_snapshot_mermaid(report["current"]),
        graph_quality=report.get("graph_quality"),
        run_url=report.get("run_url", ""),
    )
    output_path.write_text(html)
    return output_path
