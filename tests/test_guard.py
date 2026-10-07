"""Tests for the architecture guardrail (conformance engine, tools, CLI, MCP)."""

import asyncio
import itertools
import json
from pathlib import Path

import pytest

from arcade_agent.algorithms import conformance as c
from arcade_agent.ci.guard_cli import main as guard_main
from arcade_agent.tools import guard

LAYERED_SPEC = {
    "intent": "api -> service -> domain; store reached only through service.",
    "components": [
        {"name": "api", "match": "**/api/**", "layer": "presentation"},
        {"name": "service", "match": "**/service/**", "layer": "application"},
        {"name": "store", "match": "**/store/**", "layer": "infrastructure"},
        {"name": "domain", "match": "**/domain/**", "layer": "domain"},
    ],
    "layers": ["presentation", "application", "domain", "infrastructure"],
    "allow": [
        {"from": "presentation", "to": "application"},
        {"from": "application", "to": "domain"},
        {"from": "application", "to": "infrastructure"},
        {"from": "infrastructure", "to": "domain"},
    ],
    "forbid": [
        {"from": "presentation", "to": "infrastructure", "why": "go through the service"},
        {"from": "domain", "to": "infrastructure", "why": "the domain stays pure"},
    ],
    "budgets": {"no_cycles": True, "max_fan_in": 5, "max_component_entities": 50},
}

FILES = {
    "app/__init__.py": "",
    "app/domain/__init__.py": "",
    "app/domain/order.py": "class Order:\n    def __init__(self, oid):\n        self.oid = oid\n",
    "app/store/__init__.py": "",
    "app/store/orders_store.py": (
        "from app.domain.order import Order\n"
        "class OrdersStore:\n"
        "    def make(self, oid):\n"
        "        return Order(oid)\n"
    ),
    "app/service/__init__.py": "",
    "app/service/orders_service.py": (
        "from app.store.orders_store import OrdersStore\n"
        "class OrdersService:\n"
        "    def __init__(self):\n"
        "        self.store = OrdersStore()\n"
    ),
    "app/api/__init__.py": "",
    "app/api/orders_api.py": (
        "from app.service.orders_service import OrdersService\n"
        "class OrdersApi:\n"
        "    def __init__(self):\n"
        "        self.svc = OrdersService()\n"
    ),
}

SHORTCUT_API = (
    "from app.store.orders_store import OrdersStore\n"
    "class OrdersApi:\n"
    "    def count(self):\n"
    "        return OrdersStore().make(1)\n"
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    for rel, text in FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (tmp_path / guard.SPEC_FILENAME).write_text(json.dumps(LAYERED_SPEC))
    return tmp_path


@pytest.fixture
def spec() -> c.ArchitectureSpec:
    return c.ArchitectureSpec.from_dict(LAYERED_SPEC)


# -- glob --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("**/api/**", "app/api/x.py", True),
        ("**/api/**", "api/x.py", True),
        ("**/api/**", "app/rapid/x.py", False),
        ("**/api/**", "app/api_old/x.py", False),
        ("**/{web,api}/**", "src/web/h.py", True),
        ("**/{web,api}/**", "src/api/h.py", True),
        ("**/{web,api}/**", "src/cli/h.py", False),
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/sub/a.py", False),
        ("src/?.py", "src/a.py", True),
    ],
)
def test_glob_to_regex(pattern: str, path: str, expected: bool) -> None:
    assert bool(c.glob_to_regex(pattern).match(path)) is expected


def test_glob_rejects_unbalanced_brace() -> None:
    with pytest.raises(ValueError):
        c.glob_to_regex("**/{api,web/**")


# -- spec parsing --------------------------------------------------------------


def test_spec_from_dict_rejects_rule_without_endpoints() -> None:
    with pytest.raises(ValueError):
        c.ArchitectureSpec.from_dict({"forbid": [{"from": "api"}]})


def test_spec_from_dict_rejects_nameless_component() -> None:
    with pytest.raises(ValueError):
        c.ArchitectureSpec.from_dict({"components": [{"match": "**"}]})


# -- dependency rules ------------------------------------------------------------


def test_forbidden_dependency(spec: c.ArchitectureSpec) -> None:
    problems = c.dependency_problems(spec, "api", "store")
    assert [p.rule for p in problems] == ["forbidden-dependency"]
    assert "go through the service" in problems[0].message


def test_layer_violation_points_against_order(spec: c.ArchitectureSpec) -> None:
    problems = c.dependency_problems(spec, "store", "service")
    assert [p.rule for p in problems] == ["layer-violation"]


