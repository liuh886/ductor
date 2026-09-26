"""Tests for cli/factory.py: create_cli backend selection."""

from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest

from ductor_bot.cli.base import CLIConfig
from ductor_bot.cli.claude_provider import ClaudeCodeCLI
from ductor_bot.cli.codex_provider import CodexCLI
from ductor_bot.cli.factory import create_cli
from ductor_bot.cli.gemini_provider import GeminiCLI

_requires_claude_cli = pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not installed on PATH",
)


@_requires_claude_cli
def test_create_cli_returns_claude_by_default() -> None:
    cli = create_cli(CLIConfig(provider="claude"))
    assert isinstance(cli, ClaudeCodeCLI)


def test_create_cli_returns_codex() -> None:
    cli = create_cli(CLIConfig(provider="codex"))
    assert isinstance(cli, CodexCLI)


def test_create_cli_returns_gemini() -> None:
    with (
        patch("ductor_bot.cli.gemini_provider.find_gemini_cli", return_value="/usr/bin/gemini"),
        patch("ductor_bot.cli.gemini_provider.find_gemini_cli_js", return_value=None),
    ):
        cli = create_cli(CLIConfig(provider="gemini"))
    assert isinstance(cli, GeminiCLI)


@_requires_claude_cli
def test_create_cli_returns_claude_backend_for_mimo() -> None:
    cli = create_cli(CLIConfig(provider="mimo", model="mimo-v2.5-pro"))
    assert isinstance(cli, ClaudeCodeCLI)


@_requires_claude_cli
def test_create_cli_unknown_provider_returns_claude() -> None:
    cli = create_cli(CLIConfig(provider="unknown"))
    assert isinstance(cli, ClaudeCodeCLI)
