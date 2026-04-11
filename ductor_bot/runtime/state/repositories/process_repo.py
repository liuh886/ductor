"""Process repository backed by the runtime SQLite state DB."""

# ruff: noqa: PLR0913

from __future__ import annotations

from ductor_bot.runtime.state.db import RuntimeStateDB


class ProcessRepository:
    """Track process start/end facts for crash recovery and lineage."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    def create(
        self,
        process_label: str,
        chat_id: int,
        *,
        topic_id: int | None = None,
        provider: str = "",
        model: str = "",
        session_storage_key: str = "",
        abort_reason: str = "",
        timed_out: bool = False,
    ) -> int:
        """Insert a new process row and return its ID."""
        with self._db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO processes (
                    process_label, chat_id, topic_id, provider, model,
                    session_storage_key, abort_reason, timed_out
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    process_label,
                    chat_id,
                    topic_id,
                    provider,
                    model,
                    session_storage_key,
                    abort_reason,
                    int(timed_out),
                ),
            )
            return int(cursor.lastrowid)

    def finish(self, process_id: int, *, exit_code: int | None = None) -> None:
        """Mark a process as finished."""
        with self._db.connect() as conn:
            conn.execute(
                """
                UPDATE processes
                SET ended_at = unixepoch(), exit_code = ?
                WHERE id = ?
                """,
                (exit_code, process_id),
            )

    def list_active(self, chat_id: int | None = None) -> list[dict[str, object]]:
        """Return processes that have not yet been finished."""
        query = "SELECT * FROM processes WHERE ended_at IS NULL"
        params: tuple[object, ...] = ()
        if chat_id is not None:
            query += " AND chat_id = ?"
            params = (chat_id,)
        query += " ORDER BY started_at ASC, id ASC"
        with self._db.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_all(self) -> list[dict[str, object]]:
        """Return every process row."""
        with self._db.connect() as conn:
            rows = conn.execute("SELECT * FROM processes ORDER BY id ASC").fetchall()
        return [dict(row) for row in rows]
