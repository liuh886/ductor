"""Cron job CLI command building and output parsing."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from ductor_bot.cli.codex_events import parse_codex_jsonl
from ductor_bot.cli.gemini_events import parse_gemini_json
from ductor_bot.cli.gemini_utils import find_gemini_cli
from ductor_bot.cli.param_resolver import TaskExecutionConfig

logger = logging.getLogger(__name__)


def build_cmd(exec_config: TaskExecutionConfig, prompt: str) -> list[str] | None:
    """Build a CLI command for one-shot cron execution."""
    builder = _CMD_BUILDERS.get(exec_config.provider, _build_claude_cmd)
    return builder(exec_config, prompt)


def enrich_instruction(instruction: str, task_folder: str) -> str:
    """Append memory file instructions to the agent instruction."""
    memory_file = f"{task_folder}_MEMORY.md"
    return (
        f"{instruction}\n\n"
        f"IMPORTANT:\n"
        f"- Read the {memory_file} file (it contains important information!)\n"
        f"- When finished, update {memory_file} with DATE + TIME and what you have done."
    )


def parse_claude_result(stdout: bytes) -> str:
    """Extract result text from Claude CLI JSON output."""
    if not stdout:
        return ""
    raw = stdout.decode(errors="replace").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        return str(data.get("result", ""))
    except json.JSONDecodeError:
        return raw[:2000]


def parse_gemini_result(stdout: bytes) -> str:
    """Extract result text from Gemini CLI JSON output."""
    if not stdout:
        return ""
    raw = stdout.decode(errors="replace").strip()
    if not raw:
        return ""
    return parse_gemini_json(raw) or raw[:2000]


def parse_codex_result(stdout: bytes) -> str:
    """Extract result text from Codex CLI JSONL output."""
    if not stdout:
        return ""
    raw = stdout.decode(errors="replace").strip()
    if not raw:
        return ""
    result_text, _thread_id, _usage = parse_codex_jsonl(raw)
    return result_text or raw[:2000]


def parse_result(provider: str, stdout: bytes) -> str:
    """Extract result text from provider-specific CLI output."""
    parser = _RESULT_PARSERS.get(provider, parse_claude_result)
    return parser(stdout)


def indent(text: str, prefix: str) -> str:
    """Indent every line of *text* with *prefix*."""
    return "\n".join(prefix + line for line in text.splitlines())


# -- Private builders --


def _build_claude_cmd(exec_config: TaskExecutionConfig, prompt: str) -> list[str] | None:
    """Build a Claude CLI command for one-shot cron execution."""
    cli = which("claude")
    if not cli:
        return None
    cmd = [
        cli,
        "-p",
        "--output-format",
        "json",
        "--model",
        exec_config.model,
        "--permission-mode",
        exec_config.permission_mode,
        "--no-session-persistence",
    ]
    # Add extra CLI parameters
    cmd.extend(exec_config.cli_parameters)
    cmd += ["--", prompt]
    return cmd


def _build_gemini_cmd(exec_config: TaskExecutionConfig, prompt: str) -> list[str] | None:
    """Build a Gemini CLI command for one-shot cron execution."""
    try:
        cli = find_gemini_cli()
    except FileNotFoundError:
        return None
    cmd = [cli, "--output-format", "json", "--include-directories", "."]

    if exec_config.model:
        cmd += ["--model", exec_config.model]
    if exec_config.permission_mode == "bypassPermissions":
        cmd += ["--approval-mode", "yolo"]

    cmd.extend(exec_config.cli_parameters)
    cmd += ["--", prompt]
    return cmd


def _build_codex_cmd(exec_config: TaskExecutionConfig, prompt: str) -> list[str] | None:
    """Build a Codex CLI command for one-shot cron execution."""
    cli = which("codex")
    if not cli:
        return None
    cmd = [cli, "exec", "--json", "--color", "never", "--skip-git-repo-check"]

    # Sandbox flags based on permission_mode
    if exec_config.permission_mode == "bypassPermissions":
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        cmd.append("--full-auto")

    cmd += ["--model", exec_config.model]

    # Add reasoning effort (if not default)
    if exec_config.reasoning_effort and exec_config.reasoning_effort != "medium":
        cmd += ["-c", f"model_reasoning_effort={exec_config.reasoning_effort}"]

    # Add extra CLI parameters
    cmd.extend(exec_config.cli_parameters)

    cmd += ["--", prompt]
    return cmd


_CmdBuilder = Callable[[TaskExecutionConfig, str], list[str] | None]
_ResultParser = Callable[[bytes], str]

_CMD_BUILDERS: dict[str, _CmdBuilder] = {
    "claude": _build_claude_cmd,
    "gemini": _build_gemini_cmd,
    "codex": _build_codex_cmd,
}

_RESULT_PARSERS: dict[str, _ResultParser] = {
    "claude": parse_claude_result,
    "gemini": parse_gemini_result,
    "codex": parse_codex_result,
}


@dataclass(slots=True)
class OneShotExecutionResult:
    """Normalized outcome for a one-shot provider subprocess run."""

    status: str
    result_text: str
    stdout: bytes
    stderr: bytes
    returncode: int | None
    timed_out: bool


async def execute_one_shot(
    cmd: list[str],
    *,
    cwd: Path,
    provider: str,
    timeout_seconds: float,
    timeout_label: str,
) -> OneShotExecutionResult:
    """Run one provider CLI command with timeout and normalized status/result."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    timed_out = False
    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, stderr = await proc.communicate()
    except TimeoutError:
        timed_out = True
        proc.kill()
        stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise

    if timed_out:
        return OneShotExecutionResult(
            status="error:timeout",
            result_text=f"[{timeout_label} timed out after {timeout_seconds:.0f}s]",
            stdout=stdout,
            stderr=stderr,
            returncode=proc.returncode,
            timed_out=True,
        )

    returncode = proc.returncode
    status = "success" if returncode == 0 else f"error:exit_{returncode}"
    return OneShotExecutionResult(
        status=status,
        result_text=parse_result(provider, stdout),
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        timed_out=False,
    )
