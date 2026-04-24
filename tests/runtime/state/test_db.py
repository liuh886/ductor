"""Tests for the SQLite runtime-state kernel."""

# ruff: noqa: INP001

from __future__ import annotations

import sqlite3
from pathlib import Path

from ductor_bot.runtime.state.db import RuntimeStateDB


def test_runtime_state_db_creates_schema_and_wal(tmp_path: Path) -> None:
    db = RuntimeStateDB(tmp_path / "state.db")

    with db.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        mode = conn.execute("PRAGMA journal_mode").fetchone()

    assert "sessions" in tables
    assert "session_provider_state" in tables
    assert "named_sessions" in tables
    assert "tasks" in tables
    assert "messages" in tables
    assert "processes" in tables
    assert "tool_calls" in tables
    assert mode is not None
    assert str(mode[0]).lower() == "wal"


def test_runtime_state_db_connection_uses_row_factory(tmp_path: Path) -> None:
    db = RuntimeStateDB(tmp_path / "state.db")

    with db.connect() as conn:
        row = conn.execute("SELECT 1 AS value").fetchone()

    assert isinstance(row, sqlite3.Row)
    assert row["value"] == 1


def test_runtime_state_db_adds_session_lineage_columns_to_existing_db(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE sessions (
                storage_key TEXT PRIMARY KEY,
                transport TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                topic_id INTEGER,
                topic_name TEXT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_active TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

    db = RuntimeStateDB(path)

    with db.connect() as conn:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }

    assert "lineage_id" in columns
    assert "lineage_root" in columns
    assert "lineage_parent" in columns
    assert "lineage_depth" in columns
    assert "lineage_reason" in columns
    assert "lineage_created_at" in columns


def test_runtime_state_db_backfills_and_maintains_messages_fts(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_storage_key TEXT NOT NULL,
                turn_index INTEGER NOT NULL DEFAULT 0,
                role TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'normal',
                content_text TEXT NOT NULL DEFAULT '',
                content_json TEXT NOT NULL DEFAULT '{}',
                token_count INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
                is_compressed INTEGER NOT NULL DEFAULT 0,
                protected INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT (unixepoch())
            )
            """
        )
        conn.execute(
            """
            INSERT INTO messages (
                session_storage_key, turn_index, role, source, content_text
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("tg:1", 0, "user", "normal", "historical context"),
        )

    db = RuntimeStateDB(path)

    with db.connect() as conn:
        backfilled = conn.execute(
            "SELECT content_text FROM messages_fts WHERE messages_fts MATCH ?",
            ("historical",),
        ).fetchall()
        conn.execute(
            "UPDATE messages SET content_text = ?, thought = ? WHERE id = 1",
            ("updated context", "internal note"),
        )
        updated = conn.execute(
            "SELECT content_text, thought FROM messages_fts WHERE rowid = 1"
        ).fetchone()
        conn.execute("DELETE FROM messages WHERE id = 1")
        remaining = conn.execute("SELECT COUNT(*) AS count FROM messages_fts").fetchone()

    assert [str(row["content_text"]) for row in backfilled] == ["historical context"]
    assert updated is not None
    assert updated["content_text"] == "updated context"
    assert updated["thought"] == "internal note"
    assert remaining is not None
    assert remaining["count"] == 0


def test_runtime_state_db_backfills_existing_rows_when_messages_fts_already_exists(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_storage_key TEXT NOT NULL,
                turn_index INTEGER NOT NULL DEFAULT 0,
                role TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'normal',
                content_text TEXT NOT NULL DEFAULT '',
                thought TEXT NOT NULL DEFAULT '',
                content_json TEXT NOT NULL DEFAULT '{}',
                token_count INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
                is_compressed INTEGER NOT NULL DEFAULT 0,
                protected INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT (unixepoch())
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                session_storage_key UNINDEXED,
                role UNINDEXED,
                content_text,
                thought
            )
            """
        )
        conn.execute(
            """
            INSERT INTO messages (
                session_storage_key, turn_index, role, source, content_text, thought
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("tg:2", 0, "assistant", "normal", "missing index row", "analysis"),
        )

    db = RuntimeStateDB(path)

    with db.connect() as conn:
        row = conn.execute(
            "SELECT content_text, thought FROM messages_fts WHERE rowid = 1"
        ).fetchone()

    assert row is not None
    assert row["content_text"] == "missing index row"
    assert row["thought"] == "analysis"


def test_runtime_state_db_migrates_legacy_inflight_turns_table(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE inflight_turns (
                chat_id INTEGER PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO inflight_turns (chat_id, payload_json, updated_at)
            VALUES (?, ?, ?)
            """,
            (
                100,
                '{"transport":"tg","chat_id":100,"topic_id":7,"provider":"codex","model":"gpt-5.4","session_id":"sess-1","prompt_preview":"resume me","started_at":"2026-01-01T00:00:00+00:00","is_recovery":false,"path":"normal","request":{"chat_id":100,"topic_id":7}}',
                1.0,
            ),
        )

    db = RuntimeStateDB(path)

    with db.connect() as conn:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(inflight_turns)").fetchall()
        }
        row = conn.execute(
            "SELECT storage_key, payload_json FROM inflight_turns"
        ).fetchone()

    assert columns == {"storage_key", "payload_json", "updated_at"}
    assert row is not None
    assert row["storage_key"] == "tg:100:7"
