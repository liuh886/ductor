"""Tests for TaskHub."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ductor_bot.cli.process_registry import ProcessRegistry
from ductor_bot.runtime.state import (
    MessageRepository,
    OutcomeEventRepository,
    ProcessRepository,
    RuntimeStateDB,
)
from ductor_bot.tasks.hub import TaskHub
from ductor_bot.tasks.models import TaskResult, TaskSubmit
from ductor_bot.tasks.registry import TaskRegistry


@pytest.fixture
def registry(tmp_path: Path) -> TaskRegistry:
    return TaskRegistry(
        registry_path=tmp_path / "tasks.json",
        tasks_dir=tmp_path / "tasks",
    )


def _make_config(**overrides: object) -> MagicMock:
    config = MagicMock()
    config.enabled = True
    config.max_parallel = 5
    config.timeout_seconds = 60.0
    for k, v in overrides.items():
        setattr(config, k, v)
    return config


def _make_cli_service(
    result: str = "done", session_id: str = "sess-1", num_turns: int = 3
) -> MagicMock:
    cli = MagicMock()
    response = MagicMock()
    response.result = result
    response.session_id = session_id
    response.is_error = False
    response.timed_out = False
    response.num_turns = num_turns
    response.outcome = "empty_result" if not str(result).strip() else "success"
    response.failure_class = "empty_result" if not str(result).strip() else ""
    response.empty_result = not str(result).strip()
    response.recovery_attempts = 0
    cli.execute = AsyncMock(return_value=response)
    cli.resolve_provider = MagicMock(return_value=("claude", "opus"))
    return cli


def _submit(prompt: str = "test", name: str = "Test Task") -> TaskSubmit:
    return TaskSubmit(
        chat_id=42,
        prompt=prompt,
        message_id=1,
        thread_id=None,
        parent_agent="main",
        name=name,
    )


def _find_task_execute_request(cli: MagicMock, task_id: str) -> object:
    task_label = f"task:{task_id}"
    for call in cli.execute.await_args_list:
        request = call.args[0]
        if getattr(request, "process_label", "") == task_label:
            return request
    msg = f"task request {task_label} not found"
    raise AssertionError(msg)


@pytest.fixture
def state_db(tmp_path: Path) -> RuntimeStateDB:
    db = RuntimeStateDB(tmp_path / "state.db")
    db.ensure_schema()
    return db


@pytest.fixture
def state_repos(
    state_db: RuntimeStateDB,
) -> tuple[ProcessRepository, MessageRepository, OutcomeEventRepository]:
    return ProcessRepository(state_db), MessageRepository(state_db), OutcomeEventRepository(state_db)


class TestSubmit:
    async def test_creates_task_and_returns_id(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(),
            config=_make_config(),
        )
        task_id = hub.submit(_submit())
        assert isinstance(task_id, str)
        assert len(task_id) == 8  # hex(4)

        entry = registry.get(task_id)
        assert entry is not None
        assert entry.status == "running"

        await hub.shutdown()

    async def test_raises_when_disabled(self, registry: TaskRegistry, tmp_path: Path) -> None:
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(),
            config=_make_config(enabled=False),
        )
        with pytest.raises(ValueError, match="disabled"):
            hub.submit(_submit())

    async def test_raises_at_max_parallel(self, registry: TaskRegistry, tmp_path: Path) -> None:
        async def _hang(_: object) -> MagicMock:
            await asyncio.sleep(999)
            return MagicMock()  # never reached

        cli = _make_cli_service()
        # Make execute hang so tasks stay in-flight
        cli.execute = AsyncMock(side_effect=_hang)

        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(max_parallel=1),
        )
        hub.submit(_submit(name="T1"))
        with pytest.raises(ValueError, match="Too many"):
            hub.submit(_submit(name="T2"))

        await hub.shutdown()


class TestRunAndDeliver:
    async def test_delivers_success_result(self, registry: TaskRegistry, tmp_path: Path) -> None:
        delivered: list[TaskResult] = []
        handler = AsyncMock(side_effect=delivered.append)

        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service("task output"),
            config=_make_config(),
        )
        hub.set_result_handler("main", handler)

        task_id = hub.submit(_submit())
        await asyncio.sleep(0.1)  # Let task run

        assert len(delivered) == 1
        assert delivered[0].task_id == task_id
        assert delivered[0].status == "done"
        assert delivered[0].result_text.startswith("task output")
        assert "resume_task.py" in delivered[0].result_text  # resume hint appended
        assert delivered[0].name == "Test Task"
        assert delivered[0].evaluation_status == "pending_review"

        entry = registry.get(task_id)
        assert entry is not None
        assert entry.status == "done"
        assert entry.evaluation_status == "pending_review"

        await hub.shutdown()

    async def test_result_delivery_is_not_blocked_by_skill_extraction(
        self,
        registry: TaskRegistry,
        tmp_path: Path,
        state_repos: tuple[ProcessRepository, MessageRepository, OutcomeEventRepository],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, message_repo, _outcome_repo = state_repos
        extraction_started = asyncio.Event()
        release_extraction = asyncio.Event()

        async def _blocked_extract(_: object, __: object) -> None:
            extraction_started.set()
            await release_extraction.wait()

        monkeypatch.setattr(
            "ductor_bot.runtime.skills.extractor.SkillExtractor.extract",
            _blocked_extract,
        )

        delivered: list[TaskResult] = []
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service("task output"),
            message_repo=message_repo,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock(side_effect=delivered.append))

        hub.submit(_submit())
        await asyncio.wait_for(extraction_started.wait(), timeout=0.2)

        assert len(delivered) == 1
        assert delivered[0].status == "done"

        release_extraction.set()
        await asyncio.sleep(0.05)
        await hub.shutdown()


class TestResultPackaging:
    async def test_packages_taskmemory_as_summary_and_lists_artifacts(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        ready = asyncio.Event()
        cli = _make_cli_service("task output")
        response = cli.execute.return_value

        async def _wait_for_release(_: object) -> object:
            await ready.wait()
            return response

        cli.execute = AsyncMock(side_effect=_wait_for_release)

        delivered: list[TaskResult] = []
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock(side_effect=delivered.append))

        task_id = hub.submit(_submit(prompt="Build the report"))
        folder = registry.task_folder(task_id)
        (folder / "TASKMEMORY.md").write_text(
            (
                "# Task Memory\n\n## Goal\n- Build the report quickly and carefully.\n\n## Notes\n- "
                "This line should be truncated because it is intentionally very long and specific: "
                + "X" * 280
                + "\n- Keep going"
            ),
        )
        (folder / "report.txt").write_text("artifact")
        artifacts_dir = folder / "artifacts"
        artifacts_dir.mkdir()
        (artifacts_dir / "chart.png").write_text("binary")

        ready.set()
        await asyncio.sleep(0.15)

        assert len(delivered) == 1
        result_text = delivered[0].result_text
        assert "task output" in result_text
        assert "TASK MEMORY SUMMARY" in result_text
        assert "Goal:" in result_text
        assert "Notes:" in result_text
        assert "X" * 260 not in result_text
        assert "ARTIFACTS" in result_text
        assert "report.txt" in result_text
        assert "artifacts/chart.png" in result_text
        assert "CONTENT FROM TASKMEMORY.MD" not in result_text
        assert result_text.count("resume_task.py") == 1

        await hub.shutdown()

    async def test_packages_without_taskmemory_still_lists_artifacts(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        ready = asyncio.Event()
        cli = _make_cli_service("task output")
        response = cli.execute.return_value

        async def _wait_for_release(_: object) -> object:
            await ready.wait()
            return response

        cli.execute = AsyncMock(side_effect=_wait_for_release)

        delivered: list[TaskResult] = []
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock(side_effect=delivered.append))

        task_id = hub.submit(_submit(prompt="Build the report"))
        folder = registry.task_folder(task_id)
        taskmemory = folder / "TASKMEMORY.md"
        if taskmemory.exists():
            taskmemory.unlink()
        (folder / "log.txt").write_text("artifact")

        ready.set()
        await asyncio.sleep(0.15)

        assert len(delivered) == 1
        result_text = delivered[0].result_text
        assert "TASK MEMORY SUMMARY" not in result_text
        assert "ARTIFACTS" in result_text
        assert "log.txt" in result_text
        assert "task output" in result_text
        assert result_text.count("resume_task.py") == 1

        await hub.shutdown()


class TestRuntimeStatePersistence:
    async def test_records_process_and_messages(
        self,
        registry: TaskRegistry,
        tmp_path: Path,
        state_repos: tuple[ProcessRepository, MessageRepository, OutcomeEventRepository],
    ) -> None:
        process_repo, message_repo, _outcome_repo = state_repos
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service("persisted result"),
            process_repo=process_repo,
            message_repo=message_repo,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())

        task_id = hub.submit(_submit(prompt="Persist this"))
        await asyncio.sleep(0.1)

        processes = process_repo.list_all()
        assert len(processes) == 1
        assert processes[0]["process_label"] == f"task:{task_id}"
        assert processes[0]["chat_id"] == 42
        assert processes[0]["ended_at"] is not None
        assert processes[0]["exit_code"] == 0

        messages = message_repo.list_by_session(f"task:{task_id}")
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[0]["source"] == "task_submit"
        assert "Persist this" in str(messages[0]["content_text"])
        assert messages[1]["source"] == "task_result"
        assert "persisted result" in str(messages[1]["content_text"])

        await hub.shutdown()

    async def test_records_task_run_outcome_without_prompt_or_result(
        self,
        registry: TaskRegistry,
        tmp_path: Path,
        state_repos: tuple[ProcessRepository, MessageRepository, OutcomeEventRepository],
    ) -> None:
        process_repo, message_repo, outcome_repo = state_repos
        original_prompt = "secret original task prompt"
        result_body = "secret task result body"
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(result_body),
            process_repo=process_repo,
            message_repo=message_repo,
            outcome_event_repo=outcome_repo,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())

        task_id = hub.submit(_submit(prompt=original_prompt))
        await asyncio.sleep(0.1)

        events = outcome_repo.list_unlearned()
        assert len(events) == 1
        event = events[0]
        assert event["source_type"] == "task_run"
        assert event["source_id"] == f"task:{task_id}:run:1"
        assert event["event_type"] == "task_run"
        assert event["task_id"] == task_id
        assert event["session_storage_key"] == f"task:{task_id}"
        assert event["flow"] == "task"
        assert event["status"] == "done"
        assert event["outcome"] == "success"
        payload = event["payload_json"]
        assert payload["input_chars"] > len(original_prompt)
        assert payload["result_chars"] == len(result_body)
        assert original_prompt not in str(payload)
        assert result_body not in str(payload)
        assert "prompt" not in payload
        assert "result" not in payload

        await hub.shutdown()

    async def test_resume_records_additional_process_and_messages(
        self,
        registry: TaskRegistry,
        tmp_path: Path,
        state_repos: tuple[ProcessRepository, MessageRepository, OutcomeEventRepository],
    ) -> None:
        process_repo, message_repo, _outcome_repo = state_repos
        cli = _make_cli_service("initial result")
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            process_repo=process_repo,
            message_repo=message_repo,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())

        task_id = hub.submit(_submit(prompt="Start"))
        await asyncio.sleep(0.1)

        cli.execute.return_value.result = "follow-up result"
        resumed_id = hub.resume(task_id, "Continue")
        assert resumed_id == task_id
        await asyncio.sleep(0.1)

        processes = process_repo.list_all()
        assert len(processes) == 2
        assert all(process["ended_at"] is not None for process in processes)

        messages = message_repo.list_by_session(f"task:{task_id}")
        assert len(messages) == 4
        assert messages[2]["source"] == "task_resume"
        assert "Continue" in str(messages[2]["content_text"])
        assert messages[3]["source"] == "task_result"
        assert "follow-up result" in str(messages[3]["content_text"])

        await hub.shutdown()

    async def test_delivers_error_on_cli_failure(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        cli = _make_cli_service()
        cli.execute.return_value.is_error = True
        cli.execute.return_value.result = "API rate limit"

        delivered: list[TaskResult] = []
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock(side_effect=delivered.append))

        hub.submit(_submit())
        await asyncio.sleep(0.1)

        assert len(delivered) == 1
        assert delivered[0].status == "failed"
        assert delivered[0].outcome == "error"
        assert delivered[0].failure_class == "cli_error"
        assert "rate limit" in delivered[0].error.lower()

        await hub.shutdown()

    @pytest.mark.parametrize("returncode", [143, 137, -15, -9])
    async def test_sigterm_exit_is_classified_as_cancelled_not_failed(
        self, registry: TaskRegistry, tmp_path: Path, returncode: int
    ) -> None:
        """Exit 143/137 (= 128 + SIGTERM/SIGKILL) is user /stop, not a CLI error."""
        cli = _make_cli_service()
        cli.execute.return_value.is_error = True
        cli.execute.return_value.result = ""
        cli.execute.return_value.returncode = returncode

        delivered: list[TaskResult] = []
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock(side_effect=delivered.append))

        hub.submit(_submit())
        await asyncio.sleep(0.1)

        assert len(delivered) == 1
        assert delivered[0].status == "cancelled"
        assert delivered[0].error == ""

        await hub.shutdown()

    async def test_cancelled_task_delivers_partial_taskmemory(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        """MED #1: partial TASKMEMORY.md written before SIGTERM must reach the parent."""
        cli = _make_cli_service()
        cli.execute.return_value.is_error = True
        cli.execute.return_value.result = ""
        cli.execute.return_value.returncode = 143  # SIGTERM -> status=cancelled

        delivered: list[TaskResult] = []
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock(side_effect=delivered.append))

        task_id = hub.submit(_submit())
        # Write partial memory BEFORE the task's execute mock returns — the
        # task folder is seeded synchronously on submit, so this is safe.
        memory = registry.taskmemory_path(task_id)
        memory.parent.mkdir(parents=True, exist_ok=True)
        memory.write_text("partial research findings\nline 2", encoding="utf-8")

        await asyncio.sleep(0.1)

        assert len(delivered) == 1
        assert delivered[0].status == "cancelled"
        assert "partial research findings" in delivered[0].result_text
        assert "CONTENT FROM TASKMEMORY.MD" in delivered[0].result_text

        await hub.shutdown()

    async def test_empty_result_is_not_treated_as_success(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        cli = _make_cli_service(result="")

        delivered: list[TaskResult] = []
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock(side_effect=delivered.append))

        task_id = hub.submit(_submit())
        await asyncio.sleep(0.1)

        assert len(delivered) == 1
        assert delivered[0].status == "failed"
        assert delivered[0].outcome == "empty_result"
        assert delivered[0].empty_result is True
        entry = registry.get(task_id)
        assert entry is not None
        assert entry.status == "failed"
        assert entry.outcome == "empty_result"
        assert entry.empty_result is True

        await hub.shutdown()


