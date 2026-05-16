"""Memory-promotion journal repository backed by the runtime SQLite state DB."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast

from ductor_bot.runtime.state.db import RuntimeStateDB


class MemoryPromotionJournalRepository:
    """Persist candidate memory promotions before they become fragments."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    def create_candidate(  # noqa: PLR0913
        self,
        *,
        session_storage_key: str,
        source_message_ids: Sequence[int],
        agent_name: str,
        target_scope: str,
        title: str,
        body: str,
        tags: Sequence[str] | None = None,
        verification: Mapping[str, object] | None = None,
    ) -> int:
        """Create a pending candidate or return the existing duplicate row ID."""
        now = time.time()
        source_ids = _normalized_source_message_ids(source_message_ids)
        source_ids_json = _safe_json(source_ids)
        tags_json = _safe_json(list(tags or []))
        verification_json = _safe_json(dict(verification or {}))
        idempotency_key = _idempotency_key(
            target_scope=target_scope,
            agent_name=agent_name,
            title=title,
            body=body,
            source_message_ids=source_ids,
        )

        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO memory_promotion_journal (
                    idempotency_key, session_storage_key, source_message_ids_json,
                    agent_name, target_scope, title, body, tags_json, status,
                    verification_json, promoted_fragment_ulid, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, '', ?, ?)
                """,
                (
                    idempotency_key,
                    session_storage_key,
                    source_ids_json,
                    agent_name,
                    target_scope,
                    title,
                    body,
                    tags_json,
                    verification_json,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM memory_promotion_journal WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            msg = "memory_promotion_journal insert did not return a row"
            raise RuntimeError(msg)
        return int(row["id"])

    def get(self, candidate_id: int) -> dict[str, object] | None:
        """Load a journal row by ID."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_promotion_journal WHERE id = ?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_pending(
        self,
        *,
        target_scope: str = "",
        agent_name: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Return pending candidates in creation order."""
        query = "SELECT * FROM memory_promotion_journal WHERE status = 'pending'"
        params: list[object] = []
        if target_scope:
            query += " AND target_scope = ?"
            params.append(target_scope)
        if agent_name:
            query += " AND agent_name = ?"
            params.append(agent_name)
        query += " ORDER BY created_at ASC, id ASC LIMIT ?"
        params.append(limit)

        with self._db.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def set_status(
        self,
        candidate_id: int,
        status: str,
        *,
        verification: Mapping[str, object] | None = None,
    ) -> bool:
        """Set candidate status and optionally replace verification metadata."""
        now = time.time()
        with self._db.connect() as conn:
            if verification is None:
                cursor = conn.execute(
                    """
                    UPDATE memory_promotion_journal
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, now, candidate_id),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE memory_promotion_journal
                    SET status = ?, verification_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, _safe_json(dict(verification)), now, candidate_id),
                )
        return cursor.rowcount > 0

    def mark_promoted(
        self,
        candidate_id: int,
        promoted_fragment_ulid: str,
        *,
        verification: Mapping[str, object] | None = None,
    ) -> bool:
        """Mark a candidate as promoted and link it to the created fragment ULID."""
        now = time.time()
        with self._db.connect() as conn:
            if verification is None:
                cursor = conn.execute(
                    """
                    UPDATE memory_promotion_journal
                    SET status = 'promoted',
                        promoted_fragment_ulid = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (promoted_fragment_ulid, now, candidate_id),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE memory_promotion_journal
                    SET status = 'promoted',
                        verification_json = ?,
                        promoted_fragment_ulid = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        _safe_json(dict(verification)),
                        promoted_fragment_ulid,
                        now,
                        candidate_id,
                    ),
                )
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_dict(row: object) -> dict[str, object]:
        payload = dict(cast("Mapping[str, object]", row))
        payload["source_message_ids_json"] = json.loads(
            str(payload.get("source_message_ids_json", "[]"))
        )
        payload["tags_json"] = json.loads(str(payload.get("tags_json", "[]")))
        payload["verification_json"] = json.loads(str(payload.get("verification_json", "{}")))
        return payload


def _normalized_source_message_ids(source_message_ids: Sequence[int]) -> list[int]:
    return sorted({int(message_id) for message_id in source_message_ids})


def _idempotency_key(
    *,
    target_scope: str,
    agent_name: str,
    title: str,
    body: str,
    source_message_ids: Sequence[int],
) -> str:
    payload = {
        "agent_name": agent_name,
        "body": body,
        "source_message_ids": list(source_message_ids),
        "target_scope": target_scope,
        "title": title,
    }
    digest = hashlib.sha256(_safe_json(payload).encode("utf-8")).hexdigest()
    return f"mpj_{digest}"


def _safe_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
