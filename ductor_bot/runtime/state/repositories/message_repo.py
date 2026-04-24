"""Message repository backed by the runtime SQLite state DB."""

# ruff: noqa: PLR0913

from __future__ import annotations

import json

from ductor_bot.runtime.state.db import RuntimeStateDB


class MessageRepository:
    """Append and read message-level runtime facts."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    def append(
        self,
        session_storage_key: str,
        role: str,
        content_text: str = "",
        *,
        thought: str = "",
        source: str = "normal",
        content_json: dict[str, object] | None = None,
        turn_index: int | None = None,
        token_count: int = 0,
        cost_usd: float = 0.0,
        is_compressed: bool = False,
        protected: bool = False,
        tool_call_id: int | None = None,
        process_id: int | None = None,
    ) -> int:
        """Insert a message row and return its ID."""
        with self._db.connect() as conn:
            next_turn = turn_index
            if next_turn is None:
                row = conn.execute(
                    """
                    SELECT COALESCE(MAX(turn_index), -1) + 1 AS next_turn
                    FROM messages
                    WHERE session_storage_key = ?
                    """,
                    (session_storage_key,),
                ).fetchone()
                next_turn = int(row["next_turn"]) if row is not None else 0
            payload = json.dumps(content_json or {}, ensure_ascii=False)
            cursor = conn.execute(
                """
                INSERT INTO messages (
                    session_storage_key, turn_index, role, source, content_text,
                    thought, content_json, token_count, cost_usd, is_compressed,
                    protected, tool_call_id, process_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_storage_key,
                    next_turn,
                    role,
                    source,
                    content_text,
                    thought,
                    payload,
                    token_count,
                    cost_usd,
                    int(is_compressed),
                    int(protected),
                    tool_call_id,
                    process_id,
                ),
            )
            return int(cursor.lastrowid)

    def get(self, message_id: int) -> dict[str, object] | None:
        """Load a message row by ID."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_by_session(
        self,
        session_storage_key: str,
        *,
        newest_first: bool = False,
    ) -> list[dict[str, object]]:
        """Load messages for a session in chronological order."""
        query = """
            SELECT *
            FROM messages
            WHERE session_storage_key = ?
            ORDER BY turn_index ASC, id ASC
        """
        if newest_first:
            query = """
                SELECT *
                FROM messages
                WHERE session_storage_key = ?
                ORDER BY turn_index DESC, id DESC
            """
        with self._db.connect() as conn:
            rows = conn.execute(query, (session_storage_key,)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_all(self) -> list[dict[str, object]]:
        """Load all messages in insertion order."""
        with self._db.connect() as conn:
            rows = conn.execute("SELECT * FROM messages ORDER BY id ASC").fetchall()
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: object) -> dict[str, object]:
        """Convert a SQLite row to a plain dict."""
        mapping = dict(row)  # sqlite3.Row is mapping-like
        mapping["content_json"] = json.loads(str(mapping.get("content_json", "{}")))
        mapping["is_compressed"] = bool(mapping.get("is_compressed", 0))
        mapping["protected"] = bool(mapping.get("protected", 0))
        return mapping
