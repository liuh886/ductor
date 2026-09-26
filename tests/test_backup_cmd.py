"""Tests for ``ductor backup``."""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from ductor_bot.cli_commands import backup_cmd
from ductor_bot.workspace.paths import DuctorPaths

if TYPE_CHECKING:
    import pytest


def _seed_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "config.json").write_text('{"provider": "antigravity"}', encoding="utf-8")
    (home / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (home / "sessions.json").write_text("{}", encoding="utf-8")
    memory = home / "workspace" / "memory_system"
    memory.mkdir(parents=True)
    (memory / "MAINMEMORY.md").write_text("# Memory", encoding="utf-8")
    (memory / "vault_index.db").write_bytes(b"sqlite-bytes")
    agent_config = home / "agents" / "bot1" / "config"
    agent_config.mkdir(parents=True)
    (agent_config / "config.json").write_text("{}", encoding="utf-8")
    return home


def test_create_backup_zips_state_and_excludes_index(tmp_path: Path) -> None:
    home = _seed_home(tmp_path)

    archive = backup_cmd.create_backup(home)

    assert archive is not None
    assert archive.is_file()
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert "config/config.json" in names
    assert ".env" in names
    assert "sessions.json" in names
    assert "workspace/memory_system/MAINMEMORY.md" in names
    assert "agents/bot1/config/config.json" in names
    assert not any("vault_index.db" in name for name in names)


def test_create_backup_returns_none_without_state(tmp_path: Path) -> None:
    assert backup_cmd.create_backup(tmp_path / "empty") is None


def test_rotation_keeps_newest_archives(tmp_path: Path) -> None:
    home = _seed_home(tmp_path)
    base = datetime(2026, 1, 1, tzinfo=UTC)

    for offset in range(3):
        backup_cmd.create_backup(home, keep=2, now=base + timedelta(minutes=offset))

    remaining = backup_cmd.list_backups(home)
    assert len(remaining) == 2
    assert "20260101-000200" in remaining[0].name
    assert "20260101-000100" in remaining[1].name


def test_list_backups_newest_first(tmp_path: Path) -> None:
    home = _seed_home(tmp_path)
    backup_cmd.create_backup(home, now=datetime(2026, 1, 1, tzinfo=UTC))
    backup_cmd.create_backup(home, now=datetime(2026, 1, 2, tzinfo=UTC))

    names = [path.name for path in backup_cmd.list_backups(home)]
    assert len(names) == 2
    assert names[0] > names[1]


def test_cmd_backup_writes_into_resolved_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _seed_home(tmp_path)
    fw = tmp_path / "fw"
    paths = DuctorPaths(ductor_home=home, home_defaults=fw / "workspace", framework_root=fw)
    monkeypatch.setattr(backup_cmd, "resolve_paths", lambda: paths)

    backup_cmd.cmd_backup(["backup"])

    assert len(backup_cmd.list_backups(home)) == 1


def test_parse_keep_defaults_and_overrides() -> None:
    assert backup_cmd._parse_keep(["backup"]) == backup_cmd.DEFAULT_KEEP
    assert backup_cmd._parse_keep(["backup", "--keep", "3"]) == 3
    assert backup_cmd._parse_keep(["backup", "--keep", "nope"]) == backup_cmd.DEFAULT_KEEP
    assert backup_cmd._parse_keep(["backup", "--keep"]) == backup_cmd.DEFAULT_KEEP
