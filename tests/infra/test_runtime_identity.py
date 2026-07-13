"""Tests for runtime identity persistence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from ductor_bot.infra import runtime_identity
from ductor_bot.infra.runtime_identity import (
    build_runtime_identity,
    current_git_head,
    write_runtime_identity,
)


def test_current_git_head_returns_stdout(tmp_path: Path) -> None:
    completed = Mock(returncode=0, stdout="abcdef\n")

    with patch("ductor_bot.infra.runtime_identity.subprocess.run", return_value=completed) as run:
        assert current_git_head(tmp_path) == "abcdef"

    assert run.call_args.kwargs["cwd"] == tmp_path
    assert run.call_args.kwargs["timeout"] == runtime_identity.GIT_HEAD_TIMEOUT_SECONDS
    assert run.call_args.kwargs["creationflags"] == runtime_identity.CREATION_FLAGS


def test_current_git_head_returns_empty_on_failure(tmp_path: Path) -> None:
    completed = Mock(returncode=1, stdout="")

    with patch("ductor_bot.infra.runtime_identity.subprocess.run", return_value=completed):
        assert current_git_head(tmp_path) == ""


def test_current_git_head_returns_empty_on_timeout(tmp_path: Path) -> None:
    with patch(
        "ductor_bot.infra.runtime_identity.subprocess.run",
        side_effect=runtime_identity.subprocess.TimeoutExpired(cmd=["git"], timeout=1),
    ):
        assert current_git_head(tmp_path) == ""


def test_build_runtime_identity_includes_checkout_head(tmp_path: Path) -> None:
    with patch("ductor_bot.infra.runtime_identity.current_git_head", return_value="head"):
        payload = build_runtime_identity(framework_root=tmp_path, pid=123)

    assert payload["framework_root"] == str(tmp_path.resolve())
    assert payload["git_head"] == "head"
    assert payload["pid"] == 123
    assert isinstance(payload["started_at"], str)


def test_write_runtime_identity_is_best_effort(tmp_path: Path) -> None:
    with (
        patch("ductor_bot.infra.runtime_identity.build_runtime_identity", return_value={}),
        patch("ductor_bot.infra.runtime_identity.atomic_json_save", side_effect=OSError),
    ):
        assert write_runtime_identity(tmp_path / "identity.json", framework_root=tmp_path) is False
