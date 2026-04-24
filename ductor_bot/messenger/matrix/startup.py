"""Matrix-specific startup sequence.

Reuses orchestrator creation from the core but skips Telegram-specific
parts (bot username lookup, command registration, group audit).
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from ductor_bot.i18n import t
from ductor_bot.infra.restart import consume_restart_marker
from ductor_bot.orchestrator.lifecycle import start_api_server

if TYPE_CHECKING:
    from ductor_bot.messenger.matrix.bot import MatrixBot

logger = logging.getLogger(__name__)


async def _handle_recovery(bot: MatrixBot) -> None:
    """Handle interrupted foreground/named-session recovery for Matrix."""
    from ductor_bot.infra.recovery import RecoveryPlanner
    from ductor_bot.text.response_format import recovery_notification_text

    planner = RecoveryPlanner(
        inflight=bot._orch.inflight_tracker,
        named_sessions=bot._orch.named_sessions.pop_recovered_running(),
        max_age_seconds=bot._config.timeouts.normal * 2,
    )
    for action in planner.plan():
        note = recovery_notification_text(action.kind, action.prompt_preview, action.session_name)
        await bot.notification_service.notify(action.chat_id, note)
        if action.kind == "named_session" and action.session_name:
            with contextlib.suppress(Exception):
                bot._orch.submit_named_followup_bg(
                    action.chat_id,
                    action.session_name,
                    action.prompt_preview,
                    message_id=0,
                    thread_id=None,
                )
    bot._orch.inflight_tracker.clear()


async def run_matrix_startup(bot: MatrixBot) -> None:
    """Matrix-specific startup: orchestrator, observers, recovery.

    When ``bot._orchestrator`` is already set (secondary transport mode),
    orchestrator creation and all primary-only steps are skipped.
    """
    primary = bot._orchestrator is None

    if primary:
        from ductor_bot.orchestrator.core import Orchestrator

        bot._orchestrator = await Orchestrator.create(bot._config, agent_name=bot._agent_name)

        # Wire all observers + injector to bus in one call
        bot._orchestrator.wire_observers_to_bus(bot._bus)
        if bot._config.api.enabled:
            await start_api_server(
                bot._orchestrator,
                bot._config,
                bot._orchestrator.paths,
                lock_pool=bot._lock_pool,
            )

        # Handle restart sentinel
        restart_reason = _consume_restart_sentinel(bot)

        # Notify restart
        if restart_reason:
            await bot.notification_service.notify_all(
                t("startup.matrix_restart", reason=restart_reason)
            )

        await _handle_recovery(bot)

        # Update checker
        try:
            from ductor_bot.infra.install import is_upgradeable
            from ductor_bot.infra.updater import UpdateObserver
            from ductor_bot.infra.version import VersionInfo

            if is_upgradeable() and bot._config.update_check and bot._agent_name == "main":

                async def _on_update(info: VersionInfo) -> None:
                    await bot.notification_service.notify_all(
                        t("startup.matrix_update", version=info.latest)
                    )

                bot._update_observer = UpdateObserver(notify=_on_update)
                bot._update_observer.start()
        except ImportError:
            pass

    logger.info(
        "Matrix bot online: %s on %s",
        bot._config.matrix.user_id,
        bot._config.matrix.homeserver,
    )

    # Run registered startup hooks (supervisor injection)
    for hook in bot._startup_hooks:
        await hook()


def _consume_restart_sentinel(bot: MatrixBot) -> str:
    """Check and consume restart marker."""
    paths_obj = bot._orchestrator.paths if bot._orchestrator else None
    if paths_obj is None:
        return ""
    marker_path = paths_obj.ductor_home / "restart-requested"
    if consume_restart_marker(marker_path=marker_path):
        return "restart marker"
    return ""
