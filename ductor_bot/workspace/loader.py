"""Workspace file reader: safe reads with fallback defaults."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from ductor_bot.runtime.memory import extract_markdown_fragments
from ductor_bot.workspace.paths import DuctorPaths

logger = logging.getLogger(__name__)


def read_file(path: Path) -> str | None:
    """Read a file, returning None if it does not exist or cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("Failed to read file: %s", path, exc_info=True)
        return None


def read_mainmemory(
    paths: DuctorPaths,
    *,
    fragment_repo: object | None = None,
    agent_name: str = "",
) -> str:
    """Read MAINMEMORY.md, preferring fragment-backed context when available."""
    _sync_memory_fragments(
        paths,
        fragment_repo,
        agent_name=agent_name,
    )
    fragment_text = _read_fragment_context(
        fragment_repo,
        scope="mainmemory",
        agent_name=agent_name,
    )
    shared_text = _read_fragment_context(fragment_repo, scope="sharedmemory")
    rendered = "\n\n".join(part for part in (fragment_text, shared_text) if part.strip())
    if rendered.strip():
        return rendered
    return read_file(paths.mainmemory_path) or ""


def load_soul(paths: DuctorPaths) -> str:
    """Read SOUL.md from the workspace."""
    return read_file(paths.soul_path) or ""


def _sync_memory_fragments(
    paths: DuctorPaths,
    fragment_repo: object | None,
    *,
    agent_name: str = "",
) -> None:
    """Extract Markdown memory files into the runtime fragment store when available."""
    if fragment_repo is None:
        return
    replace_for_scope = getattr(fragment_repo, "replace_for_scope", None)
    list_by_scope = getattr(fragment_repo, "list_by_scope", None)
    if not callable(replace_for_scope):
        return
    if not callable(list_by_scope):
        return

    try:
        existing_main = list_by_scope("mainmemory", agent_name=agent_name) if agent_name else []
        existing_shared = list_by_scope("sharedmemory")
    except Exception:
        logger.debug("Failed to inspect existing memory fragments", exc_info=True)
        return

    try:
        if agent_name and not existing_main:
            main_text = read_file(paths.mainmemory_path) or ""
            main_fragments = (
                extract_markdown_fragments(
                    main_text,
                    source_path=str(paths.mainmemory_path),
                    source_kind="mainmemory",
                    scope="mainmemory",
                    agent_name=agent_name,
                )
                if main_text.strip()
                else []
            )
            replace_for_scope("mainmemory", main_fragments, agent_name=agent_name)
        if not existing_shared:
            shared_text = read_file(paths.sharedmemory_path) or ""
            shared_fragments = (
                extract_markdown_fragments(
                    shared_text,
                    source_path=str(paths.sharedmemory_path),
                    source_kind="sharedmemory",
                    scope="sharedmemory",
                    agent_name="",
                )
                if shared_text.strip()
                else []
            )
            replace_for_scope("sharedmemory", shared_fragments)
    except Exception:
        logger.debug("Failed to sync memory fragments", exc_info=True)


def _read_fragment_context(
    fragment_repo: object | None,
    *,
    scope: str,
    agent_name: str = "",
) -> str:
    """Return rendered memory fragments from a runtime repository if available."""
    if fragment_repo is None:
        return ""
    list_by_scope = getattr(fragment_repo, "list_by_scope", None)
    if not callable(list_by_scope):
        return ""

    rows: list[dict[str, object]] = []
    try:
        if agent_name:
            rows = list_by_scope(scope, agent_name=agent_name)
        if not rows:
            rows = list_by_scope(scope)
    except Exception:
        logger.debug("Failed to read memory fragments", exc_info=True)
        return ""
    return _render_fragments(rows)


def _render_fragments(fragments: Iterable[dict[str, object]]) -> str:
    """Render fragment rows into a deterministic Markdown block."""
    parts: list[str] = []
    for fragment in fragments:
        body = str(fragment.get("body", "")).strip()
        if not body:
            continue
        title = str(fragment.get("title", "")).strip()
        source_path = str(fragment.get("source_path", "")).strip()
        lines: list[str] = []
        if title:
            lines.append(f"## {title}")
        lines.append(body)
        if source_path:
            lines.append(f"_Source: {source_path}_")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
