"""Regression tests for distributable GitHub Actions integration."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPYABLE_WORKFLOW = ROOT / "examples/workflows/arcade-agent-analysis.yml"
ANALYZE_ACTION = ROOT / "actions/analyze/action.yml"


def _package_version() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return pyproject["project"]["version"]


def _github_script_sources(text: str) -> list[str]:
    scripts: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "script: |":
            continue
        script_lines: list[str] = []
        for script_line in lines[index + 1 :]:
            if script_line.startswith("          ") or script_line.startswith("            "):
                script_lines.append(script_line)
                continue
            break
        scripts.append("\n".join(script_lines))
    return scripts


def test_reusable_workflow_installs_released_package_not_tooling_checkout():
    workflow = (ROOT / ".github/workflows/architecture-analysis-reusable.yml").read_text()
    version = _package_version()
    expected_install_spec = (
        'python -m pip install "arcade-agent${INSTALL_EXTRAS}==${ARCADE_AGENT_VERSION}"'
    )

    assert "arcade-agent-version" in workflow
    assert f'default: "{version}"' in workflow
    assert 'if [ "${ARCADE_AGENT_VERSION}" = "latest" ]; then' in workflow
    assert 'python -m pip install "arcade-agent${INSTALL_EXTRAS}"' in workflow
    assert "tooling-repo" not in workflow
    assert "tooling-repository" not in workflow
    assert "tooling-ref" not in workflow
    assert expected_install_spec in workflow
    # "source" sentinel installs the checked-out repo (non-editable) so
    # self-dogfooding runs the code under review, not a released package that
    # necessarily lags the PR.
    assert 'if [ "${ARCADE_AGENT_VERSION}" = "source" ]; then' in workflow
    assert 'python -m pip install "./target-repo${INSTALL_EXTRAS}"' in workflow
    assert "pip install -e" not in workflow
    assert "arcade-self-analysis" in workflow
    assert "arcade-compare-baseline" in workflow
    assert "filter-non-architectural-helpers" in workflow
    assert "default: false" in workflow


def test_self_dogfood_ci_installs_from_source():
    # Self-dogfooding must analyze the code under review, so CI installs the
    # checked-out source via the reusable workflow's "source" sentinel rather
    # than a released package version that necessarily lags the PR (e.g. a bump
    # to an as-yet-unpublished version would break every analysis run).
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert 'arcade-agent-version: "source"' in workflow
    assert "filter-non-architectural-helpers: true" in workflow
    assert "tooling-ref:" not in workflow
    assert "tooling-repository:" not in workflow


def test_readme_documents_copyable_standalone_ci_template():
    readme = (ROOT / "README.md").read_text()
    version = _package_version()

    assert f"uses: arcade-agent/arcade-agent/actions/analyze@v{version}" in readme
    assert f'arcade-agent-version: "{version}"' in readme
    assert "arcade-agent-version: latest" not in readme
    assert "standalone" in readme
    assert "examples/workflows/arcade-agent-analysis.yml" in readme
    assert "Copy `.github/workflows/arch-drift.yml`" not in readme


def test_reusable_workflow_does_not_hardcode_consumer_default_branch():
    workflow = (ROOT / ".github/workflows/architecture-analysis-reusable.yml").read_text()
    readme = (ROOT / "README.md").read_text()

    assert "baseline-branch" in workflow
    assert "BASELINE_BRANCH:" in workflow
    assert "github.event.repository.default_branch" in workflow
    assert "branch: baselineBranch" in workflow
    assert "format('refs/heads/{0}', env.BASELINE_BRANCH)" in workflow
    assert "store-baseline-on-push" in workflow
    assert "store-baseline-on-main-push" not in workflow
    assert "branches: [main]" not in readme


def test_reusable_workflow_does_not_hardcode_consumer_workflow_filename():
    workflow = (ROOT / ".github/workflows/architecture-analysis-reusable.yml").read_text()

    assert 'default: ""' in workflow
    assert "listWorkflowRunsForRepo" in workflow
    assert "const baselineWorkflowId = process.env.BASELINE_WORKFLOW_ID;" in workflow
    assert 'default: "ci.yml"' not in workflow


def test_legacy_drift_workflow_uses_default_branch_for_baseline_updates():
    workflow = (ROOT / ".github/workflows/arch-drift.yml").read_text()

    assert "github.event.repository.default_branch" in workflow
    assert workflow.count("--filter-non-architectural-helpers") == 2
    assert workflow.count("ARGS=(--source src ") == 2
    assert "refs/heads/main" not in workflow
    assert "stefanzweifel/git-auto-commit-action" not in workflow
    assert "contents: read" in workflow
    assert "contents: write" in workflow


def test_copyable_workflow_template_is_standalone():
    workflow = COPYABLE_WORKFLOW.read_text()
    version = _package_version()

    assert "workflow_call:" not in workflow
    assert "uses: arcade-agent/arcade-agent/.github/workflows/" not in workflow
    assert f'ARCADE_AGENT_VERSION: "{version}"' in workflow
    assert "python -m pip install \"arcade-agent${INSTALL_EXTRAS}\"" in workflow
    assert "arcade-self-analysis" in workflow
    assert "arcade-compare-baseline" in workflow
    assert "github.event.repository.default_branch" in workflow
    assert "listWorkflowRunsForRepo" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    assert "refs/heads/main" not in workflow


def test_analyze_composite_action_provides_short_market_style_api():
    action = ANALYZE_ACTION.read_text()
    readme = (ROOT / "README.md").read_text()
    version = _package_version()

    assert "runs:" in action
    assert "using: composite" in action
    assert "arcade-agent-version:" in action
    assert f'default: "{version}"' in action
    assert "source-path:" in action
    assert "exclude-dirs:" in action
    assert "exclude-tests:" in action
    assert "baseline-branch:" in action
    assert "python -m pip install \"arcade-agent${INSTALL_EXTRAS}\"" in action
    assert "arcade-self-analysis" in action
    assert "arcade-compare-baseline" in action
    assert "github.event.repository.default_branch" in action
    assert "github.event.pull_request.head.repo.full_name == github.repository" in action
    assert "refs/heads/main" not in action

    assert f"uses: arcade-agent/arcade-agent/actions/analyze@v{version}" in readme
    assert f'arcade-agent-version: "{version}"' in readme


def test_action_inputs_are_not_embedded_in_github_script_literals():
    action = ANALYZE_ACTION.read_text()
    workflow = (ROOT / ".github/workflows/architecture-analysis-reusable.yml").read_text()

    github_script_blocks = _github_script_sources(action) + _github_script_sources(workflow)

    assert github_script_blocks
    assert all("${{ inputs." not in block for block in github_script_blocks)
    assert "BASELINE_ARTIFACT_NAME: ${{ inputs.baseline-artifact-name }}" in action
    assert "BASELINE_WORKFLOW_ID: ${{ inputs.baseline-workflow-id }}" in workflow


def test_reusable_workflow_shell_steps_use_env_and_arrays_for_inputs():
    workflow = (ROOT / ".github/workflows/architecture-analysis-reusable.yml").read_text()
    action = ANALYZE_ACTION.read_text()

    assert 'ARGS=(--source "target-repo/${SOURCE_PATH}")' in workflow
    assert 'ARGS+=(--language "${LANGUAGE}")' in workflow
    assert 'ARGS+=(--exclude-dirs "${EXCLUDE_DIRS}")' in workflow
    assert "ARGS+=(--no-exclude-tests)" in workflow
    assert 'ARGS+=(--repo-name "${REPO_NAME}")' in workflow
    assert '"${ARGS[@]}"' in workflow
    assert 'ARGS+=(--exclude-dirs "${EXCLUDE_DIRS}")' in action
    assert "ARGS+=(--no-exclude-tests)" in action
    assert 'LANGUAGE_ARGS="--language ${{ inputs.language }}"' not in workflow
    assert 'REPO_NAME_ARGS="--repo-name ${{ inputs.repo-name }}"' not in workflow
    assert '--algorithm "${{ inputs.primary-algorithm }}"' not in workflow
