"""Task-state repository backed by the runtime SQLite state DB."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from typing import cast

from ductor_bot.runtime.state.db import RuntimeStateDB
from ductor_bot.session import SessionKey


class TaskStateRepository:
    """CRUD helpers for active task-state snapshots."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    def upsert(  # noqa: PLR0913
        self,
        *,
        task_id: str,
        storage_key: str,
        status: str = "PENDING",
        current_step: int = 0,
        total_steps: int | None = None,
        step_label: str = "",
        context_snapshot_json: dict[str, object] | None = None,
        error_log: str = "",
    ) -> None:
        """Insert or update one task-state row."""
        now = time.time()
        payload = json.dumps(context_snapshot_json or {}, ensure_ascii=False)
        with self._db.connect() as conn:
            self._ensure_session_row(conn, storage_key, now)
            conn.execute(
                """
                INSERT INTO task_states (
                    task_id, storage_key, status, current_step, total_steps,
                    step_label, context_snapshot_json, error_log, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    storage_key = excluded.storage_key,
                    status = excluded.status,
                    current_step = excluded.current_step,
                    total_steps = excluded.total_steps,
                    step_label = excluded.step_label,
                    context_snapshot_json = excluded.context_snapshot_json,
                    error_log = excluded.error_log,
                    updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    storage_key,
                    status,
                    current_step,
                    total_steps,
                    step_label,
                    payload,
                    error_log,
                    now,
                    now,
                ),
            )

    def list_by_storage_key(self, storage_key: str) -> list[dict[str, object]]:
        """Return task states for one session key, most recently updated first."""
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM task_states
                WHERE storage_key = ?
                ORDER BY updated_at DESC, task_id ASC
                """,
                (storage_key,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def delete(self, task_id: str) -> None:
        """Delete one mirrored task-state row by task id."""
        with self._db.connect() as conn:
            conn.execute("DELETE FROM task_states WHERE task_id = ?", (task_id,))

    @staticmethod
    def _row_to_dict(row: object) -> dict[str, object]:
        """Convert a SQLite row to a plain dict."""
        payload = dict(cast("Mapping[str, object]", row))
        raw = str(payload.get("context_snapshot_json", "{}"))
        payload["context_snapshot_json"] = json.loads(raw)
        return payload

    @staticmethod
    def _ensure_session_row(conn: sqlite3.Connection, storage_key: str, now: float) -> None:
        """Insert a minimal session row so the FK on ``task_states`` is satisfied."""
        key = SessionKey.parse(storage_key)
        transport = key.transport
        chat_id = key.chat_id
        topic_id = key.topic_id
        conn.execute(
            """
            INSERT OR IGNORE INTO sessions (
                storage_key, transport, chat_id, topic_id, topic_name, provider, model,
                created_at, last_active, lineage_id, lineage_root, lineage_parent,
                lineage_depth, lineage_reason, lineage_created_at, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                storage_key,
                transport,
                chat_id,
                topic_id,
                "",
                "codex",
                "",
                "",
                "",
                "",
                "",
                "",
                0,
                "",
                "",
                "{}",
                now,
            ),
        )