class TestCancel:
    async def test_cancel_running_task(self, registry: TaskRegistry, tmp_path: Path) -> None:
        async def _hang(_: object) -> MagicMock:
            await asyncio.sleep(999)
            return MagicMock()  # never reached

        cli = _make_cli_service()
        cli.execute = AsyncMock(side_effect=_hang)

        delivered: list[TaskResult] = []
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock(side_effect=delivered.append))

        task_id = hub.submit(_submit())
        await asyncio.sleep(0.05)

        success = await hub.cancel(task_id)
        assert success
        await asyncio.sleep(0.05)

        assert len(delivered) == 1
        assert delivered[0].status == "cancelled"

        entry = registry.get(task_id)
        assert entry is not None
        assert entry.status == "cancelled"

    async def test_cancel_nonexistent(self, registry: TaskRegistry, tmp_path: Path) -> None:
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(),
            config=_make_config(),
        )
        assert not await hub.cancel("nonexistent")


class TestCancelWithProcessRegistry:
    """#92: cancel/cancel_all must kill the subprocess BEFORE the asyncio task
    (otherwise cli.execute's pipe stays open and CancelledError cannot propagate).
    """

    async def test_cancel_kills_subprocess_before_asyncio_cancel(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        """cancel() must invoke process_registry.kill_for_task BEFORE asyncio_task.cancel()."""
        order: list[str] = []

        process_registry = AsyncMock(spec=ProcessRegistry)

        async def _record_kill(_task_id: str) -> int:
            order.append("kill_for_task")
            return 1

        process_registry.kill_for_task.side_effect = _record_kill

        # cli.execute hangs so the task stays in-flight until cancelled.
        async def _hang(_: object) -> MagicMock:
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                order.append("asyncio_cancel")
                raise
            return MagicMock()  # never reached

        cli = _make_cli_service()
        cli.execute = AsyncMock(side_effect=_hang)

        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
            process_registry=process_registry,
        )
        hub.set_result_handler("main", AsyncMock())

        task_id = hub.submit(_submit())
        await asyncio.sleep(0.05)  # let the task actually start awaiting the pipe

        success = await hub.cancel(task_id)
        assert success

        process_registry.kill_for_task.assert_awaited_once_with(task_id)
        # Kill-order invariant: subprocess kill strictly precedes asyncio cancel.
        assert order == ["kill_for_task", "asyncio_cancel"], (
            f"expected kill-before-cancel, got {order!r}"
        )

    async def test_cancel_all_kills_each_tasks_subprocess(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        """cancel_all() must call kill_for_task once per in-flight task for the chat."""
        process_registry = AsyncMock(spec=ProcessRegistry)
        process_registry.kill_for_task.return_value = 1

        async def _hang(_: object) -> MagicMock:
            await asyncio.sleep(999)
            return MagicMock()

        cli = _make_cli_service()
        cli.execute = AsyncMock(side_effect=_hang)

        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
            process_registry=process_registry,
        )
        hub.set_result_handler("main", AsyncMock())

        task_id_a = hub.submit(_submit(name="A"))
        task_id_b = hub.submit(_submit(name="B"))
        await asyncio.sleep(0.05)

        count = await hub.cancel_all(42)
        assert count == 2
        assert process_registry.kill_for_task.await_count == 2
        awaited_ids = {c.args[0] for c in process_registry.kill_for_task.await_args_list}
        assert awaited_ids == {task_id_a, task_id_b}

    async def test_cancel_is_noop_when_process_registry_is_none(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        """Without a process_registry the old behavior is preserved (asyncio cancel only)."""
        cancel_recorded = asyncio.Event()

        async def _hang(_: object) -> MagicMock:
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                cancel_recorded.set()
                raise
            return MagicMock()

        cli = _make_cli_service()
        cli.execute = AsyncMock(side_effect=_hang)

        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
            # NOTE: no process_registry kwarg — backward compat path.
        )
        hub.set_result_handler("main", AsyncMock())

        task_id = hub.submit(_submit())
        await asyncio.sleep(0.05)

        success = await hub.cancel(task_id)
        assert success
        assert cancel_recorded.is_set(), "asyncio_task.cancel() must still fire with no registry"


