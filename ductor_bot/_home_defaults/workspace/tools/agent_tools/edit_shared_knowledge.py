#!/usr/bin/env python3
"""View or edit the shared operational alert file (SHAREDMEMORY.md).

The AgentSupervisor initializes this file but does not copy it into provider prompts.
Agents read it explicitly for short operational facts, not durable project knowledge.

Usage:
    python3 edit_shared_knowledge.py --show
    python3 edit_shared_knowledge.py --append "New fact or section"
    python3 edit_shared_knowledge.py --set "Complete replacement content"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _shared_path() -> Path:
    """Resolve SHAREDMEMORY.md path.

    Priority:
    1. DUCTOR_SHARED_MEMORY_PATH env var (set by framework)
    2. DUCTOR_HOME / SHAREDMEMORY.md (works for main agent)
    3. Navigate up from sub-agent home: agents/<name>/ -> ../../SHAREDMEMORY.md
    """
    env_path = os.environ.get("DUCTOR_SHARED_MEMORY_PATH")
    if env_path:
        return Path(env_path)

    home = Path(os.environ.get("DUCTOR_HOME", str(Path.home() / ".ductor")))
    direct = home / "SHAREDMEMORY.md"
    if direct.is_file():
        return direct

    # Sub-agent: home is ~/.ductor/agents/<name>/, shared is at ~/.ductor/SHAREDMEMORY.md
    parent_home = home.parent.parent
    parent_path = parent_home / "SHAREDMEMORY.md"
    if parent_path.is_file():
        return parent_path

    # Default to main location (may not exist yet)
    return direct


def main() -> None:
    parser = argparse.ArgumentParser(description="View or edit the shared operational note")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--show", action="store_true", help="Display current shared knowledge")
    group.add_argument("--append", type=str, help="Append text to shared knowledge")
    group.add_argument("--set", type=str, help="Replace entire shared knowledge content")
    args = parser.parse_args()

    path = _shared_path()

    if args.show:
        if not path.is_file():
            print("No shared knowledge file found.")
            print(f"Expected at: {path}")
            return
        print(path.read_text(encoding="utf-8"))
        return

    if args.set is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.set.strip() + "\n", encoding="utf-8")
        print(f"Shared operational note replaced ({len(args.set)} chars).")
        return

    if args.append is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8").rstrip() if path.is_file() else ""
        new_content = f"{existing}\n\n{args.append.strip()}\n" if existing else f"{args.append.strip()}\n"
        path.write_text(new_content, encoding="utf-8")
        print(f"Appended to shared operational note ({len(args.append)} chars).")


if __name__ == "__main__":
    main()
