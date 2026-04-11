"""Memory-fragment repository backed by the runtime SQLite state DB."""

from __future__ import annotations

import json

from ductor_bot.runtime.memory.extractor import MemoryFragment
from ductor_bot.runtime.state.db import RuntimeStateDB


class MemoryFragmentRepository:
    """CRUD helpers for extracted Markdown memory fragments."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    def create(self, fragment: MemoryFragment) -> int:
        """Insert a fragment row and return its ID."""
        with self._db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO memory_fragments (
                    agent_name, scope, source_path, source_kind, title,
                    body, tags_json, importance, last_verified_at, stale_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fragment.agent_name,
                    fragment.scope,
                    fragment.source_path,
                    fragment.source_kind,
                    fragment.title,
                    fragment.body,
                    json.dumps(fragment.tags, ensure_ascii=False),
                    fragment.importance,
                    None,
                    None,
                ),
            )
            return int(cursor.lastrowid)

    def get(self, fragment_id: int) -> dict[str, object] | None:
        """Load a fragment row by ID."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_fragments WHERE id = ?",
                (fragment_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_all(self) -> list[dict[str, object]]:
        """Return all fragments in insertion order."""
        with self._db.connect() as conn:
            rows = conn.execute("SELECT * FROM memory_fragments ORDER BY id ASC").fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_by_scope(self, scope: str, *, agent_name: str = "") -> list[dict[str, object]]:
        """Return fragments for a scope, optionally filtered by agent."""
        query = "SELECT * FROM memory_fragments WHERE scope = ?"
        params: list[object] = [scope]
        if agent_name:
            query += " AND agent_name = ?"
            params.append(agent_name)
        query += " ORDER BY importance DESC, id ASC"
        with self._db.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def replace_all(self, fragments: list[MemoryFragment]) -> None:
        """Replace the fragment table contents with a new fragment set."""
        with self._db.connect() as conn:
            conn.execute("DELETE FROM memory_fragments")
            for fragment in fragments:
                conn.execute(
                    """
                    INSERT INTO memory_fragments (
                        agent_name, scope, source_path, source_kind, title,
                        body, tags_json, importance, last_verified_at, stale_after
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fragment.agent_name,
                        fragment.scope,
                        fragment.source_path,
                        fragment.source_kind,
                        fragment.title,
                        fragment.body,
                        json.dumps(fragment.tags, ensure_ascii=False),
                        fragment.importance,
                        None,
                        None,
                    ),
                )

    def replace_for_scope(
        self,
        scope: str,
        fragments: list[MemoryFragment],
        *,
        agent_name: str = "",
    ) -> None:
        """Replace fragments for a single scope, optionally scoped to one agent."""
        query = "DELETE FROM memory_fragments WHERE scope = ?"
        params: list[object] = [scope]
        if agent_name:
            query += " AND agent_name = ?"
            params.append(agent_name)
        with self._db.connect() as conn:
            conn.execute(query, tuple(params))
            for fragment in fragments:
                conn.execute(
                    """
                    INSERT INTO memory_fragments (
                        agent_name, scope, source_path, source_kind, title,
                        body, tags_json, importance, last_verified_at, stale_after
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fragment.agent_name,
                        fragment.scope,
                        fragment.source_path,
                        fragment.source_kind,
                        fragment.title,
                        fragment.body,
                        json.dumps(fragment.tags, ensure_ascii=False),
                        fragment.importance,
                        None,
                        None,
                    ),
                )

    @staticmethod
    def _row_to_dict(row: object) -> dict[str, object]:
        """Convert a SQLite row to a plain dict."""
        payload = dict(row)
        payload["tags_json"] = json.loads(str(payload.get("tags_json", "[]")))
        return payload
