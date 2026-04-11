"""Text-pruning helpers used by runtime context compression."""

from __future__ import annotations


class ToolOutputPruner:
    """Apply deterministic truncation rules to large runtime text blocks."""

    def __init__(self, *, max_chars: int = 220) -> None:
        self._max_chars = max_chars

    def prune(self, text: str, *, source: str = "") -> str:
        """Trim large messages while preserving enough signal for prompt context."""
        cleaned = " ".join(text.split())
        if len(cleaned) <= self._max_chars:
            return cleaned
        label = "tool output" if "tool" in source else "message"
        return f"{cleaned[: self._max_chars].rstrip()} ... [{label} truncated]"
