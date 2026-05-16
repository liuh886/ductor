from __future__ import annotations

from ductor_bot.runtime.memory import MemoryFragment, detect_conflicts, govern_fragments


def _fragment(title: str, body: str, *, ulid: str, importance: float = 1.0) -> MemoryFragment:
    return MemoryFragment(
        title=title,
        body=body,
        ulid=ulid,
        source_kind="mainmemory",
        source_path="/tmp/MAINMEMORY.md",
        scope="mainmemory",
        agent_name="main",
        tags=["alpha"],
        importance=importance,
    )


def test_govern_fragments_deduplicates_exact_matches() -> None:
    governed, conflicts = govern_fragments(
        [
            _fragment("Preferences", "- Keep answers short", ulid="a1"),
            _fragment("Preferences", "- Keep answers short", ulid="a2", importance=1.4),
        ]
    )

    assert len(governed) == 1
    assert conflicts == []
    assert governed[0].ulid == "a1"
    assert governed[0].importance > 1.4


def test_govern_fragments_compresses_subset_fragments() -> None:
    governed, conflicts = govern_fragments(
        [
            _fragment("Preferences", "- Keep answers short\n- Use bullets", ulid="a1"),
            _fragment("Preferences", "- Keep answers short", ulid="a2"),
        ]
    )

    assert len(governed) == 1
    assert conflicts == []
    assert "Use bullets" in governed[0].body


def test_detect_conflicts_flags_same_title_with_different_bodies() -> None:
    conflicts = detect_conflicts(
        [
            _fragment("Preferences", "- Prefer concise replies", ulid="a1"),
            _fragment("Preferences", "- Prefer exhaustive replies", ulid="a2"),
        ]
    )

    assert len(conflicts) == 1
    assert conflicts[0].title == "Preferences"
    assert conflicts[0].ulids == ("a1", "a2")