class TestForwardQuestion:
    async def test_forwards_and_returns_immediately(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(),
            config=_make_config(),
        )

        entry = registry.create(_submit("build a website"), "claude", "opus")

        question_handler = AsyncMock()
        hub.set_question_handler("main", question_handler)

        result = await hub.forward_question(entry.task_id, "Which framework?")
        assert "forwarded" in result.lower()

        # Handler is called asynchronously (fire-and-forget)
        await asyncio.sleep(0.05)
        question_handler.assert_called_once()

    async def test_increments_question_count(self, registry: TaskRegistry, tmp_path: Path) -> None:
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(),
            config=_make_config(),
        )
        entry = registry.create(_submit(), "claude", "opus")
        hub.set_question_handler("main", AsyncMock())

        await hub.forward_question(entry.task_id, "question 1")
        await hub.forward_question(entry.task_id, "question 2")

        updated = registry.get(entry.task_id)
        assert updated is not None
        assert updated.question_count == 2

    async def test_unknown_task(self, registry: TaskRegistry, tmp_path: Path) -> None:
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(),
            config=_make_config(),
        )
        result = await hub.forward_question("nonexistent", "question?")
        assert "not found" in result.lower()

    async def test_no_handler(self, registry: TaskRegistry, tmp_path: Path) -> None:
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(),
            config=_make_config(),
        )
        entry = registry.create(_submit(), "claude", "opus")
        result = await hub.forward_question(entry.task_id, "question?")
        assert "no question handler" in result.lower()


