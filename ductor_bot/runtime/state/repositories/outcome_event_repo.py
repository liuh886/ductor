"""Outcome-event repository backed by the runtime SQLite state DB."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from ductor_bot.runtime.state.db import RuntimeStateDB

_FULL_PROMPT_KEYS = {
    "prompt",
    "full_prompt",
    "last_prompt",
    "original_prompt",
    "system_prompt",
    "user_prompt",
}


class OutcomeEventRepository:
    """Persist learnable outcome events keyed by their source."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    def upsert(  # noqa: PLR0913
        self,
        source_type: str,
        source_id: str,
        *,
        event_type: str = "",
        session_storage_key: str = "",
        task_id: str = "",
        process_id: int | None = None,
        provider: str = "",
        model: str = "",
        flow: str = "",
        outcome: str = "",
        failure_class: str = "",
        status: str = "",
        empty_result: bool = False,
        recovery_count: int = 0,
        duration_ms: float | None = None,
        confidence: float = 1.0,
        payload_json: Mapping[str, object] | None = None,
        learned: bool = False,
    ) -> int:
        """Insert or update one outcome event and return its row ID."""
        now = time.time()
        payload = _safe_payload_json(payload_json or {})
        with self._db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO outcome_events (
                    source_type, source_id, event_type, session_storage_key,
                    task_id, process_id, provider, model, flow, outcome,
                    failure_class, status, empty_result, recovery_count,
                    duration_ms, confidence, payload_json, learned, learned_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    event_type = excluded.event_type,
                    session_storage_key = excluded.session_storage_key,
                    task_id = excluded.task_id,
                    process_id = excluded.process_id,
                    provider = excluded.provider,
                    model = excluded.model,
                    flow = excluded.flow,
                    outcome = excluded.outcome,
                    failure_class = excluded.failure_class,
                    status = excluded.status,
                    empty_result = excluded.empty_result,
                    recovery_count = excluded.recovery_count,
                    duration_ms = excluded.duration_ms,
                    confidence = excluded.confidence,
                    payload_json = excluded.payload_json,
                    learned = excluded.learned,
                    learned_at = excluded.learned_at,
                    updated_at = excluded.updated_at
                RETURNING id
                """,
                (
                    source_type,
                    source_id,
                    event_type,
                    session_storage_key,
                    task_id,
                    process_id,
                    provider,
                    model,
                    flow,
                    outcome,
                    failure_class,
                    status,
                    int(empty_result),
                    recovery_count,
                    duration_ms,
                    _safe_confidence(confidence),
                    payload,
                    int(learned),
                    now if learned else None,
                    now,
                    now,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            msg = "outcome_events upsert did not return a row"
            raise RuntimeError(msg)
        return int(row["id"])

    def record(  # noqa: PLR0913
        self,
        source_type: str,
        source_id: str,
        *,
        event_type: str = "",
        session_storage_key: str = "",
        task_id: str = "",
        process_id: int | None = None,
        provider: str = "",
        model: str = "",
        flow: str = "",
        outcome: str = "",
        failure_class: str = "",
        status: str = "",
        empty_result: bool = False,
        recovery_count: int = 0,
        duration_ms: float | None = None,
        confidence: float = 1.0,
        payload_json: Mapping[str, object] | None = None,
    ) -> int:
        """Record an unlearned outcome event."""
        return self.upsert(
            source_type,
            source_id,
            event_type=event_type,
            session_storage_key=session_storage_key,
            task_id=task_id,
            process_id=process_id,
            provider=provider,
            model=model,
            flow=flow,
            outcome=outcome,
            failure_class=failure_class,
            status=status,
            empty_result=empty_result,
            recovery_count=recovery_count,
            duration_ms=duration_ms,
            confidence=confidence,
            payload_json=payload_json,
            learned=False,
        )

    def list_unlearned(self, *, limit: int = 100) -> list[dict[str, object]]:
        """Return unlearned events in update order."""
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM outcome_events
                WHERE learned = 0
                ORDER BY updated_at ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_recent(
        self,
        *,
        provider: str = "",
        flow: str = "",
        since: float | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Return recent outcome events, optionally filtered by provider/flow/time."""
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM outcome_events
                WHERE (? = '' OR provider = ?)
                  AND (? = '' OR flow = ?)
                  AND (? IS NULL OR created_at >= ?)
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (provider, provider, flow, flow, since, since, limit),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def mark_learned(self, source_type: str, source_id: str) -> None:
        """Mark one event as learned by its unique source key."""
        now = time.time()
        with self._db.connect() as conn:
            conn.execute(
                """
                UPDATE outcome_events
                SET learned = 1,
                    learned_at = ?,
                    updated_at = ?
                WHERE source_type = ? AND source_id = ?
                """,
                (now, now, source_type, source_id),
            )

    @staticmethod
    def _row_to_dict(row: object) -> dict[str, object]:
        payload = dict(cast("Mapping[str, object]", row))
        payload["payload_json"] = json.loads(str(payload.get("payload_json", "{}")))
        payload["learned"] = bool(payload.get("learned", 0))
        payload["empty_result"] = bool(payload.get("empty_result", 0))
        return payload


def _safe_payload_json(payload: Mapping[str, object]) -> str:
    safe_payload = _sanitize_payload(payload)
    return json.dumps(
        safe_payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _safe_confidence(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _sanitize_payload(value: Any) -> object:
    if isinstance(value, Mapping):
        clean: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_full_prompt_key(key_text):
                continue
            clean[key_text] = _sanitize_payload(item)
        return clean
    if isinstance(value, list | tuple | set):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, str | int | bool) or value is None:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _is_full_prompt_key(key: str) -> bool:
    normalized = key.lower()
    if normalized == "prompt_preview":
        return False
    return normalized in _FULL_PROMPT_KEYS or normalized.endswith("_prompt")
