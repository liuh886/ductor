from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ductor_bot.messenger.matrix.startup import run_matrix_startup


@pytest.mark.asyncio
async def test_matrix_primary_startup_starts_api_with_shared_lock_pool() -> None:
    lock_pool = object()
    orchestrator = MagicMock()
    orchestrator.paths = MagicMock()
    orchestrator.wire_observers_to_bus = MagicMock()
    orchestrator.inflight_tracker = MagicMock()
    orchestrator.named_sessions.pop_recovered_running = MagicMock(return_value=[])

    bot = SimpleNamespace(
        _orchestrator=None,
        _orch=orchestrator,
        _config=SimpleNamespace(
            api=SimpleNamespace(enabled=True),
            update_check=False,
            timeouts=SimpleNamespace(normal=60),
            matrix=SimpleNamespace(user_id="@bot:example", homeserver="https://matrix.example"),
        ),
        _agent_name="main",
        _bus=object(),
        _lock_pool=lock_pool,
        _startup_hooks=[],
        notification_service=SimpleNamespace(notify=AsyncMock(), notify_all=AsyncMock()),
    )

    with (
        patch(
            "ductor_bot.orchestrator.core.Orchestrator.create",
            new=AsyncMock(return_value=orchestrator),
        ),
        patch("ductor_bot.messenger.matrix.startup.start_api_server", new=AsyncMock()) as start_api_server,
        patch("ductor_bot.messenger.matrix.startup._consume_restart_sentinel", return_value=""),
        patch("ductor_bot.infra.recovery.RecoveryPlanner") as planner_cls,
        patch("ductor_bot.infra.install.is_upgradeable", return_value=False),
    ):
        planner_cls.return_value.plan.return_value = []
        await run_matrix_startup(bot)

    start_api_server.assert_awaited_once_with(
        orchestrator,
        bot._config,
        orchestrator.paths,
        lock_pool=lock_pool,
    )


@pytest.mark.asyncio
async def test_matrix_primary_startup_runs_recovery_flow() -> None:
    orchestrator = MagicMock()
    orchestrator.paths = MagicMock()
    orchestrator.wire_observers_to_bus = MagicMock()
    orchestrator.inflight_tracker = MagicMock()
    orchestrator.inflight_tracker.clear = MagicMock()
    orchestrator.named_sessions.pop_recovered_running = MagicMock(return_value=[])

    bot = SimpleNamespace(
        _orchestrator=None,
        _orch=orchestrator,
        _config=SimpleNamespace(
            api=SimpleNamespace(enabled=False),
            update_check=False,
            timeouts=SimpleNamespace(normal=90),
            matrix=SimpleNamespace(user_id="@bot:example", homeserver="https://matrix.example"),
        ),
        _agent_name="main",
        _bus=object(),
        _lock_pool=object(),
        _startup_hooks=[],
        notification_service=SimpleNamespace(notify=AsyncMock(), notify_all=AsyncMock()),
    )

    with (
        patch(
            "ductor_bot.orchestrator.core.Orchestrator.create",
            new=AsyncMock(return_value=orchestrator),
        ),
        patch("ductor_bot.messenger.matrix.startup._consume_restart_sentinel", return_value=""),
        patch("ductor_bot.infra.recovery.RecoveryPlanner") as planner_cls,
        patch("ductor_bot.infra.install.is_upgradeable", return_value=False),
    ):
        planner = planner_cls.return_value
        planner.plan.return_value = []
        await run_matrix_startup(bot)

    planner_cls.assert_called_once_with(
        inflight=orchestrator.inflight_tracker,
        named_sessions=[],
        max_age_seconds=180,
    )
    orchestrator.inflight_tracker.clear.assert_called_once_with()
