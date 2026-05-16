"""Deterministic summary selection for persisted runtime messages."""

from __future__ import annotations

from dataclasses import dataclass

from ductor_bot.runtime.compression.tool_output_pruner import ToolOutputPruner
from ductor_bot.runtime.state.repositories.message_repo import MessageRepository
from ductor_bot.runtime.state.repositories.session_summary_repo import SessionSummaryRepository

_SUMMARY_KIND = "runtime_context"
_SUMMARY_VERSION = "v1"


@dataclass(slots=True)
class SummarySelection:
    """Selected summary text plus the protected tail used to build prompt context."""

    summary_text: str
    tail_messages: list[dict[str, object]]
    covered_to_message_id: int | None = None


class SummarySelector:
    """Select or generate a reusable summary over persisted runtime messages."""

    def __init__(
        self,
        message_repo: MessageRepository,
        summary_repo: SessionSummaryRepository,
        *,
        trigger_messages: int = 8,
        protected_tail: int = 2,
        max_summary_items: int = 6,
    ) -> None:
        self._message_repo = message_repo
        self._summary_repo = summary_repo
        self._trigger_messages = trigger_messages
        self._protected_tail = protected_tail
        self._max_summary_items = max_summary_items
        self._pruner = ToolOutputPruner()

    def select(self, session_storage_key: str) -> SummarySelection:
        """Return summary + protected tail for a session."""
        messages = self._message_repo.list_by_session(session_storage_key)
        if len(messages) <= self._trigger_messages:
            return SummarySelection(summary_text="", tail_messages=messages)

        tail_messages = messages[-self._protected_tail :]
        older_messages = messages[: -self._protected_tail]
        if not older_messages:
            return SummarySelection(summary_text="", tail_messages=tail_messages)

        coverage_to = int(older_messages[-1]["id"])
        cached = self._summary_repo.latest_for_session(session_storage_key, kind=_SUMMARY_KIND)
        if cached is not None and int(cached.get("coverage_to_message_id") or 0) == coverage_to:
            return SummarySelection(
                summary_text=str(cached.get("summary_text", "")),
                tail_messages=tail_messages,
                covered_to_message_id=coverage_to,
            )

        summary_text = self._build_summary(older_messages)
        self._summary_repo.create(
            session_storage_key,
            _SUMMARY_KIND,
            summary_text,
            coverage_from_message_id=int(older_messages[0]["id"]),
            coverage_to_message_id=coverage_to,
            version=_SUMMARY_VERSION,
        )
        return SummarySelection(
            summary_text=summary_text,
            tail_messages=tail_messages,
            covered_to_message_id=coverage_to,
        )

    def _build_summary(self, messages: list[dict[str, object]]) -> str:
        """Build a compact deterministic bullet summary from older messages."""
        window = messages[-self._max_summary_items :]
        lines: list[str] = []
        for message in window:
            content = self._pruner.prune(
                str(message.get("content_text", "")),
                source=str(message.get("source", "")),
            )
            if not content:
                continue
            lines.append(
                f"- {message.get('role', 'unknown')}/{message.get('source', 'normal')}: {content}"
            )
        return "\n".join(lines)
