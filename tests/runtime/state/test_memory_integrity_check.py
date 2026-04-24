from __future__ import annotations

from pathlib import Path

from ductor_bot.runtime.memory import MemoryFragment
from ductor_bot.runtime.state import MemoryFragmentRepository, RuntimeStateDB
from ductor_bot.scripts.memory_integrity_check import check_integrity


def test_check_integrity_prints_fragment_and_conflict_counts(tmp_path: Path, capsys: object) -> None:
    root = tmp_path / "home"
    (root / "workspace" / "memory_system").mkdir(parents=True)
    (root / "workspace" / "memory_system" / "MAINMEMORY.md").write_text("# Main Memory\n", encoding="utf-8")
    (root / "SHAREDMEMORY.md").write_text("## Shared\n", encoding="utf-8")
    agent_home = root / "agents" / "bot3-writer"
    (agent_home / "workspace" / "memory_system").mkdir(parents=True)
    (agent_home / "workspace" / "memory_system" / "MAINMEMORY.md").write_text("# Main Memory\n", encoding="utf-8")

    repo = MemoryFragmentRepository(RuntimeStateDB(agent_home / "state.db"))
    repo.replace_for_scope(
        "mainmemory",
        [
            MemoryFragment(
                title="Preferences",
                body="- Keep answers short",
                scope="mainmemory",
                agent_name="bot3-writer",
                ulid="mf_a",
            ),
            MemoryFragment(
                title="Preferences",
                body="- Prefer exhaustive replies",
                scope="mainmemory",
                agent_name="bot3-writer",
                ulid="mf_b",
            ),
        ],
        agent_name="bot3-writer",
    )

    check_integrity(root)
    captured = capsys.readouterr()

    assert "Conflicts" in captured.out
    assert "bot3-writer/mainmemory" in captured.out
