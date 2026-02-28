"""Core conversation flows: normal message handling with session management."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ductor_bot.cli.types import AgentRequest, AgentResponse
from ductor_bot.config import NULLISH_TEXT_VALUES
from ductor_bot.log_context import set_log_context
from ductor_bot.orchestrator.hooks import HookContext
from ductor_bot.orchestrator.registry import OrchestratorResult
from ductor_bot.session import SessionData
from ductor_bot.text.response_format import session_error_text
from ductor_bot.workspace.loader import read_mainmemory

if TYPE_CHECKING:
    from ductor_bot.orchestrator.core import Orchestrator

logger = logging.getLogger(__name__)


async def _prepare_normal(
    orch: Orchestrator,
    chat_id: int,
    text: str,
    *,
    model_override: str | None = None,
) -> tuple[AgentRequest, SessionData]:
    """Shared setup for normal() and normal_streaming().

    Returns (request, session) so the caller can update the session after the CLI call.
    """
    requested_model = model_override or orch._config.model
    req_model, req_provider = orch.resolve_runtime_target(requested_model)

    session, is_new = await orch._sessions.resolve_session(
        chat_id,
        provider=req_provider,
        model=req_model,
        preserve_existing_target=model_override is None,
    )
    req_model = session.model
    req_provider = session.provider
    await orch._sessions.sync_session_target(
        session,
        provider=req_provider,
        model=req_model,
    )
    if session.session_id:
        set_log_context(session_id=session.session_id)
    logger.info(
        "Session resolved sid=%s new=%s msgs=%d",
        session.session_id[:8] if session.session_id else "<new>",
        is_new,
        session.message_count,
    )

    append_prompt = None
    if is_new:
        mainmemory = await asyncio.to_thread(read_mainmemory, orch.paths)
        if mainmemory.strip():
            append_prompt = mainmemory

        roster = _build_agent_roster(orch)
        if roster:
            append_prompt = f"{append_prompt}\n\n{roster}" if append_prompt else roster

    hook_ctx = HookContext(
        chat_id=chat_id,
        message_count=session.message_count,
        is_new_session=is_new,
        provider=req_provider,
        model=req_model,
    )
    prompt = orch._hook_registry.apply(text, hook_ctx)

    request = AgentRequest(
        prompt=prompt,
        append_system_prompt=append_prompt,
        model_override=req_model,
        provider_override=req_provider,
        chat_id=chat_id,
        resume_session=None if is_new else session.session_id,
        timeout_seconds=orch._config.cli_timeout,
    )
    return request, session


async def _update_session(
    orch: Orchestrator, session: SessionData, response: AgentResponse
) -> None:
    """Store the real CLI session_id and update metrics."""
    if response.session_id and response.session_id != session.session_id:
        logger.info(
            "Session ID updated: %s -> %s",
            session.session_id[:8] if session.session_id else "<new>",
            response.session_id[:8],
        )
        session.session_id = response.session_id
    await orch._sessions.update_session(
        session, cost_usd=response.cost_usd, tokens=response.total_tokens
    )


async def _reset_on_error(
    orch: Orchestrator,
    chat_id: int,
    *,
    model_name: str,
    provider_name: str,
    cli_detail: str = "",
) -> OrchestratorResult:
    """Kill processes, preserve session, return user-facing error."""
    await orch._process_registry.kill_all(chat_id)
    logger.warning("Session error preserved model=%s provider=%s", model_name, provider_name)
    return OrchestratorResult(
        text=session_error_text(model_name, cli_detail),
    )


_SIGKILL_USER_MSG = "Execution was interrupted. Please send the same request again."
_SESSION_RECOVERED_MSG = (
    "_Previous session could not be restored. A new session was started automatically._"
)


def _is_sigkill(response: AgentResponse) -> bool:
    """Return True when the response indicates SIGKILL termination."""
    return response.is_error and response.returncode == -getattr(signal, "SIGKILL", 9)


_INVALID_SESSION_MARKERS = ("invalid session", "session not found")


def _is_invalid_session(response: AgentResponse) -> bool:
    """Return True when the CLI rejected a ``--resume`` session ID.

    Happens when sessions created on host are resumed inside Docker
    (or vice-versa) because working directories differ.
    """
    if not response.is_error:
        return False
    lower = (response.result or "").lower()
    return any(marker in lower for marker in _INVALID_SESSION_MARKERS)


def _needs_session_recovery(response: AgentResponse) -> bool:
    """Return True when the response warrants an automatic session reset + retry."""
    return _is_sigkill(response) or _is_invalid_session(response)


async def _recover_session(  # noqa: PLR0913
    orch: Orchestrator,
    chat_id: int,
    text: str,
    *,
    reason: str,
    model_override: str | None,
    streaming: bool,
    on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    on_tool_activity: Callable[[str], Awaitable[None]] | None = None,
    on_system_status: Callable[[str | None], Awaitable[None]] | None = None,
) -> tuple[AgentRequest, SessionData, AgentResponse]:
    """Reset the active provider session and retry once."""
    logger.warning("recovery.%s chat=%s action=retry", reason, chat_id)
    model_name = model_override or orch._config.model
    provider_name = orch._models.provider_for(model_name)
    await orch._process_registry.kill_all(chat_id)
    orch._process_registry.clear_abort(chat_id)
    await orch._sessions.reset_provider_session(chat_id, provider=provider_name, model=model_name)

    if reason == "invalid_session" and on_text_delta is not None:
        await on_text_delta(f"{_SESSION_RECOVERED_MSG}\n\n")
    elif on_system_status is not None:
        await on_system_status("recovering")

    request, session = await _prepare_normal(orch, chat_id, text, model_override=model_override)
    if streaming:
        response = await orch._cli_service.execute_streaming(
            request,
            on_text_delta=on_text_delta,
            on_tool_activity=on_tool_activity,
            on_system_status=on_system_status,
        )
    else:
        response = await orch._cli_service.execute(request)
    return request, session, response


def _request_target(orch: Orchestrator, request: AgentRequest) -> tuple[str, str]:
    """Return the effective model/provider target of a prepared request."""
    model_name = request.model_override or orch._config.model
    provider_name = request.provider_override or orch._models.provider_for(model_name)
    return model_name, provider_name


async def _gemini_missing_config_key_warning(
    orch: Orchestrator,
    request: AgentRequest,
) -> OrchestratorResult | None:
    """Warn when Gemini API-key mode is selected but Ductor config key is empty."""
    _model_name, provider_name = _request_target(orch, request)
    if provider_name != "gemini":
        return None

    api_key_mode = orch.gemini_api_key_mode
    if not api_key_mode:
        return None

    key = (orch._config.gemini_api_key or "").strip()
    if key and key.lower() not in NULLISH_TEXT_VALUES:
        return None

    return OrchestratorResult(
        text=(
            "Gemini is set to API-key auth mode, but `gemini_api_key` in "
            '`~/.ductor/config/config.json` is `"null"` or empty.\n'
            "Why this is required: when ductor calls Gemini CLI as an external process, "
            "Gemini CLI does not expose an internally entered API key to that caller.\n"
            "Set a real API key in `gemini_api_key` and restart `ductor`."
        ),
    )


async def normal(
    orch: Orchestrator,
    chat_id: int,
    text: str,
    *,
    model_override: str | None = None,
) -> OrchestratorResult:
    """Handle normal conversation with session resume."""
    logger.info("Normal flow starting")
    request, session = await _prepare_normal(orch, chat_id, text, model_override=model_override)
    warning = await _gemini_missing_config_key_warning(orch, request)
    if warning is not None:
        logger.warning("Gemini API-key mode without configured ductor key")
        return warning

    response = await orch._cli_service.execute(request)
    session_recovered = False
    if not orch._process_registry.was_aborted(chat_id) and _needs_session_recovery(response):
        session_recovered = _is_invalid_session(response)
        reason = "invalid_session" if session_recovered else "sigkill"
        request, session, response = await _recover_session(
            orch, chat_id, text, reason=reason, model_override=model_override, streaming=False
        )
    if orch._process_registry.was_aborted(chat_id):
        logger.info("Normal flow aborted by user")
        return OrchestratorResult(text="")
    if response.is_error:
        if _is_sigkill(response):
            logger.warning("recovery.sigkill chat=%s action=user-retry", chat_id)
            return OrchestratorResult(text=_SIGKILL_USER_MSG, stream_fallback=True)
        model_name, provider_name = _request_target(orch, request)
        return await _reset_on_error(
            orch,
            chat_id,
            model_name=model_name,
            provider_name=provider_name,
            cli_detail=response.result,
        )
    await _update_session(orch, session, response)
    logger.info("Normal flow completed")
    result = _finish_normal(response, session, orch._config.session_age_warning_hours)
    if session_recovered:
        result.text = f"{_SESSION_RECOVERED_MSG}\n\n{result.text}"
    return result


async def normal_streaming(  # noqa: PLR0913
    orch: Orchestrator,
    chat_id: int,
    text: str,
    *,
    model_override: str | None = None,
    on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    on_tool_activity: Callable[[str], Awaitable[None]] | None = None,
    on_system_status: Callable[[str | None], Awaitable[None]] | None = None,
) -> OrchestratorResult:
    """Handle normal conversation with streaming output."""
    logger.info("Streaming flow starting")
    request, session = await _prepare_normal(orch, chat_id, text, model_override=model_override)
    warning = await _gemini_missing_config_key_warning(orch, request)
    if warning is not None:
        logger.warning("Gemini API-key mode without configured ductor key")
        return warning

    response = await orch._cli_service.execute_streaming(
        request,
        on_text_delta=on_text_delta,
        on_tool_activity=on_tool_activity,
        on_system_status=on_system_status,
    )
    if not orch._process_registry.was_aborted(chat_id) and _needs_session_recovery(response):
        reason = "invalid_session" if _is_invalid_session(response) else "sigkill"
        request, session, response = await _recover_session(
            orch,
            chat_id,
            text,
            reason=reason,
            model_override=model_override,
            streaming=True,
            on_text_delta=on_text_delta,
            on_tool_activity=on_tool_activity,
            on_system_status=on_system_status,
        )
    if orch._process_registry.was_aborted(chat_id):
        logger.info("Streaming flow aborted by user")
        return OrchestratorResult(text="")
    if response.is_error:
        if _is_sigkill(response):
            logger.warning("recovery.sigkill chat=%s action=user-retry", chat_id)
            return OrchestratorResult(text=_SIGKILL_USER_MSG, stream_fallback=True)
        model_name, provider_name = _request_target(orch, request)
        return await _reset_on_error(
            orch,
            chat_id,
            model_name=model_name,
            provider_name=provider_name,
            cli_detail=response.result,
        )
    await _update_session(orch, session, response)
    logger.info("Streaming flow completed")
    return _finish_normal(response, session, orch._config.session_age_warning_hours)


def _session_age_note(session: SessionData, warning_hours: int) -> str:
    """Return a short age warning if the session exceeds the configured threshold."""
    if warning_hours <= 0:
        return ""
    try:
        created = datetime.fromisoformat(session.created_at)
    except (ValueError, TypeError):
        return ""
    age_hours = (datetime.now(UTC) - created).total_seconds() / 3600
    if age_hours < warning_hours:
        return ""
    # Show once every 10 messages to avoid spam.
    if session.message_count % 10 != 0:
        return ""
    age_label = f"{int(age_hours)}h" if age_hours < 48 else f"{int(age_hours / 24)}d"
    return f"\n\n---\n[Session is {age_label} old. Use /new for a fresh start.]"


def _finish_normal(
    response: AgentResponse,
    session: SessionData | None = None,
    warning_hours: int = 0,
) -> OrchestratorResult:
    """Post-processing for normal() and normal_streaming()."""
    if response.is_error:
        if response.timed_out:
            return OrchestratorResult(text="Agent timed out. Please try again.")
        if response.result.strip():
            return OrchestratorResult(text=f"Error: {response.result[:500]}")
        return OrchestratorResult(text="Error: check logs for details.")

    text = response.result
    if session:
        text += _session_age_note(session, warning_hours)

    return OrchestratorResult(
        text=text,
        stream_fallback=response.stream_fallback,
    )


# ---------------------------------------------------------------------------
# Dynamic agent roster
# ---------------------------------------------------------------------------


def _build_agent_roster(orch: Orchestrator) -> str:
    """Build a dynamic agent roster string from the supervisor's bus.

    Returns empty string if no supervisor or only one agent is online.
    """
    supervisor = getattr(orch, "_supervisor", None)
    if supervisor is None:
        return ""

    bus = getattr(supervisor, "_bus", None) or getattr(supervisor, "bus", None)
    if bus is None:
        return ""

    agents = bus.list_agents()
    if not agents or len(agents) <= 1:
        return ""

    own_name = orch._cli_service._config.agent_name
    peers = [a for a in agents if a != own_name]

    lines = [
        "## Active Agent Roster",
        f"Your name: `{own_name}`",
        f"Other agents online: {', '.join(f'`{a}`' for a in peers)}",
        "",
        "Use `ask_agent.py` (sync) or `ask_agent_async.py` (async) to communicate.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Heartbeat flow
# ---------------------------------------------------------------------------


def _strip_ack_token(text: str, token: str) -> str:
    """Remove leading/trailing ack token from response text."""
    stripped = text.strip()
    if stripped == token:
        return ""
    if stripped.startswith(token):
        stripped = stripped[len(token) :].strip()
    if stripped.endswith(token):
        stripped = stripped[: -len(token)].strip()
    return stripped


async def named_session_flow(
    orch: Orchestrator,
    chat_id: int,
    session_name: str,
    text: str,
) -> OrchestratorResult:
    """Handle a foreground follow-up to a named session (non-streaming)."""
    ns = orch._named_sessions.get(chat_id, session_name)
    if ns is None:
        return OrchestratorResult(text=f"Session '{session_name}' not found.")
    if ns.status == "ended":
        return OrchestratorResult(
            text=f"Session '{session_name}' has ended. Start a new one with /session."
        )
    if ns.status == "running":
        return OrchestratorResult(
            text=f"Session '{session_name}' is still processing. Wait or use /stop to cancel."
        )

    tag = f"**[{session_name} | {ns.provider}]**\n"
    ns.status = "running"
    request = AgentRequest(
        prompt=text,
        model_override=ns.model,
        provider_override=ns.provider,
        chat_id=chat_id,
        process_label=f"ns:{session_name}",
        resume_session=ns.session_id or None,
        timeout_seconds=orch._config.cli_timeout,
    )
    response = await orch._cli_service.execute(request)

    if orch._process_registry.was_aborted(chat_id):
        ns.status = "idle"
        return OrchestratorResult(text="")
    if response.is_error:
        ns.status = "idle"
        return OrchestratorResult(text=f"{tag}Error: {response.result[:500]}")

    orch._named_sessions.update_after_response(chat_id, session_name, response.session_id or "")
    return OrchestratorResult(text=f"{tag}{response.result}")


async def named_session_streaming(  # noqa: PLR0913
    orch: Orchestrator,
    chat_id: int,
    session_name: str,
    text: str,
    *,
    on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    on_tool_activity: Callable[[str], Awaitable[None]] | None = None,
    on_system_status: Callable[[str | None], Awaitable[None]] | None = None,
) -> OrchestratorResult:
    """Handle a foreground streaming follow-up to a named session."""
    ns = orch._named_sessions.get(chat_id, session_name)
    if ns is None:
        return OrchestratorResult(text=f"Session '{session_name}' not found.")
    if ns.status == "ended":
        return OrchestratorResult(
            text=f"Session '{session_name}' has ended. Start a new one with /session."
        )
    if ns.status == "running":
        return OrchestratorResult(
            text=f"Session '{session_name}' is still processing. Wait or use /stop to cancel."
        )

    tag = f"**[{session_name} | {ns.provider}]**\n"
    ns.status = "running"
    request = AgentRequest(
        prompt=text,
        model_override=ns.model,
        provider_override=ns.provider,
        chat_id=chat_id,
        process_label=f"ns:{session_name}",
        resume_session=ns.session_id or None,
        timeout_seconds=orch._config.cli_timeout,
    )

    tag_sent = False

    async def _tagged_text_delta(chunk: str) -> None:
        nonlocal tag_sent
        if on_text_delta is not None:
            if not tag_sent:
                await on_text_delta(tag)
                tag_sent = True
            await on_text_delta(chunk)

    response = await orch._cli_service.execute_streaming(
        request,
        on_text_delta=_tagged_text_delta,
        on_tool_activity=on_tool_activity,
        on_system_status=on_system_status,
    )

    if orch._process_registry.was_aborted(chat_id):
        ns.status = "idle"
        return OrchestratorResult(text="")
    if response.is_error:
        ns.status = "idle"
        return OrchestratorResult(text=f"{tag}Error: {response.result[:500]}")

    orch._named_sessions.update_after_response(chat_id, session_name, response.session_id or "")
    return OrchestratorResult(text=f"{tag}{response.result}")


# ---------------------------------------------------------------------------
# Heartbeat flow
# ---------------------------------------------------------------------------


async def heartbeat_flow(orch: Orchestrator, chat_id: int) -> str | None:
    """Run a heartbeat turn in the existing session.

    Returns the alert text if the model has something to say, or None if the
    response was a HEARTBEAT_OK acknowledgment. Does NOT update session state
    (last_active, message_count) for ack responses.
    """
    hb_cfg = orch._config.heartbeat
    req_model, req_provider = orch.resolve_runtime_target(orch._config.model)

    # Read-only check: never create/overwrite a session from the heartbeat path.
    session = await orch._sessions.get_active(chat_id)

    if not session or not session.session_id:
        logger.debug("Heartbeat skipped: no active session")
        return None

    set_log_context(session_id=session.session_id)

    if session.provider != req_provider:
        logger.debug(
            "Heartbeat skipped: provider mismatch session_provider=%s current=%s",
            session.provider,
            req_provider,
        )
        return None

    await orch._sessions.sync_session_target(session, model=req_model)

    idle_seconds = (datetime.now(UTC) - datetime.fromisoformat(session.last_active)).total_seconds()
    cooldown_seconds = hb_cfg.cooldown_minutes * 60
    if idle_seconds < cooldown_seconds:
        logger.debug(
            "Heartbeat skipped: idle=%ds cooldown=%ds",
            int(idle_seconds),
            cooldown_seconds,
        )
        return None

    request = AgentRequest(
        prompt=hb_cfg.prompt,
        model_override=req_model,
        provider_override=req_provider,
        chat_id=chat_id,
        resume_session=session.session_id,
        timeout_seconds=orch._config.cli_timeout,
    )

    response = await orch._cli_service.execute(request)
    if response.is_error:
        logger.warning("Heartbeat CLI error result=%s", response.result[:200])
        return None

    alert_text = _strip_ack_token(response.result, hb_cfg.ack_token)
    if not alert_text:
        logger.info("Heartbeat OK (suppressed)")
        return None

    await _update_session(orch, session, response)
    logger.info("Heartbeat alert chars=%d", len(alert_text))
    return alert_text
