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