class TestWaitingStatus:
    async def test_task_with_question_gets_waiting_status(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        """Task that asks a question should end as 'waiting', not 'done'."""
        cli = _make_cli_service()
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())
        hub.set_question_handler("main", AsyncMock())

        task_id = hub.submit(_submit())

        # Simulate: task asks a question while running (before CLI returns)
        entry = registry.get(task_id)
        assert entry is not None
        await hub.forward_question(task_id, "Which framework?")

        # Wait for CLI to complete
        await asyncio.sleep(0.1)

        entry = registry.get(task_id)
        assert entry is not None
        assert entry.status == "waiting"
        assert entry.last_question == "Which framework?"
        assert entry.follow_up_count == 0

    async def test_resume_from_waiting(self, registry: TaskRegistry, tmp_path: Path) -> None:
        """Resuming a 'waiting' task should work and clear the question."""
        cli = _make_cli_service()
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())
        hub.set_question_handler("main", AsyncMock())

        task_id = hub.submit(_submit())
        await hub.forward_question(task_id, "Which framework?")
        await asyncio.sleep(0.1)

        entry = registry.get(task_id)
        assert entry is not None
        assert entry.status == "waiting"

        resumed_id = hub.resume(task_id, "Use React")
        assert resumed_id == task_id
        await asyncio.sleep(0.1)

        entry = registry.get(task_id)
        assert entry is not None
        assert entry.status == "done"
        assert entry.last_question == ""
        assert entry.follow_up_count == 0


