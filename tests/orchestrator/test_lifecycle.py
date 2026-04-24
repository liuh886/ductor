from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ductor_bot.orchestrator.lifecycle import create_orchestrator


@pytest.mark.asyncio
async def test_create_orchestrator_does_not_start_api_server_directly(
    workspace: tuple[object, object],
) -> None:
    paths, config = workspace
    config.api.enabled = True

    orch = MagicMock()
    orch._providers.available_providers = frozenset({"claude"})
    orch._providers.apply_auth_results = MagicMock()
    orch._providers.init_gemini_state = MagicMock()
    orch._observers.init_model_caches = AsyncMock(return_value=object())
    orch._observers.init_task_observers = MagicMock()
    orch._observers.start_all = AsyncMock()
    orch._observers.start_config_reloader = AsyncMock()
    orch._providers.on_gemini_models_refresh = MagicMock()

    with (
        patch("ductor_bot.orchestrator.lifecycle.resolve_paths", return_value=paths),
        patch("ductor_bot.orchestrator.lifecycle.inject_runtime_environment"),
        patch("ductor_bot.orchestrator.core.Orchestrator", return_value=orch),
        patch("ductor_bot.cli.auth.check_all_auth", return_value={}),
        patch("ductor_bot.orchestrator.lifecycle.start_api_server", new=AsyncMock()) as start_api_server,
    ):
        result = await create_orchestrator(config)

    assert result is orch
    start_api_server.assert_not_awaited()
