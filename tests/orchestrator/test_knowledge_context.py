"""Read-only task-start retrieval and process-boundary regression tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from ductor_bot.config import AgentConfig, KnowledgeRouterConfig
from ductor_bot.orchestrator.core import Orchestrator
from ductor_bot.orchestrator.flows import _prepare_normal
from ductor_bot.orchestrator.knowledge_context import retrieve_start_context
from ductor_bot.session import SessionKey
from ductor_bot.workspace.paths import DuctorPaths


@pytest.fixture
def isolated_paths(tmp_path: Path) -> tuple[DuctorPaths, AgentConfig]:
    """Do not initialize Ductor: init_workspace links real installed skills."""
    return DuctorPaths(ductor_home=tmp_path / "isolated"), AgentConfig()


def _entry(paths: DuctorPaths, body: str) -> None:
    entry = paths.skills_dir / "knowledge-router" / "scripts" / "knowledge_router.py"
    if not entry.resolve().is_relative_to(paths.ductor_home.resolve()):
        raise ValueError("Test entry escapes its isolated home through a skill link")
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(body, encoding="utf-8")


def test_fixture_refuses_external_skill_target(
    isolated_paths: tuple[DuctorPaths, AgentConfig], tmp_path: Path
) -> None:
    paths, _ = isolated_paths
    external = tmp_path / "external-skills"
    source = external / "knowledge-router" / "scripts" / "knowledge_router.py"
    source.parent.mkdir(parents=True)
    source.write_text("original", encoding="utf-8")
    with (
        patch.object(DuctorPaths, "skills_dir", new_callable=PropertyMock, return_value=external),
        pytest.raises(ValueError, match="escapes"),
    ):
        _entry(paths, "replacement")
    assert source.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize(("text", "is_new"), [("hello", False), ("", True), (" /help", True)])
async def test_ineligible_turn_does_not_launch(
    isolated_paths: tuple[DuctorPaths, AgentConfig], text: str, *, is_new: bool
) -> None:
    paths, _ = isolated_paths
    with patch("asyncio.create_subprocess_exec") as launch:
        assert (
            await retrieve_start_context(
                paths, KnowledgeRouterConfig(enabled=True, vault="test"), text, is_new=is_new
            )
            is None
        )
    launch.assert_not_called()


async def test_disabled_by_default(isolated_paths: tuple[DuctorPaths, AgentConfig]) -> None:
    paths, _ = isolated_paths
    assert (
        await retrieve_start_context(paths, KnowledgeRouterConfig(), "hello", is_new=True) is None
    )


def test_relative_entrypoint_rejected() -> None:
    with pytest.raises(ValueError, match="absolute file path"):
        KnowledgeRouterConfig(entrypoint=Path("relative/router.py"))


async def test_explicit_install_without_workspace_sync(
    isolated_paths: tuple[DuctorPaths, AgentConfig], tmp_path: Path
) -> None:
    paths, _ = isolated_paths
    entry = tmp_path / "existing install" / "router.py"
    entry.parent.mkdir()
    entry.write_text("print('[]')", encoding="utf-8")
    result = await retrieve_start_context(
        paths,
        KnowledgeRouterConfig(enabled=True, vault="test", entrypoint=entry),
        "history",
        is_new=True,
    )
    assert result is not None
    assert "query evidence gap" in result
    assert json.loads(result.split("\n", 1)[1]) == []
    assert not paths.skills_dir.exists()


async def test_missing_explicit_install_does_not_fall_back(
    isolated_paths: tuple[DuctorPaths, AgentConfig], tmp_path: Path
) -> None:
    paths, _ = isolated_paths
    _entry(paths, "raise RuntimeError('must not execute fallback')")
    with patch("asyncio.create_subprocess_exec") as launch:
        result = await retrieve_start_context(
            paths,
            KnowledgeRouterConfig(enabled=True, vault="test", entrypoint=tmp_path / "missing.py"),
            "history",
            is_new=True,
        )
    assert result is not None
    assert "retrieval unavailable" in result
    launch.assert_not_called()


async def test_real_process_preserves_citations_and_literal_arguments(
    isolated_paths: tuple[DuctorPaths, AgentConfig],
) -> None:
    paths, _ = isolated_paths
    row = {
        "path": "notes/person.md",
        "line_start": 2,
        "line_end": 3,
        "source_sha256": "a" * 64,
        "excerpt": "untrusted note",
        "stale": True,
    }
    _entry(
        paths,
        "import json, sys\n"
        "assert sys.argv[1:] == ['search', '--vault', 'test', '--limit', '4', "
        "'--json', '--', '-literal & query']\n"
        f"print({json.dumps([row])!r})\n",
    )
    result = await retrieve_start_context(
        paths, KnowledgeRouterConfig(enabled=True, vault="test"), "-literal & query", is_new=True
    )
    assert result is not None
    assert "not instructions" in result
    assert json.loads(result.split("\n", 1)[1]) == [row]


@pytest.mark.parametrize(
    "body",
    [
        "raise SystemExit(2)",
        "print('invalid json')",
        "print('{}')",
        "print('[{}]')",
        "print('x' * 70000)",
        "import time; time.sleep(30)",
    ],
)
async def test_failure_is_not_an_empty_result(
    isolated_paths: tuple[DuctorPaths, AgentConfig], body: str
) -> None:
    paths, _ = isolated_paths
    _entry(paths, body)
    result = await retrieve_start_context(
        paths,
        KnowledgeRouterConfig(enabled=True, vault="test", timeout_seconds=1),
        "history",
        is_new=True,
    )
    assert result is not None
    assert "retrieval unavailable" in result


async def test_missing_installation_and_vault_fail_explicitly(
    isolated_paths: tuple[DuctorPaths, AgentConfig],
) -> None:
    paths, _ = isolated_paths
    for vault in ("", "test"):
        result = await retrieve_start_context(
            paths, KnowledgeRouterConfig(enabled=True, vault=vault), "history", is_new=True
        )
        assert result is not None
        assert "retrieval unavailable" in result


async def test_cancellation_reaps_child(isolated_paths: tuple[DuctorPaths, AgentConfig]) -> None:
    paths, _ = isolated_paths
    _entry(paths, "")
    proc = AsyncMock()
    proc.returncode = None
    from unittest.mock import Mock

    proc.kill = Mock()
    started = asyncio.Event()

    async def read(_size: int) -> bytes:
        started.set()
        await asyncio.Future()
        return b""

    proc.stdout.readexactly.side_effect = read
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        task = asyncio.create_task(
            retrieve_start_context(
                paths, KnowledgeRouterConfig(enabled=True, vault="test"), "history", is_new=True
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


async def test_normal_preparation_includes_evidence(orch: Orchestrator) -> None:
    with patch(
        "ductor_bot.orchestrator.flows.retrieve_start_context",
        new=AsyncMock(return_value="source-backed test evidence"),
    ) as retrieve:
        request, _ = await _prepare_normal(orch, SessionKey(chat_id=1), "project question")
    assert "source-backed test evidence" in request.prompt
    assert retrieve.await_args is not None
    assert retrieve.await_args.kwargs["is_new"] is True