class TestResume:
    def _hub(self, registry: TaskRegistry, tmp_path: Path, **cli_kw: str) -> TaskHub:
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(**cli_kw),
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())
        return hub

    async def test_resume_reuses_same_task(self, registry: TaskRegistry, tmp_path: Path) -> None:
        hub = self._hub(registry, tmp_path)
        task_id = hub.submit(_submit())
        await asyncio.sleep(0.1)

        entry = registry.get(task_id)
        assert entry is not None
        assert entry.status == "done"
        assert entry.session_id == "sess-1"
        assert entry.evaluation_status == "pending_review"

        resumed_id = hub.resume(task_id, "now for 2 weeks")
        assert resumed_id == task_id  # Same task, no new entry
        await asyncio.sleep(0.1)

        entry = registry.get(task_id)
        assert entry is not None
        assert entry.status == "done"  # Completed again
        assert entry.name == "Test Task"
        assert entry.follow_up_count == 1
        assert entry.last_follow_up == "now for 2 weeks"
        assert entry.evaluation_status == "improved_after_followup"

    async def test_resume_uses_original_provider_model(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        hub = self._hub(registry, tmp_path)
        entry = registry.create(_submit(), "codex", "gpt-4.1", thinking="high")
        registry.update_status(entry.task_id, "done", session_id="codex-sess")

        resumed_id = hub.resume(entry.task_id, "follow up")
        assert resumed_id == entry.task_id

        updated = registry.get(entry.task_id)
        assert updated is not None
        assert updated.provider == "codex"
        assert updated.model == "gpt-4.1"
        assert updated.thinking == "high"

    def test_resume_fails_if_no_session_id(self, registry: TaskRegistry, tmp_path: Path) -> None:
        hub = self._hub(registry, tmp_path)
        entry = registry.create(_submit(), "claude", "opus")
        registry.update_status(entry.task_id, "done")  # No session_id

        with pytest.raises(ValueError, match="no resumable session"):
            hub.resume(entry.task_id, "follow up")

    def test_resume_fails_if_still_running(self, registry: TaskRegistry, tmp_path: Path) -> None:
        hub = self._hub(registry, tmp_path)
        entry = registry.create(_submit(), "claude", "opus")
        # Status is "running" by default

        with pytest.raises(ValueError, match="still running"):
            hub.resume(entry.task_id, "follow up")

    def test_resume_fails_if_no_provider(self, registry: TaskRegistry, tmp_path: Path) -> None:
        hub = self._hub(registry, tmp_path)
        entry = registry.create(_submit(), "", "")
        registry.update_status(entry.task_id, "done", session_id="sess-1")

        with pytest.raises(ValueError, match="no provider recorded"):
            hub.resume(entry.task_id, "follow up")

    def test_resume_fails_if_task_not_found(self, registry: TaskRegistry, tmp_path: Path) -> None:
        hub = self._hub(registry, tmp_path)
        with pytest.raises(ValueError, match="not found"):
            hub.resume("nonexistent", "follow up")


class TestResumeCompression:
    async def test_resume_includes_compressed_context_for_long_history(
        self,
        registry: TaskRegistry,
        tmp_path: Path,
        state_repos: tuple[ProcessRepository, MessageRepository, OutcomeEventRepository],
    ) -> None:
        process_repo, message_repo, _outcome_repo = state_repos
        cli = _make_cli_service("compressed follow-up result", session_id="sess-2")
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            process_repo=process_repo,
            message_repo=message_repo,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())

        entry = registry.create(_submit(prompt="start"), "claude", "opus")
        registry.update_status(entry.task_id, "done", session_id="sess-1")

        for idx in range(10):
            message_repo.append(
                f"task:{entry.task_id}",
                "user" if idx % 2 == 0 else "assistant",
                f"history-{idx}",
                source=f"history_{idx}",
            )

        resumed_id = hub.resume(entry.task_id, "Continue the task")
        assert resumed_id == entry.task_id
        await asyncio.sleep(0.1)

        request = _find_task_execute_request(cli, entry.task_id)
        assert request.prompt.startswith("## COMPRESSED CONTEXT")
        assert "Older session summary:" in request.prompt
        assert "Protected recent tail:" in request.prompt
        assert "Continue the task" in request.prompt
        assert "REMINDER: You are a background task agent" in request.prompt

        await hub.shutdown()

    async def test_resume_omits_compression_prefix_for_short_history(
        self,
        registry: TaskRegistry,
        tmp_path: Path,
        state_repos: tuple[ProcessRepository, MessageRepository, OutcomeEventRepository],
    ) -> None:
        process_repo, message_repo, _outcome_repo = state_repos
        cli = _make_cli_service("short history follow-up result", session_id="sess-2")
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            process_repo=process_repo,
            message_repo=message_repo,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())

        entry = registry.create(_submit(prompt="start"), "claude", "opus")
        registry.update_status(entry.task_id, "done", session_id="sess-1")

        for idx in range(3):
            message_repo.append(
                f"task:{entry.task_id}",
                "user" if idx % 2 == 0 else "assistant",
                f"short-{idx}",
                source=f"short_{idx}",
            )

        resumed_id = hub.resume(entry.task_id, "Continue briefly")
        assert resumed_id == entry.task_id
        await asyncio.sleep(0.1)

        request = _find_task_execute_request(cli, entry.task_id)
        assert not request.prompt.startswith("## COMPRESSED CONTEXT")
        assert request.prompt.startswith("Continue briefly")
        assert "REMINDER: You are a background task agent" in request.prompt

        await hub.shutdown()


