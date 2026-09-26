"""Tests for the periodic vault index rebuild observer."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from ductor_bot.config import VaultIndexConfig
from ductor_bot.workspace.paths import DuctorPaths
from ductor_bot.workspace.vault_index_observer import (
    VaultIndexObserver,
    is_root_home,
    resolve_vault_root,
    root_vault_index_path,
)

if TYPE_CHECKING:
    import pytest


def _paths(tmp_path: Path, *, agent: str | None = None) -> DuctorPaths:
    fw = tmp_path / "fw"
    home = tmp_path / "home"
    if agent:
        home = home / "agents" / agent
    return DuctorPaths(ductor_home=home, home_defaults=fw / "workspace", framework_root=fw)


def _write_registry(paths: DuctorPaths, entries: list[dict[str, str]]) -> None:
    registry = paths.ductor_home / "vaults" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({"vaults": entries}), encoding="utf-8")


def _write_script(paths: DuctorPaths) -> None:
    script = paths.framework_root / "scripts" / "vault_index_sync.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# stub", encoding="utf-8")


class _FakeProc:
    pid = 4242

    def __init__(self, returncode: int = 0, stdout: bytes = b"{}", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


# -- resolution ---------------------------------------------------------------


def test_is_root_home(tmp_path: Path) -> None:
    assert is_root_home(_paths(tmp_path)) is True
    assert is_root_home(_paths(tmp_path, agent="bot1")) is False


def test_root_vault_index_path_is_shared(tmp_path: Path) -> None:
    root = _paths(tmp_path)
    sub = _paths(tmp_path, agent="bot1")
    assert root_vault_index_path(root) == root_vault_index_path(sub)
    assert root_vault_index_path(root).name == "vault_index.db"


def test_resolve_vault_root_explicit(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = VaultIndexConfig(vault_root=str(vault))
    assert resolve_vault_root(_paths(tmp_path), cfg) == vault


def test_resolve_vault_root_explicit_missing(tmp_path: Path) -> None:
    cfg = VaultIndexConfig(vault_root=str(tmp_path / "nope"))
    assert resolve_vault_root(_paths(tmp_path), cfg) is None


def test_resolve_vault_root_from_registry(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    paths = _paths(tmp_path)
    _write_registry(paths, [{"name": "zhihaol", "root": str(vault)}])

    assert resolve_vault_root(paths, VaultIndexConfig(), vault_name="zhihaol") == vault
    assert resolve_vault_root(paths, VaultIndexConfig(), vault_name="other") is None


def test_resolve_vault_root_registry_missing(tmp_path: Path) -> None:
    assert resolve_vault_root(_paths(tmp_path), VaultIndexConfig()) is None


# -- sync ---------------------------------------------------------------------


async def test_sync_now_runs_script_with_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    paths = _paths(tmp_path)
    _write_script(paths)
    observer = VaultIndexObserver(paths, VaultIndexConfig(vault_root=str(vault)))

    calls: list[tuple[str, ...]] = []

    async def fake_exec(*args: str, **_kwargs: object) -> _FakeProc:
        calls.append(args)
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    assert await observer.sync_now() is True
    assert len(calls) == 1
    assert "--apply" in calls[0]
    assert str(vault) in calls[0]
    assert str(observer.index_path) in calls[0]


async def test_sync_now_fails_without_script(tmp_path: Path) -> None:
    observer = VaultIndexObserver(_paths(tmp_path), VaultIndexConfig(vault_root=str(tmp_path)))
    assert await observer.sync_now() is False


async def test_sync_now_fails_without_vault_root(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_script(paths)
    observer = VaultIndexObserver(paths, VaultIndexConfig())
    assert await observer.sync_now() is False


async def test_sync_now_logs_subprocess_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    paths = _paths(tmp_path)
    _write_script(paths)
    observer = VaultIndexObserver(paths, VaultIndexConfig(vault_root=str(vault)))

    async def fake_exec(*_args: str, **_kwargs: object) -> _FakeProc:
        return _FakeProc(returncode=1, stderr=b"index verification failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with caplog.at_level("WARNING", logger="ductor_bot.workspace.vault_index_observer"):
        assert await observer.sync_now() is False
    assert "index verification failed" in caplog.text


async def test_maybe_sync_skips_fresh_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    index = root_vault_index_path(paths)
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("fresh", encoding="utf-8")
    observer = VaultIndexObserver(paths, VaultIndexConfig(sync_interval_hours=168))

    called = False

    async def fake_sync() -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(observer, "sync_now", fake_sync)

    assert await observer._maybe_sync() is False
    assert called is False


async def test_maybe_sync_runs_when_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _paths(tmp_path)
    index = root_vault_index_path(paths)
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("stale", encoding="utf-8")
    old = time.time() - 200 * 3600
    import os

    os.utime(index, (old, old))
    observer = VaultIndexObserver(paths, VaultIndexConfig(sync_interval_hours=168))

    called = False

    async def fake_sync() -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(observer, "sync_now", fake_sync)

    assert await observer._maybe_sync() is True
    assert called is True


async def test_maybe_sync_runs_when_index_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    observer = VaultIndexObserver(paths, VaultIndexConfig())

    async def fake_sync() -> bool:
        return True

    monkeypatch.setattr(observer, "sync_now", fake_sync)
    assert await observer._maybe_sync() is True


async def test_start_stop_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _paths(tmp_path)
    observer = VaultIndexObserver(paths, VaultIndexConfig())
    monkeypatch.setattr(observer, "_maybe_sync", AsyncMock(return_value=False))

    await observer.start()
    assert observer.running is True
    await observer.stop()
    assert observer.running is False
