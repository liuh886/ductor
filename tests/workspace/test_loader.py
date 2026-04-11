"""Tests for workspace file reader."""

from __future__ import annotations

from pathlib import Path

from ductor_bot.runtime.memory import MemoryFragment
from ductor_bot.runtime.state import RuntimeStateDB
from ductor_bot.runtime.state.repositories.memory_fragment_repo import MemoryFragmentRepository
from ductor_bot.workspace.loader import read_file, read_mainmemory
from ductor_bot.workspace.paths import DuctorPaths


def _make_paths(tmp_path: Path) -> DuctorPaths:
    fw = tmp_path / "fw"
    return DuctorPaths(
        ductor_home=tmp_path / "home", home_defaults=fw / "workspace", framework_root=fw
    )


# -- read_file --


def test_read_existing_file(tmp_path: Path) -> None:
    f = tmp_path / "test.md"
    f.write_text("Hello world")
    assert read_file(f) == "Hello world"


def test_read_nonexistent_file(tmp_path: Path) -> None:
    assert read_file(tmp_path / "missing.md") is None


def test_read_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.md"
    f.write_text("")
    assert read_file(f) == ""


# -- read_mainmemory --


def test_read_mainmemory_exists(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.memory_system_dir.mkdir(parents=True)
    paths.mainmemory_path.write_text("# Memories\n- Learned X")
    result = read_mainmemory(paths)
    assert result == "# Memories\n- Learned X"


def test_read_mainmemory_missing(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    assert read_mainmemory(paths) == ""


def test_read_mainmemory_prefers_fragments(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    repo = MemoryFragmentRepository(RuntimeStateDB(tmp_path / "state.db"))
    repo.create(
        MemoryFragment(
            title="User Preferences",
            body="- Prefer concise replies\n- Use bullet summaries",
            source_kind="mainmemory",
            source_path=str(paths.mainmemory_path),
            scope="mainmemory",
            agent_name="main",
            tags=["preferences"],
            importance=1.0,
        ),
    )
    paths.memory_system_dir.mkdir(parents=True)
    paths.mainmemory_path.write_text("# Raw memory that should be ignored")

    result = read_mainmemory(paths, fragment_repo=repo, agent_name="main")

    assert "User Preferences" in result
    assert "Prefer concise replies" in result
    assert "Raw memory that should be ignored" not in result


def test_read_mainmemory_renders_extracted_fragments_when_repo_is_empty(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    repo = MemoryFragmentRepository(RuntimeStateDB(tmp_path / "state.db"))
    paths.memory_system_dir.mkdir(parents=True)
    paths.mainmemory_path.write_text("# Raw memory\n- Keep this")

    result = read_mainmemory(paths, fragment_repo=repo, agent_name="main")

    assert "## Raw memory" in result
    assert "- Keep this" in result
    assert "_Source:" in result


def test_read_mainmemory_extracts_and_persists_fragments_from_markdown(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    repo = MemoryFragmentRepository(RuntimeStateDB(tmp_path / "state.db"))
    paths.memory_system_dir.mkdir(parents=True)
    paths.mainmemory_path.write_text("# Main Memory\n\n## Preferences\n- Keep answers short")

    result = read_mainmemory(paths, fragment_repo=repo, agent_name="main")

    assert "## Preferences" in result
    assert "Keep answers short" in result
    stored = repo.list_by_scope("mainmemory", agent_name="main")
    assert len(stored) == 1
    assert stored[0]["title"] == "Preferences"


def test_read_mainmemory_includes_sharedmemory_fragments(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    repo = MemoryFragmentRepository(RuntimeStateDB(tmp_path / "state.db"))
    paths.memory_system_dir.mkdir(parents=True)
    paths.mainmemory_path.write_text("# Main Memory\n\n## Preferences\n- Personal note")
    paths.ductor_home.mkdir(parents=True, exist_ok=True)
    paths.sharedmemory_path.write_text("# Shared Knowledge\n\n## Team Defaults\n- Shared rule")

    result = read_mainmemory(paths, fragment_repo=repo, agent_name="main")

    assert "Personal note" in result
    assert "Shared rule" in result
    shared = repo.list_by_scope("sharedmemory")
    assert len(shared) == 1
    assert shared[0]["title"] == "Team Defaults"
