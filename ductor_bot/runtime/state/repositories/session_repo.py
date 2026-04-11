"""Session repository backed by the runtime SQLite state DB."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

from ductor_bot.runtime.state.db import RuntimeStateDB

if TYPE_CHECKING:
    from ductor_bot.session.manager import SessionData


class SessionRepository:
    """CRUD for persisted sessions and provider-local state."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    def upsert(self, storage_key: str, session: SessionData) -> None:
        """Insert or replace a session and its provider buckets."""
        payload = asdict(session)
        now = time.time()
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    storage_key, transport, chat_id, topic_id, topic_name,
                    provider, model, created_at, last_active,
                    lineage_id, lineage_root, lineage_parent, lineage_depth,
                    lineage_reason, lineage_created_at, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(storage_key) DO UPDATE SET
                    transport=excluded.transport,
                    chat_id=excluded.chat_id,
                    topic_id=excluded.topic_id,
                    topic_name=excluded.topic_name,
                    provider=excluded.provider,
                    model=excluded.model,
                    created_at=excluded.created_at,
                    last_active=excluded.last_active,
                    lineage_id=excluded.lineage_id,
                    lineage_root=excluded.lineage_root,
                    lineage_parent=excluded.lineage_parent,
                    lineage_depth=excluded.lineage_depth,
                    lineage_reason=excluded.lineage_reason,
                    lineage_created_at=excluded.lineage_created_at,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    storage_key,
                    session.transport,
                    session.chat_id,
                    session.topic_id,
                    session.topic_name,
                    session.provider,
                    session.model,
                    session.created_at,
                    session.last_active,
                    session.lineage_id,
                    session.lineage_root,
                    session.lineage_parent,
                    session.lineage_depth,
                    session.lineage_reason,
                    session.lineage_created_at,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
            conn.execute("DELETE FROM session_provider_state WHERE storage_key = ?", (storage_key,))
            for provider, provider_data in session.provider_sessions.items():
                provider_payload = asdict(provider_data)
                conn.execute(
                    """
                    INSERT INTO session_provider_state (
                        storage_key, provider, session_id, message_count,
                        total_cost_usd, total_tokens, payload_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        storage_key,
                        provider,
                        provider_data.session_id,
                        provider_data.message_count,
                        provider_data.total_cost_usd,
                        provider_data.total_tokens,
                        json.dumps(provider_payload, ensure_ascii=False),
                        now,
                    ),
                )

    def get(self, storage_key: str) -> SessionData | None:
        """Load one session by storage key."""
        from ductor_bot.session.manager import ProviderSessionData, SessionData

        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM sessions WHERE storage_key = ?",
                (storage_key,),
            ).fetchone()
            if row is None:
                return None
            payload = json.loads(str(row["payload_json"]))
            provider_rows = conn.execute(
                """
                SELECT provider, payload_json
                FROM session_provider_state
                WHERE storage_key = ?
                ORDER BY provider
                """,
                (storage_key,),
            ).fetchall()
        provider_sessions = {
            str(provider_row["provider"]): ProviderSessionData(
                **json.loads(str(provider_row["payload_json"]))
            )
            for provider_row in provider_rows
        }
        payload["provider_sessions"] = provider_sessions
        return SessionData(**payload)

    def list_all(self) -> list[SessionData]:
        """Load every persisted session."""
        with self._db.connect() as conn:
            keys = [
                str(row["storage_key"])
                for row in conn.execute(
                    "SELECT storage_key FROM sessions ORDER BY updated_at DESC"
                ).fetchall()
            ]
        results: list[SessionData] = []
        for key in keys:
            session = self.get(key)
            if session is not None:
                results.append(session)
        return results
