"""Tests for the heartbeat flow (orchestrator integration)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import time_machine

from ductor_bot.cli.types import AgentResponse
from ductor_bot.orchestrator.core import Orchestrator
from ductor_bot.orchestrator.flows import heartbeat_flow
from ductor_bot.scripts.memory_integrity_check import MemoryAuditReport, MemoryAuditRow
from ductor_bot.session.key import SessionKey


def _mock_response(**kwargs: Any) -> AgentResponse:
    defaults: dict[str, Any] = {
        "result": "HEARTBEAT_OK",
        "session_id": "sess-123",
        "is_error": False,
        "cost_usd": 0.01,
        "total_tokens": 100,
    }
    defaults.update(kwargs)
    return AgentResponse(**defaults)


def _past_cooldown() -> time_machine.travel:
    """Return a time_machine context that jumps 10 minutes into the future."""
    return time_machine.travel(datetime.now(UTC) + timedelta(minutes=10))


@pytest.fixture
def orch(orch: Orchestrator) -> Orchestrator:
    return orch


async def test_heartbeat_ok_returns_none(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HEARTBEAT_OK response is suppressed."""
    from ductor_bot.orchestrator.flows import normal

    monkeypatch.setattr(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="Hello"))
    )
    await normal(orch, SessionKey(chat_id=1), "init")

    with _past_cooldown():
        monkeypatch.setattr(
            orch._cli_service,
            "execute",
            AsyncMock(return_value=_mock_response(result="HEARTBEAT_OK")),
        )
        result = await heartbeat_flow(orch, SessionKey(chat_id=1))
        assert result is None


async def test_heartbeat_alert_returns_text(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-OK response returns the alert text."""
    from ductor_bot.orchestrator.flows import normal

    monkeypatch.setattr(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="Hello"))
    )
    await normal(orch, SessionKey(chat_id=1), "init")

    alert = "Hey! I found something interesting about Python 3.14!"
    with _past_cooldown():
        monkeypatch.setattr(
            orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result=alert))
        )
        result = await heartbeat_flow(orch, SessionKey(chat_id=1))
        assert result == alert


async def test_heartbeat_skips_new_session(orch: Orchestrator) -> None:
    """Heartbeat does nothing if there is no established session."""
    result = await heartbeat_flow(orch, SessionKey(chat_id=999))
    assert result is None


async def test_heartbeat_without_session_does_not_run_memory_audit(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heartbeat skips memory audit when there is no established session."""
    audit_mock = MagicMock()
    monkeypatch.setattr("ductor_bot.orchestrator.flows.build_integrity_report", audit_mock)

    result = await heartbeat_flow(orch, SessionKey(chat_id=999))

    assert result is None
    audit_mock.assert_not_called()


async def test_heartbeat_ok_does_not_increment_message_count(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HEARTBEAT_OK should not change message_count."""
    from ductor_bot.orchestrator.flows import normal

    monkeypatch.setattr(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="Hello"))
    )
    await normal(orch, SessionKey(chat_id=1), "init")

    session_before = await orch._sessions.get_active(SessionKey(chat_id=1))
    assert session_before is not None
    count_before = session_before.message_count

    with _past_cooldown():
        monkeypatch.setattr(
            orch._cli_service,
            "execute",
            AsyncMock(return_value=_mock_response(result="HEARTBEAT_OK")),
        )
        await heartbeat_flow(orch, SessionKey(chat_id=1))

    session_after = await orch._sessions.get_active(SessionKey(chat_id=1))
    assert session_after is not None
    assert session_after.message_count == count_before


async def test_heartbeat_alert_increments_message_count(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alert responses should update session state normally."""
    from ductor_bot.orchestrator.flows import normal

    monkeypatch.setattr(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="Hello"))
    )
    await normal(orch, SessionKey(chat_id=1), "init")

    session_before = await orch._sessions.get_active(SessionKey(chat_id=1))
    assert session_before is not None
    count_before = session_before.message_count

    alert = "Check out this cool fact!"
    with _past_cooldown():
        monkeypatch.setattr(
            orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result=alert))
        )
        await heartbeat_flow(orch, SessionKey(chat_id=1))

    session_after = await orch._sessions.get_active(SessionKey(chat_id=1))
    assert session_after is not None
    assert session_after.message_count == count_before + 1


