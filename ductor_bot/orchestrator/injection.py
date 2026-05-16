"""Session injection: routes inter-agent messages and task questions through CLIService.

Extracts the common "build prompt → get active session → execute → update"
pattern from the Orchestrator into reusable helpers.

Note: task *results* are injected via the MessageBus (see ``bus.adapters``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from ductor_bot.cli.types import AgentRequest
from ductor_bot.orchestrator.flows import _is_invalid_session, _update_session
from ductor_bot.session.key import SessionKey
from ductor_bot.session.named import NamedSession

if TYPE_CHECKING:
    from ductor_bot.multiagent.bus import AsyncInterAgentResult
    from ductor_bot.orchestrator.core import Orchestrator

logger = logging.getLogger(__name__)

_TRANSPORT_ALIASES = {"telegram": "tg", "matrix": "mx"}


def _transport_id(value: str) -> str:
    """Return the short transport id used by SessionKey and Envelope."""
    stripped = value.strip().lower()
    return _TRANSPORT_ALIASES.get(stripped, stripped or "tg")


def _state_repos(orch: Orchestrator) -> tuple[object | None, object | None]:
    """Return optional runtime-state repositories for inter-agent persistence."""
    return getattr(orch, "_process_repo", None), getattr(orch, "_message_repo", None)


async def _apply_runtime_compression(
    orch: Orchestrator,
    session_storage_key: str,
    prompt: str,
    *,
    current_label: str,
) -> str:
    """Prepend compressed runtime context when available."""
    compressor = getattr(orch, "_context_compressor", None)
    if compressor is None:
        return prompt
    prefix = await asyncio.to_thread(compressor.build_prompt_prefix, session_storage_key)
    if not prefix:
        return prompt
    return f"{prefix}\n\n## {current_label}\n{prompt}"


# ruff: noqa: PLR0913
async def _record_process_start(
    orch: Orchestrator,
    *,
    process_label: str,
    chat_id: int,
    topic_id: int | None,
    provider: str,
    model: str,
    session_storage_key: str,
) -> int | None:
    """Persist an inter-agent process row when runtime state is enabled."""
    process_repo, _message_repo = _state_repos(orch)
    if process_repo is None:
        return None
    try:
        return await asyncio.to_thread(
            process_repo.create,
            process_label,
            chat_id,
            topic_id=topic_id,
            provider=provider,
            model=model,
            session_storage_key=session_storage_key,
        )
    except Exception:
        logger.exception("Failed to record inter-agent process start label=%s", process_label)
        return None


async def _record_process_finish(orch: Orchestrator, process_id: int | None, exit_code: int) -> None:
    """Persist inter-agent process completion when runtime state is enabled."""
    process_repo, _message_repo = _state_repos(orch)
    if process_repo is None or process_id is None:
        return
    try:
        await asyncio.to_thread(process_repo.finish, process_id, exit_code=exit_code)
    except Exception:
        logger.exception("Failed to finish inter-agent process id=%s", process_id)


# ruff: noqa: PLR0913
async def _record_message(
    orch: Orchestrator,
    session_storage_key: str,
    *,
    role: str,
    content_text: str,
    source: str,
    process_id: int | None = None,
    content_json: dict[str, object] | None = None,
) -> None:
    """Append an inter-agent runtime-state message when runtime state is enabled."""
    _process_repo, message_repo = _state_repos(orch)
    if message_repo is None:
        return
    try:
        await asyncio.to_thread(
            message_repo.append,
            session_storage_key,
            role,
            content_text,
            source=source,
            process_id=process_id,
            content_json=content_json or {},
        )
    except Exception:
        logger.exception(
            "Failed to record inter-agent message key=%s source=%s",
            session_storage_key,
            source,
        )


# ---------------------------------------------------------------------------
# Shared injection helper
# ---------------------------------------------------------------------------


async def _inject_prompt(
    orch: Orchestrator,
    prompt: str,
    chat_id: int,
    process_label: str,
    *,
    topic_id: int | None = None,
    transport: str = "tg",
    source: str = "injected_prompt",
    content_json: dict[str, object] | None = None,
) -> str:
    """Execute *prompt* in the current active session and update session state.

    Shared by ``handle_async_interagent_result`` and ``inject_prompt``.
    """
    key = SessionKey(transport=transport, chat_id=chat_id, topic_id=topic_id)
    active = await orch._sessions.get_active(key)
    resume_id = active.session_id if active else None
    provider = active.provider if active else orch._config.provider
    model = active.model if active else orch._config.model
    prompt = await _apply_runtime_compression(
        orch,
        key.storage_key,
        prompt,
        current_label="CURRENT INJECTED MESSAGE",
    )

    request = AgentRequest(
        prompt=prompt,
        chat_id=chat_id,
        topic_id=topic_id,
        transport=transport,
        process_label=process_label,
        resume_session=resume_id,
        timeout_seconds=orch._config.cli_timeout,
    )
    process_id = await _record_process_start(
        orch,
        process_label=process_label,
        chat_id=chat_id,
        topic_id=topic_id,
        provider=provider,
        model=model,
        session_storage_key=key.storage_key,
    )
    await _record_message(
        orch,
        key.storage_key,
        role="user",
        content_text=prompt,
        source=source,
        process_id=process_id,
        content_json=content_json
        or {
            "process_label": process_label,
            "resume_session": resume_id or "",
        },
    )
    try:
        response = await orch._cli_service.execute(request)
        await _record_message(
            orch,
            key.storage_key,
            role="assistant",
            content_text=response.result if response else "",
            source=f"{source}_result",
            process_id=process_id,
            content_json={
                "process_label": process_label,
                "session_id": response.session_id if response else "",
                "is_error": bool(response.is_error) if response else False,
            },
        )

        if active and response:
            await _update_session(orch, active, response)

        await _record_process_finish(
            orch,
            process_id,
            1 if response and response.is_error else 0,
        )
    except Exception:
        await _record_process_finish(orch, process_id, 1)
        raise
    else:
        return response.result if response else ""


# ---------------------------------------------------------------------------
# Inter-agent session helpers
# ---------------------------------------------------------------------------


def _interagent_chat_id(orch: Orchestrator) -> int:
    """Return the real Telegram chat_id for inter-agent sessions."""
    if not orch._config.allowed_user_ids:
        logger.warning("No allowed_user_ids configured — inter-agent sessions use chat_id=0")
        return 0
    return orch._config.allowed_user_ids[0]


def _interagent_storage_key(orch: Orchestrator, sender: str) -> str:
    """Return a deterministic runtime-state storage key for inter-agent sessions."""
    own_name = orch._cli_service._config.agent_name
    return f"ia:{own_name}:{sender}"


def _get_or_create_interagent_session(
    orch: Orchestrator,
    sender: str,
    *,
    new_session: bool = False,
) -> tuple[NamedSession, bool, str]:
    """Get or create a Named Session for an inter-agent conversation.

    Uses a deterministic name ``ia-{sender}`` so follow-up messages from
    the same sender automatically resume the same session.

    If *new_session* is True, any existing session for this sender is
    ended first so a fresh one is created.

    If the active provider/model has changed since the session was created,
    the old session is ended automatically (the CLI session ID is not
    portable across providers) and a provider-switch notice is returned.

    Returns ``(session, is_new, provider_switch_notice)``.
    """
    chat_id = _interagent_chat_id(orch)
    session_name = f"ia-{sender}"
    provider_switch_notice = ""

    if new_session and orch._named_sessions.end_session(chat_id, session_name):
        logger.info("Inter-agent session reset: %s (sender=%s)", session_name, sender)

    model_name, provider_name = orch.resolve_runtime_target(orch._config.model)

    ns = orch._named_sessions.get(chat_id, session_name)
    if ns is not None and ns.status != "ended":
        # Detect provider/model mismatch → session ID is not portable
        if ns.provider != provider_name:
            old_provider = ns.provider
            orch._named_sessions.end_session(chat_id, session_name)
            logger.info(
                "Inter-agent session %s reset: provider changed %s -> %s",
                session_name,
                old_provider,
                provider_name,
            )
            provider_switch_notice = (
                f"Agent `{orch._cli_service._config.agent_name}` switched "
                f"provider from `{old_provider}` to `{provider_name}`.\n"
                f"The previous inter-agent session `{session_name}` is no longer "
                f"resumable and has been ended.\n"
                f"A new session `{session_name}` was started with `{provider_name}`."
            )
        else:
            return ns, False, ""

    ns = NamedSession(
        name=session_name,
        chat_id=chat_id,
        provider=provider_name,
        model=model_name,
        session_id="",
        prompt_preview=f"Inter-agent session with {sender}",
        status="running",
        created_at=time.time(),
    )
    orch._named_sessions.add(ns)
    logger.info("Inter-agent named session created: %s (sender=%s)", session_name, sender)
    return ns, True, provider_switch_notice


# ---------------------------------------------------------------------------
# Public handlers (called by Orchestrator as thin delegations)
# ---------------------------------------------------------------------------


async def handle_interagent_message(
    orch: Orchestrator,
    sender: str,
    message: str,
    *,
    new_session: bool = False,
) -> tuple[str, str, str]:
    """Process a message from another agent via the InterAgentBus.

    Uses a Named Session per sender so that context is preserved across
    multiple inter-agent interactions.  The session can also be resumed
    manually from Telegram via ``@ia-{sender} <message>``.

    Returns ``(result_text, session_name, provider_switch_notice)``.
    The *provider_switch_notice* is non-empty when a provider change
    caused an automatic session reset — callers should notify the user.
    """
    own_name = orch._cli_service._config.agent_name
    chat_id = _interagent_chat_id(orch)
    transport = _transport_id(orch._config.transport)
    ns, _is_new, provider_switch_notice = _get_or_create_interagent_session(
        orch,
        sender,
        new_session=new_session,
    )
    storage_key = _interagent_storage_key(orch, sender)

    prompt = (
        f"[INTER-AGENT MESSAGE from '{sender}' to '{own_name}']\n"
        f"{message}\n"
        f"[END INTER-AGENT MESSAGE]\n\n"
        f"You are agent '{own_name}'. Respond to this inter-agent request "
        f"from '{sender}'. Be direct and concise."
    )
    prompt = await _apply_runtime_compression(
        orch,
        storage_key,
        prompt,
        current_label=f"CURRENT INTER-AGENT REQUEST ({sender})",
    )

    ns.status = "running"
    request = AgentRequest(
        prompt=prompt,
        chat_id=chat_id,
        transport=transport,
        process_label=f"interagent:{sender}",
        resume_session=ns.session_id or None,
        timeout_seconds=orch._config.cli_timeout,
    )
    process_id = await _record_process_start(
        orch,
        process_label=request.process_label or f"interagent:{sender}",
        chat_id=chat_id,
        topic_id=None,
        provider=ns.provider,
        model=ns.model,
        session_storage_key=storage_key,
    )
    await _record_message(
        orch,
        storage_key,
        role="user",
        content_text=message,
        source="interagent_request",
        process_id=process_id,
        content_json={
            "sender": sender,
            "session_name": ns.name,
            "new_session": new_session,
            "provider_switch_notice": provider_switch_notice,
        },
    )

    try:
        response = await orch._cli_service.execute(request)
    except Exception:
        ns.status = "idle"
        await _record_message(
            orch,
            storage_key,
            role="assistant",
            content_text=f"Error processing inter-agent message from '{sender}'",
            source="interagent_result",
            process_id=process_id,
            content_json={"sender": sender, "session_name": ns.name, "is_error": True},
        )
        await _record_process_finish(orch, process_id, 1)
        logger.exception("Inter-agent message handling failed (from=%s)", sender)
        return (
            f"Error processing inter-agent message from '{sender}'",
            ns.name,
            provider_switch_notice,
        )

    # #81: Claude / Codex CLI can invalidate cached session IDs after a
    # version bump or cache clear. Detect the stale-session error and retry
    # ONCE with a fresh session so async inter-agent sends don't silently
    # fail. The recovery is visible: it emits a WARNING log AND prepends a
    # notice to provider_switch_notice so the caller sees what happened.
    if _is_invalid_session(response):
        stale_id = ns.session_id
        logger.warning(
            "Inter-agent session stale (from=%s session=%s stale_id=%s) -- "
            "retrying with fresh session",
            sender,
            ns.name,
            stale_id,
        )
        orch._named_sessions.end_session(chat_id, ns.name)
        ns, _, _ = _get_or_create_interagent_session(orch, sender, new_session=True)
        ns.status = "running"
        retry_request = AgentRequest(
            prompt=prompt,
            chat_id=chat_id,
            transport=transport,
            process_label=f"interagent:{sender}",
            resume_session=None,
            timeout_seconds=orch._config.cli_timeout,
        )
        try:
            response = await orch._cli_service.execute(retry_request)
        except Exception:
            ns.status = "idle"
            logger.exception("Inter-agent retry failed (from=%s)", sender)
            await _record_message(
                orch,
                storage_key,
                role="assistant",
                content_text=f"Error processing inter-agent message from '{sender}' (after stale-session retry)",
                source="interagent_result",
                process_id=process_id,
                content_json={"sender": sender, "session_name": ns.name, "is_error": True},
            )
            await _record_process_finish(orch, process_id, 1)
            return (
                f"Error processing inter-agent message from '{sender}' (after stale-session retry)",
                ns.name,
                provider_switch_notice,
            )
        recovery_notice = (
            f"Inter-agent session `{ns.name}` was stale "
            f"(CLI rejected session `{stale_id}`); started a fresh session "
            f"and retried. This is normal after a CLI update."
        )
        provider_switch_notice = (
            f"{provider_switch_notice}\n{recovery_notice}".strip()
            if provider_switch_notice
            else recovery_notice
        )

    if response and response.session_id:
        orch._named_sessions.update_after_response(
            chat_id, ns.name, response.session_id, status="idle"
        )
    else:
        ns.status = "idle"
    await _record_message(
        orch,
        storage_key,
        role="assistant",
        content_text=response.result if response else "",
        source="interagent_result",
        process_id=process_id,
        content_json={
            "sender": sender,
            "session_name": ns.name,
            "session_id": response.session_id if response else "",
            "is_error": bool(response.is_error) if response else False,
        },
    )
    await _record_process_finish(orch, process_id, 1 if response and response.is_error else 0)
    return (response.result if response else ""), ns.name, provider_switch_notice


async def handle_async_interagent_result(
    orch: Orchestrator,
    result: AsyncInterAgentResult,
    *,
    chat_id: int = 0,
) -> str:
    """Inject an async inter-agent result into the current active session.

    Called when another agent completes an async request we sent.
    Resumes the *current* active session (not the one that was active when
    the task was dispatched) so the agent has full conversation context.

    The prompt is self-contained: it includes both the original task
    description and the sub-agent's response, so the agent can process
    the result even if the session changed (``/new``, provider switch).

    Caller must hold the per-chat lock to prevent concurrent session access.
    """
    own_name = orch._cli_service._config.agent_name
    recipient = result.recipient
    task_id = result.task_id

    session_hint = (
        f"\nThe recipient processed this in session `{result.session_name}`. "
        f"The user can continue this session in the recipient's Telegram chat "
        f"via `@{result.session_name} <message>`."
        if result.session_name
        else ""
    )

    task_context = (
        f"\n\nOriginal task you sent to '{recipient}':\n{result.original_message}"
        if result.original_message
        else ""
    )

    prompt = (
        f"[ASYNC INTER-AGENT RESPONSE from '{recipient}' (task {task_id})]\n"
        f"{result.result_text}\n"
        f"[END ASYNC INTER-AGENT RESPONSE]{session_hint}{task_context}\n\n"
        f"You are agent '{own_name}'. Process this response from agent "
        f"'{recipient}' and communicate the relevant results to the user "
        f"in your Telegram chat."
    )

    logger.debug(
        "Injecting async result into main session: task=%s from=%s "
        "resume_session=%s original_msg_len=%d",
        task_id,
        recipient,
        "<pending>",
        len(result.original_message),
    )

    try:
        return await _inject_prompt(
            orch,
            prompt,
            chat_id,
            f"interagent-async:{recipient}",
            topic_id=result.topic_id,
            source="interagent_async_result",
            content_json={
                "task_id": task_id,
                "recipient": recipient,
                "session_name": result.session_name,
                "original_message": result.original_message,
                "provider_switch_notice": result.provider_switch_notice,
                "chat_id": result.chat_id,
                "topic_id": result.topic_id,
            },
        )
    except Exception:
        logger.exception(
            "Async inter-agent result handling failed (from=%s)",
            recipient,
        )
        return f"Error processing async result from '{recipient}'"
