"""Golden controls for evidence-backed scattered-functionality candidates.

The Java HA dogfood corpus exposed Service/Configuration warnings that
coincide only in generic naming suffixes across unrelated components.
"""

from arcade_agent.algorithms.architecture import Architecture, Component
from arcade_agent.algorithms.concern import detect_scattered_functionality
from arcade_agent.parsers.graph import DependencyGraph, Entity
from arcade_agent.tools.detect_smells import detect_smells


def _fixture(names_by_component: dict[str, list[str]]) -> tuple[Architecture, DependencyGraph]:
    entities = {}
    packages = {}
    components = []
    for component_name, class_names in names_by_component.items():
        fqns = []
        for class_name in class_names:
            fqn = f"example.{component_name}.{class_name}"
            fqns.append(fqn)
            entities[fqn] = Entity(
                fqn=fqn,
                name=class_name,
                package=f"example.{component_name}",
                file_path=f"{component_name}/{class_name}.java",
                kind="class",
                language="java",
            )
        packages[f"example.{component_name}"] = fqns
        components.append(Component(name=component_name, responsibility="", entities=fqns))
    return Architecture(components=components, algorithm="pkg"), DependencyGraph(
        entities=entities,
        edges=[],
        packages=packages,
    )


def test_unrelated_service_and_configuration_suffixes_are_not_a_shared_concern():
    architecture, graph = _fixture({
        "billing": ["BillingService", "BillingConfiguration"],
        "catalog": ["CatalogService", "CatalogConfiguration"],
        "orders": ["OrdersService", "OrdersConfiguration"],
    })

    # Suffix-only grouping currently produces TWO noisy SPF warnings here.
    assert detect_scattered_functionality(architecture, graph) == []


def test_shared_specific_stem_across_roles_yields_low_confidence_candidate():
    architecture, graph = _fixture({
        "billing": ["AuditService"],
        "checkout": ["AuditHandler"],
        "support": ["AuditAdapter"],
    })

    findings = detect_scattered_functionality(architecture, graph)

    assert len(findings) == 1
    assert findings[0]["pattern"] == "Audit"
    assert findings[0]["components"] == ["billing", "checkout", "support"]
    assert findings[0]["severity"] == "low"
    assert findings[0]["evidence_kind"] == "shared_name_stem"
    assert findings[0]["matched_suffixes"] == ["Adapter", "Handler", "Service"]
    assert [e["entity"] for e in findings[0]["evidence"]] == [
        "example.billing.AuditService",
        "example.checkout.AuditHandler",
        "example.support.AuditAdapter",
    ]


def test_same_suffix_in_three_components_is_not_sufficient_evidence():
    architecture, graph = _fixture({
        "a": ["AuditService"],
        "b": ["AuditService"],
        "c": ["AuditService"],
    })
    assert detect_scattered_functionality(architecture, graph) == []


def test_generic_common_stem_is_not_treated_as_architecture_violation():
    architecture, graph = _fixture({
        "a": ["CommonService", "AuditService"],
        "b": ["CommonConfiguration", "AuditHandler"],
        "c": ["CommonAdapter", "AuditAdapter"],
    })
    findings = detect_scattered_functionality(architecture, graph)
    assert [finding["pattern"] for finding in findings] == ["Audit"]


def test_report_is_explicitly_a_candidate_not_verified_violation():
    architecture, graph = _fixture({
        "billing": ["AuditService"],
        "checkout": ["AuditHandler"],
        "support": ["AuditAdapter"],
    })
    findings = [
        smell for smell in detect_smells(architecture, graph)
        if smell.smell_type == "Scattered Parasitic Functionality"
    ]
    assert len(findings) == 1
    assert findings[0].severity == "low"
    assert "possible" in findings[0].description.lower()
    assert "naming-only" in findings[0].explanation.lower()
    assert "example.billing.AuditService" in findings[0].explanation
    assert "review" in findings[0].suggestion.lower()