async def test_heartbeat_cli_error_returns_none(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI errors during heartbeat are silently logged, not propagated."""
    from ductor_bot.orchestrator.flows import normal

    monkeypatch.setattr(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="Hello"))
    )
    await normal(orch, SessionKey(chat_id=1), "init")

    with _past_cooldown():
        monkeypatch.setattr(
            orch._cli_service,
            "execute",
            AsyncMock(return_value=_mock_response(is_error=True, result="Rate limited")),
        )
        result = await heartbeat_flow(orch, SessionKey(chat_id=1))
        assert result is None


async def test_heartbeat_does_not_apply_hooks(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heartbeat prompt should not have message hooks injected."""
    from ductor_bot.orchestrator.flows import normal

    monkeypatch.setattr(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="Hello"))
    )

    # Send 5 messages to reach hook threshold
    for _ in range(5):
        await normal(orch, SessionKey(chat_id=1), "msg")

    # Now heartbeat -- the prompt should be the raw heartbeat prompt, no REMINDER
    with _past_cooldown():
        hb_mock = AsyncMock(return_value=_mock_response(result="HEARTBEAT_OK"))
        monkeypatch.setattr(orch._cli_service, "execute", hb_mock)
        await heartbeat_flow(orch, SessionKey(chat_id=1))

        hb_request = hb_mock.call_args[0][0]
        assert "REMINDER" not in hb_request.prompt
        assert "heartbeat" in hb_request.prompt.lower()


async def test_heartbeat_skips_during_cooldown(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heartbeat is skipped if user was active within cooldown_minutes."""
    from ductor_bot.orchestrator.flows import normal

    monkeypatch.setattr(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="Hello"))
    )
    await normal(orch, SessionKey(chat_id=1), "just chatting")

    # Immediately after user message -> within cooldown -> skip
    cooldown_mock = AsyncMock(return_value=_mock_response(result="HEARTBEAT_OK"))
    monkeypatch.setattr(orch._cli_service, "execute", cooldown_mock)
    result = await heartbeat_flow(orch, SessionKey(chat_id=1))
    assert result is None
    # CLI should NOT have been called since cooldown skipped early
    cooldown_mock.assert_not_awaited()


async def test_heartbeat_cooldown_does_not_run_memory_audit(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heartbeat skips memory audit while within cooldown."""
    from ductor_bot.orchestrator.flows import normal

    monkeypatch.setattr(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="Hello"))
    )
    await normal(orch, SessionKey(chat_id=1), "just chatting")

    audit_mock = MagicMock()
    monkeypatch.setattr("ductor_bot.orchestrator.flows.build_integrity_report", audit_mock)
    monkeypatch.setattr(
        orch._cli_service,
        "execute",
        AsyncMock(return_value=_mock_response(result="HEARTBEAT_OK")),
    )

    result = await heartbeat_flow(orch, SessionKey(chat_id=1))

    assert result is None
    audit_mock.assert_not_called()


