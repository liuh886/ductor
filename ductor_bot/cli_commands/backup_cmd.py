"""``ductor backup``: zip key state files and rotate old archives."""

from __future__ import annotations

import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ductor_bot.workspace.paths import DuctorPaths, resolve_paths

logger = logging.getLogger(__name__)
_console = Console()

BACKUP_DIR_NAME = "backups"
BACKUP_PREFIX = "ductor-backup-"
DEFAULT_KEEP = 7

# Curated state: user config, secrets, session/task registries, memory notes,
# and per-agent configs. Rebuildable indexes (vault_index.db) are excluded.
_FILE_TARGETS: tuple[str, ...] = (
    "config/config.json",
    ".env",
    "SHAREDMEMORY.md",
    "sessions.json",
    "named_sessions.json",
    "tasks.json",
    "cron_jobs.json",
    "webhooks.json",
    "agents.json",
    "startup_state.json",
    "chat_activity.json",
)
_GLOB_TARGETS: tuple[str, ...] = (
    "workspace/memory_system/*.md",
    "agents/*/config/config.json",
    "agents/*/workspace/memory_system/*.md",
)


def _collect_files(ductor_home: Path) -> list[Path]:
    """Return existing state files worth backing up, sorted and deduplicated."""
    files: list[Path] = []
    seen: set[Path] = set()
    for relative in _FILE_TARGETS:
        candidate = ductor_home / relative
        if candidate.is_file() and candidate not in seen:
            seen.add(candidate)
            files.append(candidate)
    for pattern in _GLOB_TARGETS:
        for candidate in sorted(ductor_home.glob(pattern)):
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                files.append(candidate)
    return files


def list_backups(ductor_home: Path) -> list[Path]:
    """Existing backup archives, newest first."""
    return sorted(
        (ductor_home / BACKUP_DIR_NAME).glob(f"{BACKUP_PREFIX}*.zip"),
        key=lambda path: path.name,
        reverse=True,
    )


def create_backup(
    ductor_home: Path,
    *,
    keep: int = DEFAULT_KEEP,
    now: datetime | None = None,
) -> Path | None:
    """Zip key state into ``backups/`` and rotate to *keep* newest archives.

    Returns the archive path, or ``None`` when no state files were found.
    """
    files = _collect_files(ductor_home)
    if not files:
        return None

    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    backup_dir = ductor_home / BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    archive = backup_dir / f"{BACKUP_PREFIX}{stamp}.zip"

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for file in files:
            bundle.write(file, arcname=file.relative_to(ductor_home).as_posix())

    if keep > 0:
        for stale in list_backups(ductor_home)[keep:]:
            try:
                stale.unlink()
                logger.info("Rotated out old backup %s", stale.name)
            except OSError:
                logger.warning("Could not remove old backup %s", stale.name, exc_info=True)
    logger.info("Backup written: %s (%d file(s))", archive, len(files))
    return archive


def _parse_keep(args: list[str]) -> int:
    if "--keep" not in args:
        return DEFAULT_KEEP
    index = args.index("--keep")
    if index + 1 >= len(args):
        return DEFAULT_KEEP
    try:
        value = int(args[index + 1])
    except ValueError:
        return DEFAULT_KEEP
    return max(0, value)


def print_backup_help() -> None:
    """Print the backup subcommand help table."""
    _console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold green", min_width=30)
    table.add_column()
    table.add_row("ductor backup", f"Back up state (keeps {DEFAULT_KEEP} archives)")
    table.add_row("ductor backup --keep <n>", "Override the number of archives to keep")
    table.add_row("ductor backup --list", "List existing backups (newest first)")
    _console.print(
        Panel(table, title="[bold]Backup Commands[/bold]", border_style="blue", padding=(1, 0)),
    )
    _console.print()


def _print_existing(ductor_home: Path) -> None:
    backups = list_backups(ductor_home)
    if not backups:
        _console.print("[dim]No backups yet.[/dim]")
        return
    for archive in backups:
        size_mb = archive.stat().st_size / (1024 * 1024)
        _console.print(f"  {archive.name}  [dim]{size_mb:.2f} MB[/dim]")


def cmd_backup(args: list[str]) -> None:
    """Handle ``ductor backup [--keep N] [--list]``."""
    commands = [a for a in args if not a.startswith("-")]
    if len(commands) > 1:
        print_backup_help()
        return

    paths: DuctorPaths = resolve_paths()
    if "--list" in args:
        _print_existing(paths.ductor_home)
        return

    keep = _parse_keep(args)
    archive = create_backup(paths.ductor_home, keep=keep)
    if archive is None:
        _console.print("[yellow]Nothing to back up (no state files found).[/yellow]")
        return
    size_mb = archive.stat().st_size / (1024 * 1024)
    _console.print(f"[green]Backup written:[/green] {archive} [dim]({size_mb:.2f} MB)[/dim]")
