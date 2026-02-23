"""Tests for cron/execution.py: CLI command building and output parsing."""

from __future__ import annotations

from unittest.mock import patch

from ductor_bot.cli.param_resolver import TaskExecutionConfig
from ductor_bot.cron.execution import (
    build_cmd,
    enrich_instruction,
    indent,
    parse_claude_result,
    parse_codex_result,
    parse_gemini_result,
    parse_result,
)


class TestBuildCmd:
    def test_claude_provider(self) -> None:
        exec_config = TaskExecutionConfig(
            provider="claude",
            model="opus",
            reasoning_effort="",
            cli_parameters=[],
            permission_mode="bypassPermissions",
            working_dir="/tmp",
            file_access="all",
        )
        with patch("ductor_bot.cron.execution.which", return_value="/usr/bin/claude"):
            cmd = build_cmd(exec_config, "hello")
        assert cmd is not None
        assert cmd[0] == "/usr/bin/claude"
        assert "--no-session-persistence" in cmd

    def test_codex_provider(self) -> None:
        exec_config = TaskExecutionConfig(
            provider="codex",
            model="gpt-4",
            reasoning_effort="medium",
            cli_parameters=[],
            permission_mode="bypassPermissions",
            working_dir="/tmp",
            file_access="all",
        )
        with patch("ductor_bot.cron.execution.which", return_value="/usr/bin/codex"):
            cmd = build_cmd(exec_config, "hello")
        assert cmd is not None
        assert cmd[0] == "/usr/bin/codex"
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_codex_full_auto(self) -> None:
        exec_config = TaskExecutionConfig(
            provider="codex",
            model="gpt-4",
            reasoning_effort="medium",
            cli_parameters=[],
            permission_mode="plan",
            working_dir="/tmp",
            file_access="all",
        )
        with patch("ductor_bot.cron.execution.which", return_value="/usr/bin/codex"):
            cmd = build_cmd(exec_config, "hello")
        assert cmd is not None
        assert "--full-auto" in cmd

    def test_returns_none_when_cli_missing(self) -> None:
        exec_config = TaskExecutionConfig(
            provider="claude",
            model="opus",
            reasoning_effort="",
            cli_parameters=[],
            permission_mode="plan",
            working_dir="/tmp",
            file_access="all",
        )
        with patch("ductor_bot.cron.execution.which", return_value=None):
            assert build_cmd(exec_config, "hello") is None

    def test_gemini_provider(self) -> None:
        exec_config = TaskExecutionConfig(
            provider="gemini",
            model="gemini-2.5-pro",
            reasoning_effort="",
            cli_parameters=[],
            permission_mode="bypassPermissions",
            working_dir="/tmp",
            file_access="all",
        )
        with patch("ductor_bot.cron.execution.find_gemini_cli", return_value="/usr/bin/gemini"):
            cmd = build_cmd(exec_config, "hello")
        assert cmd is not None
        assert cmd[0] == "/usr/bin/gemini"
        assert "--approval-mode" in cmd
        assert "yolo" in cmd

    def test_gemini_returns_none_when_cli_missing(self) -> None:
        exec_config = TaskExecutionConfig(
            provider="gemini",
            model="gemini-2.5-pro",
            reasoning_effort="",
            cli_parameters=[],
            permission_mode="plan",
            working_dir="/tmp",
            file_access="all",
        )
        with patch(
            "ductor_bot.cron.execution.find_gemini_cli",
            side_effect=FileNotFoundError("not found"),
        ):
            assert build_cmd(exec_config, "hello") is None

    def test_unknown_provider_falls_back_to_claude(self) -> None:
        exec_config = TaskExecutionConfig(
            provider="unknown",
            model="model",
            reasoning_effort="",
            cli_parameters=[],
            permission_mode="plan",
            working_dir="/tmp",
            file_access="all",
        )
        with patch("ductor_bot.cron.execution.which", return_value="/usr/bin/claude"):
            cmd = build_cmd(exec_config, "hello")
        assert cmd is not None
        assert cmd[0] == "/usr/bin/claude"


class TestEnrichInstruction:
    def test_appends_memory_instructions(self) -> None:
        result = enrich_instruction("Do the work", "daily-report")
        assert "daily-report_MEMORY.md" in result
        assert "Do the work" in result

    def test_preserves_original(self) -> None:
        original = "Original instruction"
        result = enrich_instruction(original, "weekly")
        assert result.startswith(original)


class TestParseClaude:
    def test_parses_json(self) -> None:
        import json

        stdout = json.dumps({"result": "Hello world"}).encode()
        assert parse_claude_result(stdout) == "Hello world"

    def test_empty_bytes(self) -> None:
        assert parse_claude_result(b"") == ""

    def test_non_json_returns_raw(self) -> None:
        raw = b"Some raw text output"
        assert parse_claude_result(raw) == "Some raw text output"


class TestParseCodex:
    def test_empty_bytes(self) -> None:
        assert parse_codex_result(b"") == ""


class TestParseGemini:
    def test_empty_bytes(self) -> None:
        assert parse_gemini_result(b"") == ""

    def test_json_response(self) -> None:
        import json

        data = json.dumps([{"type": "message", "role": "model", "content": "Result text"}])
        result = parse_gemini_result(data.encode())
        assert "Result text" in result

    def test_non_json_returns_raw(self) -> None:
        raw = b"Raw gemini output"
        assert parse_gemini_result(raw) == "Raw gemini output"


class TestParseResult:
    def test_dispatches_to_gemini_parser(self) -> None:
        assert parse_result("gemini", b'{"result":"ok"}') == "ok"

    def test_unknown_provider_falls_back_to_claude(self) -> None:
        assert parse_result("unknown", b'{"result":"fallback"}') == "fallback"


class TestIndent:
    def test_indents_lines(self) -> None:
        result = indent("a\nb\nc", "  ")
        assert result == "  a\n  b\n  c"

    def test_single_line(self) -> None:
        assert indent("hello", ">> ") == ">> hello"
