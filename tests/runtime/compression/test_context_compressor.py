"""Tests for runtime context compression."""

# ruff: noqa: INP001

from __future__ import annotations

from pathlib import Path

from ductor_bot.runtime.compression import ContextCompressor, SummarySelector, ToolOutputPruner
from ductor_bot.runtime.state import MessageRepository, RuntimeStateDB, SessionSummaryRepository


def _seed_messages(repo: MessageRepository, session_key: str, count: int) -> None:
    for idx in range(count):
        repo.append(
            session_key,
            "user" if idx % 2 == 0 else "assistant",
            f"message {idx} " + ("x" * 80),
            source="normal_result" if idx % 2 else "normal_prompt",
        )


def test_tool_output_pruner_truncates_large_payloads() -> None:
    pruner = ToolOutputPruner(max_chars=20)
    text = pruner.prune("a" * 40, source="tool_result")
    assert text.endswith("[tool output truncated]")


def test_context_compressor_builds_summary_and_tail(tmp_path: Path) -> None:
    db = RuntimeStateDB(tmp_path / "state.db")
    message_repo = MessageRepository(db)
    summary_repo = SessionSummaryRepository(db)
    _seed_messages(message_repo, "tg:1", 10)

    compressor = ContextCompressor(SummarySelector(message_repo, summary_repo))
    prefix = compressor.build_prompt_prefix("tg:1")

    assert "## COMPRESSED CONTEXT" in prefix
    assert "Older session summary:" in prefix
    assert "Protected recent tail:" in prefix
    assert "message 9" in prefix

    summary = summary_repo.latest_for_session("tg:1", kind="runtime_context")
    assert summary is not None
    assert summary["coverage_to_message_id"] is not None


def test_context_compressor_skips_short_sessions(tmp_path: Path) -> None:
    db = RuntimeStateDB(tmp_path / "state.db")
    message_repo = MessageRepository(db)
    summary_repo = SessionSummaryRepository(db)
    _seed_messages(message_repo, "tg:2", 4)

    compressor = ContextCompressor(SummarySelector(message_repo, summary_repo))
    assert compressor.build_prompt_prefix("tg:2") == ""
