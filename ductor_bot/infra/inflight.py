"""Track in-flight CLI turns for crash recovery."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ductor_bot.infra.json_store import atomic_json_save, load_json
from ductor_bot.runtime.state import InflightTurnRepository, RuntimeStateDB
from ductor_bot.session.key import SessionKey

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class InflightTurn:
    """State of a single in-flight CLI turn."""

    chat_id: int
    provider: str
    model: str
    session_id: str
    prompt_preview: str
    started_at: str
    is_recovery: bool
    path: str  # "normal" | "background"
    transport: str = "tg"
    topic_id: int | None = None
    request: dict[str, Any] = field(default_factory=dict)  # The full AgentRequest as a serializable dict

    @property
    def session_key(self) -> SessionKey:
        """Return the composite session key for this in-flight turn."""
        return SessionKey(
            transport=self.transport,
            chat_id=self.chat_id,
            topic_id=self.topic_id,
        )

    @property
    def storage_key(self) -> str:
        """Return the storage key used by both JSON and SQLite persistence."""
        return self.session_key.storage_key


def _turn_from_dict(data: dict[str, Any]) -> InflightTurn:
    """Reconstruct an InflightTurn from a JSON dict."""
    request = dict(data.get("request", {}))
    transport = str(data.get("transport") or request.get("transport") or "tg")
    topic_id_raw = data.get("topic_id", request.get("topic_id"))
    topic_id = None if topic_id_raw in (None, "") else int(topic_id_raw)
    return InflightTurn(
        transport=transport,
        chat_id=int(data.get("chat_id", 0)),
        topic_id=topic_id,
        provider=str(data.get("provider", "")),
        model=str(data.get("model", "")),
        session_id=str(data.get("session_id", "")),
        prompt_preview=str(data.get("prompt_preview", "")),
        started_at=str(data.get("started_at", "")),
        is_recovery=bool(data.get("is_recovery", False)),
        path=str(data.get("path", "normal")),
        request=request,
    )


def _storage_key_for(
    key: SessionKey | str | int,
    *,
    topic_id: int | None = None,
    transport: str = "tg",
) -> str:
    """Normalize a legacy or structured key to the canonical storage key."""
    if isinstance(key, SessionKey):
        return key.storage_key
    if isinstance(key, str):
        return key if ":" in key else SessionKey(transport=transport, chat_id=int(key)).storage_key
    return SessionKey(transport=transport, chat_id=key, topic_id=topic_id).storage_key


class InflightTracker:
    """Write/remove inflight state for crash recovery.

    Falls back to the legacy JSON file when no SQLite repository is provided.
    """

    def __init__(
        self,
        path: Path,
        *,
        state_repo: InflightTurnRepository | None = None,
        state_db: RuntimeStateDB | None = None,
    ) -> None:
        self._path = path
        self._state_repo = state_repo
        if self._state_repo is None and state_db is not None:
            self._state_repo = InflightTurnRepository(state_db)

    def begin(self, turn: InflightTurn) -> None:
        """Mark a turn as in-flight (atomic write)."""
        if self._state_repo is not None:
            self._state_repo.upsert(turn)
            return
        data = self._load_raw()
        data[turn.storage_key] = asdict(turn)
        atomic_json_save(self._path, {"turns": data})

    def complete(
        self,
        key: SessionKey | str | int,
        *,
        topic_id: int | None = None,
        transport: str = "tg",
    ) -> None:
        """Remove a completed turn (atomic write)."""
        storage_key = _storage_key_for(key, topic_id=topic_id, transport=transport)
        if self._state_repo is not None:
            self._state_repo.delete(storage_key)
            return
        data = self._load_raw()
        if storage_key not in data:
            return
        del data[storage_key]
        if data:
            atomic_json_save(self._path, {"turns": data})
        else:
            self._path.unlink(missing_ok=True)

    def load_interrupted(self, *, max_age_seconds: float) -> list[InflightTurn]:
        """Load turns that were in-flight at last shutdown.

        Filters:
        - ``is_recovery=True`` entries are never recovered (no infinite loops)
        - Entries older than *max_age_seconds* are dropped
        """
        if self._state_repo is not None:
            data = {str(row["storage_key"]): row for row in self._state_repo.list_all()}
        else:
            data = self._load_raw()
        now = datetime.now(UTC)
        result: list[InflightTurn] = []
        for entry in data.values():
            turn = _turn_from_dict(entry)
            if turn.is_recovery:
                continue
            if turn.chat_id <= 0:
                continue
            try:
                started = datetime.fromisoformat(turn.started_at)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                age = (now - started).total_seconds()
                if age > max_age_seconds:
                    continue
            except (ValueError, TypeError):
                continue
            result.append(turn)
        return result

    def clear(self) -> None:
        """Remove the inflight file entirely."""
        if self._state_repo is not None:
            for row in self._state_repo.list_all():
                self._state_repo.delete(str(row["storage_key"]))
            return
        self._path.unlink(missing_ok=True)

    def _load_raw(self) -> dict[str, Any]:
        """Load the raw turns dict from disk."""
        raw = load_json(self._path)
        if raw is None:
            return {}
        turns = raw.get("turns")
        if not isinstance(turns, dict):
            return {}
        return dict(turns)
