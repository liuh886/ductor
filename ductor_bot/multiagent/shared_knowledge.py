"""SharedKnowledgeSync: maintains shared operations and removes old projections."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ductor_bot.multiagent.supervisor import AgentSupervisor

logger = logging.getLogger(__name__)

_START_MARKER = "--- SHARED KNOWLEDGE START ---"
_END_MARKER = "--- SHARED KNOWLEDGE END ---"

# Legacy HTML markers for backward compatibility (read-only).
_LEGACY_START = "<!-- SHARED:START -->"
_LEGACY_END = "<!-- SHARED:END -->"


def _find_markers(text: str) -> tuple[str, str] | None:
    """Detect which marker pair is present in *text*. Returns (start, end) or None."""
    start_idx = text.find(_START_MARKER)
    if start_idx >= 0 and text.find(_END_MARKER, start_idx + len(_START_MARKER)) >= 0:
        return _START_MARKER, _END_MARKER
    start_idx = text.find(_LEGACY_START)
    if start_idx >= 0 and text.find(_LEGACY_END, start_idx + len(_LEGACY_START)) >= 0:
        return _LEGACY_START, _LEGACY_END
    return None


def _strip_shared_projection(text: str) -> tuple[str, bool]:
    """Remove a legacy shared-knowledge projection block from MAINMEMORY text."""
    markers = _find_markers(text)
    if markers is None:
        return text, False

    start_marker, end_marker = markers
    start_idx = text.index(start_marker)
    end_idx = text.index(end_marker, start_idx + len(start_marker)) + len(end_marker)
    prefix = text[:start_idx].rstrip("\n")
    suffix = text[end_idx:].lstrip("\n").rstrip("\n")
    parts = [part for part in (prefix, suffix) if part]
    return ("\n\n".join(parts) + "\n" if parts else ""), True


def _remove_agent_projection_io(mainmemory_path: Path) -> bool:
    """Remove an obsolete SHAREDMEMORY block from one agent's MAINMEMORY.

    Returns True if the file was written.
    """
    if not mainmemory_path.is_file():
        return False
    current = mainmemory_path.read_text(encoding="utf-8")
    updated, changed = _strip_shared_projection(current)
    if not changed:
        return False
    mainmemory_path.write_text(updated, encoding="utf-8")
    return True


class SharedKnowledgeSync:
    """Maintain the shared operational note without copying it into agent prompts."""

    def __init__(self, shared_path: Path, supervisor: AgentSupervisor) -> None:
        self._path = shared_path
        self._supervisor = supervisor

    async def start(self) -> None:
        """Start watching and perform an initial sync.

        Creates an empty SHAREDMEMORY.md if it does not exist, then performs a
        one-time cleanup of obsolete prompt projections.
        """
        if not self._path.is_file():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                "# Shared Operations — All Agents\n\n"
                "Keep this file short: ports, environment changes, and active incidents only.\n",
                encoding="utf-8",
            )
            logger.info("Created seed SHAREDMEMORY.md at %s", self._path)
        await self._sync_all()
        logger.info("Shared operational note ready at %s", self._path)

    async def stop(self) -> None:
        """Compatibility lifecycle hook; no background watcher is running."""

    async def sync_agent(self, mainmemory_path: Path) -> None:
        """Remove an old shared projection from a single agent workspace."""
        written = await asyncio.to_thread(_remove_agent_projection_io, mainmemory_path)
        if written:
            logger.info("Removed legacy shared projection from %s", mainmemory_path)

    async def _sync_all(self) -> None:
        """Remove legacy projections from all registered agent workspaces."""
        for name, stack in self._supervisor.stacks.items():
            try:
                await self.sync_agent(stack.paths.mainmemory_path)
            except Exception:
                logger.exception("Failed to sync shared knowledge to agent '%s'", name)