class TestThinkingPersisted:
    async def test_thinking_stored_on_entry(self, registry: TaskRegistry, tmp_path: Path) -> None:
        submit = TaskSubmit(
            chat_id=42,
            prompt="test",
            message_id=1,
            thread_id=None,
            parent_agent="main",
            name="Think Test",
            thinking_override="high",
        )
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(),
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())

        task_id = hub.submit(submit)
        entry = registry.get(task_id)
        assert entry is not None
        assert entry.thinking == "high"


class TestNumTurns:
    async def test_num_turns_stored_on_completion(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(num_turns=7),
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())

        task_id = hub.submit(_submit())
        await asyncio.sleep(0.1)

        entry = registry.get(task_id)
        assert entry is not None
        assert entry.num_turns == 7

    async def test_resume_accumulates_turns(self, registry: TaskRegistry, tmp_path: Path) -> None:
        """Resumed task carries forward + adds new turns."""
        cli = _make_cli_service(num_turns=5)
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())

        task_id = hub.submit(_submit())
        await asyncio.sleep(0.1)

        original = registry.get(task_id)
        assert original is not None
        assert original.num_turns == 5

        # Resume — CLI returns 3 more turns
        cli.execute.return_value.num_turns = 3
        resumed_id = hub.resume(task_id, "follow up")
        assert resumed_id == task_id
        await asyncio.sleep(0.1)

        entry = registry.get(task_id)
        assert entry is not None
        assert entry.num_turns == 8  # 5 carried + 3 new


