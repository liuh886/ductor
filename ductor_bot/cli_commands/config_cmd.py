"""``ductor config`` subcommands: prune config keys this build does not consume."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ductor_bot.config import AgentConfig
from ductor_bot.infra.json_store import atomic_json_save
from ductor_bot.workspace.paths import resolve_paths

_console = Console()


def unknown_config_keys(data: dict[str, object]) -> list[str]:
    """Return top-level keys ``AgentConfig`` does not consume.

    Keys starting with ``_`` are treated as comments and never reported.
    """
    known = set(AgentConfig.model_fields)
    return sorted(key for key in data if not key.startswith("_") and key not in known)


def prune_config_file(path: Path, *, dry_run: bool = False) -> list[str]:
    """Drop unknown top-level keys from one config file.

    Writes a timestamped ``.bak`` copy next to the config before modifying it.
    Returns the keys that were (or would be) removed.
    """
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _console.print(f"[yellow]Skipping unreadable config: {path}[/yellow]")
        return []
    if not isinstance(data, dict):
        return []

    unknown = unknown_config_keys(data)
    if not unknown or dry_run:
        return unknown

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.pruned-{timestamp}.bak")
    shutil.copy2(path, backup)
    for key in unknown:
        data.pop(key, None)
    atomic_json_save(path, data)
    _console.print(f"[dim]Backup written to {backup}[/dim]")
    return unknown


def _target_paths(args: list[str]) -> list[Path]:
    """Resolve which config files ``prune`` should touch."""
    paths = resolve_paths()
    agent_name: str | None = None
    if "--agent" in args:
        index = args.index("--agent")
        if index + 1 < len(args):
            agent_name = args[index + 1]

    if agent_name:
        return [paths.ductor_home / "agents" / agent_name / "config" / "config.json"]
    if "--all" in args:
        targets = [paths.config_path]
        targets.extend(sorted((paths.ductor_home / "agents").glob("*/config/config.json")))
        return targets
    return [paths.config_path]


def print_config_help() -> None:
    """Print the config subcommand help table."""
    _console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold green", min_width=36)
    table.add_column()
    table.add_row("ductor config prune", "Remove config keys this build ignores (main agent)")
    table.add_row("ductor config prune --all", "Prune the main config and every sub-agent")
    table.add_row("ductor config prune --agent <name>", "Prune one sub-agent's config")
    table.add_row("ductor config prune --dry-run", "Only list the keys that would be removed")
    _console.print(
        Panel(table, title="[bold]Config Commands[/bold]", border_style="blue", padding=(1, 0)),
    )
    _console.print()


def cmd_config(args: list[str]) -> None:
    """Handle ``ductor config <subcommand>``."""
    commands = [a for a in args if not a.startswith("-")]
    subcommand = commands[1] if len(commands) > 1 else ""
    if subcommand != "prune":
        print_config_help()
        return

    dry_run = "--dry-run" in args
    total = 0
    for path in _target_paths(args):
        if not path.is_file():
            _console.print(f"[dim]No config at {path}[/dim]")
            continue
        removed = prune_config_file(path, dry_run=dry_run)
        total += len(removed)
        if not removed:
            _console.print(f"[green]Nothing to prune[/green] in {path}")
            continue
        verb = "would remove" if dry_run else "removed"
        _console.print(f"[green]{verb} {len(removed)} key(s)[/green] from {path}:")
        for key in removed:
            _console.print(f"  - {key}")

    if dry_run and total:
        _console.print("[yellow]Dry run: no files were modified.[/yellow]")
