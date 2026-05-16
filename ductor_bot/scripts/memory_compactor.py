"""Memory compaction script for the Context Pyramid v3.0."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from ductor_bot.runtime.state.db import RuntimeStateDB
from ductor_bot.runtime.state.repositories.memory_fragment_repo import MemoryFragmentRepository
from ductor_bot.workspace.paths import resolve_paths

logger = logging.getLogger(__name__)


def compact_memory(ductor_home: Path | str | None = None) -> None:
    """Read state.db, identify old/redundant fragments, and archive them."""
    paths = resolve_paths(ductor_home=ductor_home)
    db = RuntimeStateDB(paths.state_db_path)
    repo = MemoryFragmentRepository(db)

    # Group fragments by (agent_name, scope, title)
    fragments = repo.list_all()
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for f in fragments:
        key = (str(f.get("agent_name", "")), str(f.get("scope", "")), str(f.get("title", "")))
        groups.setdefault(key, []).append(f)

    to_archive: list[dict[str, object]] = []

    for group in groups.values():
        if len(group) <= 1:
            continue
        # Keep only the one with the highest ID (most recent)
        group.sort(key=lambda x: _safe_int(x.get("id")))
        others = group[:-1]
        to_archive.extend(others)

    if not to_archive:
        print("No redundant fragments to archive.")
        return

    # Archive redundant fragments
    # We'll put the archive in the global ductor_home/archive/history_memory.md
    archive_dir = paths.ductor_home / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / "history_memory.md"

    with archive_path.open("a", encoding="utf-8") as handle:
        for frag in to_archive:
            handle.write(f"## ARCHIVED: {frag.get('title')} (ID: {frag.get('id')})\n")
            handle.write(f"Agent: {frag.get('agent_name')}, Scope: {frag.get('scope')}\n")
            handle.write(f"Source: {frag.get('source_path')} ({frag.get('source_kind')})\n")
            handle.write("\n")
            handle.write(str(frag.get("body", "")))
            handle.write("\n\n---\n\n")

    # Delete from DB
    with db.connect() as conn:
        archive_ids = [_safe_int(frag.get("id")) for frag in to_archive]
        # Use chunks to avoid too many parameters in SQL
        for i in range(0, len(archive_ids), 500):
            chunk = archive_ids[i : i + 500]
            placeholders = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM memory_fragments WHERE id IN ({placeholders})", tuple(chunk))  # noqa: S608

    print(f"Archived {len(to_archive)} fragments to {archive_path}")


def _safe_int(value: object) -> int:
    try:
        return int(cast("Any", value))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    compact_memory()
