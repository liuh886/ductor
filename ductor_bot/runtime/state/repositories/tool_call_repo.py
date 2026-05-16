"""Tool-call repository backed by the runtime SQLite state DB."""

# ruff: noqa: PLR0913

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import cast

from ductor_bot.runtime.state.db import RuntimeStateDB


class ToolCallRepository:
    """Track tool calls for later pruning and analysis."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    def record(
        self,
        session_storage_key: str,
        tool_name: str,
        *,
        message_id: int | None = None,
        provider: str = "",
        tool_namespace: str = "",
        arguments_json: dict[str, object] | None = None,
        result_preview: str = "",
        latency_ms: float = 0.0,
        success: bool = True,
        outcome: str = "success",
        attempt: int = 1,
        details_json: dict[str, object] | None = None,
        sensitive: bool = False,
        compressible: bool = True,
    ) -> int:
        """Insert a tool-call row and return its ID."""
        with self._db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tool_calls (
                    session_storage_key, message_id, provider, tool_name,
                    tool_namespace, arguments_json, result_preview, latency_ms,
                    success, outcome, attempt, details_json, sensitive, compressible, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_storage_key,
                    message_id,
                    provider,
                    tool_name,
                    tool_namespace,
                    json.dumps(arguments_json or {}, ensure_ascii=False),
                    result_preview,
                    latency_ms,
                    int(success),
                    outcome,
                    attempt,
                    json.dumps(details_json or {}, ensure_ascii=False),
                    int(sensitive),
                    int(compressible),
                    time.time(),
                ),
            )
            return int(cursor.lastrowid or 0)

    def finish(
        self,
        tool_call_id: int,
        *,
        result_preview: str = "",
        latency_ms: float = 0.0,
        success: bool = True,
        outcome: str = "success",
        details_json: dict[str, object] | None = None,
    ) -> None:
        """Update one tool-call row with its final observed outcome."""
        with self._db.connect() as conn:
            conn.execute(
                """
                UPDATE tool_calls
                SET result_preview = ?,
                    latency_ms = ?,
                    success = ?,
                    outcome = ?,
                    details_json = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (
                    result_preview,
                    latency_ms,
                    int(success),
                    outcome,
                    json.dumps(details_json or {}, ensure_ascii=False),
                    time.time(),
                    tool_call_id,
                ),
            )

    def get(self, tool_call_id: int) -> dict[str, object] | None:
        """Load one tool-call row by ID."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tool_calls WHERE id = ?",
                (tool_call_id,),
            ).fetchone()
        if row is None:
            return None
        payload = dict(cast("Mapping[str, object]", row))
        payload["arguments_json"] = json.loads(str(payload.get("arguments_json", "{}")))
        payload["details_json"] = json.loads(str(payload.get("details_json", "{}")))
        payload["success"] = bool(payload.get("success", 0))
        payload["sensitive"] = bool(payload.get("sensitive", 0))
        payload["compressible"] = bool(payload.get("compressible", 0))
        return payload

    def list_by_session(self, session_storage_key: str) -> list[dict[str, object]]:
        """Load tool calls for a session."""
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM tool_calls
                WHERE session_storage_key = ?
                ORDER BY created_at ASC, id ASC
                """,
                (session_storage_key,),
            ).fetchall()
        results: list[dict[str, object]] = []
        for row in rows:
            payload = self.get(int(row["id"]))
            if payload is not None:
                results.append(payload)
        return results
