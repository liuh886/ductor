# ruff: noqa: INP001

"""Atomic memory manipulation tools: patch and delete fragments."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ductor_bot.runtime.memory.sync import reverse_sync_source
from ductor_bot.runtime.state.db import RuntimeStateDB
from ductor_bot.runtime.state.repositories.memory_fragment_repo import MemoryFragmentRepository
from ductor_bot.workspace.paths import resolve_paths


def _resolve_root_home(ductor_home: Path) -> Path:
    if ductor_home.parent.name == "agents":
        return ductor_home.parent.parent
    return ductor_home


def _resolve_agent_home(root_home: Path, agent_name: str) -> Path:
    return root_home if agent_name == "main" else root_home / "agents" / agent_name


def _resolve_source_path(source_path: str, *, owner_home: Path, root_home: Path) -> Path:
    if source_path.startswith("@ductor/workspace/"):
        return owner_home / "workspace" / source_path.removeprefix("@ductor/workspace/")
    if source_path.startswith("@ductor/"):
        return root_home / source_path.removeprefix("@ductor/")

    resolved = Path(source_path)
    if resolved.is_absolute():
        return resolved
    return owner_home / resolved


def _find_fragment(
    ulid: str,
    *,
    repos: list[tuple[MemoryFragmentRepository, Path]],
) -> tuple[dict[str, object] | None, MemoryFragmentRepository | None, Path | None]:
    for repo, owner_home in repos:
        fragment = repo.get_by_ulid(ulid)
        if fragment is not None:
            return fragment, repo, owner_home
    return None, None, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Patch or delete a memory fragment by ULID.")
    parser.add_argument("--ulid", required=True, help="The unique identifier of the fragment.")
    parser.add_argument("--action", choices=["patch", "delete"], required=True, help="Action to perform.")
    parser.add_argument("--body", help="New body content for patch action.")
    parser.add_argument("--agent", default="main", help="Agent name context.")
    args = parser.parse_args()

    paths = resolve_paths()
    root_home = _resolve_root_home(paths.ductor_home)
    target_home = _resolve_agent_home(root_home, args.agent)

    repo_candidates: list[tuple[MemoryFragmentRepository, Path]] = []
    target_db_path = target_home / "state.db"
    if target_db_path.exists():
        repo_candidates.append((MemoryFragmentRepository(RuntimeStateDB(target_db_path)), target_home))

    shared_db_path = root_home / "state.db"
    if shared_db_path.exists() and shared_db_path != target_db_path:
        repo_candidates.append((MemoryFragmentRepository(RuntimeStateDB(shared_db_path)), root_home))

    if not repo_candidates:
        print(f"Error: No state.db found for agent '{args.agent}' or the main workspace.")
        sys.exit(1)

    fragment, active_repo, owner_home = _find_fragment(args.ulid, repos=repo_candidates)
    if fragment is None or active_repo is None or owner_home is None:
        print(f"Error: Fragment with ULID '{args.ulid}' not found.")
        sys.exit(1)

    if args.action == "patch" and not args.body:
        print("Error: --body is required for patch action.")
        sys.exit(1)

    success = (
        active_repo.update_by_ulid(args.ulid, str(args.body))
        if args.action == "patch"
        else active_repo.delete_by_ulid(args.ulid)
    )
    print(f"{args.action.title()} success: {success}")

    if not success:
        sys.exit(1)

    source_path = str(fragment.get("source_path", "")).strip()
    if not source_path:
        return

    real_path = _resolve_source_path(source_path, owner_home=owner_home, root_home=root_home)
    all_fragments = active_repo.list_by_source_path(source_path)
    if reverse_sync_source(real_path, all_fragments):
        print(f"Reverse sync completed for {real_path}")
    else:
        print(f"Warning: Reverse sync skipped or failed for {real_path}")


if __name__ == "__main__":
    main()
