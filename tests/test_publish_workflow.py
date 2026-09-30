"""Run the actual publication scripts against failed CI/release/HTTP boundaries."""

import json
import os
import subprocess
import textwrap
from pathlib import Path
from typing import cast

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/publish.yml"
SHA = "a" * 40


def _step_code(step: str, key: str) -> str:
    lines = WORKFLOW.read_text().splitlines()
    start = lines.index(f"        id: {step}")
    for index in range(start + 1, len(lines)):
        if lines[index].strip() == f"{key}: |":
            block: list[str] = []
            indent = len(lines[index]) - len(lines[index].lstrip()) + 2
            for line in lines[index + 1:]:
                if line.strip() and len(line) - len(line.lstrip()) < indent:
                    break
                block.append(line)
            return textwrap.dedent("\n".join(block))
        if lines[index].startswith("      - "):
            break
    raise AssertionError(f"Missing {step} {key}")


RUNNER = """
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const outputs = {};
const context = {repo: {owner: 'arcade-agent', repo: 'arcade-agent'}};
const github = {rest: {actions: {
  getWorkflowRun: async () => ({data: input.run}),
  getWorkflow: async () => ({data: {id: 10}}),
  listWorkflowRunArtifacts: () => {},
}, repos: {
  getReleaseByTag: async () => {
    if (input.status) throw Object.assign(new Error('API failure'), {status: input.status});
    return {data: input.release};
  },
}, git: {
  getRef: async () => ({data: {object: input.object}}),
  getTag: async () => ({data: {object: input.tagObject}}),
}}, paginate: async () => input.artifacts};
const core = {setOutput: (name, value) => {outputs[name] = value;}, info: () => {}};
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
(async () => {
  let error = null;
  try { await new AsyncFunction('github', 'context', 'core', input.script)(github, context, core); }
  catch (e) { error = e.message; }
  process.stdout.write(JSON.stringify({outputs, error}));
})();
"""


def _javascript(step: str, fixture: dict[str, object]) -> dict[str, object]:
    env = {**os.environ, "REQUESTED_RUN_ID": "42", "TESTED_SHA": SHA, "RELEASE_VERSION": "0.4.0"}
    result = subprocess.run(
        ["node", "-e", RUNNER], input=json.dumps({**fixture, "script": _step_code(step, "script")}),
        text=True, capture_output=True, env=env, timeout=10, check=True,
    )
    value: object = json.loads(result.stdout)
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _run_fixture() -> dict[str, object]:
    return {
        "workflow_id": 10, "status": "completed", "conclusion": "success", "event": "push",
        "head_branch": "main", "head_repository": {"full_name": "arcade-agent/arcade-agent"},
        "head_sha": SHA,
    }


def test_source_gate_accepts_only_identified_main_ci_artifact() -> None:
    result = _javascript("source", {
        "run": _run_fixture(), "artifacts": [{"name": "release-dist", "id": 7, "expired": False}],
    })
    assert result["error"] is None
    assert result["outputs"] == {"run-id": 42, "sha": SHA, "artifact-id": 7}


@pytest.mark.parametrize("field,value", [
    ("workflow_id", 11), ("status", "in_progress"), ("conclusion", "failure"),
    ("event", "pull_request"), ("head_branch", "feature"),
    ("head_repository", {"full_name": "someone/fork"}), ("head_sha", "not-a-sha"),
])
def test_source_gate_rejects_failed_pr_fork_or_wrong_workflow(field: str, value: object) -> None:
    run = {**_run_fixture(), field: value}
    result = _javascript("source", {
        "run": run, "artifacts": [{"name": "release-dist", "id": 7, "expired": False}],
    })
    assert result["error"]
    assert result["outputs"] == {}


@pytest.mark.parametrize("artifacts", [
    [], [{"name": "release-dist", "id": 7, "expired": True}],
    [{"name": "release-dist", "id": 7}, {"name": "release-dist", "id": 8}],
])
def test_source_gate_rejects_missing_expired_or_ambiguous_artifacts(
    artifacts: list[dict[str, object]],
) -> None:
    result = _javascript("source", {"run": _run_fixture(), "artifacts": artifacts})
    assert result["error"]
    assert result["outputs"] == {}


@pytest.mark.parametrize("annotated", [False, True])
def test_release_gate_accepts_lightweight_and_annotated_tags(annotated: bool) -> None:
    result = _javascript("release", {
        "release": {"draft": False, "prerelease": False},
        "object": {"type": "tag" if annotated else "commit", "sha": SHA},
        "tagObject": {"type": "commit", "sha": SHA},
    })
    assert result["error"] is None
    assert result["outputs"] == {"ready": "true"}


@pytest.mark.parametrize("fixture,error", [
    ({"status": 404}, False), ({"status": 403}, True),
    ({"release": {"draft": True}}, True), ({"release": {"prerelease": True}}, True),
    ({"object": {"type": "commit", "sha": "b" * 40}}, False),
    ({"object": {"type": "blob", "sha": SHA}}, True),
    ({"object": {"type": "tag", "sha": SHA}, "tagObject": {"type": "tag", "sha": SHA}}, True),
])
def test_release_gate_never_publishes_missing_unstable_or_wrong_commit_releases(
    fixture: dict[str, object], error: bool,
) -> None:
    result = _javascript("release", {
        "release": {"draft": False, "prerelease": False},
        "object": {"type": "commit", "sha": SHA}, **fixture,
    })
    assert bool(result["error"]) == error
    assert result["outputs"] == {}


@pytest.mark.parametrize("status,exit_code,ready", [
    ("200", 0, "false"), ("404", 0, "true"), ("403", 0, None), ("500", 0, None),
    ("000", 6, None), ("000", 28, None),
])
def test_pypi_probe_distinguishes_absence_from_http_and_network_errors(
    tmp_path: Path, status: str, exit_code: int, ready: str | None,
) -> None:
    curl = tmp_path / "curl"
    curl.write_text('#!/bin/bash\nprintf "%s" "$PROBE_STATUS"\nexit "$PROBE_EXIT"\n')
    curl.chmod(0o755)
    # The HTTP router delegates 200 response parsing to the separately tested helper.
    python = tmp_path / "python"
    python.write_text('#!/bin/bash\necho "ready=false" >> "$GITHUB_OUTPUT"\n')
    python.chmod(0o755)
    output = tmp_path / "outputs"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", _step_code("pypi", "run")],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "GITHUB_OUTPUT": str(output), "RELEASE_VERSION": "0.4.0",
             "SOURCE_SHA": SHA, "GITHUB_REPOSITORY": "arcade-agent/arcade-agent",
             "PROBE_STATUS": status, "PROBE_EXIT": str(exit_code)},
        text=True, capture_output=True, timeout=10,
    )
    if ready is None:
        assert result.returncode != 0
        assert not output.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert output.read_text() == f"ready={ready}\n"
