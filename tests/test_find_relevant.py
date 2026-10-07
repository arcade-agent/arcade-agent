"""Tests for the find_relevant tool."""

from arcade_agent.tools.context_for_task import context_for_task
from arcade_agent.tools.find_relevant import _query_keywords, _tokenize, find_relevant


def test_tokenize_simple():
    assert _tokenize("hello world") == ["hello", "world"]


def test_tokenize_camel_case():
    tokens = _tokenize("MyClassName")
    assert "my" in tokens
    assert "class" in tokens
    assert "name" in tokens


def test_tokenize_snake_case():
    tokens = _tokenize("my_function_name")
    assert "my" in tokens
    assert "function" in tokens
    assert "name" in tokens


def test_find_relevant_exact_name(sample_graph):
    result = find_relevant(sample_graph, "Calculator")
    assert result["num_results"] > 0
    fqns = [r["fqn"] for r in result["results"]]
    assert "com.example.calc.Calculator" in fqns


def test_find_relevant_partial_name(sample_graph):
    result = find_relevant(sample_graph, "Math Helper")
    assert result["num_results"] > 0
    fqns = [r["fqn"] for r in result["results"]]
    assert "com.example.util.MathHelper" in fqns


def test_find_relevant_package_match(sample_graph):
    result = find_relevant(sample_graph, "calc")
    assert result["num_results"] > 0
    # Both Calculator and AdvancedCalculator are in com.example.calc
    fqns = [r["fqn"] for r in result["results"]]
    assert any("calc" in fqn.lower() for fqn in fqns)


def test_find_relevant_top_k(sample_graph):
    result = find_relevant(sample_graph, "example", top_k=2)
    assert result["num_results"] <= 2


def test_find_relevant_no_match(sample_graph):
    result = find_relevant(sample_graph, "zzzznotfound")
    assert result["num_results"] == 0


def test_find_relevant_with_architecture(sample_graph, sample_architecture):
    result = find_relevant(
        sample_graph, "Calculator", architecture=sample_architecture,
    )
    assert result["num_results"] > 0
    # Should include component info
    first = result["results"][0]
    assert "component" in first
    assert first["component"] == "Calc"


def test_find_relevant_architecture_boost(sample_graph, sample_architecture):
    # "utility helpers" matches Util component responsibility
    result = find_relevant(
        sample_graph, "utility helpers", architecture=sample_architecture,
    )
    assert result["num_results"] > 0
    fqns = [r["fqn"] for r in result["results"]]
    assert "com.example.util.MathHelper" in fqns


def test_find_relevant_empty_query(sample_graph):
    result = find_relevant(sample_graph, "")
    assert "error" in result


def test_query_keywords_drop_stopwords_and_duplicates():
    assert _query_keywords("Add support for the math helper to the math module") == [
        "math",
        "helper",
        "module",
    ]


def test_stopwords_do_not_substring_match(sample_graph):
    # "to" is a substring of "Calculator"; before stopwords it matched both
    # calculator classes by name and path.
    result = find_relevant(sample_graph, "go to the math helper")
    assert [r["fqn"] for r in result["results"]] == ["com.example.util.MathHelper"]


def test_only_stopwords_is_no_keywords(sample_graph):
    result = find_relevant(sample_graph, "add it to the")
    assert result["error"] == "No searchable keywords found"


def test_context_for_task_ignores_stopwords(sample_graph):
    result = context_for_task(sample_graph, "add a helper to the math utilities")
    assert "to" not in result["keywords"]
    assert "add" not in result["keywords"]
