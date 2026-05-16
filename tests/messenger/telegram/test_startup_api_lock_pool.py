from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ductor_bot.messenger.telegram.startup import _run_primary_startup


class _FakeTelegramBot(SimpleNamespace):
    @property
    def _orch(self) -> object:
        return self._orchestrator


@pytest.mark.asyncio
async def test_primary_startup_starts_api_with_shared_lock_pool() -> None:
    lock_pool = object()
    orchestrator = MagicMock()
    orchestrator.paths.chat_activity_path = "chat-activity.json"
    orchestrator._sessions.list_all = AsyncMock(return_value=[])
    orchestrator._sessions.set_topic_name_resolver = MagicMock()
    orchestrator.wire_observers_to_bus = MagicMock()
    orchestrator.set_config_hot_reload_handler = MagicMock()
    orchestrator._observers.heartbeat.set_chat_validator = MagicMock()

    bot = _FakeTelegramBot(
        config=SimpleNamespace(api=SimpleNamespace(enabled=True), update_check=False),
        _agent_name="main",
        _lock_pool=lock_pool,
        _bus=object(),
        _handle_webhook_wake=AsyncMock(),
        _on_auth_hot_reload=MagicMock(),
        _topic_names=SimpleNamespace(seed_from_sessions=MagicMock(return_value=0), resolve=MagicMock()),
        bot_instance=MagicMock(),
        notification_service=MagicMock(),
    )

    with (
        patch(
            "ductor_bot.orchestrator.core.Orchestrator.create",
            new=AsyncMock(return_value=orchestrator),
        ),
        patch("ductor_bot.messenger.telegram.chat_tracker.ChatTracker", return_value=MagicMock()),
        patch(
            "ductor_bot.messenger.telegram.startup.start_api_server",
            new=AsyncMock(),
        ) as start_api_server,
        patch(
            "ductor_bot.messenger.telegram.startup._handle_restart_sentinel",
            new=AsyncMock(return_value=None),
        ),
        patch("ductor_bot.messenger.telegram.startup._handle_recovery", new=AsyncMock()),
        patch("ductor_bot.infra.install.is_upgradeable", return_value=False),
    ):
        await _run_primary_startup(bot)

    start_api_server.assert_awaited_once_with(
        orchestrator,
        bot.config,
        orchestrator.paths,
        lock_pool=lock_pool,
    )
