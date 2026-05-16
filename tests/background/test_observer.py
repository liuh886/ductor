"""Tests for BackgroundObserver: submit, execute, cancel, deliver."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ductor_bot.background.models import BackgroundResult, BackgroundSubmit
from ductor_bot.background.observer import MAX_TASKS_PER_CHAT, BackgroundObserver
from ductor_bot.cli.param_resolver import TaskExecutionConfig
from ductor_bot.cli.types import AgentResponse
from ductor_bot.cron.execution import OneShotExecutionResult
from ductor_bot.infra.task_runner import TaskResult
from ductor_bot.workspace.paths import DuctorPaths


def _sub(chat_id: int = 123, prompt: str = "", message_id: int = 1) -> BackgroundSubmit:
    return BackgroundSubmit(chat_id=chat_id, prompt=prompt, message_id=message_id, thread_id=None)


def _make_paths(tmp_path: Path) -> DuctorPaths:
    fw = tmp_path / "fw"
    paths = DuctorPaths(
        ductor_home=tmp_path / "home", home_defaults=fw / "workspace", framework_root=fw
    )
    paths.workspace.mkdir(parents=True, exist_ok=True)
    return paths


def _make_exec_config(**overrides: Any) -> TaskExecutionConfig:
    defaults: dict[str, Any] = {
        "provider": "claude",
        "model": "sonnet",
        "reasoning_effort": "",
        "cli_parameters": [],
        "permission_mode": "bypassPermissions",
        "working_dir": "/tmp/test",
        "file_access": "workspace",
    }
    defaults.update(overrides)
    return TaskExecutionConfig(**defaults)


def _make_observer(paths: DuctorPaths, timeout: float = 300.0) -> BackgroundObserver:
    return BackgroundObserver(paths, timeout_seconds=timeout)


def _success_task_result(text: str = "") -> TaskResult:
    return TaskResult(
        status="success",
        result_text=text,
        execution=OneShotExecutionResult(
            status="success",
            result_text=text,
            stdout=b"",
            stderr=b"",
            returncode=0,
            timed_out=False,
        ),
    )


def _cli_not_found_task_result() -> TaskResult:
    return TaskResult(
        status="error:cli_not_found_claude",
        result_text="[claude CLI not found]",
        execution=None,
    )


def _blocking_run(event: asyncio.Event) -> AsyncMock:
    """Return a mock run_oneshot_task that blocks until *event* is set."""

    async def _slow(*_args: Any, **_kw: Any) -> TaskResult:
        await event.wait()
        return _success_task_result()

    return AsyncMock(side_effect=_slow)


@pytest.fixture
def paths(tmp_path: Path) -> DuctorPaths:
    return _make_paths(tmp_path)


@pytest.fixture
async def observer(paths: DuctorPaths) -> AsyncIterator[BackgroundObserver]:
    obs = _make_observer(paths)
    yield obs
    await obs.shutdown()
    await asyncio.sleep(0.01)


class TestSubmit:
    async def test_returns_task_id(self, observer: BackgroundObserver) -> None:
        config = _make_exec_config()
        with patch(
            "ductor_bot.background.observer.run_oneshot_task",
            return_value=_cli_not_found_task_result(),
        ):
            handler = AsyncMock()
            observer.set_result_handler(handler)
            task_id = observer.submit(_sub(prompt="test prompt"), config)
            assert isinstance(task_id, str)
            assert len(task_id) == 8

    async def test_task_appears_in_active(self, observer: BackgroundObserver) -> None:
        config = _make_exec_config()
        event = asyncio.Event()
        with patch(
            "ductor_bot.background.observer.run_oneshot_task",
            new=_blocking_run(event),
        ):
            observer.set_result_handler(AsyncMock())
            observer.submit(_sub(prompt="test"), config)
            await asyncio.sleep(0)
            assert len(observer.active_tasks(123)) == 1
            assert len(observer.active_tasks(999)) == 0
            event.set()
            await asyncio.sleep(0.05)

    async def test_max_tasks_limit(self, observer: BackgroundObserver) -> None:
        config = _make_exec_config()
        event = asyncio.Event()
        with patch(
            "ductor_bot.background.observer.run_oneshot_task",
            new=_blocking_run(event),
        ):
            observer.set_result_handler(AsyncMock())
            for _ in range(MAX_TASKS_PER_CHAT):
                observer.submit(_sub(prompt="task"), config)

            with pytest.raises(ValueError, match="Too many"):
                observer.submit(_sub(prompt="one more"), config)

            event.set()
            await asyncio.sleep(0.05)


class TestExecution:
    async def test_success_delivers_result(self, observer: BackgroundObserver) -> None:
        config = _make_exec_config()
        handler = AsyncMock()
        observer.set_result_handler(handler)

        result = _success_task_result("Hello world")
        with patch("ductor_bot.background.observer.run_oneshot_task", return_value=result):
            observer.submit(_sub(prompt="say hello", message_id=42), config)
            await asyncio.sleep(0.05)

        handler.assert_awaited_once()
        bg_result: BackgroundResult = handler.call_args[0][0]
        assert bg_result.status == "success"
        assert bg_result.result_text == "Hello world"
        assert bg_result.chat_id == 123
        assert bg_result.message_id == 42
        assert bg_result.prompt_preview == "say hello"

    async def test_cli_not_found(self, observer: BackgroundObserver) -> None:
        config = _make_exec_config()
        handler = AsyncMock()
        observer.set_result_handler(handler)

        with patch(
            "ductor_bot.background.observer.run_oneshot_task",
            return_value=_cli_not_found_task_result(),
        ):
            observer.submit(_sub(prompt="test"), config)
            await asyncio.sleep(0.05)

        handler.assert_awaited_once()
        bg_result: BackgroundResult = handler.call_args[0][0]
        assert bg_result.status == "error:cli_not_found"

    async def test_timeout_status(self, observer: BackgroundObserver) -> None:
        config = _make_exec_config()
        handler = AsyncMock()
        observer.set_result_handler(handler)

        result = TaskResult(
            status="error:timeout",
            result_text="timed out",
            execution=OneShotExecutionResult(
                status="error:timeout",
                result_text="timed out",
                stdout=b"",
                stderr=b"",
                returncode=None,
                timed_out=True,
            ),
        )
        with patch("ductor_bot.background.observer.run_oneshot_task", return_value=result):
            observer.submit(_sub(prompt="slow task"), config)
            await asyncio.sleep(0.05)

        bg_result: BackgroundResult = handler.call_args[0][0]
        assert bg_result.status == "error:timeout"

    async def test_named_session_resume_and_silent_delivery(self, paths: DuctorPaths) -> None:
        cli = AsyncMock()
        cli.execute = AsyncMock(
            return_value=AgentResponse(
                result="maintenance complete",
                session_id="sess-bg-1",
                cost_usd=0.0,
                total_tokens=0,
            )
        )
        observer = BackgroundObserver(paths, timeout_seconds=30.0, cli_service=cli)
        handler = AsyncMock()
        observer.set_result_handler(handler)

        observer.submit(
            BackgroundSubmit(
                chat_id=123,
                prompt="maintain memory",
                message_id=0,
                thread_id=None,
                session_name="memory_synthesis_deadbeef",
                resume_session_id="sess-live-1",
                provider_override="claude",
                model_override="opus",
                silent=True,
            ),
            _make_exec_config(),
        )
        await asyncio.sleep(0.05)

        request = cli.execute.call_args[0][0]
        assert request.resume_session == "sess-live-1"

        bg_result: BackgroundResult = handler.call_args[0][0]
        assert bg_result.session_name == "memory_synthesis_deadbeef"
        assert bg_result.session_id == "sess-bg-1"
        assert bg_result.silent is True

    async def test_named_session_timeout_returns_user_facing_text(self, paths: DuctorPaths) -> None:
        cli = AsyncMock()
        cli.execute = AsyncMock(
            return_value=AgentResponse(
                result="",
                session_id="sess-bg-2",
                timed_out=True,
                is_error=True,
            )
        )
        observer = BackgroundObserver(paths, timeout_seconds=2400.0, cli_service=cli)
        handler = AsyncMock()
        observer.set_result_handler(handler)

        observer.submit(
            BackgroundSubmit(
                chat_id=123,
                prompt="long running task",
                message_id=0,
                thread_id=None,
                session_name="deep-task",
                provider_override="gemini",
                model_override="gemini-3-flash-preview",
            ),
            _make_exec_config(provider="gemini", model="gemini-3-flash-preview"),
        )
        await asyncio.sleep(0.05)

        bg_result: BackgroundResult = handler.call_args[0][0]
        assert bg_result.status == "error:timeout"
        assert "cli was terminated" in bg_result.result_text.lower()

    async def test_named_session_cli_error_without_text_returns_session_error(
        self, paths: DuctorPaths
    ) -> None:
        cli = AsyncMock()
        cli.execute = AsyncMock(
            return_value=AgentResponse(
                result="",
                session_id="sess-bg-3",
                is_error=True,
            )
        )
        observer = BackgroundObserver(paths, timeout_seconds=30.0, cli_service=cli)
        handler = AsyncMock()
        observer.set_result_handler(handler)

        observer.submit(
            BackgroundSubmit(
                chat_id=123,
                prompt="failing task",
                message_id=0,
                thread_id=None,
                session_name="broken-task",
                provider_override="codex",
                model_override="gpt-5.4",
            ),
            _make_exec_config(provider="codex", model="gpt-5.4"),
        )
        await asyncio.sleep(0.05)

        bg_result: BackgroundResult = handler.call_args[0][0]
        assert bg_result.status == "error:cli"
        assert "error occurred" in bg_result.result_text.lower()


class TestCancel:
    async def test_cancel_all(self, observer: BackgroundObserver) -> None:
        config = _make_exec_config()
        event = asyncio.Event()
        handler = AsyncMock()
        observer.set_result_handler(handler)

        with patch(
            "ductor_bot.background.observer.run_oneshot_task",
            new=_blocking_run(event),
        ):
            observer.submit(_sub(prompt="task1"), config)
            observer.submit(_sub(prompt="task2", message_id=2), config)
            await asyncio.sleep(0)

            cancelled = await observer.cancel_all(123)
            assert cancelled == 2
            await asyncio.sleep(0.05)

    async def test_cancel_delivers_aborted(self, observer: BackgroundObserver) -> None:
        config = _make_exec_config()
        event = asyncio.Event()
        handler = AsyncMock()
        observer.set_result_handler(handler)

        with patch(
            "ductor_bot.background.observer.run_oneshot_task",
            new=_blocking_run(event),
        ):
            observer.submit(_sub(prompt="cancellable"), config)
            await asyncio.sleep(0)

            await observer.cancel_all(123)
            await asyncio.sleep(0.05)

        aborted_calls = [c for c in handler.call_args_list if c[0][0].status == "aborted"]
        assert len(aborted_calls) == 1

    async def test_shutdown_cancels_all(self, observer: BackgroundObserver) -> None:
        config = _make_exec_config()
        event = asyncio.Event()

        with patch(
            "ductor_bot.background.observer.run_oneshot_task",
            new=_blocking_run(event),
        ):
            observer.set_result_handler(AsyncMock())
            observer.submit(_sub(prompt="t1"), config)
            observer.submit(_sub(chat_id=456, prompt="t2", message_id=2), config)
            await asyncio.sleep(0)

            await observer.shutdown()
            assert len(observer.active_tasks()) == 0


class TestCleanup:
    async def test_task_removed_after_completion(self, observer: BackgroundObserver) -> None:
        config = _make_exec_config()
        handler = AsyncMock()
        observer.set_result_handler(handler)

        result = _success_task_result("ok")
        with patch("ductor_bot.background.observer.run_oneshot_task", return_value=result):
            observer.submit(_sub(prompt="quick"), config)
            await asyncio.sleep(0.05)

        assert len(observer.active_tasks(123)) == 0
