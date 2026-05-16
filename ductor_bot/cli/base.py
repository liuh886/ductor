"""Base types and abstract interface for CLI backends."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import sys
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ductor_bot.cli.stream_events import StreamEvent
from ductor_bot.cli.types import CLIResponse
from ductor_bot.orchestrator.capabilities.models import CapabilityExecutionPlan

if TYPE_CHECKING:
    from ductor_bot.cli.process_registry import ProcessRegistry
    from ductor_bot.cli.timeout_controller import TimeoutController

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"
_REDACTED_ENV_DISPLAY = "<redacted>"


def _win_feed_stdin(process: asyncio.subprocess.Process, data: str) -> None:
    """Write prompt to stdin and close on Windows; no-op on POSIX."""
    writer = getattr(process, "stdin", None)
    if _IS_WINDOWS and writer is not None and data:
        writer.write(data.encode())
        writer.close()


async def _feed_stdin_and_close(
    process: asyncio.subprocess.Process,
    data: str,
    *,
    windows_only: bool = False,
) -> None:
    """Write prompt to stdin and close the writer gracefully."""
    if windows_only and not _IS_WINDOWS:
        return

    writer = process.stdin
    if writer is None:
        return

    with contextlib.suppress(BrokenPipeError, ConnectionResetError, RuntimeError, ValueError):
        writer.write(data.encode())
        drain_result = writer.drain()
        if inspect.isawaitable(drain_result):
            await drain_result

    writer.close()
    wait_closed = getattr(writer, "wait_closed", None)
    if wait_closed is None:
        return
    with contextlib.suppress(
        BrokenPipeError,
        ConnectionResetError,
        RuntimeError,
        OSError,
        ValueError,
    ):
        closed_result = wait_closed()
        if inspect.isawaitable(closed_result):
            await closed_result


@dataclass(slots=True)
class CLIConfig:
    """Configuration for any CLI wrapper."""

    provider: str = "claude"
    working_dir: str | Path = "."
    model: str | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: str = "bypassPermissions"
    docker_container: str = ""
    # Codex-specific fields (ignored by Claude provider):
    sandbox_mode: str = "read-only"
    images: list[str] = field(default_factory=list)
    instructions: str | None = None
    reasoning_effort: str = "medium"
    # Process tracking (shared across providers):
    process_registry: ProcessRegistry | None = None
    chat_id: int = 0
    topic_id: int | None = None
    process_label: str = "main"
    # Gemini-specific auth fallback:
    gemini_api_key: str | None = None
    capability_plan: CapabilityExecutionPlan | None = None
    # Extra CLI parameters (provider-specific):
    cli_parameters: list[str] = field(default_factory=list)
    # Transport identification (for routing results back):
    transport: str = "tg"
    # Multi-agent identification:
    agent_name: str = "main"
    interagent_port: int = 8799
    interagent_token: str = ""
    # External transcription hooks (#66) — empty strings keep built-in strategies.
    transcribe_command: str = ""
    video_transcribe_command: str = ""


_CONTAINER_DUCTOR_MOUNT = "/ductor"
_SCOPED_SECRET_KEYS: dict[str, frozenset[str]] = {
    "gemini": frozenset(
        {
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_GENAI_USE_GCA",
            "GOOGLE_GENAI_USE_VERTEXAI",
        }
    ),
    "codex": frozenset({"OPENAI_API_KEY"}),
    "claude": frozenset(),
}
_COMMON_RUNTIME_SECRET_KEYS = frozenset(
    {
        "OBSIDIAN_GATEWAY_URL",
        "OBSIDIAN_GATEWAY_KEY",
    }
)


def redact_command_for_logging(
    cmd: list[str],
    *,
    truncate_long_options: bool = True,
) -> list[str]:
    """Return a log-safe copy of *cmd* with secret env values redacted."""
    safe: list[str] = []
    idx = 0
    while idx < len(cmd):
        part = cmd[idx]
        if part == "-e" and idx + 1 < len(cmd):
            safe.append(part)
            key_value = cmd[idx + 1]
            key, sep, _value = key_value.partition("=")
            if sep and _should_redact_env_key(key):
                safe.append(f"{key}={_REDACTED_ENV_DISPLAY}")
            elif truncate_long_options and len(key_value) > 80:
                safe.append(key_value[:80] + "...")
            else:
                safe.append(key_value)
            idx += 2
            continue

        if (
            len(part) > 80
            and (
                not truncate_long_options
                or (idx > 0 and cmd[idx - 1].startswith("--"))
            )
        ):
            safe.append(part[:80] + "...")
        else:
            safe.append(part)
        idx += 1
    return safe


def _should_redact_env_key(key: str) -> bool:
    """Return True when an env-var name is likely to contain a secret."""
    lowered = key.lower()
    return any(token in lowered for token in ("token", "secret", "password", "key"))


def _append_env_flag(env_flags: list[str], key: str, value: str | int) -> None:
    """Append one Docker environment flag pair."""
    env_flags += ["-e", f"{key}={value}"]


def _provider_secret_env(provider: str, secret_env: dict[str, str]) -> dict[str, str]:
    """Return the subset of secrets needed by one provider process."""
    allowed = _SCOPED_SECRET_KEYS.get(provider, frozenset()) | _COMMON_RUNTIME_SECRET_KEYS
    return {key: value for key, value in secret_env.items() if key in allowed}


def _to_container_path(host_path: Path, main_home: Path) -> str:
    """Map a host path under *main_home* to its container equivalent.

    The Docker container mounts the root ductor home at ``/ductor``.
    """
    rel = host_path.relative_to(main_home)
    if str(rel) == ".":
        return _CONTAINER_DUCTOR_MOUNT
    return f"{_CONTAINER_DUCTOR_MOUNT}/{rel.as_posix()}"


def _docker_env_flags(
    config: CLIConfig,
    container_home: str,
    container_shared: str,
) -> list[str]:
    """Build the ``-e KEY=VAL`` argv flags for ``docker exec``."""
    env_flags: list[str] = [
        "-e",
        f"DUCTOR_CHAT_ID={config.chat_id}",
        "-e",
        f"DUCTOR_TRANSPORT={config.transport}",
        "-e",
        f"DUCTOR_AGENT_NAME={config.agent_name}",
        "-e",
        f"DUCTOR_INTERAGENT_PORT={config.interagent_port}",
        "-e",
        f"DUCTOR_HOME={container_home}",
        "-e",
        f"DUCTOR_SHARED_MEMORY_PATH={container_shared}",
        "-e",
        "DUCTOR_INTERAGENT_HOST=host.docker.internal",
    ]
    if config.interagent_token:
        env_flags += ["-e", f"DUCTOR_INTERAGENT_TOKEN={config.interagent_token}"]
    if config.topic_id:
        env_flags += ["-e", f"DUCTOR_TOPIC_ID={config.topic_id}"]
    if config.transcribe_command:
        env_flags += ["-e", f"DUCTOR_TRANSCRIBE_COMMAND={config.transcribe_command}"]
    if config.video_transcribe_command:
        env_flags += [
            "-e",
            f"DUCTOR_VIDEO_TRANSCRIBE_COMMAND={config.video_transcribe_command}",
        ]
    return env_flags


def docker_wrap(
    cmd: list[str],
    config: CLIConfig,
    *,
    extra_env: dict[str, str] | None = None,
    interactive: bool = False,
) -> tuple[list[str], str | None]:
    """Wrap a CLI command for Docker execution if a container is set.

    *interactive* adds ``-i`` to keep stdin open (required for providers
    that pipe the prompt via stdin, e.g. Gemini).

    *extra_env* vars are injected as ``-e`` flags into ``docker exec``
    (set **inside** the container, unlike ``env=`` on the host process).
    """
    if config.docker_container:
        logger.debug("docker_wrap container=%s", config.docker_container)
        stdin_flag: list[str] = ["-i"] if interactive else []
        working_dir = Path(config.working_dir)
        ductor_home = working_dir.parent if working_dir.name == "workspace" else working_dir

        # Resolve root ductor home for host → container path mapping.
        # Sub-agents live at <root>/agents/<name>/; the Docker mount is the root.
        main_home = ductor_home
        if main_home.parent.name == "agents":
            main_home = main_home.parent.parent

        container_cwd = _to_container_path(working_dir, main_home)
        container_home = _to_container_path(ductor_home, main_home)
        container_shared = _to_container_path(main_home / "SHAREDMEMORY.md", main_home)

        # Merge user secrets from .env (low priority — never override).
        import os

        from ductor_bot.infra.env_secrets import load_env_secrets

        merged_extra = _provider_secret_env(config.provider, load_env_secrets(main_home / ".env"))
        # Remove keys already in host env (subprocess inherits docker binary env).
        for key in list(merged_extra):
            if key in os.environ:
                del merged_extra[key]
        if extra_env:
            merged_extra.update(extra_env)  # Provider-specific overrides win.
        extra_env = merged_extra or None

        env_flags = _docker_env_flags(config, container_home, container_shared)
        if extra_env:
            for key, value in extra_env.items():
                env_flags += ["-e", f"{key}={value}"]
        return (
            [
                "docker",
                "exec",
                *stdin_flag,
                "-w",
                container_cwd,
                *env_flags,
                config.docker_container,
                *cmd,
            ],
            None,
        )
    return cmd, str(Path(config.working_dir).resolve())


class BaseCLI(ABC):
    """Abstract interface for CLI backends (Claude, Codex, etc.)."""

    @abstractmethod
    async def send(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,
    ) -> CLIResponse: ...

    @abstractmethod
    def send_streaming(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,
    ) -> AsyncGenerator[StreamEvent, None]: ...
