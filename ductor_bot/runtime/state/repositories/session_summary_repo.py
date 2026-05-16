"""Session-summary repository backed by the runtime SQLite state DB."""

from __future__ import annotations

from ductor_bot.runtime.state.db import RuntimeStateDB


class SessionSummaryRepository:
    """CRUD helpers for persisted session summaries."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    # ruff: noqa: PLR0913
    def create(
        self,
        session_storage_key: str,
        kind: str,
        summary_text: str,
        *,
        coverage_from_message_id: int | None = None,
        coverage_to_message_id: int | None = None,
        model: str = "",
        version: str = "",
    ) -> int:
        """Insert a summary row and return its ID."""
        with self._db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO session_summaries (
                    session_storage_key,
                    kind,
                    summary_text,
                    coverage_from_message_id,
                    coverage_to_message_id,
                    model,
                    version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_storage_key,
                    kind,
                    summary_text,
                    coverage_from_message_id,
                    coverage_to_message_id,
                    model,
                    version,
                ),
            )
            return int(cursor.lastrowid or 0)

    def latest_for_session(
        self,
        session_storage_key: str,
        *,
        kind: str | None = None,
    ) -> dict[str, object] | None:
        """Return the newest summary row for a session."""
        query = """
            SELECT *
            FROM session_summaries
            WHERE session_storage_key = ?
        """
        params: list[object] = [session_storage_key]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY created_at DESC, id DESC LIMIT 1"
        with self._db.connect() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        if row is None:
            return None
        return dict(row)
