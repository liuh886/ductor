"""Async wrapper around the Google Gemini CLI."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ductor_bot.cli.auth import gemini_api_key_mode_selected
from ductor_bot.cli.base import (
    BaseCLI,
    CLIConfig,
    _feed_stdin_and_close,
    docker_wrap,
    redact_command_for_logging,
)
from ductor_bot.cli.gemini_events import extract_result_text, extract_text, parse_gemini_stream_line
from ductor_bot.cli.gemini_utils import (
    create_system_prompt_file,
    find_gemini_cli,
    find_gemini_cli_js,
)
from ductor_bot.cli.stream_events import ResultEvent, StreamEvent, SystemInitEvent
from ductor_bot.cli.types import CLIResponse
from ductor_bot.config import NULLISH_TEXT_VALUES
from ductor_bot.infra.platform import CREATION_FLAGS as _CREATION_FLAGS
from ductor_bot.infra.process_tree import force_kill_process_tree
from ductor_bot.workspace.paths import resolve_paths

if TYPE_CHECKING:
    from ductor_bot.cli.process_registry import ProcessRegistry, TrackedProcess
    from ductor_bot.cli.timeout_controller import TimeoutController

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300.0
_GEMINI_RUNTIME_FILES = (
    "settings.json",
    "oauth_creds.json",
    "oauth_creds.json.bak",
    "google_accounts.json",
    "installation_id",
    "projects.json",
    "state.json",
    "trustedFolders.json",
)
_CONTAINER_ONLY_INCLUDE_PREFIXES = ("/mnt", "/ductor")
_HOST_FAST_PATH_DISABLED_AGENTS = frozenset({"bot4-assistant"})

# Must match ``_DUCTOR_MOUNT`` in ``ductor_bot.infra.docker``.
_CONTAINER_DUCTOR = "/ductor"


@dataclass(slots=True)
class _GeminiStreamState:
    """Mutable stream-state for Gemini event processing."""

    last_session_id: str | None
    saw_result: bool = False

    def track(self, event: StreamEvent) -> None:
        """Track session + final-result information from one stream event."""
        if isinstance(event, (SystemInitEvent, ResultEvent)) and event.session_id:
            self.last_session_id = event.session_id

        if isinstance(event, ResultEvent):
            self.saw_result = True
            if not event.session_id:
                event.session_id = self.last_session_id


class GeminiCLI(BaseCLI):
    """Async wrapper around the Google Gemini CLI."""

    def __init__(self, config: CLIConfig) -> None:
        self._config = config
        self._working_dir = Path(config.working_dir).resolve()
        self._docker_cli: str = "gemini"
        self._docker_cli_js: str | None = None
        self._host_cli = find_gemini_cli()
        self._host_cli_js = find_gemini_cli_js()
        self._cli = self._docker_cli if config.docker_container else self._host_cli
        self._cli_js = self._docker_cli_js if config.docker_container else self._host_cli_js

        logger.info("GeminiCLI: cwd=%s model=%s", self._working_dir, config.model)

    def _build_command(
        self,
        *,
        streaming: bool = False,
        resume_session: str | None = None,
        continue_session: bool = False,
        use_host_exec: bool = False,
    ) -> list[str]:
        """Build the CLI command list."""
        cfg = self._config
        if self._config.docker_container and not use_host_exec:
            cli = self._docker_cli
            cli_js = self._docker_cli_js
        else:
            cli = self._host_cli
            cli_js = self._host_cli_js
        cmd = ["node", cli_js] if cli_js else [cli]
        cmd += ["--output-format", "stream-json" if streaming else "json"]
        plan = cfg.capability_plan
        if plan is None or plan.include_directories:
            cmd += ["--include-directories", "."]

        if cfg.model:
            cmd += ["--model", cfg.model]
        if cfg.permission_mode == "bypassPermissions":
            cmd += ["--approval-mode", "yolo"]
        if resume_session:
            cmd += ["--resume", resume_session]
        elif continue_session:
            cmd += ["--resume", "latest"]
        if cfg.allowed_tools:
            cmd += ["--allowed-tools", *cfg.allowed_tools]
        if cfg.cli_parameters:
            cmd.extend(self._filtered_cli_parameters(use_host_exec=use_host_exec))

        return cmd

    def _filtered_cli_parameters(self, *, use_host_exec: bool) -> list[str]:
        """Return CLI parameters after removing host-incompatible container paths."""
        if not self._config.cli_parameters:
            return []

        if not use_host_exec:
            return list(self._config.cli_parameters)

        filtered: list[str] = []
        idx = 0
        params = self._config.cli_parameters
        while idx < len(params):
            part = params[idx]
            if part == "--include-directories" and idx + 1 < len(params):
                value = params[idx + 1]
                if value.startswith(_CONTAINER_ONLY_INCLUDE_PREFIXES):
                    logger.info(
                        "Skipping container-only include directory on host Gemini path: %s",
                        value,
                    )
                    idx += 2
                    continue
                filtered.extend((part, value))
                idx += 2
                continue
            filtered.append(part)
            idx += 1
        return filtered

    def _prepare_env(
        self,
        system_prompt_path: str | None = None,
        *,
        cli_path: str | None = None,
    ) -> dict[str, str]:
        """Build environment dict with Gemini-specific vars."""
        env = os.environ.copy()
        # Ensure ``node`` resolution works when gemini was discovered via an
        # absolute path outside the inherited PATH (service/runtime environments).
        cli_parent = ""
        effective_cli = cli_path or self._host_cli
        if effective_cli.startswith("/"):
            cli_parent = str(PurePosixPath(effective_cli).parent)
        else:
            resolved_cli = Path(effective_cli)
            if resolved_cli.is_absolute():
                cli_parent = str(resolved_cli.parent)
        if cli_parent:
            path_entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
            if cli_parent not in path_entries:
                path_entries.insert(0, cli_parent)
            env["PATH"] = os.pathsep.join(path_entries) if path_entries else cli_parent
        env["GEMINI_IDE_ENABLED"] = "false"
        if system_prompt_path:
            env["GEMINI_SYSTEM_MD"] = system_prompt_path
        runtime_home = self._prepare_isolated_runtime(use_copies=False)
        if runtime_home is not None:
            env["GEMINI_CLI_HOME"] = str(runtime_home)
        self._inject_config_gemini_api_key(env)
        return env

    def _inject_config_gemini_api_key(self, env: dict[str, str]) -> None:
        """Inject GEMINI_API_KEY from ductor config when API-key auth mode is active."""
        existing = (env.get("GEMINI_API_KEY") or "").strip()
        if existing and existing.lower() not in NULLISH_TEXT_VALUES:
            return
        key = (self._config.gemini_api_key or "").strip()
        if not key or key.lower() in NULLISH_TEXT_VALUES:
            return
        if (
            env.get("GOOGLE_GENAI_USE_GCA") == "true"
            or env.get("GOOGLE_GENAI_USE_VERTEXAI") == "true"
        ):
            return

        settings_file = _gemini_settings_path(env)
        if not gemini_api_key_mode_selected(settings_file):
            return

        env["GEMINI_API_KEY"] = key
        logger.debug("Injected GEMINI_API_KEY from ductor config for Gemini API key mode")

    async def send(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,
    ) -> CLIResponse:
        """Execute a non-streaming Gemini CLI call."""
        use_host_exec = self._should_use_host_fast_path()
        cmd = self._build_command(
            streaming=False,
            resume_session=resume_session,
            continue_session=continue_session,
            use_host_exec=use_host_exec,
        )

        system_prompt_path = self._create_system_prompt_path()
        try:
            exec_cmd, use_cwd, subprocess_env = self._resolve_exec(
                cmd,
                system_prompt_path,
                use_host_exec=use_host_exec,
            )
            _log_cmd(exec_cmd)

            process = await asyncio.create_subprocess_exec(
                *exec_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=use_cwd,
                env=subprocess_env,
                creationflags=_CREATION_FLAGS,
            )

            reg, tracked = self._track_process(process)
            try:
                stdout, stderr = await _communicate_with_timeout(
                    process,
                    prompt,
                    timeout_seconds=timeout_seconds or _DEFAULT_TIMEOUT,
                    timeout_controller=timeout_controller,
                )
            except TimeoutError:
                logger.warning("Gemini send timed out")
                force_kill_process_tree(process.pid)
                stdout, stderr = await process.communicate()
                return CLIResponse(
                    result="Timeout",
                    is_error=True,
                    timed_out=True,
                    returncode=process.returncode,
                    stderr=stderr.decode(errors="replace")[:2000] if stderr else "",
                )
            finally:
                self._untrack_process(reg, tracked)

            return _parse_response(stdout, stderr, process.returncode)
        finally:
            await _cleanup_file(system_prompt_path)

    async def send_streaming(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream events from Gemini CLI."""
        use_host_exec = self._should_use_host_fast_path()
        cmd = self._build_command(
            streaming=True,
            resume_session=resume_session,
            continue_session=continue_session,
            use_host_exec=use_host_exec,
        )

        system_prompt_path = self._create_system_prompt_path()
        try:
            exec_cmd, use_cwd, subprocess_env = self._resolve_exec(
                cmd,
                system_prompt_path,
                use_host_exec=use_host_exec,
            )
            _log_cmd(exec_cmd, streaming=True)

            process = await asyncio.create_subprocess_exec(
                *exec_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=use_cwd,
                env=subprocess_env,
                limit=4 * 1024 * 1024,
                creationflags=_CREATION_FLAGS,
            )

            if process.stderr is None:
                msg = "Gemini subprocess created without stderr pipe"
                raise RuntimeError(msg)

            stderr_task = asyncio.create_task(process.stderr.read())
            reg, tracked = self._track_process(process)
            state = _GeminiStreamState(last_session_id=resume_session)
            timed_out = False

            try:
                await _feed_prompt(process, prompt)
                try:
                    async for event in self._stream_events(
                        process,
                        state,
                        timeout_seconds,
                        timeout_controller=timeout_controller,
                    ):
                        yield event
                except TimeoutError:
                    timed_out = True
                    yield ResultEvent(
                        type="result",
                        result="Timeout",
                        is_error=True,
                        session_id=state.last_session_id,
                    )
            finally:
                stderr_bytes = await _finish_stream_process(process, stderr_task)
                self._untrack_process(reg, tracked)

            final_event = _build_stream_exit_event(
                returncode=process.returncode,
                stderr_bytes=stderr_bytes,
                state=state,
            )
            was_aborted = bool(
                reg and reg.was_aborted(self._config.chat_id, self._config.topic_id)
            )
            if final_event is not None and not timed_out and not was_aborted:
                yield final_event
        finally:
            await _cleanup_file(system_prompt_path)

    async def _stream_events(
        self,
        process: asyncio.subprocess.Process,
        state: _GeminiStreamState,
        timeout_seconds: float | None,
        *,
        timeout_controller: TimeoutController | None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Read NDJSON lines and yield normalized stream events."""
        if process.stdout is None:
            msg = "Gemini subprocess created without stdout pipe"
            raise RuntimeError(msg)

        reg = self._config.process_registry
        if timeout_controller is None:
            async for event in _stream_events_plain(
                process,
                state,
                timeout_seconds=timeout_seconds or _DEFAULT_TIMEOUT,
                process_registry=reg,
                chat_id=self._config.chat_id,
            ):
                yield event
            return

        async for event in _stream_events_with_controller(
            process,
            state,
            timeout_controller=timeout_controller,
            process_registry=reg,
            chat_id=self._config.chat_id,
        ):
            yield event

    def _create_system_prompt_path(self) -> str | None:
        """Create a temporary system prompt file when prompt content is present.

        In Docker mode the file is written to ``~/.ductor/tmp/`` which is
        bind-mounted into the container so it can be read via a translated
        container-side path.
        """
        if not (self._config.system_prompt or self._config.append_system_prompt):
            return None
        directory: str | None = None
        if self._config.docker_container:
            tmp_dir = resolve_paths().ductor_home / "tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            directory = str(tmp_dir)
        return create_system_prompt_file(
            self._config.system_prompt or "",
            self._config.append_system_prompt or "",
            directory=directory,
        )

    def _docker_extra_env(self, system_prompt_path: str | None = None) -> dict[str, str]:
        """Build Docker ``-e`` flags for Gemini-specific env vars.

        These are injected into the container via ``docker exec -e``.
        """
        extra: dict[str, str] = {"GEMINI_IDE_ENABLED": "false"}

        # Forward host GEMINI_API_KEY if set, otherwise inject from config.
        host_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if host_key and host_key.lower() not in NULLISH_TEXT_VALUES:
            extra["GEMINI_API_KEY"] = host_key
        else:
            key = (self._config.gemini_api_key or "").strip()
            if key and key.lower() not in NULLISH_TEXT_VALUES:
                settings = _gemini_settings_path(dict(os.environ))
                if gemini_api_key_mode_selected(settings):
                    extra["GEMINI_API_KEY"] = key

        # Forward Google Cloud auth vars when present on host.
        for var in ("GOOGLE_GENAI_USE_GCA", "GOOGLE_GENAI_USE_VERTEXAI"):
            val = os.environ.get(var, "").strip()
            if val:
                extra[var] = val

        # Translate system prompt path to container-side path.
        if system_prompt_path:
            container_path = self._host_to_container_path(system_prompt_path)
            if container_path:
                extra["GEMINI_SYSTEM_MD"] = container_path
        runtime_home = self._prepare_isolated_runtime(use_copies=True)
        if runtime_home is not None:
            container_runtime = self._host_to_container_path(str(runtime_home))
            if container_runtime:
                extra["GEMINI_CLI_HOME"] = container_runtime

        return extra

    def _prepare_isolated_runtime(self, *, use_copies: bool) -> Path | None:
        """Create a minimal Gemini runtime for the current execution plan.

        Returns the runtime root used as ``GEMINI_CLI_HOME`` or ``None`` when
        no isolation should be applied.
        """
        plan = self._config.capability_plan
        if plan is None:
            return None

        paths = resolve_paths()
        source_dotgemini = _source_gemini_dotdir()
        if not source_dotgemini.is_dir():
            return None

        selected = tuple(sorted(skill.source_path for skill in plan.selected_skills))
        payload = json.dumps(
            {
                "profile": plan.runtime_profile,
                "skills": selected,
                "include_directories": plan.include_directories,
            },
            sort_keys=True,
        )
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        runtime_root = paths.ductor_home / "tmp" / "gemini_cli" / fingerprint
        dotgemini = runtime_root / ".gemini"
        skills_dir = dotgemini / "skills"
        try:
            skills_dir.mkdir(parents=True, exist_ok=True)
            _sync_gemini_runtime_files(source_dotgemini, dotgemini)
            _sync_selected_skills(
                skills_dir,
                selected,
                use_copies=use_copies,
            )
        except OSError:
            logger.warning("Failed to prepare isolated Gemini runtime", exc_info=True)
            return None
        return runtime_root

    @staticmethod
    def _host_to_container_path(host_path: str) -> str | None:
        """Translate a host path under ``~/.ductor/`` to its container mount."""
        prefix = str(resolve_paths().ductor_home)
        if host_path.startswith(prefix):
            return _CONTAINER_DUCTOR + host_path[len(prefix) :].replace("\\", "/")
        return None

    def _resolve_exec(
        self,
        cmd: list[str],
        system_prompt_path: str | None,
        *,
        use_host_exec: bool = False,
    ) -> tuple[list[str], str | None, dict[str, str] | None]:
        """Resolve command, cwd, and env for subprocess execution.

        Returns ``(exec_cmd, use_cwd, subprocess_env)``.  In Docker mode
        ``subprocess_env`` is ``None`` (inherit host env for the ``docker``
        binary) and Gemini-specific vars are forwarded via ``-e`` flags.
        """
        if self._config.docker_container and not use_host_exec:
            extra_env = self._docker_extra_env(system_prompt_path)
            exec_cmd, use_cwd = docker_wrap(
                cmd, self._config, extra_env=extra_env, interactive=True
            )
            return exec_cmd, use_cwd, None

        env = self._prepare_env(system_prompt_path, cli_path=self._host_cli)
        return cmd, str(self._working_dir), env

    def _should_use_host_fast_path(self) -> bool:
        """Return True when a simple Gemini chat turn should bypass Docker."""
        if not self._config.docker_container:
            return False
        if self._config.agent_name in _HOST_FAST_PATH_DISABLED_AGENTS:
            return False
        plan = self._config.capability_plan
        if plan is None:
            return False
        return (
            plan.runtime_profile == "chat_light"
            and not plan.include_directories
            and not plan.needs_workspace_write
        )

    def _track_process(
        self,
        process: asyncio.subprocess.Process,
    ) -> tuple[ProcessRegistry | None, TrackedProcess | None]:
        """Register a subprocess in ProcessRegistry if tracking is enabled."""
        reg = self._config.process_registry
        tracked = (
            reg.register(
                self._config.chat_id,
                process,
                self._config.process_label,
                topic_id=self._config.topic_id,
            )
            if reg
            else None
        )
        return reg, tracked

    @staticmethod
    def _untrack_process(reg: ProcessRegistry | None, tracked: TrackedProcess | None) -> None:
        """Unregister a previously tracked subprocess."""
        if tracked is not None and reg is not None:
            reg.unregister(tracked)


async def _feed_prompt(process: asyncio.subprocess.Process, prompt: str) -> None:
    """Write prompt to stdin and close the pipe."""
    await _feed_stdin_and_close(process, prompt)


async def _communicate_with_timeout(
    process: asyncio.subprocess.Process,
    prompt: str,
    *,
    timeout_seconds: float,
    timeout_controller: TimeoutController | None,
) -> tuple[bytes, bytes]:
    """Run ``process.communicate`` using either plain or managed timeouts."""
    communicate_coro = process.communicate(input=prompt.encode())
    if timeout_controller is not None:
        return await timeout_controller.run_with_timeout(communicate_coro)
    async with asyncio.timeout(timeout_seconds):
        return await communicate_coro


async def _stream_events_plain(
    process: asyncio.subprocess.Process,
    state: _GeminiStreamState,
    *,
    timeout_seconds: float,
    process_registry: ProcessRegistry | None,
    chat_id: int,
) -> AsyncGenerator[StreamEvent, None]:
    """Read stream output with a fixed timeout (legacy behavior)."""
    assert process.stdout is not None
    async with asyncio.timeout(timeout_seconds):
        while True:
            if process_registry and process_registry.was_aborted(chat_id):
                logger.info("Gemini streaming aborted by user")
                return

            line_bytes = await process.stdout.readline()
            if not line_bytes:
                return

            line = line_bytes.decode(errors="replace").rstrip()
            if not line:
                continue

            logger.debug("Gemini raw line: %.200s", line)
            for event in parse_gemini_stream_line(line):
                state.track(event)
                yield event


async def _stream_events_with_controller(
    process: asyncio.subprocess.Process,
    state: _GeminiStreamState,
    *,
    timeout_controller: TimeoutController,
    process_registry: ProcessRegistry | None,
    chat_id: int,
) -> AsyncGenerator[StreamEvent, None]:
    """Read stream output with managed timeout extensions + warnings."""
    assert process.stdout is not None
    timeout_controller.begin()
    warning_task = timeout_controller.start_warning_loop()
    timeout_secs = timeout_controller.timeout_seconds
    try:
        while True:
            try:
                async with asyncio.timeout(timeout_secs):
                    while True:
                        if process_registry and process_registry.was_aborted(chat_id):
                            logger.info("Gemini streaming aborted by user")
                            return

                        line_bytes = await process.stdout.readline()
                        if not line_bytes:
                            return
                        timeout_controller.record_activity()

                        line = line_bytes.decode(errors="replace").rstrip()
                        if not line:
                            continue

                        logger.debug("Gemini raw line: %.200s", line)
                        for event in parse_gemini_stream_line(line):
                            state.track(event)
                            yield event
            except TimeoutError:
                if timeout_controller.try_extend():
                    timeout_secs = timeout_controller.activity_extension_seconds
                    continue
                raise
    finally:
        if warning_task and not warning_task.done():
            warning_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await warning_task


async def _finish_stream_process(
    process: asyncio.subprocess.Process,
    stderr_task: asyncio.Task[bytes],
) -> bytes:
    """Ensure process shutdown and return collected stderr."""
    if process.returncode is None:
        force_kill_process_tree(process.pid)
    await process.wait()
    return await stderr_task


def _build_stream_exit_event(
    *,
    returncode: int | None,
    stderr_bytes: bytes,
    state: _GeminiStreamState,
) -> ResultEvent | None:
    """Build a synthetic final ResultEvent when the stream lacked one."""
    if state.saw_result:
        return None

    if returncode == 0:
        return ResultEvent(
            type="result",
            result="",
            is_error=False,
            returncode=returncode,
            session_id=state.last_session_id,
        )

    detail = stderr_bytes.decode(errors="replace").strip()
    if not detail:
        detail = f"Gemini exited with code {returncode}"
    return ResultEvent(
        type="result",
        result=detail[:500],
        is_error=True,
        returncode=returncode,
        session_id=state.last_session_id,
    )


async def _cleanup_file(path: str | None) -> None:
    """Delete a temporary file from an async context."""
    if not path:
        return
    with contextlib.suppress(OSError):
        await asyncio.to_thread(Path(path).unlink, missing_ok=True)


def _log_cmd(cmd: list[str], *, streaming: bool = False) -> None:
    """Log the CLI command with sensitive env values masked."""
    safe = redact_command_for_logging(cmd)
    logger.info("%s: %s", "Gemini stream cmd" if streaming else "Gemini cmd", " ".join(safe))


def _gemini_settings_path(env: dict[str, str]) -> Path:
    """Resolve Gemini settings path honoring GEMINI_CLI_HOME."""
    base = Path(env.get("GEMINI_CLI_HOME", str(Path.home()))).expanduser()
    return base / ".gemini" / "settings.json"


def _source_gemini_dotdir() -> Path:
    """Return the source ``.gemini`` directory used for auth/config seeding."""
    base = Path(os.environ.get("GEMINI_CLI_HOME", str(Path.home()))).expanduser()
    return base / ".gemini"


def _sync_gemini_runtime_files(source_dotgemini: Path, target_dotgemini: Path) -> None:
    """Copy the minimal set of Gemini auth/config files into the isolated runtime."""
    target_dotgemini.mkdir(parents=True, exist_ok=True)
    for filename in _GEMINI_RUNTIME_FILES:
        source = source_dotgemini / filename
        if not source.exists() or not source.is_file():
            continue
        dest = target_dotgemini / filename
        try:
            source_mtime = source.stat().st_mtime_ns
            dest_mtime = dest.stat().st_mtime_ns if dest.exists() else -1
        except OSError:
            source_mtime = -1
            dest_mtime = -2
        if dest.exists() and source_mtime == dest_mtime:
            continue
        shutil.copy2(source, dest)


def _sync_selected_skills(  # noqa: C901, PLR0912
    skills_dir: Path,
    selected_skill_paths: tuple[str, ...],
    *,
    use_copies: bool,
) -> None:
    """Materialize only the selected skills into an isolated Gemini runtime."""
    use_copies = use_copies or os.name == "nt"
    desired: dict[str, Path] = {}
    for skill_path in selected_skill_paths:
        source = Path(skill_path)
        if source.is_dir():
            desired[source.name] = source

    for existing in list(skills_dir.iterdir()):
        if existing.name not in desired:
            if existing.is_symlink() or existing.is_file():
                existing.unlink(missing_ok=True)
            elif existing.is_dir():
                shutil.rmtree(existing, ignore_errors=True)

    for name, source in desired.items():
        dest = skills_dir / name
        if use_copies:
            if dest.is_symlink() or dest.is_file():
                dest.unlink(missing_ok=True)
            elif dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(source, dest, symlinks=True, dirs_exist_ok=True)
            continue

        if dest.is_symlink():
            try:
                if dest.resolve() == source.resolve():
                    continue
            except OSError:
                pass
            dest.unlink(missing_ok=True)
        elif dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.symlink_to(source, target_is_directory=True)


def _parse_response(stdout: bytes, stderr: bytes, returncode: int | None) -> CLIResponse:
    """Parse Gemini CLI JSON output into CLIResponse."""
    stderr_text = stderr.decode(errors="replace")[:2000] if stderr else ""
    raw = stdout.decode(errors="replace").strip()
    if not raw:
        return CLIResponse(result="", is_error=True, returncode=returncode, stderr=stderr_text)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return CLIResponse(
            result=raw[:2000],
            is_error=returncode != 0,
            returncode=returncode,
            stderr=stderr_text,
        )

    if not isinstance(parsed, dict):
        return CLIResponse(
            result=raw[:2000],
            is_error=returncode != 0,
            returncode=returncode,
            stderr=stderr_text,
        )

    stats = parsed.get("stats", {})
    if not isinstance(stats, dict):
        stats = {}

    usage = {
        "input_tokens": stats.get("input_tokens", 0),
        "output_tokens": stats.get("output_tokens", 0),
        "cached_tokens": stats.get("cached_tokens", stats.get("cached", 0)),
    }

    is_cli_error = bool(parsed.get("is_error")) or parsed.get("status") == "error"
    result = extract_result_text(parsed)
    if not result and is_cli_error:
        result = _extract_error(parsed)
    if not result:
        result = raw[:2000]

    return CLIResponse(
        session_id=parsed.get("session_id"),
        result=result,
        is_error=returncode != 0 or is_cli_error,
        returncode=returncode,
        stderr=stderr_text,
        duration_ms=stats.get("duration_ms"),
        usage=usage,
    )


def _extract_error(data: dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        text = extract_text(error, ("message", "error", "detail"))
        if text:
            return text
    elif error is not None:
        return error if isinstance(error, str) else str(error)
    return extract_text(data, ("message", "detail"))
