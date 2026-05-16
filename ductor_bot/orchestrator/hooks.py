"""Centralized message hook system for injecting prompts based on session state."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HookContext:
    """Immutable snapshot of session state passed to hook conditions."""

    chat_id: int
    message_count: int
    is_new_session: bool
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class MessageHook:
    """A named hook that appends text to the prompt when its condition is met."""

    name: str
    condition: Callable[[HookContext], bool]
    suffix: str


class MessageHookRegistry:
    """Registry of message hooks. Applied before each CLI call."""

    def __init__(self) -> None:
        self._hooks: list[MessageHook] = []

    def register(self, hook: MessageHook) -> None:
        """Register a new message hook."""
        self._hooks.append(hook)
        logger.debug("Hook registered: %s", hook.name)

    def apply(self, prompt: str, ctx: HookContext) -> str:
        """Evaluate all hooks and append matching suffixes to the prompt."""
        suffixes: list[str] = []
        for hook in self._hooks:
            if hook.condition(ctx):
                logger.info("Hook fired: %s msgs=%d", hook.name, ctx.message_count)
                suffixes.append(hook.suffix)
        if not suffixes:
            return prompt
        return prompt + "\n\n" + "\n\n".join(suffixes)


# ---------------------------------------------------------------------------
# Reusable condition factories
# ---------------------------------------------------------------------------


def every_n_messages(n: int) -> Callable[[HookContext], bool]:
    """Fire on every n-th message (6th, 12th, 18th, ...). Never on first message."""

    def _check(ctx: HookContext) -> bool:
        # message_count is pre-increment (0-indexed at call time).
        # count=5 means this is the 6th message about to be sent.
        effective = ctx.message_count + 1
        return effective >= n and effective % n == 0

    return _check


def on_new_session(ctx: HookContext) -> bool:
    """Fire only on the very first message of a new session."""
    return ctx.is_new_session


def _is_delegation_reminder_due(ctx: HookContext) -> bool:
    """Fire every 15th message, but not on new sessions (DELEGATION_BRIEF covers those)."""
    if ctx.is_new_session:
        return False
    effective = ctx.message_count + 1
    return effective >= 15 and effective % 15 == 0


# ---------------------------------------------------------------------------
# Built-in hooks
# ---------------------------------------------------------------------------

MAINMEMORY_REMINDER = MessageHook(
    name="mainmemory_reminder",
    condition=every_n_messages(6),
    suffix=(
        "## Memory Check\n"
        "Quietly compare this conversation with `memory_system/MAINMEMORY.md`.\n"
        "Update durable memory only for reusable facts, preferences, decisions, or obligations.\n"
        "If a single missing detail from the user would materially improve future work, ask one natural follow-up question."
    ),
)

DELEGATION_BRIEF = MessageHook(
    name="delegation_brief",
    condition=on_new_session,
    suffix=(
        "## Routing Reminder\n"
        "Start by deciding whether to answer directly or route the work.\n"
        "Use `capability-router` plus background workers for tasks that are multi-step, cross-functional, long-running, or require formal deliverables.\n"
        "Task tools:\n"
        '- Create: `tools/task_tools/create_task.py --name "..." "prompt with full context"`\n'
        "- Cancel: `tools/task_tools/cancel_task.py TASK_ID`\n"
        '- Resume: `tools/task_tools/resume_task.py TASK_ID "follow-up"`\n'
        "Keep the user-facing response focused on progress, outcomes, and the next useful step."
    ),
)

DELEGATION_REMINDER = MessageHook(
    name="delegation_reminder",
    condition=_is_delegation_reminder_due,
    suffix=(
        "## Routing Reminder\n"
        "If the work now looks multi-step or long-running, route it instead of forcing a one-shot answer.\n"
        "Resume existing tasks for follow-ups so the delegated context stays intact."
    ),
)
