"""Core conversation flows: normal message handling with session management."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ductor_bot.cli.context_builder import ContextBudget, ContextBuilder
from ductor_bot.cli.timeout_controller import TimeoutConfig as TCConfig
from ductor_bot.cli.timeout_controller import TimeoutController
from ductor_bot.cli.types import AgentRequest, AgentResponse
from ductor_bot.config import NULLISH_TEXT_VALUES, resolve_timeout
from ductor_bot.i18n import t
from ductor_bot.infra.inflight import InflightTurn
from ductor_bot.log_context import set_log_context
from ductor_bot.orchestrator.hooks import HookContext
from ductor_bot.orchestrator.registry import OrchestratorResult
from ductor_bot.runtime.state import MessageRepository, ProcessRepository
from ductor_bot.session import SessionData, SessionKey
from ductor_bot.text.response_format import session_error_text, timeout_error_text
from ductor_bot.workspace.loader import load_soul, load_task_state, read_mainmemory

if TYPE_CHECKING:
    from ductor_bot.orchestrator.core import Orchestrator
    from ductor_bot.session.named import NamedSession

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StreamingCallbacks:
    """Bundle of optional streaming callbacks passed through flow functions."""

    on_text_delta: Callable[[str], Awaitable[None]] | None = field(default=None)
    on_tool_activity: Callable[[str], Awaitable[None]] | None = field(default=None)
    on_system_status: Callable[[str | None], Awaitable[None]] | None = field(default=None)
    on_compact_boundary: Callable[[], Awaitable[None]] | None = field(default=None)


def _make_timeout_controller(orch: Orchestrator, kind: str) -> TimeoutController | None:
    """Create a TimeoutController when extended timeout features are configured."""
    cfg = orch._config.timeouts
    if not cfg.warning_intervals and not cfg.extend_on_activity:
        return None
    return TimeoutController(
        TCConfig(
            timeout_seconds=resolve_timeout(orch._config, kind),
            warning_intervals=cfg.warning_intervals,
            extend_on_activity=cfg.extend_on_activity,
            activity_extension=cfg.activity_extension,
            max_extensions=cfg.max_extensions,
        ),
    )


def _state_repos(orch: Orchestrator) -> tuple[ProcessRepository | None, MessageRepository | None]:
    """Return the optional runtime-state repositories if they are wired in."""
    return getattr(orch, "_process_repo", None), getattr(orch, "_message_repo", None)


async def _fetch_soul(orch: Orchestrator) -> str | None:
    """Read the SOUL context if available."""
    soul = await asyncio.to_thread(load_soul, orch.paths)
    if not soul or not soul.strip():
        return None
    return soul.strip()


def _append_prompts(*parts: str | None) -> str | None:
    """Join optional system-prompt sections into one appended prompt."""
    rendered = [part.strip() for part in parts if part and part.strip()]
    if not rendered:
        return None
    return "\n\n".join(rendered)


def _build_agent_role_prompt(orch: Orchestrator) -> str | None:
    """Return a short stable prompt describing the current agent's role."""
    role = orch._config.role.strip()
    role_description = orch._config.role_description.strip()
    if not role and not role_description:
        return None
    lines = ["## Agent Role"]
    if role:
        lines.append(f"Primary role: {role}")
    if role_description:
        lines.append(role_description)
    return "\n".join(lines)


async def _apply_runtime_compression(
    orch: Orchestrator,
    session_storage_key: str,
    prompt: str,
    *,
    current_label: str,
) -> str:
    """Prepend compressed runtime context when the compressor is enabled."""
    compressor = getattr(orch, "_context_compressor", None)
    if compressor is None:
        return prompt
    prefix = await asyncio.to_thread(compressor.build_prompt_prefix, session_storage_key)
    if not prefix:
        return prompt
    return f"{prefix}\n\n## {current_label}\n{prompt}"


async def _record_process_start(
    orch: Orchestrator,
    *,
    process_label: str,
    key: SessionKey,
    provider: str,
    model: str,
) -> int | None:
    """Persist a process-start row when runtime state is enabled."""
    process_repo, _message_repo = _state_repos(orch)
    if process_repo is None:
        return None
    try:
        return await asyncio.to_thread(
            process_repo.create,
            process_label,
            key.chat_id,
            topic_id=key.topic_id,
            provider=provider,
            model=model,
            session_storage_key=key.storage_key,
        )
    except Exception:
        logger.exception("Failed to record process start label=%s chat=%s", process_label, key.chat_id)
        return None


async def _record_process_finish(orch: Orchestrator, process_id: int | None, exit_code: int) -> None:
    """Persist process completion without affecting the main flow."""
    process_repo, _message_repo = _state_repos(orch)
    if process_repo is None or process_id is None:
        return
    with contextlib.suppress(Exception):
        await asyncio.to_thread(process_repo.finish, process_id, exit_code=exit_code)