def test_inward_and_allowed_edges_pass(spec: c.ArchitectureSpec) -> None:
    for src, dst in [("api", "service"), ("service", "store"), ("store", "domain")]:
        assert c.dependency_problems(spec, src, dst) == []


def test_allow_overrides_layer_order() -> None:
    spec = c.ArchitectureSpec.from_dict(
        {
            "components": [
                {"name": "a", "layer": "top"},
                {"name": "b", "layer": "bottom"},
            ],
            "layers": ["top", "bottom"],
            "allow": [{"from": "b", "to": "a"}],
        }
    )
    assert c.dependency_problems(spec, "b", "a") == []


def test_forbid_globs_match_component_names() -> None:
    spec = c.ArchitectureSpec.from_dict(
        {
            "components": [{"name": "models"}, {"name": "views"}, {"name": "controllers"}],
            "forbid": [{"from": "models", "to": "{views,controllers}"}],
        }
    )
    assert c.dependency_problems(spec, "models", "views")
    assert c.dependency_problems(spec, "models", "controllers")
    assert not c.dependency_problems(spec, "views", "models")


def test_preview_and_check_agree_on_every_pair(project: Path) -> None:
    """The proactive answer must never disagree with the after-the-fact verdict."""
    spec = c.load_spec(project / guard.SPEC_FILENAME)
    names = spec.component_names()
    for src, dst in itertools.permutations(names, 2):
        preview = guard.preview_impact(src, dst, str(project))
        found = c.check_conformance(spec, {}, {(src, dst): 1})
        edge_errors = [v for v in found if v.source == src and v.target == dst]
        assert preview["allowed"] is (not edge_errors), (src, dst)


# -- whole-codebase check ----------------------------------------------------------


def test_conformant_project_passes(project: Path) -> None:
    result = guard.check_architecture(str(project), use_cache=False)
    assert result["verdict"] == "PASS"
    assert result["unmapped_entities"] == 0
    assert {"from": "api", "to": "service", "count": 2} in result["component_edges"]


def test_shortcut_import_fails(project: Path) -> None:
    (project / "app/api/orders_api.py").write_text(SHORTCUT_API)
    result = guard.check_architecture(str(project), use_cache=False)
    assert result["verdict"] == "FAIL"
    assert [(v["rule"], v["from"], v["to"]) for v in result["violations"]] == [
        ("forbidden-dependency", "api", "store")
    ]


def test_check_is_deterministic(project: Path) -> None:
    (project / "app/api/orders_api.py").write_text(SHORTCUT_API)
    first = guard.check_architecture(str(project), use_cache=False)
    second = guard.check_architecture(str(project), use_cache=False)
    assert first == second


def test_cycle_between_components(spec: c.ArchitectureSpec) -> None:
    found = c.check_conformance(spec, {}, {("service", "store"): 1, ("store", "service"): 1})
    rules = [v.rule for v in found]
    assert "dependency-cycle" in rules
    assert c.verdict(found) == "FAIL"


def test_size_budget_is_a_warning(spec: c.ArchitectureSpec) -> None:
    mapping = {f"e{i}": "service" for i in range(51)}
    found = c.check_conformance(spec, mapping, {})
    assert [v.rule for v in found] == ["max-component-size"]
    assert c.verdict(found) == "WARN"


def test_violation_ids_are_unique_and_stable(spec: c.ArchitectureSpec) -> None:
    edges = {("api", "store"): 1, ("store", "service"): 1, ("service", "store"): 1}
    found = c.check_conformance(spec, {}, edges)
    ids = [v.id for v in found]
    assert len(ids) == len(set(ids))
    assert ids == [v.id for v in c.check_conformance(spec, {}, edges)]


def test_smell_budget_needs_a_baseline(project: Path) -> None:
    data = dict(LAYERED_SPEC, budgets={"max_new_smells": 0})
    (project / guard.SPEC_FILENAME).write_text(json.dumps(data))
    result = guard.check_architecture(str(project), use_cache=False)
    assert result["budgets_not_evaluated"] == [
        "max_new_smells (no baseline smell count given)"
    ]


def test_missing_spec_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        guard.check_architecture(str(tmp_path))


# -- propose ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "component"),
    [
        ("an HTTP endpoint that lists orders", "api"),
        ("business logic to total a user's orders", "service"),
        ("an in-memory store for orders", "store"),
        ("an Order entity with id and total", "domain"),
    ],
)
def test_propose_placement(spec: c.ArchitectureSpec, intent: str, component: str) -> None:
    assert c.propose_placement(spec, intent)["suggested_component"] == component


