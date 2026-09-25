#!/usr/bin/env python3
# ruff: noqa: INP001
"""Ductor workspace bridge to the installed local Knowledge Router skill."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Forward explicit Router CLI arguments without importing Ductor internals."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    entry = Path.home() / ".codex" / "skills" / "knowledge-router" / "scripts" / "knowledge_router.py"
    if arguments == ["--bridge-check"]:
        print(json.dumps({"bridge": "knowledge-router", "entry": str(entry),
                          "installed": entry.is_file(), "python": sys.executable}))
        return 0 if entry.is_file() else 2
    if not entry.is_file():
        print("Knowledge Router is not installed in ~/.codex/skills/knowledge-router.", file=sys.stderr)
        return 2
    try:
        return subprocess.run(  # noqa: S603
            [sys.executable, str(entry), *arguments],
            cwd=entry.parent, check=False, timeout=120,
        ).returncode
    except subprocess.TimeoutExpired:
        print("Knowledge Router timed out. Inspect an event before retrying its commit.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

