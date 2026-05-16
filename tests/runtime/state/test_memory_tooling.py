"""Tests for memory/session search tooling."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ductor_bot._home_defaults.workspace.tools.agent_tools.search_past_sessions import (
    search_past_sessions,
)
from ductor_bot.config import AgentConfig
from ductor_bot.runtime.state.db import RuntimeStateDB
from ductor_bot.runtime.state.repositories.memory_promotion_journal_repo import (
    MemoryPromotionJournalRepository,
)
from ductor_bot.runtime.state.repositories.message_repo import MessageRepository
from ductor_bot.runtime.state.repositories.session_repo import SessionRepository
from ductor_bot.scripts.memory_synthesis import _build_request, _find_latest_session, run_synthesis
from ductor_bot.session.manager import ProviderSessionData, SessionData
from ductor_bot.tools.agent_tools.memory_atomic_op import _resolve_source_path
from ductor_bot.tools.agent_tools.search_session_history import search_session_history
from ductor_bot.workspace.paths import resolve_paths


def test_search_session_history_searches_root_and_agent_state_dbs(tmp_path: Path) -> None:
    ductor_home = tmp_path / ".ductor"
    main_db = RuntimeStateDB(ductor_home / "state.db")
    subagent_db = RuntimeStateDB(ductor_home / "agents" / "research" / "state.db")

    MessageRepository(main_db).append("tg:main", "user", "nebula root memory")
    MessageRepository(subagent_db).append("tg:agent", "assistant", "nebula agent memory")

    results = search_session_history("nebula", ductor_home=ductor_home)

    assert len(results) == 2
    assert {(str(row["agent_name"]), str(row["session_id"])) for row in results} == {
        ("main", "tg:main"),
        ("research", "tg:agent"),
    }
    assert {str(row["state_scope"]) for row in results} == {"main", "subagent"}


def test_search_past_sessions_searches_messages_and_memory_fragments(tmp_path: Path) -> None:
    ductor_home = tmp_path / ".ductor"
    main_db = RuntimeStateDB(ductor_home / "state.db")
    subagent_db = RuntimeStateDB(ductor_home / "agents" / "research" / "state.db")

    MessageRepository(main_db).append("tg:main", "user", "quasar discussion")
    with main_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO memory_fragments (
                agent_name, scope, title, body, tags_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("", "sharedmemory", "Infra", "quasar note", '["ops"]', 10.0, 10.0),
        )
    with subagent_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO memory_fragments (
                agent_name, scope, title, body, tags_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("research", "mainmemory", "Research", "quasar finding", '["science"]', 20.0, 20.0),
        )

    results = search_past_sessions("quasar", ductor_home=ductor_home, limit=10)

    assert {str(row["result_type"]) for row in results} == {"message", "memory_fragment"}
    assert ("main", "main") in {
        (str(row["agent_name"]), str(row["state_scope"]))
        for row in results
        if row["result_type"] == "message"
    }
    assert ("research", "subagent") in {
        (str(row["agent_name"]), str(row["state_scope"]))
        for row in results
        if row["result_type"] == "memory_fragment"
    }


def test_resolve_source_path_uses_owner_home_for_workspace_aliases(tmp_path: Path) -> None:
    root_home = tmp_path / ".ductor"
    owner_home = root_home / "agents" / "research"

    mainmemory_path = _resolve_source_path(
        "@ductor/workspace/memory_system/MAINMEMORY.md",
        owner_home=owner_home,
        root_home=root_home,
    )
    sharedmemory_path = _resolve_source_path(
        "@ductor/SHAREDMEMORY.md",
        owner_home=owner_home,
        root_home=root_home,
    )
    relative_path = _resolve_source_path(
        "workspace/memory_system/NOTES.md",
        owner_home=owner_home,
        root_home=root_home,
    )

    assert mainmemory_path == owner_home / "workspace" / "memory_system" / "MAINMEMORY.md"
    assert sharedmemory_path == root_home / "SHAREDMEMORY.md"
    assert relative_path == owner_home / "workspace" / "memory_system" / "NOTES.md"


def test_find_latest_session_returns_newest_session_for_chat(tmp_path: Path) -> None:
    db = RuntimeStateDB(tmp_path / "state.db")
    repo = SessionRepository(db)

    first = SessionData(
        chat_id=123,
        provider="claude",
        model="opus",
        provider_sessions={"claude": ProviderSessionData(session_id="sid-old", message_count=1)},
    )
    second = SessionData(
        chat_id=123,
        provider="claude",
        model="sonnet",
        provider_sessions={"claude": ProviderSessionData(session_id="sid-new", message_count=2)},
    )
    repo.upsert("tg:old", first)
    repo.upsert("tg:new", second)

    loaded = _find_latest_session(db, 123)

    assert loaded is not None
    assert loaded.session_id == "sid-new"
    assert loaded.model == "sonnet"


def test_build_request_uses_resumed_provider_session() -> None:
    session = SessionData(
        chat_id=321,
        topic_id=7,
        provider="claude",
        model="opus",
        provider_sessions={"claude": ProviderSessionData(session_id="sid-123", message_count=10)},
    )
    request = _build_request(session, AgentConfig(), 25)

    assert request.resume_session == "sid-123"
    assert request.provider_override == "claude"
    assert request.model_override == "opus"
    assert "memory_promotion_journal" in request.prompt
    assert "JSON object" in request.prompt
    assert "MAINMEMORY.md" in request.prompt
    assert "Do not edit MAINMEMORY.md" in request.prompt
    assert "memory_atomic_op.py" not in request.prompt


async def test_run_synthesis_returns_error_when_latest_session_has_no_session_id(
    tmp_path: Path,
) -> None:
    ductor_home = tmp_path / ".ductor"
    config = AgentConfig(
        ductor_home=str(ductor_home),
        state_backend="sqlite",
        state_db_path=str(ductor_home / "state.db"),
    )
    paths = resolve_paths(ductor_home=ductor_home)
    db = RuntimeStateDB(ductor_home / "state.db")
    SessionRepository(db).upsert(
        "tg:123",
        SessionData(chat_id=123, provider="claude", model="opus"),
    )

    result = await run_synthesis(123, config=config, paths=paths)

    assert result == 1


async def test_run_synthesis_executes_resumed_cli_turn(tmp_path: Path) -> None:
    ductor_home = tmp_path / ".ductor"
    config = AgentConfig(
        ductor_home=str(ductor_home),
        state_backend="sqlite",
        state_db_path=str(ductor_home / "state.db"),
    )
    paths = resolve_paths(ductor_home=ductor_home)
    db = RuntimeStateDB(ductor_home / "state.db")
    message_id = MessageRepository(db).append("tg:123", "user", "Remember that I prefer bullets.")
    SessionRepository(db).upsert(
        "tg:123",
        SessionData(
            chat_id=123,
            provider="claude",
            model="opus",
            provider_sessions={
                "claude": ProviderSessionData(session_id="sid-live", message_count=5)
            },
        ),
    )

    fake_orch = AsyncMock()
    fake_orch._cli_service.execute = AsyncMock(
        return_value=type(
            "Resp",
            (),
            {
                "result": json.dumps(
                    {
                        "candidates": [
                            {
                                "target_scope": "mainmemory",
                                "title": "Formatting preference",
                                "body": "- Prefers bullets.",
                                "tags": ["preference"],
                                "source_message_ids": [message_id],
                            }
                        ]
                    }
                ),
                "is_error": False,
            },
        )()
    )

    with patch("ductor_bot.scripts.memory_synthesis.Orchestrator", return_value=fake_orch):
        result = await run_synthesis(123, limit=12, config=config, paths=paths)

    assert result == 0
    request = fake_orch._cli_service.execute.call_args[0][0]
    assert request.resume_session == "sid-live"
    assert request.provider_override == "claude"
    assert request.model_override == "opus"
    assert "memory_promotion_journal" in request.prompt
    assert f"id={message_id}" in request.prompt
    pending = MemoryPromotionJournalRepository(db).list_pending(target_scope="mainmemory")
    assert len(pending) == 1
    assert pending[0]["title"] == "Formatting preference"
