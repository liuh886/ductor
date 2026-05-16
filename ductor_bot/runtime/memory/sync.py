"""Reverse synchronization from SQLite memory fragments to Markdown source files."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$", re.MULTILINE)


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
        content = _render_fragments_to_markdown(fragments, source_path=source_path)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(content, encoding="utf-8")
        logger.info("Reverse synced %s", source_path)
    except Exception:
        logger.exception("Failed to reverse sync %s", source_path)
        return False
    else:
        return True


def _render_fragments_to_markdown(fragments: Iterable[dict[str, object]], source_path: Path | None = None) -> str:
    """Render fragment rows back into Markdown while preserving file structure when possible."""
    fragment_list = list(fragments)
    if source_path is None:
        source_path = _resolve_source_path(fragment_list)
    existing = ""
    if source_path is not None and source_path.exists():
        try:
            existing = source_path.read_text(encoding="utf-8")
        except OSError:
            logger.debug("Failed to read existing source %s", source_path, exc_info=True)
    if existing:
        return _merge_fragments_into_existing(existing, fragment_list)
    return _render_fragments_from_scratch(fragment_list)


def _resolve_source_path(fragments: list[dict[str, object]]) -> Path | None:
    """Return the first absolute source path present in the fragment set."""
    for fragment in fragments:
        raw = str(fragment.get("source_path", "")).strip()
        path = Path(raw)
        if raw and path.is_absolute():
            return path
    return None


def _merge_fragments_into_existing(existing: str, fragments: list[dict[str, object]]) -> str:
    """Replace matching sections in an existing Markdown file, preserving surrounding structure."""
    frontmatter, body = _split_frontmatter(existing)
    sections = _parse_sections(body)
    if not sections:
        return _render_fragments_from_scratch(fragments, frontmatter=frontmatter)

    root_fragment = next((f for f in fragments if str(f.get("title", "")).strip() == "ROOT"), None)
    if root_fragment is not None:
        sections[0]["body"] = str(root_fragment.get("body", "")).strip()

    used_titles: set[tuple[str, int]] = set()
    for fragment in fragments:
        title = str(fragment.get("title", "")).strip()
        if not title or title == "ROOT":
            continue
        match_idx = _find_section_index(sections, title, used_titles)
        if match_idx is None:
            sections.append(
                {
                    "heading": f"## {title}",
                    "title": title,
                    "body": str(fragment.get("body", "")).strip(),
                }
            )
            continue
        used_titles.add((title.casefold(), match_idx))
        sections[match_idx]["body"] = str(fragment.get("body", "")).strip()

    rendered_body = _render_sections(sections)
    return f"{frontmatter}{rendered_body}".rstrip() + "\n"


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split YAML frontmatter from the remaining Markdown body."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    end += len("\n---\n")
    return text[:end], text[end:]


def _parse_sections(body: str) -> list[dict[str, str]]:
    """Parse Markdown into a preamble/root section plus heading sections."""
    sections: list[dict[str, str]] = []
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        return [{"heading": "", "title": "ROOT", "body": body.strip()}]

    preamble = body[: matches[0].start()].strip()
    sections.append({"heading": "", "title": "ROOT", "body": preamble})

    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        sections.append(
            {
                "heading": match.group(0).strip(),
                "title": match.group(2).strip(),
                "body": body[start:end].strip(),
            }
        )
    return sections


def _find_section_index(
    sections: list[dict[str, str]],
    title: str,
    used_titles: set[tuple[str, int]],
) -> int | None:
    """Return the first unused section index matching the fragment title."""
    folded = title.casefold()
    for idx, section in enumerate(sections):
        if idx == 0:
            continue
        if section["title"].casefold() == folded and (folded, idx) not in used_titles:
            return idx
    return None


def _render_sections(sections: list[dict[str, str]]) -> str:
    """Render parsed sections back into Markdown."""
    blocks: list[str] = []
    for idx, section in enumerate(sections):
        chunk: list[str] = []
        if idx > 0 and section["heading"]:
            chunk.append(section["heading"])
        body = section["body"].strip()
        if body:
            chunk.append(body)
        block = "\n".join(chunk).strip()
        if block:
            blocks.append(block)
    return "\n\n".join(blocks) + "\n"


def _render_fragments_from_scratch(
    fragments: list[dict[str, object]],
    *,
    frontmatter: str = "",
) -> str:
    """Fallback renderer when no existing Markdown skeleton is available."""
    parts: list[str] = []
    for fragment in fragments:
        title = str(fragment.get("title", "")).strip()
        body = str(fragment.get("body", "")).strip()
        block = []
        if title and title != "ROOT":
            block.append(f"## {title}")
        if body:
            block.append(body)
        part = "\n".join(block).strip()
        if part:
            parts.append(part)
    body = "\n\n".join(parts).rstrip() + "\n" if parts else ""
    return f"{frontmatter}{body}".rstrip() + "\n"