async def test_heartbeat_prompt_appends_memory_integrity_evidence(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heartbeat prompt includes memory integrity evidence after cooldown."""
    from ductor_bot.orchestrator.flows import normal

    monkeypatch.setattr(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="Hello"))
    )
    await normal(orch, SessionKey(chat_id=1), "init")
    report = MemoryAuditReport(
        ductor_home=str(orch.paths.ductor_home),
        rows=(
            MemoryAuditRow(
                name="main/mainmemory",
                scope="mainmemory",
                agent_name="main",
                fragment_count=1,
                conflict_count=0,
                duplicate_groups=0,
                file_status="EXISTS (10 bytes)",
                runtime_read="FRAGMENTS (20 body chars)",
                warnings=(),
            ),
        ),
    )
    audit_mock = MagicMock(return_value=report)
    monkeypatch.setattr("ductor_bot.orchestrator.flows.build_integrity_report", audit_mock)

    with _past_cooldown():
        hb_mock = AsyncMock(return_value=_mock_response(result="HEARTBEAT_OK"))
        monkeypatch.setattr(orch._cli_service, "execute", hb_mock)
        await heartbeat_flow(orch, SessionKey(chat_id=1), prompt="heartbeat check")

    hb_request = hb_mock.call_args[0][0]
    assert hb_request.prompt.startswith("heartbeat check")
    assert "## Self-Health Evidence" in hb_request.prompt
    assert "Memory integrity: OK" in hb_request.prompt
    audit_mock.assert_called_once_with(orch.paths.ductor_home)


async def test_heartbeat_continues_when_memory_audit_raises(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Memory audit failures are logged and do not block heartbeat."""
    from ductor_bot.orchestrator.flows import normal

    monkeypatch.setattr(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="Hello"))
    )
    await normal(orch, SessionKey(chat_id=1), "init")

    def raise_audit(_ductor_home: object) -> MemoryAuditReport:
        raise RuntimeError("audit failed")

    monkeypatch.setattr("ductor_bot.orchestrator.flows.build_integrity_report", raise_audit)

    with _past_cooldown():
        hb_mock = AsyncMock(return_value=_mock_response(result="HEARTBEAT_OK"))
        monkeypatch.setattr(orch._cli_service, "execute", hb_mock)
        result = await heartbeat_flow(orch, SessionKey(chat_id=1), prompt="heartbeat check")

    assert result is None
    hb_mock.assert_awaited_once()
    hb_request = hb_mock.call_args[0][0]
    assert hb_request.prompt == "heartbeat check"


async def test_heartbeat_runs_after_cooldown(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heartbeat fires when last_active is older than cooldown_minutes."""
    from ductor_bot.orchestrator.flows import normal

    monkeypatch.setattr(
        orch._cli_service, "execute", AsyncMock(return_value=_mock_response(result="Hello"))
    )
    await normal(orch, SessionKey(chat_id=1), "init")

    # Travel 10 minutes into the future -> past default 5 min cooldown
    future = datetime.now(UTC) + timedelta(minutes=10)
    with time_machine.travel(future):
        after_cooldown_mock = AsyncMock(return_value=_mock_response(result="HEARTBEAT_OK"))
        monkeypatch.setattr(orch._cli_service, "execute", after_cooldown_mock)
        result = await heartbeat_flow(orch, SessionKey(chat_id=1))
        assert result is None
        # CLI WAS called (cooldown passed, returned OK)
        after_cooldown_mock.assert_awaited_once()


async def test_heartbeat_skips_when_session_provider_differs_from_configured_provider(
    orch: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heartbeat should skip when active session provider does not match current provider target."""
    orch._providers._available_providers = frozenset({"codex"})

    key = SessionKey(chat_id=1)
    session, _ = await orch._sessions.resolve_session(key, provider="codex", model="opus")
    session.session_id = "legacy-heartbeat-sid"
    await orch._sessions.update_session(session)
    count_before = session.message_count

    with _past_cooldown():
        monkeypatch.setattr(
            orch._cli_service,
            "execute",
            AsyncMock(return_value=_mock_response(result="HEARTBEAT_OK")),
        )
        result = await heartbeat_flow(orch, SessionKey(chat_id=1))
        assert result is None

    session_after = await orch._sessions.get_active(SessionKey(chat_id=1))
    assert session_after is not None
    assert session_after.provider == "codex"
    assert session_after.model == "opus"
    assert session_after.message_count == count_before
