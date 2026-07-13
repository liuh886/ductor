"""Tests for multiagent/shared_knowledge.py: legacy projection cleanup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from ductor_bot.multiagent.shared_knowledge import (
    _END_MARKER,
    _LEGACY_END,
    _LEGACY_START,
    _START_MARKER,
    SharedKnowledgeSync,
    _find_markers,
    _remove_agent_projection_io,
    _strip_shared_projection,
)
from ductor_bot.multiagent.supervisor import AgentSupervisor


class TestFindMarkers:
    """Test _find_markers() detection."""

    def test_finds_new_markers(self) -> None:
        text = f"before\n{_START_MARKER}\ncontent\n{_END_MARKER}\nafter"
        result = _find_markers(text)
        assert result == (_START_MARKER, _END_MARKER)

    def test_finds_legacy_markers(self) -> None:
        text = f"before\n{_LEGACY_START}\ncontent\n{_LEGACY_END}\nafter"
        result = _find_markers(text)
        assert result == (_LEGACY_START, _LEGACY_END)

    def test_prefers_new_over_legacy(self) -> None:
        """When both marker types exist, prefer new format."""
        text = f"{_START_MARKER}\n{_LEGACY_START}\ncontent\n{_LEGACY_END}\n{_END_MARKER}"
        result = _find_markers(text)
        assert result == (_START_MARKER, _END_MARKER)

    def test_returns_none_when_no_markers(self) -> None:
        assert _find_markers("plain text without markers") is None

    def test_returns_none_for_empty_string(self) -> None:
        assert _find_markers("") is None

    @pytest.mark.parametrize(
        "text",
        [
            f"{_START_MARKER}\nmissing end",
            f"{_END_MARKER}\n{_START_MARKER}",
            f"{_LEGACY_START}\nmissing end",
            f"{_LEGACY_END}\n{_LEGACY_START}",
        ],
    )
    def test_returns_none_for_incomplete_or_reversed_markers(self, text: str) -> None:
        assert _find_markers(text) is None


class TestRemoveAgentProjectionIO:
    """Test cleanup of old SHAREDMEMORY projections in MAINMEMORY files."""

    @pytest.fixture
    def mainmemory_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "workspace" / "memory_system" / "MAINMEMORY.md"
        p.parent.mkdir(parents=True)
        p.write_text("# Main Memory\nAgent notes.\n", encoding="utf-8")
        return p

    def test_returns_false_without_projection(self, mainmemory_path: Path) -> None:
        result = _remove_agent_projection_io(mainmemory_path)
        assert result is False

        content = mainmemory_path.read_text(encoding="utf-8")
        assert "# Main Memory" in content

    def test_removes_existing_projection(self, mainmemory_path: Path) -> None:
        mainmemory_path.write_text(
            f"# Main Memory\nAgent notes.\n\n{_START_MARKER}\nShared content\n{_END_MARKER}\n",
            encoding="utf-8",
        )
        result = _remove_agent_projection_io(mainmemory_path)
        assert result is True

        content = mainmemory_path.read_text(encoding="utf-8")
        assert "Agent notes." in content
        assert "Shared content" not in content
        assert _START_MARKER not in content
        assert _END_MARKER not in content

    def test_removes_legacy_markers(self, mainmemory_path: Path) -> None:
        legacy_content = f"# Main Memory\n{_LEGACY_START}\nold content\n{_LEGACY_END}\n# After\n"
        mainmemory_path.write_text(legacy_content, encoding="utf-8")

        result = _remove_agent_projection_io(mainmemory_path)
        assert result is True

        content = mainmemory_path.read_text(encoding="utf-8")
        assert _LEGACY_START not in content
        assert _LEGACY_END not in content
        assert "old content" not in content
        assert "# After" in content

    def test_returns_false_when_mainmemory_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing_mainmem.md"
        assert _remove_agent_projection_io(missing) is False

    def test_strip_projection_preserves_content_before_and_after(self) -> None:
        text = f"# Before\n{_START_MARKER}\nold\n{_END_MARKER}\n# After\n"

        updated, changed = _strip_shared_projection(text)

        assert changed is True
        assert updated == "# Before\n\n# After\n"

    def test_strip_projection_preserves_malformed_content(self) -> None:
        text = f"# Before\n{_END_MARKER}\ncontent\n{_START_MARKER}\n# After\n"

        updated, changed = _strip_shared_projection(text)

        assert changed is False
        assert updated == text


async def test_start_seeds_operations_and_cleans_projection_once(tmp_path: Path) -> None:
    shared_path = tmp_path / "SHAREDMEMORY.md"
    mainmemory_path = tmp_path / "agent" / "MAINMEMORY.md"
    mainmemory_path.parent.mkdir()
    mainmemory_path.write_text(
        f"# Main Memory\n{_START_MARKER}\nobsolete\n{_END_MARKER}\n",
        encoding="utf-8",
    )
    supervisor = cast(
        "AgentSupervisor",
        SimpleNamespace(
            stacks={"worker": SimpleNamespace(paths=SimpleNamespace(mainmemory_path=mainmemory_path))}
        ),
    )
    sync = SharedKnowledgeSync(shared_path, supervisor)

    await sync.start()
    await sync.stop()

    assert "Shared Operations" in shared_path.read_text(encoding="utf-8")
    assert "obsolete" not in mainmemory_path.read_text(encoding="utf-8")
