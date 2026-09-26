"""Periodic rebuild of the read-only vault index used by ``vault_search.py``.

The index at ``<root home>/workspace/memory_system/vault_index.db`` is a
rebuildable projection of the Markdown vault (see ``scripts/vault_index_sync.py``
in the framework root).  Without a scheduler it silently goes stale, so this
observer rebuilds it when it is older than ``vault_index.sync_interval_hours``.
Only the root agent runs the rebuild; sub-agent homes share the root index.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ductor_bot.infra.base_observer import BaseObserver
from ductor_bot.infra.platform import CREATION_FLAGS
from ductor_bot.infra.process_tree import force_kill_process_tree

if TYPE_CHECKING:
    from ductor_bot.config import VaultIndexConfig
    from ductor_bot.workspace.paths import DuctorPaths

logger = logging.getLogger(__name__)

_SYNC_TIMEOUT_S = 1800.0
_SCRIPT_RELATIVE_PATH = Path("scripts") / "vault_index_sync.py"


def is_root_home(paths: DuctorPaths) -> bool:
    """True when *paths* belongs to the root agent (not a sub-agent home)."""
    return paths.ductor_home.parent.name != "agents"


def root_vault_index_path(paths: DuctorPaths) -> Path:
    """Index path as seen by ``vault_search.py`` (shared across sub-agents)."""
    root_home = (
        paths.ductor_home.parent.parent
        if paths.ductor_home.parent.name == "agents"
        else paths.ductor_home
    )
    return root_home / "workspace" / "memory_system" / "vault_index.db"


def resolve_vault_root(
    paths: DuctorPaths,
    config: VaultIndexConfig,
    *,
    vault_name: str = "",
) -> Path | None:
    """Resolve the vault directory to index.

    Order: explicit ``vault_index.vault_root``, then the knowledge-router
    registry entry matching *vault_name* (or the first attached vault).
    Returns ``None`` when nothing usable is found.
    """
    if config.vault_root.strip():
        root = Path(config.vault_root).expanduser()
        return root if root.is_dir() else None

    registry = paths.ductor_home / "vaults" / "registry.json"
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    vaults = data.get("vaults")
    if not isinstance(vaults, list):
        return None
    candidates = [entry for entry in vaults if isinstance(entry, dict)]
    if vault_name:
        candidates = [entry for entry in candidates if entry.get("name") == vault_name]
    for entry in candidates:
        raw = entry.get("root")
        if isinstance(raw, str) and raw:
            root = Path(raw)
            if root.is_dir():
                return root
    return None


class VaultIndexObserver(BaseObserver):
    """Rebuild the vault index at startup when stale, then every interval."""

    def __init__(
        self,
        paths: DuctorPaths,
        config: VaultIndexConfig,
        *,
        vault_name: str = "",
    ) -> None:
        super().__init__()
        self._paths = paths
        self._config = config
        self._vault_name = vault_name
        self._index_path = root_vault_index_path(paths)

    @property
    def index_path(self) -> Path:
        return self._index_path

    def _index_age_hours(self) -> float | None:
        try:
            stat = self._index_path.stat()
        except OSError:
            return None
        return (time.time() - stat.st_mtime) / 3600.0

    async def _maybe_sync(self) -> bool:
        """Sync when the index is missing or older than the configured interval."""
        age = self._index_age_hours()
        if age is not None and age < self._config.sync_interval_hours:
            logger.debug(
                "Vault index is fresh (%.1fh old, interval %dh)",
                age,
                self._config.sync_interval_hours,
            )
            return False
        return await self.sync_now()

    async def sync_now(self) -> bool:
        """Run ``scripts/vault_index_sync.py --apply`` once. Returns success."""
        script = self._paths.framework_root / _SCRIPT_RELATIVE_PATH
        if not script.is_file():
            logger.warning("Vault index sync script not found: %s", script)
            return False
        vault_root = resolve_vault_root(self._paths, self._config, vault_name=self._vault_name)
        if vault_root is None:
            logger.warning(
                "Vault index sync skipped: no vault root (set vault_index.vault_root or attach a vault)"
            )
            return False

        logger.info("Rebuilding vault index from %s", vault_root)
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script),
            "--vault",
            str(vault_root),
            "--index",
            str(self._index_path),
            "--apply",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=CREATION_FLAGS,
        )
        try:
            async with asyncio.timeout(_SYNC_TIMEOUT_S):
                stdout, stderr = await proc.communicate()
        except TimeoutError:
            logger.warning("Vault index sync timed out after %.0fs", _SYNC_TIMEOUT_S)
            force_kill_process_tree(proc.pid)
            await proc.communicate()
            return False

        if proc.returncode != 0:
            tail = (stderr or stdout).decode(errors="replace").strip()[-400:]
            logger.warning("Vault index sync failed (rc=%s): %s", proc.returncode, tail)
            return False
        logger.info("Vault index rebuilt: %s", self._index_path)
        return True

    async def _run(self) -> None:
        try:
            await self._maybe_sync()
            while self._running:
                await asyncio.sleep(self._config.sync_interval_hours * 3600)
                if not self._running:
                    break  # type: ignore[unreachable]
                try:
                    await self._maybe_sync()
                except Exception:
                    logger.exception("Vault index sync failed, will retry next interval")
        except asyncio.CancelledError:
            logger.debug("Vault index observer cancelled")
            raise
