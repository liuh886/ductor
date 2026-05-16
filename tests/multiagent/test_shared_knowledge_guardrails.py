"""Focused tests for shared-memory watcher guardrails."""

# ruff: noqa: TC002

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ductor_bot.multiagent.shared_knowledge import (
    _MAX_SHARED_BYTES,
    _MAX_SHARED_LINES,
    SharedKnowledgeSync,
    _audit_shared_memory,
)


class TestSharedKnowledgeAudit:
    def test_allows_short_benign_alerts(self) -> None:
        report = _audit_shared_memory("Agent alpha owns the port migration.\nAsk beta for logs.")

        assert report.line_count == 2
        assert report.byte_count > 0
        assert report.secret_line_numbers == ()
        assert report.warnings == ()

    def test_warns_when_nonempty_line_budget_is_exceeded(self) -> None:
        text = "\n".join(f"line {index}" for index in range(_MAX_SHARED_LINES + 1))

        report = _audit_shared_memory(text)

        assert any("keep it under" in warning for warning in report.warnings)

    def test_warns_when_content_is_too_large(self) -> None:
        report = _audit_shared_memory("x" * (_MAX_SHARED_BYTES + 1))

        assert any("too large" in warning for warning in report.warnings)

    def test_warns_on_secret_like_lines_without_logging_values(self) -> None:
        report = _audit_shared_memory(
            "Coordination note\n"
            "gateway_key = prod-super-secret-value\n"
            "api_token: ghp_1234567890abcdefghijklmnopqrstuvwxyz\n"
        )

        assert report.secret_line_numbers == (2, 3)
        assert any("secret-like" in warning for warning in report.warnings)
        assert all("prod-super-secret-value" not in warning for warning in report.warnings)
        assert all("ghp_1234567890abcdefghijklmnopqrstuvwxyz" not in warning for warning in report.warnings)


class TestSharedKnowledgeWatcher:
    async def test_start_creates_seed_file_with_guardrail_guidance(self, tmp_path: Path) -> None:
        shared_path = tmp_path / "SHAREDMEMORY.md"
        sync = SharedKnowledgeSync(shared_path, MagicMock())
        sync._watcher.start = AsyncMock()
        sync._watcher.update_mtime = AsyncMock()

        await sync.start()

        content = shared_path.read_text(encoding="utf-8")
        assert "Keep this file short" in content
        assert "Do not put secrets here" in content
        sync._watcher.start.assert_awaited_once()
        sync._watcher.update_mtime.assert_awaited_once()

    async def test_on_changed_logs_guardrail_warnings(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        shared_path = tmp_path / "SHAREDMEMORY.md"
        shared_path.write_text(
            "status ok\n"
            "gateway_key = prod-super-secret-value\n"
            + "\n".join(f"line {index}" for index in range(_MAX_SHARED_LINES)),
            encoding="utf-8",
        )
        sync = SharedKnowledgeSync(shared_path, MagicMock())

        with caplog.at_level("WARNING"):
            await sync._on_changed()

        assert "secret-like" in caplog.text
        assert "keep it under" in caplog.text
        assert "prod-super-secret-value" not in caplog.text
