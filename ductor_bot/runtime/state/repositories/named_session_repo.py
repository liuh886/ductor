"""Named-session repository backed by the runtime SQLite state DB."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

from ductor_bot.runtime.state.db import RuntimeStateDB

if TYPE_CHECKING:
    from ductor_bot.session.named import NamedSession


class NamedSessionRepository:
    """CRUD for named sessions."""

    def __init__(self, db: RuntimeStateDB) -> None:
        self._db = db

    def replace_all(self, sessions: list[NamedSession]) -> None:
        """Replace the persisted named-session set."""
        now = time.time()
        with self._db.connect() as conn:
            conn.execute("DELETE FROM named_sessions")
            for session in sessions:
                payload = asdict(session)
                conn.execute(
                    """
                    INSERT INTO named_sessions (
                        chat_id, name, transport, provider, model, session_id,
                        prompt_preview, status, created_at, message_count,
                        last_prompt, payload_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.chat_id,
                        session.name,
                        session.transport,
                        session.provider,
                        session.model,
                        session.session_id,
                        session.prompt_preview,
                        session.status,
                        session.created_at,
                        session.message_count,
                        session.last_prompt,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                    ),
                )

    def list_all(self) -> list[NamedSession]:
        """Load all named sessions from the DB."""
        from ductor_bot.session.named import NamedSession

        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM named_sessions ORDER BY created_at ASC"
            ).fetchall()
        return [NamedSession(**json.loads(str(row["payload_json"]))) for row in rows]
