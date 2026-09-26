"""Tests for ``ductor config prune``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ductor_bot.cli_commands import config_cmd
from ductor_bot.workspace.paths import DuctorPaths

if TYPE_CHECKING:
    import pytest


def _paths(tmp_path: Path) -> DuctorPaths:
    fw = tmp_path / "fw"
    return DuctorPaths(
        ductor_home=tmp_path / "home", home_defaults=fw / "workspace", framework_root=fw
    )


def _write(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_unknown_config_keys_ignores_comments_and_known_keys() -> None:
    data: dict[str, object] = {
        "telegram_token": "x",
        "_comment": "note",
        "capability_preselector": {},
    }
    assert config_cmd.unknown_config_keys(data) == ["capability_preselector"]


def test_prune_removes_keys_and_writes_backup(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    _write(cfg, {"telegram_token": "x", "runtime_context": {}, "style_policy": {}})

    removed = config_cmd.prune_config_file(cfg)

    assert removed == ["runtime_context", "style_policy"]
    assert _read(cfg) == {"telegram_token": "x"}
    backups = list(tmp_path.glob("config.json.pruned-*.bak"))
    assert len(backups) == 1
    assert _read(backups[0]) == {
        "telegram_token": "x",
        "runtime_context": {},
        "style_policy": {},
    }


def test_prune_dry_run_leaves_file_untouched(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    _write(cfg, {"telegram_token": "x", "runtime_context": {}})

    removed = config_cmd.prune_config_file(cfg, dry_run=True)

    assert removed == ["runtime_context"]
    assert _read(cfg) == {"telegram_token": "x", "runtime_context": {}}
    assert not list(tmp_path.glob("*.bak"))


def test_cmd_config_prune_all_targets_main_and_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(config_cmd, "resolve_paths", lambda: paths)
    _write(paths.config_path, {"telegram_token": "x", "style_policy": {}})
    agent_cfg = paths.ductor_home / "agents" / "bot1" / "config" / "config.json"
    _write(agent_cfg, {"model": "m", "role": "old"})

    config_cmd.cmd_config(["config", "prune", "--all"])

    assert _read(paths.config_path) == {"telegram_token": "x"}
    assert _read(agent_cfg) == {"model": "m"}