class TestPerAgentTasksDir:
    async def test_task_folder_in_agent_workspace(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        """Task folders land in the submitting agent's workspace."""
        from ductor_bot.workspace.paths import DuctorPaths

        agent_home = tmp_path / "agents" / "test"
        agent_paths = DuctorPaths(ductor_home=agent_home)

        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(),
            config=_make_config(),
        )
        hub.set_agent_paths("test", agent_paths)
        hub.set_result_handler("test", AsyncMock())

        submit = TaskSubmit(
            chat_id=99,
            prompt="do stuff",
            message_id=1,
            thread_id=None,
            parent_agent="test",
            name="Agent Task",
        )
        task_id = hub.submit(submit)
        await asyncio.sleep(0.1)

        # Task folder should be in agent's workspace, not main
        entry = registry.get(task_id)
        assert entry is not None
        assert str(agent_home) in entry.tasks_dir

        folder = registry.task_folder(task_id)
        assert str(agent_home) in str(folder)
        assert folder.is_dir()
        assert (folder / "TASKMEMORY.md").is_file()

        # Default main tasks dir should NOT have this task
        assert not (tmp_path / "tasks" / task_id).exists()

        await hub.shutdown()

    async def test_main_agent_uses_default_dir(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        """Main agent tasks use the default tasks_dir when no override registered."""
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service(),
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())

        task_id = hub.submit(_submit())
        await asyncio.sleep(0.1)

        folder = registry.task_folder(task_id)
        assert str(tmp_path / "tasks") in str(folder)

        await hub.shutdown()


class TestPerAgentCLI:
    async def test_uses_agent_specific_cli(self, registry: TaskRegistry, tmp_path: Path) -> None:
        """Tasks use the CLI service registered for their parent_agent."""
        main_cli = _make_cli_service("main-output")
        sub_cli = _make_cli_service("sub-output")

        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=main_cli,
            config=_make_config(),
        )
        hub.set_cli_service("sub1", sub_cli)

        delivered: list[TaskResult] = []
        hub.set_result_handler("sub1", AsyncMock(side_effect=delivered.append))

        submit = TaskSubmit(
            chat_id=99,
            prompt="do stuff",
            message_id=1,
            thread_id=None,
            parent_agent="sub1",
            name="Sub Task",
        )
        hub.submit(submit)
        await asyncio.sleep(0.1)

        # sub_cli should have been called, not main_cli
        sub_cli.execute.assert_called_once()
        main_cli.execute.assert_not_called()
        assert len(delivered) == 1
        assert delivered[0].result_text.startswith("sub-output")

        await hub.shutdown()

    async def test_falls_back_to_default_cli(self, registry: TaskRegistry, tmp_path: Path) -> None:
        """Tasks fall back to default CLI when no per-agent CLI is registered."""
        default_cli = _make_cli_service("default-output")

        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=default_cli,
            config=_make_config(),
        )

        delivered: list[TaskResult] = []
        hub.set_result_handler("unknown_agent", AsyncMock(side_effect=delivered.append))

        submit = TaskSubmit(
            chat_id=99,
            prompt="do stuff",
            message_id=1,
            thread_id=None,
            parent_agent="unknown_agent",
            name="Fallback Task",
        )
        hub.submit(submit)
        await asyncio.sleep(0.1)

        default_cli.execute.assert_called_once()
        assert len(delivered) == 1
        assert delivered[0].result_text.startswith("default-output")

        await hub.shutdown()

    async def test_enabled_with_only_per_agent_cli(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        """Hub works when only per-agent CLIs are set (no default)."""
        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=None,
            config=_make_config(),
        )
        agent_cli = _make_cli_service("agent-output")
        hub.set_cli_service("main", agent_cli)

        delivered: list[TaskResult] = []
        hub.set_result_handler("main", AsyncMock(side_effect=delivered.append))

        hub.submit(_submit())
        await asyncio.sleep(0.1)

        agent_cli.execute.assert_called_once()
        assert len(delivered) == 1

        await hub.shutdown()


