"""Tests for cli/base.py: docker_wrap helper."""

from __future__ import annotations

from ductor_bot.cli.base import CLIConfig, docker_wrap


def test_docker_wrap_without_container() -> None:
    cmd = ["claude", "-p", "hello"]
    cfg = CLIConfig(docker_container="", chat_id=123, working_dir="/workspace")
    result_cmd, cwd = docker_wrap(cmd, cfg)
    assert result_cmd == cmd
    assert cwd == "/workspace"


def test_docker_wrap_with_container() -> None:
    cmd = ["claude", "-p", "hello"]
    cfg = CLIConfig(docker_container="my-sandbox", chat_id=42, working_dir="/workspace")
    result_cmd, cwd = docker_wrap(cmd, cfg)
    assert result_cmd == [
        "docker",
        "exec",
        "-e",
        "DUCTOR_CHAT_ID=42",
        "my-sandbox",
        "claude",
        "-p",
        "hello",
    ]
    assert cwd is None


def test_docker_wrap_interactive() -> None:
    cmd = ["gemini", "--output-format", "json"]
    cfg = CLIConfig(docker_container="my-sandbox", chat_id=42, working_dir="/workspace")
    result_cmd, cwd = docker_wrap(cmd, cfg, interactive=True)
    assert result_cmd == [
        "docker",
        "exec",
        "-i",
        "-e",
        "DUCTOR_CHAT_ID=42",
        "my-sandbox",
        "gemini",
        "--output-format",
        "json",
    ]
    assert cwd is None


def test_docker_wrap_preserves_full_command() -> None:
    cmd = ["claude", "-p", "test", "--model", "opus", "--verbose"]
    cfg = CLIConfig(docker_container="sandbox", chat_id=1, working_dir="/w")
    result_cmd, _ = docker_wrap(cmd, cfg)
    assert result_cmd[-6:] == cmd


def test_docker_wrap_injects_chat_id() -> None:
    cmd = ["codex", "exec"]
    cfg = CLIConfig(docker_container="box", chat_id=999, working_dir="/w")
    result_cmd, _ = docker_wrap(cmd, cfg)
    assert "DUCTOR_CHAT_ID=999" in result_cmd


def test_docker_wrap_extra_env() -> None:
    cmd = ["gemini"]
    cfg = CLIConfig(docker_container="box", chat_id=1, working_dir="/w")
    result_cmd, _ = docker_wrap(cmd, cfg, extra_env={"FOO": "bar"})
    assert "-e" in result_cmd
    assert "FOO=bar" in result_cmd
