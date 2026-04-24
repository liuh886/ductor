"""SharedKnowledgeSync helpers and watcher for ``SHAREDMEMORY.md``."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ductor_bot.infra.file_watcher import FileWatcher

if TYPE_CHECKING:
    from ductor_bot.multiagent.supervisor import AgentSupervisor

logger = logging.getLogger(__name__)

_START_MARKER = "<!-- SHARED_KNOWLEDGE:START -->"
_END_MARKER = "<!-- SHARED_KNOWLEDGE:END -->"
_LEGACY_START = "<!-- SHARED_MEMORY_SYNC:START -->"
_LEGACY_END = "<!-- SHARED_MEMORY_SYNC:END -->"
_MAX_SHARED_LINES = 15
_MAX_SHARED_BYTES = 8 * 1024

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"api[_ -]?key|access[_ -]?key|auth[_ -]?token|bearer|client[_ -]?secret|"
    r"gateway[_ -]?key|password|passwd|private[_ -]?key|secret|session[_ -]?token|token"
    r")\b\s*[:=]\s*\S+"
)
_SECRET_PREFIX_RE = re.compile(
    r"(?i)\b("
    r"sk-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AIza[0-9A-Za-z_-]{10,}|xox[baprs]-[0-9A-Za-z-]{10,}|hf_[A-Za-z0-9]{10,}|"
    r"ya29\.[0-9A-Za-z._-]{10,}"
    r")\b"
)
_PLACEHOLDER_HINTS = ("<redacted>", "redacted", "example", "placeholder", "your_", "your-")


@dataclass(frozen=True)
class SharedKnowledgeAudit:
    """Advisory audit result for SHAREDMEMORY.md content."""

    line_count: int
    byte_count: int
    secret_line_numbers: tuple[int, ...]
    warnings: tuple[str, ...]


def _find_markers(text: str) -> tuple[str, str] | None:
    """Return the marker pair present in ``text``, preferring the current format."""
    if _START_MARKER in text and _END_MARKER in text:
        return (_START_MARKER, _END_MARKER)
    if _LEGACY_START in text and _LEGACY_END in text:
        return (_LEGACY_START, _LEGACY_END)
    return None


def _render_shared_block(shared_text: str) -> str:
    body = shared_text.rstrip("\n")
    return f"{_START_MARKER}\n{body}\n{_END_MARKER}"


def _looks_secret_like(line: str) -> bool:
    lowered = line.strip().lower()
    if not lowered:
        return False
    if any(hint in lowered for hint in _PLACEHOLDER_HINTS):
        return False
    if "-----begin" in lowered and "private key-----" in lowered:
        return True
    if _SECRET_ASSIGNMENT_RE.search(line):
        return True
    if _SECRET_PREFIX_RE.search(line):
        return True
    return "authorization:" in lowered and "bearer " in lowered


def _audit_shared_memory(text: str) -> SharedKnowledgeAudit:
    """Return advisory warnings for shared-memory content."""
    lines = text.splitlines()
    nonempty_lines = [line for line in lines if line.strip()]
    byte_count = len(text.encode("utf-8"))
    warnings: list[str] = []

    if len(nonempty_lines) > _MAX_SHARED_LINES:
        warnings.append(
            "SHAREDMEMORY.md is being used as a dump; keep it under "
            f"{_MAX_SHARED_LINES} non-empty lines and reserve it for cross-agent alerts."
        )
    if byte_count > _MAX_SHARED_BYTES:
        warnings.append(
            "SHAREDMEMORY.md is too large for an alert channel "
            f"({byte_count} bytes > {_MAX_SHARED_BYTES} bytes). Move durable details elsewhere."
        )

    secret_line_numbers = tuple(
        line_number
        for line_number, line in enumerate(lines, start=1)
        if _looks_secret_like(line)
    )
    if secret_line_numbers:
        joined_lines = ", ".join(str(line_number) for line_number in secret_line_numbers[:10])
        overflow = "" if len(secret_line_numbers) <= 10 else ", ..."
        warnings.append(
            "SHAREDMEMORY.md contains secret-like content on line(s) "
            f"{joined_lines}{overflow}. Remove secrets; shared memory is not a secret store."
        )

    return SharedKnowledgeAudit(
        line_count=len(nonempty_lines),
        byte_count=byte_count,
        secret_line_numbers=secret_line_numbers,
        warnings=tuple(warnings),
    )


def _sync_agent_io(shared_path: Path, mainmemory_path: Path) -> bool:
    """Synchronize shared knowledge into an agent MAINMEMORY file.

    Returns ``True`` when the target file changed, otherwise ``False``.
    """
    if not shared_path.is_file():
        return False
    if not mainmemory_path.is_file():
        return False

    shared_text = shared_path.read_text(encoding="utf-8").strip()
    if not shared_text:
        return False

    original = mainmemory_path.read_text(encoding="utf-8")
    markers = _find_markers(original)
    shared_block = _render_shared_block(shared_text)

    if markers is None:
        updated = original.rstrip("\n")
        updated = f"{updated}\n\n{shared_block}\n" if updated else f"{shared_block}\n"
    else:
        start_marker, end_marker = markers
        start_idx = original.index(start_marker)
        end_idx = original.index(end_marker) + len(end_marker)
        prefix = original[:start_idx].rstrip("\n")
        suffix = original[end_idx:].lstrip("\n")

        parts: list[str] = []
        if prefix:
            parts.append(prefix)
        parts.append(shared_block)
        if suffix:
            parts.append(suffix.rstrip("\n"))
        updated = "\n\n".join(parts) + "\n"

    if updated == original:
        return False

    mainmemory_path.write_text(updated, encoding="utf-8")
    return True


class SharedKnowledgeSync:
    """Watches ``SHAREDMEMORY.md`` and emits advisory governance warnings."""

    def __init__(self, shared_path: Path, supervisor: AgentSupervisor) -> None:
        self._path = shared_path
        self._supervisor = supervisor
        self._watcher = FileWatcher(self._path, self._on_changed)

    @property
    def path(self) -> Path:
        return self._path

    async def start(self) -> None:
        """Start watching."""
        if not self._path.is_file():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                "# Shared Knowledge — All Agents\n\n"
                "Keep this file short. Use it only for cross-agent alerts or coordination notes.\n"
                "Do not put secrets here. Durable details belong in the proper memory files.\n"
                "Only a small tail may be surfaced dynamically at runtime.\n",
                encoding="utf-8",
            )
            logger.info("Created seed SHAREDMEMORY.md at %s", self._path)
        self._warn_if_unsafe()
        await self._watcher.update_mtime()
        await self._watcher.start()
        logger.info("SharedKnowledgeSync watching %s", self._path)

    async def stop(self) -> None:
        await self._watcher.stop()

    async def _on_changed(self) -> None:
        """FileWatcher callback — SHAREDMEMORY.md was modified."""
        self._warn_if_unsafe()
        logger.info("SHAREDMEMORY.md changed (handled dynamically by workspace loader).")

    async def sync_agent(self, mainmemory_path: Path) -> None:
        """No-op for backward compatibility."""

    def _warn_if_unsafe(self) -> None:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            logger.exception("Failed to inspect SHAREDMEMORY.md at %s", self._path)
            return

        report = _audit_shared_memory(text)
        for warning in report.warnings:
            logger.warning("%s (%s)", warning, self._path)
