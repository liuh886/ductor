# ruff: noqa: INP001

"""Search session history across available runtime state databases."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from ductor_bot.runtime.state.db import RuntimeStateDB
from ductor_bot.workspace.paths import resolve_paths


def _resolve_root_home(ductor_home: Path) -> Path:
    if ductor_home.parent.name == "agents":
        return ductor_home.parent.parent
    return ductor_home


def _iter_state_dbs(ductor_home: Path) -> list[tuple[str, str, Path]]:
    root_home = _resolve_root_home(ductor_home)
    candidates: list[tuple[str, str, Path]] = []

    root_db = root_home / "state.db"
    if root_db.exists():
        candidates.append(("main", "main", root_db))

    agents_dir = root_home / "agents"
    if agents_dir.exists():
        for agent_dir in sorted(path for path in agents_dir.iterdir() if path.is_dir()):
            state_db = agent_dir / "state.db"
            if state_db.exists():
                candidates.append((agent_dir.name, "subagent", state_db))

    current_db = ductor_home / "state.db"
    if current_db.exists() and current_db not in {path for _, _, path in candidates}:
        label = ductor_home.name if ductor_home.parent.name == "agents" else "main"
        scope = "subagent" if ductor_home.parent.name == "agents" else "main"
        candidates.append((label, scope, current_db))

    return candidates


def _build_fts_query(query: str) -> str:
    tokens = [token.replace('"', '""') for token in query.strip().split() if token.strip()]
    if not tokens:
        raise ValueError("Query must not be empty.")
    return " AND ".join(f'"{token}"' for token in tokens)


def _to_float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: object, default: int = 0) -> int:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def search_session_history(query: str, ductor_home: Path | str | None = None) -> list[dict[str, object]]:
    """Search message history across the root and sub-agent ``state.db`` files."""
    paths = resolve_paths(ductor_home=ductor_home)
    match_query = _build_fts_query(query)
    results: list[dict[str, object]] = []

    for agent_name, state_scope, db_path in _iter_state_dbs(paths.ductor_home):
        db = RuntimeStateDB(db_path)
        try:
            with db.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        m.id,
                        m.session_storage_key AS session_id,
                        m.role,
                        m.content_text AS content,
                        m.thought,
                        m.created_at
                    FROM messages_fts
                    JOIN messages AS m ON m.id = messages_fts.rowid
                    WHERE messages_fts MATCH ?
                    ORDER BY rank, m.created_at DESC, m.id DESC
                    """,
                    (match_query,),
                ).fetchall()
        except sqlite3.OperationalError:
            continue

        for row in rows:
            payload = dict(row)
            payload["agent_name"] = agent_name
            payload["state_scope"] = state_scope
            payload["state_db_path"] = str(db_path)
            results.append(payload)

    results.sort(
        key=lambda row: (
            _to_float(row.get("created_at", 0.0)),
            str(row.get("state_scope", "")),
            str(row.get("agent_name", "")),
            _to_int(row.get("id", 0)),
        ),
        reverse=True,
    )
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing query argument"}))
        sys.exit(1)

    query_str = sys.argv[1]
    ductor_home_arg = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        results = search_session_history(query_str, ductor_home=ductor_home_arg)
        print(json.dumps(results, indent=2, ensure_ascii=False))
    except Exception as exc:  # pragma: no cover - CLI surface
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
