"""Prompt-context compression built on persisted runtime state."""

from __future__ import annotations

from ductor_bot.runtime.compression.summary_selector import SummarySelector
from ductor_bot.runtime.compression.tool_output_pruner import ToolOutputPruner


class ContextCompressor:
    """Generate additive prompt context from persisted summaries + protected tail."""

    def __init__(self, selector: SummarySelector) -> None:
        self._selector = selector
        self._pruner = ToolOutputPruner()

    def build_prompt_prefix(self, session_storage_key: str) -> str:
        """Return a compact context prefix for a session, or empty string when not needed."""
        selection = self._selector.select(session_storage_key)
        if not selection.summary_text:
            return ""

        lines = [
            "## COMPRESSED CONTEXT",
            "Older session summary:",
            selection.summary_text,
            "",
            "Protected recent tail:",
        ]
        for message in selection.tail_messages:
            content = self._pruner.prune(
                str(message.get("content_text", "")),
                source=str(message.get("source", "")),
            )
            if not content:
                continue
            lines.append(
                f"- {message.get('role', 'unknown')}/{message.get('source', 'normal')}: {content}"
            )
        lines.append("")
        lines.append("Use this compressed context when continuing the session.")
        return "\n".join(lines)
