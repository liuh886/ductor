"""Tests for conversation flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ductor_bot.cli.types import AgentResponse
from ductor_bot.config import AgentConfig
from ductor_bot.orchestrator.core import Orchestrator
from ductor_bot.orchestrator.flows import (
    StreamingCallbacks,
    _finish_normal,
    _strip_ack_token,
    _update_session,
    heartbeat_flow,
    named_session_flow,
    named_session_streaming,
    normal,
    normal_streaming,
)
from ductor_bot.orchestrator.registry import OrchestratorResult
from ductor_bot.runtime.memory import MemoryFragment
from ductor_bot.session import SessionData
from ductor_bot.session.key import SessionKey
from ductor_bot.workspace.paths import DuctorPaths


@pytest.fixture
def orch(orch: Orchestrator) -> Orchestrator:
    """Re-export with default mock setup."""
    return orch


def _mock_response(**kwargs: object) -> AgentResponse:
    defaults: dict[str, object] = {
        "result": "Hello from agent",
        "session_id": "sess-123",
        "is_error": False,
        "cost_usd": 0.01,
        "total_tokens": 500,
    }
    defaults.update(kwargs)
    return AgentResponse(**defaults)  # type: ignore[arg-type]


async def _establish_session(orch: Orchestrator) -> None:
    """Run a successful normal() call so the session has a real session_id."""
    mock_exec = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute", mock_exec)
    await normal(orch, SessionKey(chat_id=1), "Setup")


def _make_state_orch(paths: DuctorPaths) -> Orchestrator:
    """Create an orchestrator with sqlite runtime-state storage enabled."""
    config = AgentConfig(
        state_backend="sqlite",
        state_db_path=str(paths.ductor_home / "runtime_state.db"),
    )
    orch = Orchestrator(config, paths)
    mock_cli = MagicMock()
    mock_cli.execute = AsyncMock()
    mock_cli.execute_streaming = AsyncMock()
    object.__setattr__(orch, "_cli_service", mock_cli)
    return orch


async def _seed_active_session(orch: Orchestrator) -> SessionData:
    """Create a persisted active session for heartbeat tests."""
    session = await orch._sessions.reset_session(
        SessionKey(chat_id=1),
        provider="claude",
        model="opus",
    )
    session.session_id = "sess-heartbeat"
    await orch._sessions.update_session(session)
    return session


async def _seed_named_session(orch: Orchestrator) -> str:
    """Create a persisted named session that named_session_flow can resume."""
    ns = orch._named_sessions.create(1, "claude", "opus", "hello")
    orch._named_sessions.update_after_response(1, ns.name, "ns-sess-1", status="idle")
    return ns.name


# -- normal flow --


async def test_normal_returns_result(orch: Orchestrator) -> None:
    object.__setattr__(orch._cli_service, "execute", AsyncMock(return_value=_mock_response()))
    result = await normal(orch, SessionKey(chat_id=1), "Hello")
    assert result.text == "Hello from agent"
    assert not result.stream_fallback


async def test_normal_new_session_injects_mainmemory(orch: Orchestrator) -> None:
    orch.paths.mainmemory_path.write_text("# Important Context")
    mock_execute = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute", mock_execute)

    await normal(orch, SessionKey(chat_id=1), "Hello")

    call_args = mock_execute.call_args
    request = call_args[0][0]
    assert request.append_system_prompt is not None
    assert "Important Context" in request.append_system_prompt
    assert request.resume_session is None  # New session


async def test_normal_new_session_prefers_fragment_memory(
    workspace: tuple[DuctorPaths, AgentConfig],
) -> None:
    paths, _ = workspace
    state_orch = _make_state_orch(paths)
    state_orch.paths.mainmemory_path.write_text("# Raw memory that should not be used")
    fragment_repo = getattr(state_orch, "_memory_fragment_repo", None)
    assert fragment_repo is not None
    fragment_repo.create(
        MemoryFragment(
            title="Fragment Memory",
            body="- Use compact memory\n- Prefer structured context",
            source_kind="mainmemory",
            source_path=str(state_orch.paths.mainmemory_path),
            scope="mainmemory",
            agent_name="main",
            tags=["memory"],
            importance=1.0,
        ),
    )

    mock_execute = AsyncMock(return_value=_mock_response())
    object.__setattr__(state_orch._cli_service, "execute", mock_execute)

    await normal(state_orch, SessionKey(chat_id=1), "Hello")

    call_args = mock_execute.call_args
    request = call_args[0][0]
    assert request.append_system_prompt is not None
    assert "Fragment Memory" in request.append_system_prompt
    assert "Use compact memory" in request.append_system_prompt
    assert "Raw memory that should not be used" not in request.append_system_prompt
    assert request.resume_session is None


async def test_normal_resume_session_no_append(orch: Orchestrator) -> None:
    mock_execute = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute", mock_execute)

    # First call creates session
    await normal(orch, SessionKey(chat_id=1), "Hello")
    # Second call resumes
    await normal(orch, SessionKey(chat_id=1), "Follow up")

    second_call = mock_execute.call_args_list[1]
    request = second_call[0][0]
    assert request.append_system_prompt is None
    assert request.resume_session is not None


async def test_normal_error_preserves_session(orch: Orchestrator) -> None:
    """On persistent error with existing session: keep session, no auto-retry."""
    # Establish a session first so resume_session is set on subsequent calls
    await _establish_session(orch)

    mock_execute = AsyncMock(
        return_value=_mock_response(is_error=True, result="Rate limited"),
    )
    mock_kill = AsyncMock(return_value=0)
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    object.__setattr__(orch._process_registry, "kill_all", mock_kill)

    result = await normal(orch, SessionKey(chat_id=1), "Hello")
    assert "Session Error" in result.text
    assert "[opus]" in result.text
    assert "/new" in result.text
    assert mock_execute.call_count == 1
    mock_kill.assert_called_once_with(1)


async def test_normal_timeout_preserves_session(orch: Orchestrator) -> None:
    """On persistent timeout with existing session: keep session, no auto-retry."""
    await _establish_session(orch)

    mock_execute = AsyncMock(
        return_value=_mock_response(is_error=True, timed_out=True, result=""),
    )
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    object.__setattr__(orch._process_registry, "kill_all", AsyncMock(return_value=0))

    result = await normal(orch, SessionKey(chat_id=1), "Hello")
    assert "Timeout" in result.text
    assert "session has been preserved" in result.text
    assert mock_execute.call_count == 1


async def test_normal_next_message_can_succeed_after_error(orch: Orchestrator) -> None:
    """No auto-retry, but a follow-up user message can succeed with same session."""
    await _establish_session(orch)

    error_resp = _mock_response(is_error=True, result="Temporary error")
    success_resp = _mock_response(result="All good")
    mock_execute = AsyncMock(side_effect=[error_resp, success_resp])
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    object.__setattr__(orch._process_registry, "kill_all", AsyncMock(return_value=0))

    first = await normal(orch, SessionKey(chat_id=1), "Hello")
    second = await normal(orch, SessionKey(chat_id=1), "Hello again")
    assert "Session Error" in first.text
    assert second.text == "All good"
    assert mock_execute.call_count == 2


async def test_normal_model_override(orch: Orchestrator) -> None:
    mock_execute = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    await normal(orch, SessionKey(chat_id=1), "Hello", model_override="sonnet")

    request = mock_execute.call_args[0][0]
    assert request.model_override == "sonnet"


async def test_normal_sigkill_recovers_once_then_succeeds(orch: Orchestrator) -> None:
    """SIGKILL triggers one recovery retry before returning success."""
    sigkill_resp = _mock_response(is_error=True, result="killed", returncode=-9)
    success_resp = _mock_response(result="Recovered")
    mock_execute = AsyncMock(side_effect=[sigkill_resp, success_resp])
    mock_reset_provider = AsyncMock()
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    object.__setattr__(orch._process_registry, "kill_all", AsyncMock(return_value=0))
    object.__setattr__(orch._sessions, "reset_provider_session", mock_reset_provider)

    result = await normal(orch, SessionKey(chat_id=1), "Hello")
    assert result.text == "Recovered"
    assert mock_execute.call_count == 2
    mock_reset_provider.assert_called_once_with(
        SessionKey(chat_id=1), provider="claude", model="opus"
    )


async def test_normal_sigkill_recovers_once_then_asks_user_retry(orch: Orchestrator) -> None:
    """If recovery retry also SIGKILLs, return explicit user guidance."""
    sigkill_resp = _mock_response(is_error=True, result="killed", returncode=-9)
    mock_execute = AsyncMock(side_effect=[sigkill_resp, sigkill_resp])
    mock_reset_provider = AsyncMock()
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    object.__setattr__(orch._process_registry, "kill_all", AsyncMock(return_value=0))
    object.__setattr__(orch._sessions, "reset_provider_session", mock_reset_provider)

    result = await normal(orch, SessionKey(chat_id=1), "Hello")
    assert result.text == "Execution was interrupted. Please send the same request again."
    assert mock_execute.call_count == 2
    mock_reset_provider.assert_called_once_with(
        SessionKey(chat_id=1), provider="claude", model="opus"
    )


async def test_normal_does_not_auto_fallback_provider(orch: Orchestrator) -> None:
    mock_execute = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    orch._providers._available_providers = frozenset({"codex"})

    await normal(orch, SessionKey(chat_id=1), "Hello")

    request = mock_execute.call_args[0][0]
    assert request.model_override == "opus"
    assert request.provider_override == "claude"

    session = await orch._sessions.get_active(SessionKey(chat_id=1))
    assert session is not None
    assert session.provider == "claude"
    assert session.model == "opus"


async def test_normal_preserves_existing_session_target_on_restart(orch: Orchestrator) -> None:
    orch._providers._available_providers = frozenset({"codex"})
    await orch._sessions.reset_session(
        SessionKey(chat_id=1),
        provider="gemini",
        model="gemini-3-pro-preview",
    )
    existing = await orch._sessions.get_active(SessionKey(chat_id=1))
    assert existing is not None
    existing.session_id = "sess-gemini-1"
    await orch._sessions.sync_session_target(
        existing,
        provider="gemini",
        model="gemini-3-pro-preview",
    )

    mock_execute = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute", mock_execute)

    orch._providers._gemini_api_key_mode = False
    await normal(orch, SessionKey(chat_id=1), "Hello")

    request = mock_execute.call_args[0][0]
    assert request.provider_override == "gemini"
    assert request.model_override == "gemini-3-pro-preview"


async def test_normal_warns_for_gemini_api_key_mode_without_ductor_key(
    orch: Orchestrator,
) -> None:
    mock_execute = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    orch._config.gemini_api_key = "null"

    orch._providers._gemini_api_key_mode = True
    result = await normal(
        orch, SessionKey(chat_id=1), "Hello", model_override="gemini-3-pro-preview"
    )

    assert "Gemini is set to API-key auth mode" in result.text
    assert "gemini_api_key" in result.text
    mock_execute.assert_not_awaited()


async def test_streaming_warns_for_gemini_api_key_mode_without_ductor_key(
    orch: Orchestrator,
) -> None:
    mock_streaming = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute_streaming", mock_streaming)
    orch._config.gemini_api_key = "null"

    orch._providers._gemini_api_key_mode = True
    result = await normal_streaming(
        orch, SessionKey(chat_id=1), "Hello", model_override="gemini-3-pro-preview"
    )

    assert "Gemini is set to API-key auth mode" in result.text
    mock_streaming.assert_not_awaited()


async def test_normal_allows_gemini_api_key_mode_with_configured_key(orch: Orchestrator) -> None:
    mock_execute = AsyncMock(return_value=_mock_response(result="Gemini OK"))
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    orch._config.gemini_api_key = "cfg-key-123"

    orch._providers._gemini_api_key_mode = True
    result = await normal(
        orch, SessionKey(chat_id=1), "Hello", model_override="gemini-3-pro-preview"
    )

    assert result.text == "Gemini OK"
    mock_execute.assert_awaited_once()


async def test_normal_records_runtime_state(workspace: tuple[DuctorPaths, AgentConfig]) -> None:
    paths, _config = workspace
    orch = _make_state_orch(paths)
    object.__setattr__(orch._cli_service, "execute", AsyncMock(return_value=_mock_response()))

    result = await normal(orch, SessionKey(chat_id=1), "Hello runtime state")

    assert result.text == "Hello from agent"
    assert orch._process_repo is not None
    assert orch._message_repo is not None
    processes = orch._process_repo.list_all()
    messages = orch._message_repo.list_by_session("tg:1")
    assert len(processes) == 1
    assert processes[0]["process_label"] == "normal"
    assert processes[0]["ended_at"] is not None
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["source"] == "normal_prompt"
    assert messages[1]["source"] == "normal_result"


async def test_streaming_records_runtime_state(workspace: tuple[DuctorPaths, AgentConfig]) -> None:
    paths, _config = workspace
    orch = _make_state_orch(paths)
    object.__setattr__(
        orch._cli_service, "execute_streaming", AsyncMock(return_value=_mock_response())
    )

    result = await normal_streaming(orch, SessionKey(chat_id=1), "Hello stream runtime")

    assert result.text == "Hello from agent"
    assert orch._process_repo is not None
    assert orch._message_repo is not None
    processes = orch._process_repo.list_all()
    messages = orch._message_repo.list_by_session("tg:1")
    assert len(processes) == 1
    assert processes[0]["process_label"] == "normal_streaming"
    assert processes[0]["ended_at"] is not None
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["source"] == "normal_stream_prompt"
    assert messages[1]["source"] == "normal_stream_result"


async def test_normal_resume_prompt_includes_compressed_context(
    workspace: tuple[DuctorPaths, AgentConfig],
) -> None:
    paths, _config = workspace
    orch = _make_state_orch(paths)
    mock_execute = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute", mock_execute)

    for idx in range(6):
        await normal(orch, SessionKey(chat_id=1), f"message-{idx}")

    request = mock_execute.call_args_list[-1][0][0]
    assert "## COMPRESSED CONTEXT" in request.prompt
    assert "CURRENT USER MESSAGE" in request.prompt


async def test_named_session_records_runtime_state(workspace: tuple[DuctorPaths, AgentConfig]) -> None:
    paths, _config = workspace
    orch = _make_state_orch(paths)
    session_name = await _seed_named_session(orch)
    object.__setattr__(orch._cli_service, "execute", AsyncMock(return_value=_mock_response()))

    result = await named_session_flow(orch, SessionKey(chat_id=1), session_name, "Named hello")

    assert result.text.startswith(f"**[{session_name} | claude]**")
    assert orch._process_repo is not None
    assert orch._message_repo is not None
    processes = orch._process_repo.list_all()
    messages = orch._message_repo.list_by_session("tg:1")
    assert len(processes) == 1
    assert processes[0]["process_label"] == f"ns:{session_name}"
    assert processes[0]["ended_at"] is not None
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["source"] == "named_session_prompt"
    assert messages[1]["source"] == "named_session_result"


async def test_named_session_prompt_includes_compressed_context(
    workspace: tuple[DuctorPaths, AgentConfig],
) -> None:
    paths, _config = workspace
    orch = _make_state_orch(paths)
    session_name = await _seed_named_session(orch)
    assert orch._message_repo is not None
    for idx in range(10):
        orch._message_repo.append(
            "tg:1",
            "user" if idx % 2 == 0 else "assistant",
            f"named-history-{idx}",
            source="named_session_result" if idx % 2 else "named_session_prompt",
        )
    mock_execute = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute", mock_execute)

    await named_session_flow(orch, SessionKey(chat_id=1), session_name, "Named hello")

    request = mock_execute.call_args[0][0]
    assert "## COMPRESSED CONTEXT" in request.prompt
    assert session_name in request.prompt


async def test_named_session_streaming_records_runtime_state(
    workspace: tuple[DuctorPaths, AgentConfig]
) -> None:
    paths, _config = workspace
    orch = _make_state_orch(paths)
    session_name = await _seed_named_session(orch)
    object.__setattr__(
        orch._cli_service, "execute_streaming", AsyncMock(return_value=_mock_response())
    )

    result = await named_session_streaming(
        orch, SessionKey(chat_id=1), session_name, "Named stream hello"
    )

    assert result.text.startswith(f"**[{session_name} | claude]**")
    assert orch._process_repo is not None
    assert orch._message_repo is not None
    processes = orch._process_repo.list_all()
    messages = orch._message_repo.list_by_session("tg:1")
    assert len(processes) == 1
    assert processes[0]["process_label"] == f"ns:{session_name}:streaming"
    assert processes[0]["ended_at"] is not None
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["source"] == "named_session_stream_prompt"
    assert messages[1]["source"] == "named_session_stream_result"


async def test_heartbeat_records_runtime_state(workspace: tuple[DuctorPaths, AgentConfig]) -> None:
    paths, _config = workspace
    orch = _make_state_orch(paths)
    await _seed_active_session(orch)
    orch._config.heartbeat.cooldown_minutes = 0
    object.__setattr__(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="PING"))
    )

    result = await heartbeat_flow(orch, SessionKey(chat_id=1), prompt="HB prompt", ack_token="ACK")

    assert result == "PING"
    assert orch._process_repo is not None
    assert orch._message_repo is not None
    processes = orch._process_repo.list_all()
    messages = orch._message_repo.list_by_session("tg:1")
    assert len(processes) == 1
    assert processes[0]["process_label"] == "heartbeat"
    assert processes[0]["ended_at"] is not None
    assert [message["role"] for message in messages] == ["system", "assistant"]
    assert messages[0]["source"] == "heartbeat_prompt"
    assert messages[1]["source"] == "heartbeat_alert"


# -- streaming flow --


async def test_streaming_delegates_correctly(orch: Orchestrator) -> None:
    mock_streaming = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute_streaming", mock_streaming)
    on_delta = AsyncMock()

    result = await normal_streaming(
        orch, SessionKey(chat_id=1), "Hello", cbs=StreamingCallbacks(on_text_delta=on_delta)
    )
    assert result.text == "Hello from agent"
    mock_streaming.assert_called_once()


async def test_streaming_fallback_flag(orch: Orchestrator) -> None:
    object.__setattr__(
        orch._cli_service,
        "execute_streaming",
        AsyncMock(return_value=_mock_response(stream_fallback=True)),
    )
    result = await normal_streaming(orch, SessionKey(chat_id=1), "Hello")
    assert result.stream_fallback is True


async def test_streaming_sigkill_recovers_once_then_succeeds(orch: Orchestrator) -> None:
    """Streaming path also retries once after SIGKILL."""
    sigkill_resp = _mock_response(is_error=True, result="killed", returncode=-9)
    success_resp = _mock_response(result="Recovered stream")
    mock_streaming = AsyncMock(side_effect=[sigkill_resp, success_resp])
    mock_reset_provider = AsyncMock()
    object.__setattr__(orch._cli_service, "execute_streaming", mock_streaming)
    object.__setattr__(orch._process_registry, "kill_all", AsyncMock(return_value=0))
    object.__setattr__(orch._sessions, "reset_provider_session", mock_reset_provider)

    result = await normal_streaming(orch, SessionKey(chat_id=1), "Hello")
    assert result.text == "Recovered stream"
    assert mock_streaming.call_count == 2
    mock_reset_provider.assert_called_once_with(
        SessionKey(chat_id=1), provider="claude", model="opus"
    )


async def test_streaming_error_preserves_session(orch: Orchestrator) -> None:
    """Streaming error keeps the session and advises /new."""
    object.__setattr__(
        orch._cli_service,
        "execute_streaming",
        AsyncMock(return_value=_mock_response(is_error=True, result="Stream failed")),
    )
    mock_kill = AsyncMock(return_value=0)
    object.__setattr__(orch._process_registry, "kill_all", mock_kill)

    result = await normal_streaming(orch, SessionKey(chat_id=1), "Hello")
    assert "Session Error" in result.text
    assert "[opus]" in result.text
    mock_kill.assert_called_once_with(1)


async def test_streaming_error_with_model_override(orch: Orchestrator) -> None:
    """Streaming error shows the override model name."""
    object.__setattr__(
        orch._cli_service,
        "execute_streaming",
        AsyncMock(return_value=_mock_response(is_error=True, result="Error")),
    )
    object.__setattr__(orch._process_registry, "kill_all", AsyncMock(return_value=0))
    object.__setattr__(orch._sessions, "reset_session", AsyncMock())

    result = await normal_streaming(orch, SessionKey(chat_id=1), "Hello", model_override="sonnet")
    assert "[sonnet]" in result.text


# ---------------------------------------------------------------------------
# Gap 1: _strip_ack_token() unit tests
# ---------------------------------------------------------------------------


def test_strip_ack_token_exact_match() -> None:
    """Exact token match returns empty string."""
    assert _strip_ack_token("HEARTBEAT_OK", "HEARTBEAT_OK") == ""


def test_strip_ack_token_exact_match_with_whitespace() -> None:
    """Token surrounded by whitespace still matches exactly after strip()."""
    assert _strip_ack_token("  HEARTBEAT_OK  ", "HEARTBEAT_OK") == ""


def test_strip_ack_token_starts_with_token() -> None:
    """Token at start is removed, remainder returned."""
    result = _strip_ack_token("HEARTBEAT_OK but also this alert", "HEARTBEAT_OK")
    assert result == "but also this alert"


def test_strip_ack_token_ends_with_token() -> None:
    """Token at end is removed, remainder returned."""
    result = _strip_ack_token("Alert happened HEARTBEAT_OK", "HEARTBEAT_OK")
    assert result == "Alert happened"


def test_strip_ack_token_both_start_and_end() -> None:
    """Token at both start and end -- both are stripped."""
    result = _strip_ack_token("HEARTBEAT_OK middle text HEARTBEAT_OK", "HEARTBEAT_OK")
    assert result == "middle text"


def test_strip_ack_token_mid_text_passthrough() -> None:
    """Token in the middle (not at start or end) passes through unchanged."""
    result = _strip_ack_token("Hello HEARTBEAT_OK World", "HEARTBEAT_OK")
    assert result == "Hello HEARTBEAT_OK World"


def test_strip_ack_token_no_token() -> None:
    """Text without token passes through unchanged."""
    assert _strip_ack_token("Regular alert text", "HEARTBEAT_OK") == "Regular alert text"


def test_strip_ack_token_empty_string() -> None:
    """Empty string returns empty string (not exact match since stripped == token is false)."""
    assert _strip_ack_token("", "HEARTBEAT_OK") == ""


def test_strip_ack_token_custom_token() -> None:
    """Works with a different ack_token value."""
    assert _strip_ack_token("ACK", "ACK") == ""
    assert _strip_ack_token("ACK alert text", "ACK") == "alert text"
    assert _strip_ack_token("alert text ACK", "ACK") == "alert text"


# ---------------------------------------------------------------------------
# Gap 2: _finish_normal() direct unit tests
# ---------------------------------------------------------------------------


def test_finish_normal_happy_path() -> None:
    """Non-error response returns result text."""
    resp = AgentResponse(result="Hello", is_error=False)
    result = _finish_normal(resp)
    assert result.text == "Hello"
    assert not result.stream_fallback


def test_finish_normal_with_stream_fallback() -> None:
    """Non-error response preserves stream_fallback flag."""
    resp = AgentResponse(result="Hello", is_error=False, stream_fallback=True)
    result = _finish_normal(resp)
    assert result.text == "Hello"
    assert result.stream_fallback is True


def test_finish_normal_timed_out() -> None:
    """Timed-out error returns timeout message."""
    resp = AgentResponse(result="", is_error=True, timed_out=True)
    result = _finish_normal(resp)
    assert result.text == "Agent timed out. Please try again."


def test_finish_normal_error_with_details() -> None:
    """Error with non-empty result shows error details."""
    resp = AgentResponse(result="Rate limit exceeded", is_error=True)
    result = _finish_normal(resp)
    assert result.text == "Error: Rate limit exceeded"


def test_finish_normal_error_empty_result() -> None:
    """Error with empty/whitespace result shows generic message."""
    resp = AgentResponse(result="", is_error=True)
    result = _finish_normal(resp)
    assert result.text == "Error: check logs for details."


def test_finish_normal_error_whitespace_only_result() -> None:
    """Error with whitespace-only result shows generic message."""
    resp = AgentResponse(result="   ", is_error=True)
    result = _finish_normal(resp)
    assert result.text == "Error: check logs for details."


def test_finish_normal_error_truncates_long_result() -> None:
    """Error result is truncated to 500 chars."""
    long_msg = "x" * 600
    resp = AgentResponse(result=long_msg, is_error=True)
    result = _finish_normal(resp)
    assert result.text == f"Error: {'x' * 500}"
    assert len(result.text) == 507  # "Error: " (7) + 500


def test_finish_normal_returns_orchestrator_result() -> None:
    """All branches return OrchestratorResult instances."""
    cases = [
        AgentResponse(result="ok", is_error=False),
        AgentResponse(result="", is_error=True, timed_out=True),
        AgentResponse(result="err", is_error=True),
        AgentResponse(result="", is_error=True),
    ]
    for resp in cases:
        result = _finish_normal(resp)
        assert isinstance(result, OrchestratorResult)


# ---------------------------------------------------------------------------
# Gap 3: No auto-retry on resume failure (user-controlled retry)
# ---------------------------------------------------------------------------


async def test_normal_no_auto_retry_on_resume_failure(orch: Orchestrator) -> None:
    """normal() no longer auto-retries on resume failure."""
    # Establish session so resume_session is set
    await _establish_session(orch)

    mock_execute = AsyncMock(
        return_value=_mock_response(is_error=True, result="Resume failed"),
    )
    mock_kill = AsyncMock(return_value=0)
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    object.__setattr__(orch._process_registry, "kill_all", mock_kill)

    await normal(orch, SessionKey(chat_id=1), "Hello")
    assert mock_execute.call_count == 1


async def test_streaming_no_auto_retry_on_resume_failure(orch: Orchestrator) -> None:
    """normal_streaming() no longer auto-retries on resume failure."""
    # Establish session so resume_session is set
    mock_exec = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute_streaming", mock_exec)
    await normal_streaming(orch, SessionKey(chat_id=1), "Setup")

    mock_streaming = AsyncMock(
        return_value=_mock_response(is_error=True, result="Resume failed"),
    )
    mock_kill = AsyncMock(return_value=0)
    object.__setattr__(orch._cli_service, "execute_streaming", mock_streaming)
    object.__setattr__(orch._process_registry, "kill_all", mock_kill)

    await normal_streaming(orch, SessionKey(chat_id=1), "Hello")
    assert mock_streaming.call_count == 1


async def test_normal_no_retry_on_new_session_error(orch: Orchestrator) -> None:
    """normal() does NOT retry when error occurs on a brand-new session (no resume)."""
    mock_execute = AsyncMock(
        return_value=_mock_response(is_error=True, result="Error"),
    )
    mock_kill = AsyncMock(return_value=0)
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    object.__setattr__(orch._process_registry, "kill_all", mock_kill)
    object.__setattr__(orch._sessions, "reset_session", AsyncMock())

    await normal(orch, SessionKey(chat_id=1), "Hello")
    # No resume_session on new session, so no retry -- execute called once
    assert mock_execute.call_count == 1


async def test_streaming_no_retry_on_new_session_error(orch: Orchestrator) -> None:
    """normal_streaming() does NOT retry when error occurs on a brand-new session."""
    mock_streaming = AsyncMock(
        return_value=_mock_response(is_error=True, result="Error"),
    )
    mock_kill = AsyncMock(return_value=0)
    object.__setattr__(orch._cli_service, "execute_streaming", mock_streaming)
    object.__setattr__(orch._process_registry, "kill_all", mock_kill)
    object.__setattr__(orch._sessions, "reset_session", AsyncMock())

    await normal_streaming(orch, SessionKey(chat_id=1), "Hello")
    # No resume_session on new session, so no retry -- called once
    assert mock_streaming.call_count == 1


# ---------------------------------------------------------------------------
# Gap 4: _update_session() session_id change
# ---------------------------------------------------------------------------


async def test_update_session_changes_session_id(orch: Orchestrator) -> None:
    """When CLI returns a different session_id, _update_session updates it."""
    session = SessionData(session_id="old-sess", chat_id=1)
    response = AgentResponse(
        result="ok",
        session_id="new-sess",
        cost_usd=0.01,
        total_tokens=100,
    )
    await _update_session(orch, session, response)
    assert session.session_id == "new-sess"


async def test_update_session_preserves_same_session_id(orch: Orchestrator) -> None:
    """When CLI returns the same session_id, it stays unchanged."""
    session = SessionData(session_id="same-sess", chat_id=1)
    response = AgentResponse(
        result="ok",
        session_id="same-sess",
        cost_usd=0.01,
        total_tokens=100,
    )
    await _update_session(orch, session, response)
    assert session.session_id == "same-sess"


async def test_update_session_no_session_id_in_response(orch: Orchestrator) -> None:
    """When CLI returns no session_id, the original is preserved."""
    session = SessionData(session_id="original", chat_id=1)
    response = AgentResponse(
        result="ok",
        session_id=None,
        cost_usd=0.0,
        total_tokens=0,
    )
    await _update_session(orch, session, response)
    assert session.session_id == "original"


# ---------------------------------------------------------------------------
# Gap 5: Abort discards response -- /stop should suppress ALL output
# ---------------------------------------------------------------------------


async def test_normal_abort_skips_retry(orch: Orchestrator) -> None:
    """When process is aborted (via /stop), normal() returns empty instead of retrying."""
    await _establish_session(orch)

    mock_execute = AsyncMock(
        return_value=_mock_response(is_error=True, result="killed"),
    )
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    orch._process_registry._aborted.add(1)

    result = await normal(orch, SessionKey(chat_id=1), "Hello")
    assert result.text == ""
    assert mock_execute.call_count == 1  # No retry


async def test_streaming_abort_skips_retry(orch: Orchestrator) -> None:
    """When process is aborted (via /stop), normal_streaming() returns empty instead of retrying."""
    mock_exec = AsyncMock(return_value=_mock_response())
    object.__setattr__(orch._cli_service, "execute_streaming", mock_exec)
    await normal_streaming(orch, SessionKey(chat_id=1), "Setup")

    mock_streaming = AsyncMock(
        return_value=_mock_response(is_error=True, result="killed"),
    )
    object.__setattr__(orch._cli_service, "execute_streaming", mock_streaming)
    orch._process_registry._aborted.add(1)

    result = await normal_streaming(orch, SessionKey(chat_id=1), "Hello")
    assert result.text == ""
    assert mock_streaming.call_count == 1  # No retry


async def test_normal_abort_discards_successful_response(orch: Orchestrator) -> None:
    """Even when CLI responds successfully, abort flag causes empty result."""
    mock_execute = AsyncMock(
        return_value=_mock_response(is_error=False, result="Agent replied"),
    )
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    orch._process_registry._aborted.add(1)

    result = await normal(orch, SessionKey(chat_id=1), "Hello")
    assert result.text == ""


async def test_streaming_abort_discards_successful_response(orch: Orchestrator) -> None:
    """Even when streaming CLI responds successfully, abort flag causes empty result."""
    mock_streaming = AsyncMock(
        return_value=_mock_response(is_error=False, result="Agent replied via stream"),
    )
    object.__setattr__(orch._cli_service, "execute_streaming", mock_streaming)
    orch._process_registry._aborted.add(1)

    result = await normal_streaming(orch, SessionKey(chat_id=1), "Hello")
    assert result.text == ""


async def test_normal_abort_on_new_session_returns_empty(orch: Orchestrator) -> None:
    """Abort on a new session (no resume) also returns empty, not reset error."""
    mock_execute = AsyncMock(
        return_value=_mock_response(is_error=True, result="killed"),
    )
    object.__setattr__(orch._cli_service, "execute", mock_execute)
    object.__setattr__(orch._process_registry, "kill_all", AsyncMock(return_value=0))
    object.__setattr__(orch._sessions, "reset_session", AsyncMock())
    orch._process_registry._aborted.add(1)

    result = await normal(orch, SessionKey(chat_id=1), "Hello")
    assert result.text == ""
