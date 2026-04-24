"""Reverse synchronization from SQLite memory fragments to Markdown source files."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)


def reverse_sync_source(
    source_path: Path,
    fragments: Iterable[dict[str, object]],
) -> bool:
    """Reconstruct a Markdown file from its fragments.

    This ensures that updates in SQLite are reflected in the .md source of truth.
    """
    if source_path.suffix != ".md":
        return False

    try:
        content = _render_fragments_to_markdown(fragments)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(content, encoding="utf-8")
        logger.info("Reverse synced %s", source_path)
    except Exception:
        logger.exception("Failed to reverse sync %s", source_path)
        return False
    else:
        return True


def _render_fragments_to_markdown(fragments: Iterable[dict[str, object]]) -> str:
    """Render fragment rows back into a standard Markdown structure."""
    parts: list[str] = []

    # Simple strategy: group by heading; fragments are already title-based.
    # title-based, we just emit them in sequence.
    for fragment in fragments:
        title = str(fragment.get("title", "")).strip()
        body = str(fragment.get("body", "")).strip()

        block = []
        if title and title != "ROOT":
            block.append(f"## {title}")
        block.append(body)
        parts.append("\n".join(block))

    return "\n\n".join(parts) + "\n"
