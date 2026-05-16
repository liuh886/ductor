from __future__ import annotations

from pathlib import Path

from ductor_bot.runtime.memory.sync import reverse_sync_source


def test_reverse_sync_preserves_frontmatter_and_heading_level(tmp_path: Path) -> None:
    source = tmp_path / "MAINMEMORY.md"
    source.write_text(
        "---\n"
        "title: Memory\n"
        "---\n\n"
        "# Main Memory\n\n"
        "Intro text.\n\n"
        "### Preferences\n"
        "- old\n\n"
        "## Unrelated\n"
        "Keep me\n",
        encoding="utf-8",
    )

    ok = reverse_sync_source(
        source,
        [
            {"title": "ROOT", "body": "Intro text refreshed.", "source_path": str(source)},
            {"title": "Preferences", "body": "- new preference", "source_path": str(source)},
        ],
    )

    text = source.read_text(encoding="utf-8")
    assert ok is True
    assert text.startswith("---\ntitle: Memory\n---\n")
    assert "### Preferences\n- new preference" in text
    assert "## Unrelated\nKeep me" in text
    assert "Intro text refreshed." in text


def test_reverse_sync_appends_new_sections_when_missing(tmp_path: Path) -> None:
    source = tmp_path / "MAINMEMORY.md"
    source.write_text("# Main Memory\n\n## Existing\nBody\n", encoding="utf-8")

    reverse_sync_source(
        source,
        [
            {"title": "Existing", "body": "Updated", "source_path": str(source)},
            {"title": "New Section", "body": "Created", "source_path": str(source)},
        ],
    )

    text = source.read_text(encoding="utf-8")
    assert "## Existing\nUpdated" in text
    assert "## New Section\nCreated" in text
