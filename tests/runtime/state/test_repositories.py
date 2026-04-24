"""Tests for SQLite-backed runtime-state repositories."""

# ruff: noqa: INP001

from __future__ import annotations

from pathlib import Path

from ductor_bot.runtime.memory import MemoryFragment
from ductor_bot.runtime.state import (
    MemoryFragmentRepository,
    MessageRepository,
    NamedSessionRepository,
    ProcessRepository,
    RuntimeStateDB,
    SessionRepository,
    TaskRepository,
    TaskStateRepository,
    ToolCallRepository,
)
from ductor_bot.session.manager import ProviderSessionData, SessionData
from ductor_bot.session.named import NamedSession
from ductor_bot.tasks.models import TaskEntry


def test_session_repository_round_trip(tmp_path: Path) -> None:
    repo = SessionRepository(RuntimeStateDB(tmp_path / "state.db"))
    session = SessionData(
        chat_id=1,
        transport="tg",
        topic_id=42,
        topic_name="Topic 42",
        provider="claude",
        model="opus",
        provider_sessions={
            "claude": ProviderSessionData(
                session_id="sid-1",
                message_count=3,
                total_cost_usd=0.25,
                total_tokens=1234,
            )
        },
    )

    repo.upsert("tg:1:42", session)
    loaded = repo.get("tg:1:42")

    assert loaded is not None
    assert loaded.chat_id == 1
    assert loaded.topic_id == 42
    assert loaded.topic_name == "Topic 42"
    assert loaded.lineage_root == loaded.lineage_id
    assert loaded.lineage_parent == ""
    assert loaded.lineage_depth == 0
    assert loaded.provider_sessions["claude"].session_id == "sid-1"


def test_named_session_repository_round_trip(tmp_path: Path) -> None:
    repo = NamedSessionRepository(RuntimeStateDB(tmp_path / "state.db"))
    session = NamedSession(
        name="blueowl",
        chat_id=5,
        provider="claude",
        model="opus",
        session_id="ns-1",
        prompt_preview="hello",
        status="idle",
        created_at=1.0,
        message_count=2,
        last_prompt="full prompt",
        transport="tg",
    )

    repo.replace_all([session])
    loaded = repo.list_all()

    assert len(loaded) == 1
    assert loaded[0].name == "blueowl"
    assert loaded[0].last_prompt == "full prompt"


def test_task_repository_round_trip(tmp_path: Path) -> None:
    repo = TaskRepository(RuntimeStateDB(tmp_path / "state.db"))
    task = TaskEntry(
        task_id="deadbeef",
        chat_id=9,
        parent_agent="main",
        transport="tg",
        name="Task",
        prompt_preview="preview",
        provider="claude",
        model="opus",
        status="running",
        original_prompt="full prompt",
        tasks_dir=str(tmp_path / "tasks"),
        thread_id=7,
    )

    repo.replace_all([task])
    loaded = repo.list_all()

    assert len(loaded) == 1
    assert loaded[0].task_id == "deadbeef"
    assert loaded[0].thread_id == 7
    assert loaded[0].original_prompt == "full prompt"


def test_task_state_repository_round_trip(tmp_path: Path) -> None:
    repo = TaskStateRepository(RuntimeStateDB(tmp_path / "state.db"))

    repo.upsert(
        task_id="task-1",
        storage_key="tg:1:5",
        status="RUNNING",
        current_step=2,
        total_steps=4,
        step_label="write tests",
        context_snapshot_json={"owner": "main", "priority": "high"},
    )

    loaded = repo.list_by_storage_key("tg:1:5")

    assert len(loaded) == 1
    assert loaded[0]["task_id"] == "task-1"
    assert loaded[0]["status"] == "RUNNING"
    assert loaded[0]["current_step"] == 2
    assert loaded[0]["total_steps"] == 4
    assert loaded[0]["step_label"] == "write tests"
    assert loaded[0]["context_snapshot_json"] == {"owner": "main", "priority": "high"}


def test_message_repository_append_and_list(tmp_path: Path) -> None:
    repo = MessageRepository(RuntimeStateDB(tmp_path / "state.db"))

    first_id = repo.append(
        "tg:1",
        "user",
        "hello",
        content_json={"kind": "input"},
        token_count=5,
    )
    second_id = repo.append(
        "tg:1",
        "assistant",
        "world",
        source="normal",
        protected=True,
    )

    assert first_id > 0
    assert second_id > first_id

    messages = repo.list_by_session("tg:1")
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content_json"] == {"kind": "input"}
    assert messages[1]["protected"] is True


