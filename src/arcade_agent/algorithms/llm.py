"""Claude CLI wrapper for LLM-powered architecture analysis.

Uses the local ``claude`` CLI (Claude Code) in print mode.
Set ``ARCADE_MOCK=1`` to skip all LLM calls and return empty results.
Set ``ARCADE_MODEL=haiku|sonnet|opus`` to select the Claude model (default: sonnet).
"""

import json
import os
import shutil
import subprocess

MOCK_MODE = os.environ.get("ARCADE_MOCK", "").strip() in ("1", "true", "yes")
CLAUDE_MODEL = os.environ.get("ARCADE_MODEL", "sonnet")


class LLMUnavailableError(RuntimeError):
    """The ``claude`` CLI needed for LLM-assisted analysis is not installed."""


def ask_claude(
    prompt: str,
    system: str = "",
    model: str | None = None,
    timeout: int = 120,
) -> str:
    """Send a prompt to the local claude CLI and return the text response.

    Args:
        prompt: The user prompt to send.
        system: Optional system prompt to append.
        model: Claude model override (default: env ARCADE_MODEL or sonnet).
        timeout: Subprocess timeout in seconds (default: 120).

    Returns:
        The raw text response from Claude.

    Raises:
        LLMUnavailableError: If the ``claude`` CLI is not on PATH.
    """
    if MOCK_MODE:
        return "{}"
    if shutil.which("claude") is None:
        raise LLMUnavailableError(
            "LLM-assisted analysis (recover algorithm 'arc' or 'limbo', or "
            "use_llm=True) needs the `claude` CLI on PATH. Install and log in to "
            "Claude Code, choose a structural algorithm ('pkg', 'wca', 'acdc'), "
            "or set ARCADE_MOCK=1 to use the name-based heuristic instead."
        )

    model = model or CLAUDE_MODEL
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "text",
        "--no-session-persistence",
    ]
    if system:
        cmd.extend(["--append-system-prompt", system])

    # Strip CLAUDECODE env var so nested invocation works inside Claude Code
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed (exit {result.returncode}): {result.stderr}"
        )

    return result.stdout.strip()


def ask_claude_json(
    prompt: str,
    system: str = "",
    model: str | None = None,
    timeout: int = 120,
) -> dict:
    """Send a prompt to claude CLI and parse the JSON response.

    Args:
        prompt: The user prompt to send.
        system: Optional system prompt to append.
        model: Claude model override.
        timeout: Subprocess timeout in seconds.

    Returns:
        Parsed JSON dict from Claude's response.
    """
    if MOCK_MODE:
        return {}

    text = ask_claude(prompt, system=system, model=model, timeout=timeout)

    # Handle markdown code blocks wrapping the JSON
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # skip opening ```json
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    return json.loads(text)
