# ruff: noqa: INP001

"""Memory integrity check for the fragment-backed memory files."""

from __future__ import annotations

from pathlib import Path

from ductor_bot.runtime.state import MemoryFragmentRepository, RuntimeStateDB
from ductor_bot.workspace.paths import resolve_paths


def _repo_for(db_path: Path) -> MemoryFragmentRepository | None:
    if not db_path.exists():
        return None
    return MemoryFragmentRepository(RuntimeStateDB(db_path))


def _count_fragments(repo: MemoryFragmentRepository | None, scope: str, *, agent_name: str = "") -> int:
    if repo is None:
        return 0
    return len(repo.list_by_scope(scope, agent_name=agent_name))


def _count_conflicts(repo: MemoryFragmentRepository | None, scope: str, *, agent_name: str = "") -> int:
    if repo is None:
        return 0
    return len(repo.list_conflicts(scope, agent_name=agent_name))


def _format_file_status(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    return f"EXISTS ({path.stat().st_size} bytes)"


def check_integrity(ductor_home: Path | str | None = None) -> None:
    """Print fragment/file alignment for root and sub-agent memory stores."""
    paths = resolve_paths(ductor_home=ductor_home)
    root_home = paths.ductor_home.parent.parent if paths.ductor_home.parent.name == "agents" else paths.ductor_home

    root_repo = _repo_for(root_home / "state.db")

    print(f"{'Agent/Scope':<30} | {'Fragments':<10} | {'Conflicts':<10} | {'File Status':<20}")
    print("-" * 80)

    shared_file = root_home / "SHAREDMEMORY.md"
    shared_count = _count_fragments(root_repo, "sharedmemory")
    shared_conflicts = _count_conflicts(root_repo, "sharedmemory")
    print(
        f"{'GLOBAL/sharedmemory':<30} | {shared_count:<10} | {shared_conflicts:<10} | {_format_file_status(shared_file):<20}"
    )

    mainmemory_file = root_home / "workspace" / "memory_system" / "MAINMEMORY.md"
    main_count = _count_fragments(root_repo, "mainmemory", agent_name="main")
    main_conflicts = _count_conflicts(root_repo, "mainmemory", agent_name="main")
    print(
        f"{'main/mainmemory':<30} | {main_count:<10} | {main_conflicts:<10} | {_format_file_status(mainmemory_file):<20}"
    )

    agents_dir = root_home / "agents"
    if not agents_dir.exists():
        return

    for agent_dir in sorted(path for path in agents_dir.iterdir() if path.is_dir()):
        repo = _repo_for(agent_dir / "state.db")
        count = _count_fragments(repo, "mainmemory", agent_name=agent_dir.name)
        conflicts = _count_conflicts(repo, "mainmemory", agent_name=agent_dir.name)
        main_file = agent_dir / "workspace" / "memory_system" / "MAINMEMORY.md"
        print(
            f"{agent_dir.name + '/mainmemory':<30} | {count:<10} | {conflicts:<10} | "
            f"{_format_file_status(main_file):<20}"
        )


if __name__ == "__main__":
    check_integrity()
