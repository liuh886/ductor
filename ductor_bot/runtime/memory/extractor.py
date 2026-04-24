"""Deterministic Markdown fragment extraction for runtime memory files."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|(\d+\.))\s+(.*\S)\s*$")


@dataclass(slots=True)
class MemoryFragment:
    """A deterministic fragment extracted from a Markdown memory file."""

    title: str
    body: str
    ulid: str = ""
    source_kind: str = ""
    source_path: str = ""
    scope: str = ""
    agent_name: str = ""
    tags: list[str] = field(default_factory=list)
    importance: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0


def extract_markdown_fragments(
    text: str,
    *,
    source_path: str = "",
    source_kind: str = "",
    scope: str = "",
    agent_name: str = "",
) -> list[MemoryFragment]:
    """Split Markdown into heading-aware fragments with bullet content preserved."""
    lines = text.splitlines()
    fragments: list[MemoryFragment] = []
    current_title = "ROOT"
    current_lines: list[str] = []
    current_tags: list[str] = []
    fragment_index = 0

    def flush() -> None:
        nonlocal current_lines, current_tags, fragment_index
        body = "\n".join(line.rstrip() for line in current_lines).strip()
        if body:
            fragment_index += 1
            ulid = _deterministic_fragment_ulid(
                fragment_index,
                (
                    source_path,
                    source_kind,
                    scope,
                    agent_name,
                    current_title,
                    body,
                ),
            )
            fragments.append(
                MemoryFragment(
                    title=current_title,
                    body=body,
                    ulid=ulid,
                    source_path=source_path,
                    source_kind=source_kind,
                    scope=scope,
                    agent_name=agent_name,
                    tags=list(dict.fromkeys(current_tags)),
                    importance=_score_fragment(current_title, body),
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
        current_lines = []
        current_tags = []

    for raw_line in lines:
        line = raw_line.rstrip()
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            current_title = heading.group(2).strip()
            continue

        if not line.strip():
            if current_lines and current_lines[-1] != "":
                current_lines.append("")
            continue

        bullet = _LIST_RE.match(line)
        if bullet:
            item = bullet.group(4).strip()
            current_lines.append(f"- {item}")
            current_tags.extend(_extract_tags(item))
            continue

        current_lines.append(line.strip())
        current_tags.extend(_extract_tags(line))

    flush()
    return fragments


def _extract_tags(text: str) -> list[str]:
    """Collect low-noise tags from a fragment line."""
    tags: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_./-]{3,}", text):
        cleaned = token.strip(".,:;()[]{}\"'`")
        if cleaned and cleaned.lower() not in {"the", "and", "for", "with", "from", "this"}:
            tags.append(cleaned.lower())
    return tags


def _deterministic_fragment_ulid(
    fragment_index: int,
    parts: tuple[str, str, str, str, str, str],
) -> str:
    """Return a stable content-derived identifier for a fragment."""
    digest = hashlib.sha256(
        "\x1f".join((str(fragment_index), *parts)).encode("utf-8")
    ).hexdigest()
    return f"mf_{digest[:26]}"


def _score_fragment(title: str, body: str) -> float:
    """Assign a stable heuristic importance score."""
    score = 0.0
    if title and title != "ROOT":
        score += 1.0
    score += min(len(body.splitlines()), 20) * 0.05
    score += min(len(body), 500) / 5000.0
    if body.startswith("- "):
        score += 0.2
    return round(score, 3)