class TestTopicIdPlumbing:
    """#74: TaskEntry.thread_id must flow into AgentRequest.topic_id so that
    DUCTOR_TOPIC_ID is set in the task subprocess env. Without this, sub-tasks
    created from within a running task lose the originating topic context and
    route their results to the base/General topic."""

    async def test_run_passes_topic_id_to_agent_request(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        """AgentRequest.topic_id equals TaskEntry.thread_id when the latter is set."""
        cli = _make_cli_service()

        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())

        submit = TaskSubmit(
            chat_id=42,
            prompt="do stuff in a topic",
            message_id=1,
            thread_id=5150,
            parent_agent="main",
            name="Topic Task",
        )
        hub.submit(submit)
        await asyncio.sleep(0.1)

        # Capture the AgentRequest passed to cli.execute.
        cli.execute.assert_called_once()
        agent_request = cli.execute.call_args[0][0]
        assert agent_request.topic_id == 5150
        assert agent_request.chat_id == 42

        await hub.shutdown()

    async def test_run_passes_none_topic_id_when_thread_id_missing(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        """Backward-compat: thread_id=None yields topic_id=None on AgentRequest."""
        cli = _make_cli_service()

        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=cli,
            config=_make_config(),
        )
        hub.set_result_handler("main", AsyncMock())

        hub.submit(_submit())  # thread_id=None via helper
        await asyncio.sleep(0.1)

        cli.execute.assert_called_once()
        agent_request = cli.execute.call_args[0][0]
        assert agent_request.topic_id is None

        await hub.shutdown()


class TestPerAgentDeliveryIsolation:
    """#73: TaskResult delivery must route through the parent_agent's registered
    handler only -- sibling agents' handlers MUST NOT see results that weren't
    addressed to them. Locks in the architectural per-agent routing so a future
    refactor cannot silently regress it into delivering everything to main."""

    async def test_result_isolation_between_agents(
        self, registry: TaskRegistry, tmp_path: Path
    ) -> None:
        """A task with parent_agent='sub1' invokes only sub1's handler."""
        main_handler = AsyncMock()
        sub1_handler = AsyncMock()
        sub2_handler = AsyncMock()

        sub_cli = _make_cli_service("sub1-output")

        hub = TaskHub(
            registry,
            MagicMock(workspace=tmp_path),
            cli_service=_make_cli_service("main-output"),
            config=_make_config(),
        )
        hub.set_cli_service("sub1", sub_cli)
        hub.set_result_handler("main", main_handler)
        hub.set_result_handler("sub1", sub1_handler)
        hub.set_result_handler("sub2", sub2_handler)

        submit = TaskSubmit(
            chat_id=55,
            prompt="sub-agent task",
            message_id=1,
            thread_id=None,
            parent_agent="sub1",
            name="Sub1 Task",
        )
        hub.submit(submit)
        await asyncio.sleep(0.1)

        sub1_handler.assert_called_once()
        main_handler.assert_not_called()
        sub2_handler.assert_not_called()

        delivered_result = sub1_handler.call_args[0][0]
        assert delivered_result.parent_agent == "sub1"

        await hub.shutdown()


class TestAppendTaskmemory:
    """#91: _append_taskmemory must emit a WARNING log and include the original
    length + full file path in the suffix when truncation occurs. Without this,
    parent agents receive silently-truncated memory content."""

    def test_warns_on_truncation(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        from ductor_bot.tasks.hub import _TASKMEMORY_MAX_LEN, _append_taskmemory

        memory_file = tmp_path / "TASKMEMORY.md"
        original_len = _TASKMEMORY_MAX_LEN + 1000
        memory_file.write_text("X" * original_len, encoding="utf-8")

        with caplog.at_level("WARNING", logger="ductor_bot.tasks.hub"):
            result = _append_taskmemory("result_text", memory_file)

        # WARNING log fired
        assert any("TASKMEMORY truncated" in rec.message for rec in caplog.records)
        # Suffix shows original length so the parent agent knows how much was cut
        assert str(original_len) in result
        # Suffix points to the full file path so the parent agent can read it
        assert str(memory_file) in result
        assert "truncated" in result.lower()

    def test_no_warning_under_limit(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        from ductor_bot.tasks.hub import _append_taskmemory

        memory_file = tmp_path / "TASKMEMORY.md"
        memory_file.write_text("short content", encoding="utf-8")

        with caplog.at_level("WARNING", logger="ductor_bot.tasks.hub"):
            result = _append_taskmemory("result_text", memory_file)

        assert not any("TASKMEMORY truncated" in rec.message for rec in caplog.records)
        assert "truncated" not in result.lower()
        assert "short content" in result