def test_propose_reports_dependency_guidance(spec: c.ArchitectureSpec) -> None:
    result = c.propose_placement(spec, "an HTTP endpoint")
    assert result["may_depend_on"] == ["service", "domain"]
    assert [m["component"] for m in result["must_not_depend_on"]] == ["store"]


def test_propose_agrees_with_preview(spec: c.ArchitectureSpec) -> None:
    for comp in spec.components:
        result = c.propose_placement(spec, comp.name)
        assert result["suggested_component"] == comp.name
        for other in result["may_depend_on"]:
            assert c.dependency_problems(spec, comp.name, other) == []
        for item in result["must_not_depend_on"]:
            assert c.dependency_problems(spec, comp.name, item["component"])
        listed = set(result["may_depend_on"]) | {m["component"] for m in
                                                 result["must_not_depend_on"]}
        assert listed == set(spec.component_names()) - {comp.name}


def test_propose_without_match_says_so(spec: c.ArchitectureSpec) -> None:
    result = c.propose_placement(spec, "zzz qqq")
    assert result["suggested_component"] is None
    assert "api" in result["note"]


def test_propose_uses_spec_keywords() -> None:
    spec = c.ArchitectureSpec.from_dict(
        {"components": [{"name": "core"}, {"name": "billing", "keywords": ["invoice"]}]}
    )
    assert c.propose_placement(spec, "send an invoice")["suggested_component"] == "billing"


# -- templates and init ------------------------------------------------------------


@pytest.mark.parametrize("template", sorted(guard.SPEC_TEMPLATES))
def test_templates_are_valid_specs(template: str) -> None:
    spec = c.ArchitectureSpec.from_dict(guard.SPEC_TEMPLATES[template])
    for comp in spec.components:
        c.glob_to_regex(comp.match)
        if spec.layers:
            assert comp.layer in spec.layers


def test_layered_template_matches_brace_globs(tmp_path: Path) -> None:
    spec = c.ArchitectureSpec.from_dict(guard.SPEC_TEMPLATES["layered"])
    rx = {comp.name: c.glob_to_regex(comp.match) for comp in spec.components}
    assert rx["presentation"].match("src/web/routes.py")
    assert rx["repository"].match("src/dao/orders.py")


def test_init_spec_refuses_to_overwrite(tmp_path: Path) -> None:
    guard.init_spec(str(tmp_path), "hexagonal")
    with pytest.raises(FileExistsError):
        guard.init_spec(str(tmp_path), "hexagonal")
    guard.init_spec(str(tmp_path), "mvc", overwrite=True)
    assert json.loads((tmp_path / guard.SPEC_FILENAME).read_text())["layers"] == [
        "presentation",
        "domain",
    ]


# -- CLI -------------------------------------------------------------------------


def test_cli_check_gates_on_failure(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert guard_main(["check", str(project), "--no-cache"]) == 0
    (project / "app/api/orders_api.py").write_text(SHORTCUT_API)
    assert guard_main(["check", str(project), "--no-cache"]) == 1
    assert guard_main(["check", str(project), "--no-cache", "--fail-on", "never"]) == 0
    assert "api -> store is forbidden" in capsys.readouterr().out


def test_cli_preview_json(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert guard_main(["preview", str(project), "--from", "api", "--to", "store", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["allowed"] is False


def test_cli_reports_missing_spec(tmp_path: Path) -> None:
    assert guard_main(["check", str(tmp_path)]) == 2


# -- MCP -------------------------------------------------------------------------


def test_mcp_exposes_guard_tools(project: Path) -> None:
    pytest.importorskip("mcp", reason="mcp extra not installed")
    from arcade_agent.tools.adapters.mcp import get_server

    server = get_server()
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {
        "init_spec",
        "propose_placement",
        "preview_impact",
        "check_architecture",
        "remediate",
    } <= names

    content = asyncio.run(
        server.call_tool(
            "preview_impact",
            {"from_component": "api", "to_component": "store", "source_path": str(project)},
        )
    )
    blocks = content[0] if isinstance(content, tuple) else content
    assert json.loads(blocks[0].text)["allowed"] is False


def test_mcp_returns_spec_errors_as_data(tmp_path: Path) -> None:
    pytest.importorskip("mcp", reason="mcp extra not installed")
    from arcade_agent.tools.adapters.mcp import get_server

    content = asyncio.run(
        get_server().call_tool("check_architecture", {"source_path": str(tmp_path)})
    )
    blocks = content[0] if isinstance(content, tuple) else content
    assert "No architecture spec" in json.loads(blocks[0].text)["error"]
