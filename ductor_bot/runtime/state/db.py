"""SQLite runtime-state kernel."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ductor_bot.runtime.state.schema import load_schema_sql

_BUSY_TIMEOUT_MS = 5_000
_SESSION_LINEAGE_COLUMNS: dict[str, str] = {
    "lineage_id": "TEXT NOT NULL DEFAULT ''",
    "lineage_root": "TEXT NOT NULL DEFAULT ''",
    "lineage_parent": "TEXT NOT NULL DEFAULT ''",
    "lineage_depth": "INTEGER NOT NULL DEFAULT 0",
    "lineage_reason": "TEXT NOT NULL DEFAULT ''",
    "lineage_created_at": "TEXT NOT NULL DEFAULT ''",
}


class RuntimeStateDB:
    """Manage the shared SQLite runtime-state database."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    @property
    def path(self) -> Path:
        """Return the database path."""
        return self._path

    def connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection."""
        conn = sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_MS / 1000.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        return conn

    def ensure_schema(self) -> None:
        """Create tables and enable WAL mode."""
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(load_schema_sql())
            self._ensure_session_lineage_columns(conn)
            self._ensure_messages_features(conn)
            self._ensure_memory_fragments_ulid(conn)
            self._ensure_memory_fragments_timestamps(conn)
            self._ensure_inflight_turns_storage_key(conn)

    @staticmethod
    def _ensure_memory_fragments_ulid(conn: sqlite3.Connection) -> None:
        """Add ulid column to memory_fragments."""
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(memory_fragments)").fetchall()
        }
        if "ulid" not in existing:
            conn.execute("ALTER TABLE memory_fragments ADD COLUMN ulid TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _ensure_memory_fragments_timestamps(conn: sqlite3.Connection) -> None:
        """Add updated_at column to memory_fragments if missing."""
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(memory_fragments)").fetchall()
        }
        if "updated_at" not in existing:
            conn.execute("ALTER TABLE memory_fragments ADD COLUMN updated_at REAL NOT NULL DEFAULT 0")
        if "created_at" not in existing:
            # created_at should be there from 001_initial, but for robustness:
            conn.execute("ALTER TABLE memory_fragments ADD COLUMN created_at REAL NOT NULL DEFAULT 0")

    @staticmethod
    def _ensure_session_lineage_columns(conn: sqlite3.Connection) -> None:
        """Add lineage columns for pre-existing databases."""
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        for column, definition in _SESSION_LINEAGE_COLUMNS.items():
            if column in existing:
                continue
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} {definition}")

    @staticmethod
    def _ensure_messages_features(conn: sqlite3.Connection) -> None:
        """Add thought column and FTS to messages."""
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "thought" not in existing:
            conn.execute("ALTER TABLE messages ADD COLUMN thought TEXT NOT NULL DEFAULT ''")

        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                session_storage_key UNINDEXED,
                role UNINDEXED,
                content_text,
                thought
            )
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS messages_after_insert
            AFTER INSERT ON messages
            BEGIN
                INSERT INTO messages_fts (rowid, session_storage_key, role, content_text, thought)
                VALUES (new.id, new.session_storage_key, new.role, new.content_text, new.thought);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS messages_after_update
            AFTER UPDATE ON messages
            BEGIN
                DELETE FROM messages_fts WHERE rowid = old.id;
                INSERT INTO messages_fts (rowid, session_storage_key, role, content_text, thought)
                VALUES (new.id, new.session_storage_key, new.role, new.content_text, new.thought);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS messages_after_delete
            AFTER DELETE ON messages
            BEGIN
                DELETE FROM messages_fts WHERE rowid = old.id;
            END;
            """
        )
        conn.execute("DELETE FROM messages_fts")
        conn.execute(
            """
            INSERT INTO messages_fts (rowid, session_storage_key, role, content_text, thought)
            SELECT id, session_storage_key, role, content_text, thought
            FROM messages
            """
        )

    @staticmethod
    def _ensure_inflight_turns_storage_key(conn: sqlite3.Connection) -> None:
        """Migrate legacy inflight_turns(chat_id PK) to storage_key PK."""
        existing = conn.execute("PRAGMA table_info(inflight_turns)").fetchall()
        if not existing:
            return
        names = {str(row["name"]) for row in existing}
        if "storage_key" in names:
            return
        if "chat_id" not in names:
            return

        rows = conn.execute("SELECT chat_id, payload_json, updated_at FROM inflight_turns").fetchall()
        migrated: list[tuple[str, str, float]] = []
        for row in rows:
            payload_json = str(row["payload_json"])
            storage_key = f"tg:{int(row['chat_id'])}"
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                payload = {}
            transport = str(payload.get("transport", "tg") or "tg")
            chat_id = int(payload.get("chat_id", row["chat_id"]))
            topic_id = payload.get("topic_id")
            if topic_id in (None, ""):
                storage_key = f"{transport}:{chat_id}"
            else:
                storage_key = f"{transport}:{chat_id}:{int(topic_id)}"
            migrated.append((storage_key, payload_json, float(row["updated_at"])))

        conn.execute("ALTER TABLE inflight_turns RENAME TO inflight_turns_legacy")
        conn.execute(
            """
            CREATE TABLE inflight_turns (
                storage_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO inflight_turns (storage_key, payload_json, updated_at)
            VALUES (?, ?, ?)
            """,
            migrated,
        )
        conn.execute("DROP TABLE inflight_turns_legacy")
