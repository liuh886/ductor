"""Inflight-turn repository backed by the runtime SQLite state DB."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from ductor_bot.runtime.state.db import RuntimeStateDB

if TYPE_CHECKING:
    from ductor_bot.infra.inflight import InflightTurn


class InflightTurnRepository:
    """CRUD helpers for foreground inflight turns."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    def upsert(self, turn: InflightTurn) -> None:
        """Insert or replace a chat's inflight turn."""
        payload = {
            "chat_id": turn.chat_id,
            "provider": turn.provider,
            "model": turn.model,
            "session_id": turn.session_id,
            "prompt_preview": turn.prompt_preview,
            "started_at": turn.started_at,
            "is_recovery": turn.is_recovery,
            "path": turn.path,
        }
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO inflight_turns (chat_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (turn.chat_id, json.dumps(payload, ensure_ascii=False), time.time()),
            )

    def delete(self, chat_id: int) -> None:
        """Remove one inflight turn."""
        with self._db.connect() as conn:
            conn.execute("DELETE FROM inflight_turns WHERE chat_id = ?", (chat_id,))

    def get(self, chat_id: int) -> dict[str, object] | None:
        """Load one inflight turn by chat id."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM inflight_turns WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(str(row["payload_json"]))

    def list_all(self) -> list[dict[str, object]]:
        """Return all inflight turns."""
        with self._db.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM inflight_turns ORDER BY chat_id ASC").fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]
