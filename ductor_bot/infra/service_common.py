"""Shared helpers for platform-specific service backends."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console


def collect_nvm_bin_dirs(home: Path) -> list[str]:
    """Return bin directories for all NVM-managed Node.js versions."""
    nvm_dir = home / ".nvm"
    if not nvm_dir.is_dir():
        return []
    return [str(node_dir) for node_dir in sorted(nvm_dir.glob("versions/node/*/bin"), reverse=True)]


def ensure_console(console: Console | None) -> Console:
    """Return an initialized Rich console instance."""
    if console is not None:
        return console

    from rich.console import Console as RichConsole

    return RichConsole()
