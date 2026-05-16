"""Tests for deterministic Markdown memory fragment extraction."""

# ruff: noqa: INP001

from __future__ import annotations

from ductor_bot.runtime.memory import MemoryFragment, extract_markdown_fragments


def test_extract_markdown_fragments_sections_and_bullets() -> None:
    text = """# Main Memory

## Preferences
- likes concise answers
- prefers structured plans

Paragraph that should stay in the section.

## Task Notes
1. first step
2. second step
"""

    fragments = extract_markdown_fragments(
        text,
        source_path="workspace/memory_system/MAINMEMORY.md",
        source_kind="mainmemory",
        scope="mainmemory",
        agent_name="main",
    )

    assert [fragment.title for fragment in fragments] == ["Preferences", "Task Notes"]
    assert (
        fragments[0].body
        == "- likes concise answers\n- prefers structured plans\n\nParagraph that should stay in the section."
    )
    assert fragments[0].source_kind == "mainmemory"
    assert fragments[0].source_path.endswith("MAINMEMORY.md")
    assert fragments[0].scope == "mainmemory"
    assert "concise" in fragments[0].tags
    assert fragments[0].importance > 0


def test_extract_markdown_fragments_is_deterministic() -> None:
    text = """# Shared Knowledge

- Alpha
- Beta
"""

    first = extract_markdown_fragments(
        text,
        source_path="SHAREDMEMORY.md",
        source_kind="shared",
    )
    second = extract_markdown_fragments(
        text,
        source_path="SHAREDMEMORY.md",
        source_kind="shared",
    )

    assert first == second
    assert len(first) == 1
    assert all(isinstance(fragment, MemoryFragment) for fragment in first)


def test_extract_markdown_fragments_ignores_yaml_frontmatter() -> None:
    text = """---
schema_version: 1
memory_format: hybrid
---

# Main Memory

## Preferences
- keep answers short
"""

    fragments = extract_markdown_fragments(
        text,
        source_path="workspace/memory_system/MAINMEMORY.md",
        source_kind="mainmemory",
        scope="mainmemory",
        agent_name="main",
    )

    assert [fragment.title for fragment in fragments] == ["Preferences"]
    assert "schema_version" not in fragments[0].body