def test_process_repository_create_finish_and_list(tmp_path: Path) -> None:
    repo = ProcessRepository(RuntimeStateDB(tmp_path / "state.db"))

    process_id = repo.create(
        "task:deadbeef",
        7,
        topic_id=3,
        provider="claude",
        model="opus",
        session_storage_key="tg:7:3",
    )

    active = repo.list_active(7)
    assert len(active) == 1
    assert active[0]["id"] == process_id

    repo.finish(process_id, exit_code=0)

    assert repo.list_active(7) == []
    loaded = repo.list_all()
    assert loaded[0]["exit_code"] == 0
    assert loaded[0]["session_storage_key"] == "tg:7:3"


def test_tool_call_repository_record_and_list(tmp_path: Path) -> None:
    repo = ToolCallRepository(RuntimeStateDB(tmp_path / "state.db"))

    tool_call_id = repo.record(
        "tg:1",
        "search",
        provider="claude",
        tool_namespace="mcp",
        arguments_json={"query": "ductors"},
        result_preview="ok",
    )

    loaded = repo.get(tool_call_id)
    assert loaded is not None
    assert loaded["tool_name"] == "search"
    assert loaded["arguments_json"] == {"query": "ductors"}

    by_session = repo.list_by_session("tg:1")
    assert len(by_session) == 1
    assert by_session[0]["id"] == tool_call_id


def test_memory_fragment_repository_round_trip(tmp_path: Path) -> None:
    repo = MemoryFragmentRepository(RuntimeStateDB(tmp_path / "state.db"))
    fragment = MemoryFragment(
        title="Preferences",
        body="- likes concise answers\n- prefers structured plans",
        source_path="workspace/memory_system/MAINMEMORY.md",
        source_kind="mainmemory",
        scope="main",
        agent_name="main",
        tags=["preferences", "answers"],
        importance=1.25,
    )

    fragment_id = repo.create(fragment)
    loaded = repo.get(fragment_id)

    assert loaded is not None
    assert loaded["title"] == "Preferences"
    assert loaded["scope"] == "main"
    assert loaded["agent_name"] == "main"
    assert loaded["tags_json"] == ["preferences", "answers"]


def test_memory_fragment_repository_scope_query(tmp_path: Path) -> None:
    repo = MemoryFragmentRepository(RuntimeStateDB(tmp_path / "state.db"))
    repo.replace_all(
        [
            MemoryFragment(title="A", body="alpha", scope="mainmemory", agent_name="main"),
            MemoryFragment(title="B", body="beta", scope="shared", agent_name="main"),
            MemoryFragment(title="C", body="gamma", scope="mainmemory", agent_name="sub"),
        ]
    )

    scoped = repo.list_by_scope("mainmemory", agent_name="main")
    assert [row["title"] for row in scoped] == ["A"]

    all_rows = repo.list_all()
    assert [row["title"] for row in all_rows] == ["A", "B", "C"]
    assert all(float(row["created_at"]) > 0 for row in all_rows)
    assert all(float(row["updated_at"]) > 0 for row in all_rows)


def test_memory_fragment_repository_replace_for_scope(tmp_path: Path) -> None:
    repo = MemoryFragmentRepository(RuntimeStateDB(tmp_path / "state.db"))
    repo.replace_all(
        [
            MemoryFragment(title="Old Main", body="alpha", scope="mainmemory", agent_name="main"),
            MemoryFragment(title="Shared", body="beta", scope="sharedmemory", agent_name=""),
        ]
    )

    repo.replace_for_scope(
        "mainmemory",
        [MemoryFragment(title="New Main", body="gamma", scope="mainmemory", agent_name="main")],
        agent_name="main",
    )

    scoped = repo.list_by_scope("mainmemory", agent_name="main")
    shared = repo.list_by_scope("sharedmemory")
    assert [row["title"] for row in scoped] == ["New Main"]
    assert [row["title"] for row in shared] == ["Shared"]
    assert float(scoped[0]["created_at"]) > 0
    assert float(scoped[0]["updated_at"]) > 0
    assert float(shared[0]["created_at"]) > 0
    assert float(shared[0]["updated_at"]) > 0


def test_memory_fragment_repository_deduplicates_and_reports_conflicts(tmp_path: Path) -> None:
    repo = MemoryFragmentRepository(RuntimeStateDB(tmp_path / "state.db"))
    repo.replace_for_scope(
        "mainmemory",
        [
            MemoryFragment(
                title="Preferences",
                body="- Keep answers short",
                scope="mainmemory",
                agent_name="main",
                ulid="mf_1",
            ),
            MemoryFragment(
                title="Preferences",
                body="- Keep answers short",
                scope="mainmemory",
                agent_name="main",
                ulid="mf_2",
            ),
            MemoryFragment(
                title="Preferences",
                body="- Prefer exhaustive replies",
                scope="mainmemory",
                agent_name="main",
                ulid="mf_3",
            ),
        ],
        agent_name="main",
    )

    stored = repo.list_by_scope("mainmemory", agent_name="main")
    conflicts = repo.list_conflicts("mainmemory", agent_name="main")

    assert len(stored) == 2
    assert len(conflicts) == 1
    assert conflicts[0].title == "Preferences"
