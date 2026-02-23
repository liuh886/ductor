"""Shared log rendering helpers for service backends."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console


def print_recent_logs(
    console: Console,
    logs_dir: Path,
    *,
    preferred_name: str = "agent.log",
    line_count: int = 50,
) -> None:
    """Print the last lines from a preferred or newest log file."""
    preferred_log = logs_dir / preferred_name
    if preferred_log.exists():
        latest_log = preferred_log
    else:
        log_files = sorted(
            logs_dir.glob("*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not log_files:
            console.print("[dim]No log files found.[/dim]")
            return
        latest_log = log_files[0]

    console.print(f"[dim]Showing last {line_count} lines from {latest_log.name}[/dim]\n")

    try:
        lines = latest_log.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-line_count:]:
            console.print(line)
    except OSError as exc:
        console.print(f"[red]Could not read log file: {exc}[/red]")
        return

    console.print(f"\n[dim]Full log: {latest_log}[/dim]")
