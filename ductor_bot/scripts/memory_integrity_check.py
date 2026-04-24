# ruff: noqa: INP001

"""Memory integrity check for the fragment-backed memory files."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ductor_bot.workspace.paths import resolve_paths


def _count_fragments(db_path: Path) -> dict[tuple[str, str], int]:
    if not db_path.exists():
        return {}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT agent_name, scope, COUNT(*) AS count
            FROM memory_fragments
            GROUP BY agent_name, scope
            """
        ).fetchall()
    return {(str(row["agent_name"]), str(row["scope"])): int(row["count"]) for row in rows}


def _format_file_status(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    return f"EXISTS ({path.stat().st_size} bytes)"


def check_integrity(ductor_home: Path | str | None = None) -> None:
    """Print fragment/file alignment for root and sub-agent memory stores."""
    paths = resolve_paths(ductor_home=ductor_home)
    root_home = paths.ductor_home.parent.parent if paths.ductor_home.parent.name == "agents" else paths.ductor_home

    root_counts = _count_fragments(root_home / "state.db")

    print(f"{'Agent/Scope':<30} | {'Fragments':<10} | {'File Status':<20}")
    print("-" * 65)

    shared_file = root_home / "SHAREDMEMORY.md"
    shared_count = root_counts.get(("", "sharedmemory"), 0)
    print(
        f"{'GLOBAL/sharedmemory':<30} | {shared_count:<10} | {_format_file_status(shared_file):<20}"
    )

    mainmemory_file = root_home / "workspace" / "memory_system" / "MAINMEMORY.md"
    main_count = root_counts.get(("", "mainmemory"), 0) + root_counts.get(("main", "mainmemory"), 0)
    print(f"{'main/mainmemory':<30} | {main_count:<10} | {_format_file_status(mainmemory_file):<20}")

    agents_dir = root_home / "agents"
    if not agents_dir.exists():
        return

    for agent_dir in sorted(path for path in agents_dir.iterdir() if path.is_dir()):
        counts = _count_fragments(agent_dir / "state.db")
        count = counts.get((agent_dir.name, "mainmemory"), 0) + counts.get(("", "mainmemory"), 0)
        main_file = agent_dir / "workspace" / "memory_system" / "MAINMEMORY.md"
        print(
            f"{agent_dir.name + '/mainmemory':<30} | {count:<10} | "
            f"{_format_file_status(main_file):<20}"
        )


if __name__ == "__main__":
    check_integrity()
