"""Memory-fragment repository backed by the runtime SQLite state DB."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ductor_bot.runtime.memory import MemoryConflict, govern_fragments
from ductor_bot.runtime.memory.extractor import MemoryFragment
from ductor_bot.runtime.state.db import RuntimeStateDB


class MemoryFragmentRepository:
    """CRUD helpers for extracted Markdown memory fragments."""

    def __init__(self, db: RuntimeStateDB, *, shared_db: RuntimeStateDB | None = None) -> None:
        self._db = db
        self._shared_db = shared_db

    def create(self, fragment: MemoryFragment) -> int:
        """Insert a fragment row and return its ID."""
        with self._db.connect() as conn:
            cursor = self._insert_fragment(conn, fragment)
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

        target_db = self._shared_db if scope == "sharedmemory" and self._shared_db else self._db

        with target_db.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def replace_all(self, fragments: list[MemoryFragment]) -> None:
        """Replace the fragment table contents with a new fragment set."""
        governed, _conflicts = govern_fragments(fragments)
        with self._db.connect() as conn:
            conn.execute("DELETE FROM memory_fragments")
            for fragment in governed:
                self._insert_fragment(conn, fragment)

    def replace_for_scope(
        self,
        scope: str,
        fragments: list[MemoryFragment],
        *,
        agent_name: str = "",
    ) -> None:
        """Replace fragments for a single scope, optionally scoped to one agent."""
        governed, _conflicts = govern_fragments(fragments)
        query = "DELETE FROM memory_fragments WHERE scope = ?"
        params: list[object] = [scope]
        if agent_name:
            query += " AND agent_name = ?"
            params.append(agent_name)
        with self._db.connect() as conn:
            conn.execute(query, tuple(params))
            for fragment in governed:
                self._insert_fragment(conn, fragment)

    def list_conflicts(self, scope: str, *, agent_name: str = "") -> list[MemoryConflict]:
        """Analyze persisted fragments for likely semantic conflicts."""
        rows = self.list_by_scope(scope, agent_name=agent_name)
        fragments = [
            MemoryFragment(
                title=str(row["title"]),
                body=str(row["body"]),
                ulid=str(row.get("ulid", "")),
                source_kind=str(row.get("source_kind", "")),
                source_path=str(row.get("source_path", "")),
                scope=str(row.get("scope", "")),
                agent_name=str(row.get("agent_name", "")),
                tags=list(row.get("tags_json", [])),
                importance=float(row.get("importance", 0.0)),
                created_at=float(row.get("created_at", 0.0)),
                updated_at=float(row.get("updated_at", 0.0)),
            )
            for row in rows
        ]
        _governed, conflicts = govern_fragments(fragments)
        return conflicts

    def update_by_ulid(self, ulid: str, body: str) -> bool:
        """Update a fragment body by its ULID. Returns True if found."""
        with self._db.connect() as conn:
            cursor = conn.execute(
                "UPDATE memory_fragments SET body = ?, updated_at = unixepoch() WHERE ulid = ?",
                (body, ulid),
            )
            return cursor.rowcount > 0

    def delete_by_ulid(self, ulid: str) -> bool:
        """Delete a fragment by its ULID. Returns True if found."""
        with self._db.connect() as conn:
            cursor = conn.execute("DELETE FROM memory_fragments WHERE ulid = ?", (ulid,))
            return cursor.rowcount > 0

    def get_by_ulid(self, ulid: str) -> dict[str, object] | None:
        """Load a fragment by ULID."""
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM memory_fragments WHERE ulid = ?", (ulid,)).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_by_source_path(self, source_path: str) -> list[dict[str, object]]:
        """List all fragments belonging to a single source file."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_fragments WHERE source_path = ? ORDER BY id ASC",
                (source_path,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: object) -> dict[str, object]:
        """Convert a SQLite row to a plain dict."""
        payload = dict(row)
        payload["tags_json"] = json.loads(str(payload.get("tags_json", "[]")))
        return payload

    def _insert_fragment(
        self,
        conn: sqlite3.Connection,
        fragment: MemoryFragment,
    ) -> sqlite3.Cursor:
        """Insert a fragment row with non-null timestamps."""
        created_at, updated_at = self._resolved_timestamps(fragment)
        return conn.execute(
            """
            INSERT INTO memory_fragments (
                ulid, agent_name, scope, source_path, source_kind, title,
                body, tags_json, importance, last_verified_at, stale_after,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fragment.ulid,
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
                created_at,
                updated_at,
            ),
        )

    @staticmethod
    def _resolved_timestamps(fragment: MemoryFragment) -> tuple[float, float]:
        """Return non-null created/updated timestamps for a fragment insert."""
        created_at = float(fragment.created_at) if fragment.created_at > 0 else _source_mtime(fragment)
        updated_at = float(fragment.updated_at) if fragment.updated_at > 0 else created_at
        return created_at, updated_at


def _source_mtime(fragment: MemoryFragment) -> float:
    """Prefer the source file mtime over wall-clock time when it is directly resolvable."""
    source_path = fragment.source_path.strip()
    if source_path:
        path = Path(source_path)
        if path.is_absolute():
            try:
                return path.stat().st_mtime
            except OSError:
                pass
    return datetime.now(UTC).timestamp()