# ruff: noqa: PLR0913
async def _record_message(
    orch: Orchestrator,
    key: SessionKey,
    *,
    role: str,
    content_text: str,
    source: str,
    process_id: int | None = None,
    token_count: int = 0,
    cost_usd: float = 0.0,
    content_json: dict[str, object] | None = None,
) -> None:
    """Append a message row when runtime state is enabled."""
    _process_repo, message_repo = _state_repos(orch)
    if message_repo is None:
        return
    try:
        await asyncio.to_thread(
            message_repo.append,
            key.storage_key,
            role,
            content_text,
            source=source,
            content_json=content_json or {},
            token_count=token_count,
            cost_usd=cost_usd,
            process_id=process_id,
        )
    except Exception:
        logger.exception("Failed to record message source=%s chat=%s", source, key.chat_id)


def _response_exit_code(response: AgentResponse, *, aborted: bool = False) -> int:
    """Map a response to a process exit code for runtime-state tracking."""
    if aborted:
        return 130
    if response.timed_out:
        return 124
    if response.is_error:
        return 1
    return 0


async def _prepare_normal(
    orch: Orchestrator,
    key: SessionKey,
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
        key,
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

    # 1. Initialize Governed ContextBuilder
    builder = ContextBuilder(ContextBudget(total_limit=128000))

    # 2. Gather Context Components
    soul = await _fetch_soul(orch)

    # Roster (only for new sessions)
    roster = _build_agent_roster(orch) if is_new else None

    # Main Memory (Fragments)
    main_memory = await asyncio.to_thread(
        read_mainmemory,
        orch.paths,
        fragment_repo=getattr(orch, "_memory_fragment_repo", None),
        agent_name=orch._cli_service._config.agent_name
    )

    task_state = await asyncio.to_thread(
        load_task_state,
        orch.paths,
        storage_key=key.storage_key,
        task_state_repo=getattr(orch, "_task_state_repo", None),
    )

    # 3. Dynamic Hints (Applied directly to prompt via Hooks or Builder)
    hook_ctx = HookContext(
        chat_id=key.chat_id,
        message_count=session.message_count,
        is_new_session=is_new,
        provider=req_provider,
        model=req_model,
    )
    raw_prompt = orch._hook_registry.apply(text, hook_ctx)

    # Apply runtime compression if not new
    if not is_new:
        raw_prompt = await _apply_runtime_compression(
            orch,
            key.storage_key,
            raw_prompt,
            current_label="CURRENT USER MESSAGE",
        )

    # 4. Assemble Governed Request
    request = builder.build_request(
        user_prompt=raw_prompt,
        soul=soul,
        main_memory=main_memory,
        task_state=task_state,
        session=session,
        model=req_model,
        provider=req_provider,
    )

    # Carry forward system settings
    request = replace(
        request,
        timeout_seconds=resolve_timeout(orch._config, "normal"),
        timeout_controller=_make_timeout_controller(orch, "normal"),
        append_system_prompt=_append_prompts(_build_agent_role_prompt(orch), roster),
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

    # Cognitive Maintenance: Trigger autonomous memory synthesis every 20 messages
    if session.message_count > 0 and session.message_count % 20 == 0:
        logger.info("Triggering autonomous memory synthesis for chat=%s", session.chat_id)
        if orch.bg_observer:
            from ductor_bot.background import BackgroundSubmit
            from ductor_bot.cli.param_resolver import resolve_cli_config

            prompt = _build_memory_synthesis_prompt()

            sub = BackgroundSubmit(
                chat_id=session.chat_id,
                prompt=prompt,
                message_id=0,  # Synthesized task
                thread_id=session.topic_id,
                session_name=f"memory_synthesis_{session.session_id[:8]}",
                resume_session_id=session.session_id,
                provider_override=session.provider,
                model_override=session.model,
                silent=True,
            )
            exec_config = resolve_cli_config(orch._config, orch._observers.codex_cache)
            orch.bg_observer.submit(sub, exec_config)


def _build_memory_synthesis_prompt() -> str:
    """Return the autonomous maintenance prompt used for periodic memory synthesis."""
    return (
        "SYSTEM INSTRUCTION: You are performing Cognitive Maintenance.\n"
        "You are resuming the current conversation session so you can review the recent history.\n"
        "Do not reply conversationally to the user. Silently update durable memory instead.\n\n"
        "Tasks:\n"
        "1. Distill any new persistent user preferences, durable facts, architectural decisions, "
        "or unresolved follow-up obligations.\n"
        "2. Update `workspace/memory_system/MAINMEMORY.md` directly when the memory should live "
        "with this agent.\n"
        "3. Update cross-agent facts with "
        '`python3 tools/agent_tools/edit_shared_knowledge.py --append "..."` only when the fact '
        "belongs in shared knowledge.\n"
        "4. If you need historical recall across sessions or agents, run "
        '`python3 tools/agent_tools/search_past_sessions.py "keyword"`.\n'
        "5. If you need a surgical fragment edit after identifying a fragment ULID, run "
        "`python3 tools/agent_tools/memory_atomic_op.py --agent main --ulid <ULID> "
        '--action patch --body "..."` or `--action delete`.\n\n'
        "Rules:\n"
        "- Keep updates concise and durable.\n"
        "- Do not duplicate ephemeral conversation details.\n"
        "- Do not send a user-facing summary.\n"
        "- Finish after the memory updates are complete."
    )


async def _reset_on_error(
    orch: Orchestrator,
    key: SessionKey,
    *,
    model_name: str,
    provider_name: str,
    cli_detail: str = "",
) -> OrchestratorResult:
    """Kill processes, preserve session, return user-facing error."""
    await orch._process_registry.kill_all(key.chat_id, topic_id=key.topic_id)
    logger.warning("Session error preserved model=%s provider=%s", model_name, provider_name)
    return OrchestratorResult(
        text=session_error_text(model_name, cli_detail),
    )


async def _handle_timeout(
    orch: Orchestrator,
    key: SessionKey,
    session: SessionData,
    response: AgentResponse,
    request: AgentRequest,
) -> OrchestratorResult:
    """Preserve session after timeout and return a clear user-facing message.

    Unlike ``_reset_on_error``, this persists the session_id from the response
    so that the next user message can ``--resume`` the timed-out session.
    """
    model_name, _provider_name = _request_target(orch, request)
    await orch._process_registry.kill_all(key.chat_id, topic_id=key.topic_id)

    # Persist the session_id captured from SystemInitEvent so resume works.
    if response.session_id and response.session_id != session.session_id:
        logger.info(
            "Timeout: preserving session_id %s for resume",
            response.session_id[:8],
        )
        session.session_id = response.session_id
    await orch._sessions.update_session(
        session, cost_usd=response.cost_usd, tokens=response.total_tokens
    )

    timeout_s = request.timeout_seconds or 0
    logger.warning("Session timed out after %.0fs model=%s", timeout_s, model_name)
    return OrchestratorResult(text=timeout_error_text(model_name, timeout_s))


def _sigkill_user_msg() -> str:
    return t("session.sigkill")


def _session_recovered_msg() -> str:
    return t("session.recovered")


def _session_recovery_failed_msg() -> str:
    return t("session.recovery_failed")


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


@dataclass(slots=True)
class _RecoveryContext:
    """Context for session recovery."""

    reason: str
    model_override: str | None
    streaming: bool = False
    cbs: StreamingCallbacks = field(default_factory=StreamingCallbacks)


@dataclass(slots=True)
class _RecoveryOutcome:
    """Result of the one-shot session-recovery gate.

    ``retry_performed`` is True when a fresh-session retry actually ran.
    ``session_recovered`` is True only when that retry succeeded after an
    invalid-session rejection (used to prepend the user-facing notice).
    ``failed_result`` is non-None when the retry still returned stale-session
    and callers must short-circuit with it (cap at one retry).
    """

    request: AgentRequest
    session: SessionData
    response: AgentResponse
    retry_performed: bool
    session_recovered: bool
    failed_result: OrchestratorResult | None


async def _maybe_recover_session(
    orch: Orchestrator,
    key: SessionKey,
    text: str,
    request: AgentRequest,
    session: SessionData,
    response: AgentResponse,
    *,
    model_override: str | None,
    streaming: bool = False,
    cbs: StreamingCallbacks | None = None,
) -> _RecoveryOutcome:
    """Run the one-shot recovery gate shared by normal() and normal_streaming().

    If the CLI reported a recoverable failure (SIGKILL or stale session) AND
    the user did not abort/interrupt, retry exactly once with a fresh session.
    If the retry ALSO returns stale-session, emit a clear error and surface
    ``failed_result`` so callers can short-circuit (cap at one retry).
    """
    _reg = orch._process_registry
    if (
        _reg.was_aborted(key.chat_id, key.topic_id)
        or _reg.was_interrupted(key.chat_id, key.topic_id)
        or not _needs_session_recovery(response)
    ):
        return _RecoveryOutcome(
            request=request,
            session=session,
            response=response,
            retry_performed=False,
            session_recovered=False,
            failed_result=None,
        )

    session_recovered = _is_invalid_session(response)
    reason = "invalid_session" if session_recovered else "sigkill"
    ctx = _RecoveryContext(
        reason=reason,
        model_override=model_override,
        streaming=streaming,
        cbs=cbs or StreamingCallbacks(),
    )
    request, session, response = await _recover_session(orch, key, text, ctx)
    failed_result: OrchestratorResult | None = None
    if _is_invalid_session(response):
        logger.error("Session recovery failed on retry for chat_id=%s", key.chat_id)
        failed_result = OrchestratorResult(text=_session_recovery_failed_msg())
    return _RecoveryOutcome(
        request=request,
        session=session,
        response=response,
        retry_performed=True,
        session_recovered=session_recovered,
        failed_result=failed_result,
    )


async def _recover_session(
    orch: Orchestrator,
    key: SessionKey,
    text: str,
    ctx: _RecoveryContext,
) -> tuple[AgentRequest, SessionData, AgentResponse]:
    """Reset the active provider session and retry once.

    When callbacks are set in *ctx.cbs*, the retry uses streaming execution.
    """
    logger.warning("recovery.%s chat=%s action=retry", ctx.reason, key.chat_id)
    model_name = ctx.model_override or orch._config.model
    provider_name = orch.models.provider_for(model_name)
    await orch._process_registry.kill_all(key.chat_id, topic_id=key.topic_id)
    orch._process_registry.clear_abort(key.chat_id, key.topic_id)
    await orch._sessions.reset_provider_session(key, provider=provider_name, model=model_name)

    cb = ctx.cbs
    if ctx.reason == "invalid_session" and cb.on_text_delta is not None:
        await cb.on_text_delta(f"{_session_recovered_msg()}\n\n")
    elif cb.on_system_status is not None:
        await cb.on_system_status("recovering")

    request, session = await _prepare_normal(orch, key, text, model_override=ctx.model_override)
    if ctx.streaming:
        response = await orch._cli_service.execute_streaming(
            request,
            on_text_delta=cb.on_text_delta,
            on_tool_activity=cb.on_tool_activity,
            on_system_status=cb.on_system_status,
        )
    else:
        response = await orch._cli_service.execute(request)
    return request, session, response


def _request_target(orch: Orchestrator, request: AgentRequest) -> tuple[str, str]:
    """Return the effective model/provider target of a prepared request."""
    model_name = request.model_override or orch._config.model
    provider_name = request.provider_override or orch.models.provider_for(model_name)
    return model_name, provider_name


def _begin_inflight(
    orch: Orchestrator,
    request: AgentRequest,
    session: SessionData | NamedSession,
    *,
    is_recovery: bool = False,
    path: str = "normal",
) -> None:
    """Record an in-flight turn for crash recovery."""
    from dataclasses import asdict

    model_name, provider_name = _request_target(orch, request)
    request_dict = {k: v for k, v in asdict(request).items() if k != "timeout_controller"}

    orch._inflight_tracker.begin(
        InflightTurn(
            chat_id=request.chat_id,
            provider=provider_name,
            model=model_name,
            session_id=session.session_id or "",
            prompt_preview=request.prompt[:100],
            started_at=datetime.now(UTC).isoformat(),
            is_recovery=is_recovery,
            path=path,
            transport=request.transport,
            topic_id=request.topic_id,
            request=request_dict,
        )
    )


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

    return OrchestratorResult(text=t("gemini.missing_key"))


async def normal(
    orch: Orchestrator,
    key: SessionKey,
    text: str,
    *,
    model_override: str | None = None,
    is_recovery: bool = False,
) -> OrchestratorResult:
    """Handle normal conversation with session resume."""
    logger.info("Normal flow starting")
    request, session = await _prepare_normal(orch, key, text, model_override=model_override)
    warning = await _gemini_missing_config_key_warning(orch, request)
    if warning is not None:
        logger.warning("Gemini API-key mode without configured ductor key")
        return warning

    _begin_inflight(orch, request, session, is_recovery=is_recovery)
    model_name, provider_name = _request_target(orch, request)
    process_id = await _record_process_start(
        orch,
        process_label="normal",
        key=key,
        provider=provider_name,
        model=model_name,
    )
    await _record_message(
        orch,
        key,
        role="user",
        content_text=text,
        source="normal_prompt",
        process_id=process_id,
        content_json={
            "flow": "normal",
            "model": model_name,
            "provider": provider_name,
        },
    )
    exit_code = 1
    try:
        response = await orch._cli_service.execute(request)
        outcome = await _maybe_recover_session(
            orch, key, text, request, session, response, model_override=model_override
        )
        if outcome.failed_result is not None:
            return outcome.failed_result
        request, session, response = outcome.request, outcome.session, outcome.response
        session_recovered = outcome.session_recovered

        _reg = orch._process_registry
        if _reg.was_aborted(key.chat_id, key.topic_id) or _reg.was_interrupted(
            key.chat_id, key.topic_id
        ):
            _reg.clear_interrupt(key.chat_id, key.topic_id)
            logger.info("Normal flow aborted/interrupted by user")
            exit_code = _response_exit_code(response, aborted=True)
            return OrchestratorResult(text="")
        await _record_message(
            orch,
            key,
            role="assistant",
            content_text=response.result,
            source="normal_result",
            process_id=process_id,
            token_count=response.total_tokens,
            cost_usd=response.cost_usd,
            content_json={
                "flow": "normal",
                "is_error": response.is_error,
                "timed_out": response.timed_out,
                "session_id": response.session_id or "",
            },
        )
        if response.timed_out:
            exit_code = _response_exit_code(response)
            return await _handle_timeout(orch, key, session, response, request)
        if response.is_error:
            if _is_sigkill(response):
                logger.warning("recovery.sigkill chat=%s action=user-retry", key.chat_id)
                exit_code = _response_exit_code(response)
                return OrchestratorResult(text=_sigkill_user_msg(), stream_fallback=True)
            model_name, provider_name = _request_target(orch, request)
            exit_code = _response_exit_code(response)
            return await _reset_on_error(
                orch,
                key,
                model_name=model_name,
                provider_name=provider_name,
                cli_detail=response.result,
            )
        await _update_session(orch, session, response)
        if orch._memory_flusher:
            await orch._memory_flusher.maybe_flush(key, session)
        logger.info("Normal flow completed")
        req_model, _prov = _request_target(orch, request)
        exit_code = _response_exit_code(response)
        result = _finish_normal(
            response, session, orch._config.session_age_warning_hours, model_name=req_model
        )
        if session_recovered:
            result.text = f"{_session_recovered_msg()}\n\n{result.text}"
        return result
    finally:
        await _record_process_finish(orch, process_id, exit_code)
        orch._inflight_tracker.complete(key)


async def normal_streaming(
    orch: Orchestrator,
    key: SessionKey,
    text: str,
    *,
    model_override: str | None = None,
    cbs: StreamingCallbacks | None = None,
) -> OrchestratorResult:
    """Handle normal conversation with streaming output."""
    logger.info("Streaming flow starting")
    request, session = await _prepare_normal(orch, key, text, model_override=model_override)
    warning = await _gemini_missing_config_key_warning(orch, request)
    if warning is not None:
        logger.warning("Gemini API-key mode without configured ductor key")
        return warning

    _begin_inflight(orch, request, session, is_recovery=False)
    model_name, provider_name = _request_target(orch, request)
    process_id = await _record_process_start(
        orch,
        process_label="normal_streaming",
        key=key,
        provider=provider_name,
        model=model_name,
    )
    await _record_message(
        orch,
        key,
        role="user",
        content_text=text,
        source="normal_stream_prompt",
        process_id=process_id,
        content_json={
            "flow": "normal_streaming",
            "model": model_name,
            "provider": provider_name,
        },
    )
    exit_code = 1
    try:
        cb = cbs or StreamingCallbacks()

        async def _on_boundary() -> None:
            if orch._memory_flusher:
                orch._memory_flusher.mark_boundary(key)

        response = await orch._cli_service.execute_streaming(
            request,
            on_text_delta=cb.on_text_delta,
            on_tool_activity=cb.on_tool_activity,
            on_system_status=cb.on_system_status,
            on_compact_boundary=_on_boundary if cb.on_compact_boundary is None else cb.on_compact_boundary,
        )
        outcome = await _maybe_recover_session(
            orch,
            key,
            text,
            request,
            session,
            response,
            model_override=model_override,
            streaming=True,
            cbs=cb,
        )
        if outcome.failed_result is not None:
            return outcome.failed_result
        request, session, response = outcome.request, outcome.session, outcome.response

        _reg = orch._process_registry
        if (
            not _reg.was_aborted(key.chat_id, key.topic_id)
            and not _reg.was_interrupted(key.chat_id, key.topic_id)
            and _needs_session_recovery(response)
        ):
            # This block is now partially redundant due to _maybe_recover_session,
            # but _maybe_recover_session handles the one-shot retry.
            # If we are here, it means _maybe_recover_session decided NOT to retry
            # (e.g. was_aborted returned True) or it already retried and we have the final response.
            pass
        if _reg.was_aborted(key.chat_id, key.topic_id) or _reg.was_interrupted(
            key.chat_id, key.topic_id
        ):
            _reg.clear_interrupt(key.chat_id, key.topic_id)
            logger.info("Streaming flow aborted/interrupted by user")
            exit_code = _response_exit_code(response, aborted=True)
            return OrchestratorResult(text="")
        await _record_message(
            orch,
            key,
            role="assistant",
            content_text=response.result,
            source="normal_stream_result",
            process_id=process_id,
            token_count=response.total_tokens,
            cost_usd=response.cost_usd,
            content_json={
                "flow": "normal_streaming",
                "is_error": response.is_error,
                "timed_out": response.timed_out,
                "session_id": response.session_id or "",
            },
        )
        if response.timed_out:
            exit_code = _response_exit_code(response)
            return await _handle_timeout(orch, key, session, response, request)
        if response.is_error:
            if _is_sigkill(response):
                logger.warning("recovery.sigkill chat=%s action=user-retry", key.chat_id)
                exit_code = _response_exit_code(response)
                return OrchestratorResult(text=_sigkill_user_msg(), stream_fallback=True)
            model_name, provider_name = _request_target(orch, request)
            exit_code = _response_exit_code(response)
            return await _reset_on_error(
                orch,
                key,
                model_name=model_name,
                provider_name=provider_name,
                cli_detail=response.result,
            )
        await _update_session(orch, session, response)
        if orch._memory_flusher:
            await orch._memory_flusher.maybe_flush(key, session)
        logger.info("Streaming flow completed")
        req_model, _prov = _request_target(orch, request)
        exit_code = _response_exit_code(response)
        return _finish_normal(
            response, session, orch._config.session_age_warning_hours, model_name=req_model
        )
    finally:
        await _record_process_finish(orch, process_id, exit_code)
        orch._inflight_tracker.complete(key)


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
    return "\n\n---\n" + t("session.age_warning", age=age_label)


def _finish_normal(
    response: AgentResponse,
    session: SessionData | None = None,
    warning_hours: int = 0,
    *,
    model_name: str = "",
) -> OrchestratorResult:
    """Post-processing for normal() and normal_streaming()."""
    if response.is_error:
        if response.timed_out:
            return OrchestratorResult(text=t("timeout.generic"))
        if response.result.strip():
            return OrchestratorResult(text=t("error.generic", detail=response.result[:500]))
        return OrchestratorResult(text=t("error.check_logs"))

    text = response.result
    if session:
        text += _session_age_note(session, warning_hours)

    return OrchestratorResult(
        text=text,
        stream_fallback=response.stream_fallback,
        model_name=model_name,
        total_tokens=response.total_tokens,
        input_tokens=response.input_tokens,
        cost_usd=response.cost_usd,
        duration_ms=response.duration_ms,
    )


# ---------------------------------------------------------------------------
# Dynamic agent roster
# ---------------------------------------------------------------------------


def _build_agent_roster(orch: Orchestrator) -> str:
    """Build a dynamic agent roster string from the supervisor's bus.

    Returns empty string if no supervisor or only one agent is online.
    """
    supervisor = orch._supervisor
    if supervisor is None:
        return ""

    bus = supervisor.bus
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
    key: SessionKey,
    session_name: str,
    text: str,
) -> OrchestratorResult:
    """Handle a foreground follow-up to a named session (non-streaming)."""
    ns = orch._named_sessions.get(key.chat_id, session_name)
    if ns is None:
        return OrchestratorResult(text=t("session.not_found", name=session_name))
    if ns.status == "ended":
        return OrchestratorResult(text=t("session.ended", name=session_name))
    if ns.status == "running":
        return OrchestratorResult(text=t("session.still_running", name=session_name))

    tag = f"**[{session_name} | {ns.provider}]**\n"
    orch._named_sessions.mark_running(key.chat_id, session_name, text)
    prompt = await _apply_runtime_compression(
        orch,
        key.storage_key,
        text,
        current_label=f"CURRENT NAMED SESSION MESSAGE ({session_name})",
    )
    soul = await _fetch_soul(orch)
    process_id = await _record_process_start(
        orch,
        process_label=f"ns:{session_name}",
        key=key,
        provider=ns.provider,
        model=ns.model,
    )
    await _record_message(
        orch,
        key,
        role="user",
        content_text=text,
        source="named_session_prompt",
        process_id=process_id,
        content_json={
            "flow": "named_session",
            "session_name": session_name,
            "provider": ns.provider,
            "model": ns.model,
        },
    )
    exit_code = 1
    request = AgentRequest(
        prompt=prompt,
        append_system_prompt=_append_prompts(_build_agent_role_prompt(orch), soul),
        model_override=ns.model,
        provider_override=ns.provider,
        transport=key.transport,
        chat_id=key.chat_id,
        topic_id=key.topic_id,
        process_label=f"ns:{session_name}",
        resume_session=ns.session_id or None,
        timeout_seconds=resolve_timeout(orch._config, "normal"),
        timeout_controller=_make_timeout_controller(orch, "normal"),
    )
    _begin_inflight(orch, request, ns, path="background")
    try:
        response = await orch._cli_service.execute(request)

        _reg = orch._process_registry
        if _reg.was_aborted(key.chat_id, key.topic_id) or _reg.was_interrupted(
            key.chat_id, key.topic_id
        ):
            _reg.clear_interrupt(key.chat_id, key.topic_id)
            ns.status = "idle"
            exit_code = 130
            return OrchestratorResult(text="")
        if response.is_error:
            ns.status = "idle"
            await _record_message(
                orch,
                key,
                role="assistant",
                content_text=response.result,
                source="named_session_result",
                process_id=process_id,
                token_count=response.total_tokens,
                cost_usd=response.cost_usd,
                content_json={
                    "flow": "named_session",
                    "session_name": session_name,
                    "is_error": True,
                    "session_id": response.session_id or "",
                },
            )
            exit_code = _response_exit_code(response)
            return OrchestratorResult(text=f"{tag}{t('error.generic', detail=response.result[:500])}")

        await _record_message(
            orch,
            key,
            role="assistant",
            content_text=response.result,
            source="named_session_result",
            process_id=process_id,
            token_count=response.total_tokens,
            cost_usd=response.cost_usd,
            content_json={
                "flow": "named_session",
                "session_name": session_name,
                "is_error": False,
                "session_id": response.session_id or "",
            },
        )
        orch._named_sessions.update_after_response(key.chat_id, session_name, response.session_id or "")
        exit_code = _response_exit_code(response)
        return OrchestratorResult(text=f"{tag}{response.result}")
    finally:
        await _record_process_finish(orch, process_id, exit_code)
        orch._inflight_tracker.complete(key)


async def named_session_streaming(
    orch: Orchestrator,
    key: SessionKey,
    session_name: str,
    text: str,
    *,
    cbs: StreamingCallbacks | None = None,
) -> OrchestratorResult:
    """Handle a foreground streaming follow-up to a named session."""
    ns = orch._named_sessions.get(key.chat_id, session_name)
    if ns is None:
        return OrchestratorResult(text=t("session.not_found", name=session_name))
    if ns.status == "ended":
        return OrchestratorResult(text=t("session.ended", name=session_name))
    if ns.status == "running":
        return OrchestratorResult(text=t("session.still_running", name=session_name))

    cb = cbs or StreamingCallbacks()
    tag = f"**[{session_name} | {ns.provider}]**\n"
    orch._named_sessions.mark_running(key.chat_id, session_name, text)
    prompt = await _apply_runtime_compression(
        orch,
        key.storage_key,
        text,
        current_label=f"CURRENT NAMED SESSION MESSAGE ({session_name})",
    )
    soul = await _fetch_soul(orch)
    process_id = await _record_process_start(
        orch,
        process_label=f"ns:{session_name}:streaming",
        key=key,
        provider=ns.provider,
        model=ns.model,
    )
    await _record_message(
        orch,
        key,
        role="user",
        content_text=text,
        source="named_session_stream_prompt",
        process_id=process_id,
        content_json={
            "flow": "named_session_streaming",
            "session_name": session_name,
            "provider": ns.provider,
            "model": ns.model,
        },
    )
    exit_code = 1
    request = AgentRequest(
        prompt=prompt,
        append_system_prompt=_append_prompts(_build_agent_role_prompt(orch), soul),
        model_override=ns.model,
        provider_override=ns.provider,
        transport=key.transport,
        chat_id=key.chat_id,
        topic_id=key.topic_id,
        process_label=f"ns:{session_name}",
        resume_session=ns.session_id or None,
        timeout_seconds=resolve_timeout(orch._config, "normal"),
        timeout_controller=_make_timeout_controller(orch, "normal"),
    )

    tag_sent = False

    async def _tagged_text_delta(chunk: str) -> None:
        nonlocal tag_sent
        if cb.on_text_delta is not None:
            if not tag_sent:
                await cb.on_text_delta(tag)
                tag_sent = True
            await cb.on_text_delta(chunk)
    _begin_inflight(orch, request, ns, path="background")
    try:
        response = await orch._cli_service.execute_streaming(
            request,
            on_text_delta=_tagged_text_delta,
            on_tool_activity=cb.on_tool_activity,
            on_system_status=cb.on_system_status,
        )

        _reg2 = orch._process_registry
        if _reg2.was_aborted(key.chat_id, key.topic_id) or _reg2.was_interrupted(
            key.chat_id, key.topic_id
        ):
            _reg2.clear_interrupt(key.chat_id, key.topic_id)
            ns.status = "idle"
            exit_code = 130
            return OrchestratorResult(text="")
        if response.is_error:
            ns.status = "idle"
            await _record_message(
                orch,
                key,
                role="assistant",
                content_text=response.result,
                source="named_session_stream_result",
                process_id=process_id,
                token_count=response.total_tokens,
                cost_usd=response.cost_usd,
                content_json={
                    "flow": "named_session_streaming",
                    "session_name": session_name,
                    "is_error": True,
                    "session_id": response.session_id or "",
                },
            )
            exit_code = _response_exit_code(response)
            return OrchestratorResult(text=f"{tag}{t('error.generic', detail=response.result[:500])}")

        await _record_message(
            orch,
            key,
            role="assistant",
            content_text=response.result,
            source="named_session_stream_result",
            process_id=process_id,
            token_count=response.total_tokens,
            cost_usd=response.cost_usd,
            content_json={
                "flow": "named_session_streaming",
                "session_name": session_name,
                "is_error": False,
                "session_id": response.session_id or "",
            },
        )
        orch._named_sessions.update_after_response(key.chat_id, session_name, response.session_id or "")
        exit_code = _response_exit_code(response)
        return OrchestratorResult(text=f"{tag}{response.result}")
    finally:
        await _record_process_finish(orch, process_id, exit_code)
        orch._inflight_tracker.complete(key)


# ---------------------------------------------------------------------------
# Heartbeat flow
# ---------------------------------------------------------------------------


async def heartbeat_flow(
    orch: Orchestrator,
    key: SessionKey,
    *,
    prompt: str | None = None,
    ack_token: str | None = None,
) -> str | None:
    """Run a heartbeat turn in the existing session.

    Returns the alert text if the model has something to say, or None if the
    response was a HEARTBEAT_OK acknowledgment. Does NOT update session state
    (last_active, message_count) for ack responses.

    *prompt* and *ack_token* override the global heartbeat config when set
    (used by per-target overrides in HeartbeatObserver).
    """
    hb_cfg = orch._config.heartbeat
    effective_prompt = prompt or hb_cfg.prompt
    effective_ack = ack_token or hb_cfg.ack_token
    req_model, req_provider = orch.resolve_runtime_target(orch._config.model)

    # Read-only check: never create/overwrite a session from the heartbeat path.
    session = await orch._sessions.get_active(key)

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
        prompt=effective_prompt,
        model_override=req_model,
        provider_override=req_provider,
        transport=key.transport,
        chat_id=key.chat_id,
        topic_id=key.topic_id,
        resume_session=session.session_id,
        timeout_seconds=resolve_timeout(orch._config, "normal"),
        timeout_controller=_make_timeout_controller(orch, "normal"),
    )
    process_id = await _record_process_start(
        orch,
        process_label="heartbeat",
        key=key,
        provider=req_provider,
        model=req_model,
    )
    await _record_message(
        orch,
        key,
        role="system",
        content_text=effective_prompt,
        source="heartbeat_prompt",
        process_id=process_id,
        content_json={
            "flow": "heartbeat",
            "ack_token": effective_ack,
            "provider": req_provider,
            "model": req_model,
        },
    )
    _begin_inflight(orch, request, session, path="heartbeat")
    exit_code = 0
    try:
        response = await orch._cli_service.execute(request)
        if response.is_error:
            logger.warning("Heartbeat CLI error result=%s", response.result[:200])
            await _record_message(
                orch,
                key,
                role="assistant",
                content_text=response.result,
                source="heartbeat_result",
                process_id=process_id,
                token_count=response.total_tokens,
                cost_usd=response.cost_usd,
                content_json={
                    "flow": "heartbeat",
                    "is_error": True,
                    "session_id": response.session_id or "",
                },
            )
            exit_code = _response_exit_code(response)
            return None

        alert_text = _strip_ack_token(response.result, effective_ack)
        if not alert_text:
            logger.info("Heartbeat OK (suppressed)")
            exit_code = _response_exit_code(response)
            return None

        await _record_message(
            orch,
            key,
            role="assistant",
            content_text=alert_text,
            source="heartbeat_alert",
            process_id=process_id,
            token_count=response.total_tokens,
            cost_usd=response.cost_usd,
            content_json={
                "flow": "heartbeat",
                "is_error": False,
                "session_id": response.session_id or "",
            },
        )
        await _update_session(orch, session, response)
        logger.info("Heartbeat alert chars=%d", len(alert_text))
        exit_code = _response_exit_code(response)
        return alert_text
    finally:
        await _record_process_finish(orch, process_id, exit_code)
        orch._inflight_tracker.complete(key)
