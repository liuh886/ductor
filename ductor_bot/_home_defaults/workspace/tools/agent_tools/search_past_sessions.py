#!/usr/bin/env python3
"""Search past messages and memory fragments across available state databases."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from ductor_bot.tools.agent_tools.search_session_history import _iter_state_dbs, _resolve_root_home
from ductor_bot.workspace.paths import resolve_paths


def _search_memory_fragments(
    db_path: Path,
    *,
    agent_name: str,
    state_scope: str,
    query: str,
    limit: int,
) -> list[dict[str, object]]:
    search_pattern = f"%{query}%"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, agent_name, scope, title, body, tags_json, created_at, source_path
            FROM memory_fragments
            WHERE body LIKE ? OR title LIKE ? OR tags_json LIKE ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (search_pattern, search_pattern, search_pattern, limit),
        ).fetchall()

    results: list[dict[str, object]] = []
    for row in rows:
        payload = dict(row)
        payload["result_type"] = "memory_fragment"
        payload["agent_name"] = payload.get("agent_name") or agent_name
        payload["state_scope"] = state_scope
        payload["state_db_path"] = str(db_path)
        results.append(payload)
    return results


def search_past_sessions(query: str, ductor_home: Path | str | None = None, limit: int = 50) -> list[dict[str, object]]:
    """Search available root and sub-agent ``state.db`` files for messages and fragments."""
    paths = resolve_paths(ductor_home=ductor_home)
    root_home = _resolve_root_home(paths.ductor_home)

    from ductor_bot.tools.agent_tools.search_session_history import search_session_history

    message_results = search_session_history(query, ductor_home=root_home)
    for row in message_results:
        row["result_type"] = "message"

    fragment_results: list[dict[str, object]] = []
    for agent_name, state_scope, db_path in _iter_state_dbs(root_home):
        try:
            fragment_results.extend(
                _search_memory_fragments(
                    db_path,
                    agent_name=agent_name,
                    state_scope=state_scope,
                    query=query,
                    limit=limit,
                )
            )
        except sqlite3.Error:
            continue

    combined = message_results + fragment_results
    combined.sort(
        key=lambda row: (
            _sort_timestamp(row.get("created_at")),
            str(row.get("result_type", "")),
            str(row.get("agent_name", "")),
            int(row.get("id", 0)) if str(row.get("id", "")).isdigit() else 0,
        ),
        reverse=True,
    )
    return combined[:limit]


def _format_timestamp(value: object) -> str:
    parsed = _sort_timestamp(value)
    if parsed <= 0:
        return "unknown"
    return datetime.fromtimestamp(parsed).strftime("%Y-%m-%d %H:%M:%S")


def _sort_timestamp(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _print_result(row: dict[str, object]) -> None:
    if row.get("result_type") == "message":
        print(
            f"### Message #{row.get('id')} "
            f"(Agent: {row.get('agent_name')}, Scope: {row.get('state_scope')}, "
            f"Session: {row.get('session_id')}, Role: {row.get('role')})"
        )
        print(f"**Created At**: {_format_timestamp(row.get('created_at'))}")
        content = str(row.get("content", "")).strip()
        thought = str(row.get("thought", "")).strip()
        if content:
            print(f"\n{content}")
        if thought:
            print(f"\n[thought]\n{thought}")
        print("\n" + "-" * 40 + "\n")
        return

    print(
        f"### Fragment {row.get('id')} "
        f"(Agent: {row.get('agent_name')}, Memory Scope: {row.get('scope')}, "
        f"DB Scope: {row.get('state_scope')})"
    )
    if row.get("title"):
        print(f"**Title**: {row.get('title')}")
    print(f"**Created At**: {_format_timestamp(row.get('created_at'))}")
    tags = row.get("tags_json")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = []
    if isinstance(tags, list) and tags:
        print(f"**Tags**: {', '.join(str(tag) for tag in tags)}")
    if row.get("source_path"):
        print(f"**Source**: {row.get('source_path')}")
    print(f"\n{row.get('body', '')}")
    print("\n" + "-" * 40 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Search past messages and memory fragments across the available root and sub-agent "
            "state.db files."
        )
    )
    parser.add_argument("query", help="Keyword to search for.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum number of combined results.")
    args = parser.parse_args()

    try:
        results = search_past_sessions(args.query, limit=args.limit)
    except (sqlite3.Error, ValueError) as exc:
        print(f"Search error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not results:
        print(f"No results found for query: '{args.query}' across the available state.db files.")
        return

    print(f"Found {len(results)} results for '{args.query}' across the available state.db files:\n")
    for row in results:
        _print_result(row)


if __name__ == "__main__":
    main()
