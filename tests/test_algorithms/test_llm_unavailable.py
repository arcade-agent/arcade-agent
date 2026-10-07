"""LLM-assisted recovery fails with an actionable error when claude is absent."""

import pytest

from arcade_agent.algorithms import llm


def test_missing_claude_cli_raises_actionable_error(monkeypatch):
    monkeypatch.setattr(llm, "MOCK_MODE", False)
    monkeypatch.setattr(llm.shutil, "which", lambda _name: None)
    with pytest.raises(llm.LLMUnavailableError, match="ARCADE_MOCK=1"):
        llm.ask_claude("hi")


def test_recover_arc_without_claude(monkeypatch, sample_graph):
    from arcade_agent.tools.recover import recover

    monkeypatch.setattr(llm, "MOCK_MODE", False)
    monkeypatch.setattr(llm.shutil, "which", lambda _name: None)
    with pytest.raises(llm.LLMUnavailableError):
        recover(sample_graph, algorithm="arc")
