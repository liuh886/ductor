"""SQLite runtime-state kernel."""

from __future__ import annotations

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
