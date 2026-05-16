"""Dynamic Codex model discovery via ``codex`` CLI helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from shutil import which

from ductor_bot.infra.platform import CREATION_FLAGS as _CREATION_FLAGS

logger = logging.getLogger(__name__)

_INIT_MSG = json.dumps(
    {
        "jsonrpc": "2.0",
        "method": "initialize",
        "id": 1,
        "params": {"clientInfo": {"name": "ductor", "version": "1.0"}},
    }
)
_LIST_MSG = json.dumps(
    {
        "jsonrpc": "2.0",
        "method": "model/list",
        "id": 2,
        "params": {},
    }
)
_INPUT = f"{_INIT_MSG}\n{_LIST_MSG}\n"


@dataclass(frozen=True, slots=True)
class CodexModelInfo:
    """A model discovered from the Codex app-server."""

    id: str
    display_name: str
    description: str
    supported_efforts: tuple[str, ...]
    default_effort: str
    is_default: bool


DISCOVERY_TIMEOUT = 30.0


async def discover_codex_models(*, deadline: float = DISCOVERY_TIMEOUT) -> list[CodexModelInfo]:
    """Query ``codex`` for available models.

    Returns an empty list on timeout, missing CLI, or parse error.
    Never raises -- all errors are logged and swallowed.
    """
    codex_path = which("codex")
    if not codex_path:
        logger.debug("codex CLI not found, skipping model discovery")
        return []

    started = time.monotonic()
    app_server_deadline = min(deadline, 10.0)
    raw_stdout = await _discover_via_app_server(codex_path, deadline=app_server_deadline)
    models = _parse_response(raw_stdout, suppress_warning=True)
    if models:
        logger.info("Codex discovery found %d models via app-server", len(models))
        return models

    remaining = max(1.0, deadline - (time.monotonic() - started))
    models = await _discover_via_debug_models(codex_path, deadline=remaining)
    if not models and raw_stdout.strip():
        _log_missing_app_server_response(raw_stdout)
    logger.info("Codex discovery found %d models", len(models))
    return models


async def _discover_via_app_server(codex_path: str, *, deadline: float) -> str:
    """Query ``codex app-server`` and return raw stdout."""
    process: asyncio.subprocess.Process | None = None
    raw_stdout = ""

    try:
        process = await asyncio.create_subprocess_exec(
            codex_path,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=_CREATION_FLAGS,
        )
        if process.stdin is None or process.stdout is None:
            logger.warning("Codex app-server spawned without pipes")
            return ""

        async with asyncio.timeout(deadline):
            stdout, _stderr = await process.communicate(input=_INPUT.encode())
            raw_stdout = stdout.decode(errors="replace")
    except TimeoutError:
        logger.warning("Codex app-server discovery timeout after %.0fs", deadline)
        return ""
    except OSError:
        logger.warning("Failed to spawn codex app-server", exc_info=True)
        return ""
    finally:
        if process is not None:
            await _kill_process(process)
    return raw_stdout


async def _discover_via_debug_models(codex_path: str, *, deadline: float) -> list[CodexModelInfo]:
    """Fallback to ``codex debug models`` when app-server discovery fails."""
    process: asyncio.subprocess.Process | None = None
    raw_stdout = ""
    try:
        process = await asyncio.create_subprocess_exec(
            codex_path,
            "debug",
            "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=_CREATION_FLAGS,
        )
        if process.stdout is None:
            logger.warning("Codex debug models spawned without stdout pipe")
            return []

        async with asyncio.timeout(deadline):
            stdout, _stderr = await process.communicate()
            raw_stdout = stdout.decode(errors="replace")
    except TimeoutError:
        logger.warning("Codex debug models discovery timeout after %.0fs", deadline)
        return []
    except OSError:
        logger.warning("Failed to spawn codex debug models", exc_info=True)
        return []
    finally:
        if process is not None:
            await _kill_process(process)
    return _parse_debug_models(raw_stdout)


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    """Best-effort kill of a hung process."""
    with contextlib.suppress(OSError):
        process.kill()
    with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
        await asyncio.wait_for(process.wait(), timeout=0.2)


def _parse_response(raw: str, *, suppress_warning: bool = False) -> list[CodexModelInfo]:
    """Parse JSON-RPC stdout lines for the model/list response."""
    for line in raw.strip().splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") != 2:
            continue
        data = msg.get("result", {}).get("data", [])
        return [_parse_model(m) for m in data if isinstance(m, dict)]

    if not suppress_warning:
        _log_missing_app_server_response(raw)
    return []


def _log_missing_app_server_response(raw: str) -> None:
    """Log a compact warning when ``codex app-server`` returns no model/list payload."""
    preview = raw.strip().replace("\n", "\\n")[:500]
    if preview:
        logger.warning("No model/list response found in codex app-server output: %s", preview)
    else:
        logger.warning("No model/list response found in codex app-server output")


def _parse_debug_models(raw: str) -> list[CodexModelInfo]:
    """Parse ``codex debug models`` JSON output."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        preview = raw.strip().replace("\n", "\\n")[:500]
        if preview:
            logger.warning("Failed to parse codex debug models output: %s", preview)
        else:
            logger.warning("Failed to parse codex debug models output")
        return []

    items = payload.get("models", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        logger.warning("codex debug models returned unexpected payload shape")
        return []
    return [_parse_debug_model(item) for item in items if isinstance(item, dict)]


def _parse_model(entry: dict[str, object]) -> CodexModelInfo:
    """Parse a single model entry from the JSON-RPC response."""
    efforts_raw = entry.get("supportedReasoningEfforts", [])
    efforts = tuple(
        e["reasoningEffort"]
        for e in (efforts_raw if isinstance(efforts_raw, list) else [])
        if isinstance(e, dict) and "reasoningEffort" in e
    )
    return CodexModelInfo(
        id=str(entry.get("id", "")),
        display_name=str(entry.get("displayName", "")),
        description=str(entry.get("description", "")),
        supported_efforts=efforts or ("medium",),
        default_effort=str(entry.get("defaultReasoningEffort", "medium")),
        is_default=bool(entry.get("isDefault", False)),
    )


def _parse_debug_model(entry: dict[str, object]) -> CodexModelInfo:
    """Parse a single ``codex debug models`` model entry."""
    efforts_raw = entry.get("supported_reasoning_levels", [])
    efforts = tuple(
        str(e.get("effort", "")).strip()
        for e in (efforts_raw if isinstance(efforts_raw, list) else [])
        if isinstance(e, dict) and str(e.get("effort", "")).strip()
    )
    slug = str(entry.get("slug", "")).strip()
    display_name = str(entry.get("display_name", "")).strip() or slug
    default_effort = str(entry.get("default_reasoning_level", "medium")).strip() or "medium"
    return CodexModelInfo(
        id=slug,
        display_name=display_name,
        description=str(entry.get("description", "")).strip(),
        supported_efforts=efforts or ("medium",),
        default_effort=default_effort,
        is_default=bool(entry.get("priority", 0) == 1),
    )
