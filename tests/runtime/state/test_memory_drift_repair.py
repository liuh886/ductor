from __future__ import annotations

from pathlib import Path

from ductor_bot.multiagent.shared_knowledge import _END_MARKER, _START_MARKER
from ductor_bot.runtime.memory import MemoryFragment
from ductor_bot.runtime.state.db import RuntimeStateDB
from ductor_bot.runtime.state.repositories.memory_fragment_repo import MemoryFragmentRepository
from ductor_bot.scripts.memory_drift_repair import repair_all, repair_memory_home


def test_repair_memory_home_backfills_empty_template_from_db(tmp_path: Path) -> None:
    home = tmp_path / "agent"
    (home / "workspace" / "memory_system").mkdir(parents=True)
    mm = home / "workspace" / "memory_system" / "MAINMEMORY.md"
    mm.write_text(
        "# Main Memory\n\n"
        "## About the User\n\n"
        "(Empty -- will be populated as you learn about your human.)\n\n"
        "## Learned Facts\n\n"
        "(Empty -- will be populated as the agent learns.)\n\n"
        "## Decisions and Preferences\n\n"
        "(Empty -- record important decisions and their reasoning here.)\n",
        encoding="utf-8",
    )
    repo = MemoryFragmentRepository(RuntimeStateDB(home / "state.db"))
    repo.replace_for_scope(
        "mainmemory",
        [
            MemoryFragment(
                title="About the User",
                body="- Speaks Chinese",
                source_path=str(mm),
                source_kind="mainmemory",
                scope="mainmemory",
                agent_name="bot3-writer",
            )
        ],
        agent_name="bot3-writer",
    )

    result = repair_memory_home(home, agent_name="bot3-writer")

    assert result.action == "db_to_markdown"
    text = mm.read_text(encoding="utf-8")
    assert "Speaks Chinese" in text


def test_repair_memory_home_backfills_db_from_markdown(tmp_path: Path) -> None:
    home = tmp_path / "agent"
    (home / "workspace" / "memory_system").mkdir(parents=True)
    mm = home / "workspace" / "memory_system" / "MAINMEMORY.md"
    mm.write_text("# Main Memory\n\n## Preferences\n- Keep answers short\n", encoding="utf-8")

    result = repair_memory_home(home, agent_name="bot7-agency")

    repo = MemoryFragmentRepository(RuntimeStateDB(home / "state.db"))
    stored = repo.list_by_scope("mainmemory", agent_name="bot7-agency")
    assert result.action == "markdown_to_db"
    assert len(stored) == 1
    assert stored[0]["title"] == "Preferences"


def test_repair_memory_home_bootstraps_empty_template_from_shared(tmp_path: Path) -> None:
    root = tmp_path / "home"
    home = root / "agents" / "seismic-bot"
    (home / "workspace" / "memory_system").mkdir(parents=True)
    mm = home / "workspace" / "memory_system" / "MAINMEMORY.md"
    mm.write_text(
        "# Main Memory\n\n"
        "## About the User\n\n"
        "(Empty -- will be populated as you learn about your human.)\n\n"
        "## Learned Facts\n\n"
        "(Empty -- will be populated as the agent learns.)\n\n"
        "## Decisions and Preferences\n\n"
        "(Empty -- record important decisions and their reasoning here.)\n",
        encoding="utf-8",
    )
    (root / "SHAREDMEMORY.md").write_text(
        "## Shared Cross-Agent Alerts\n- Keep unified logs separate from Focus Now.\n",
        encoding="utf-8",
    )

    result = repair_memory_home(home, agent_name="seismic-bot")

    assert result.action == "bootstrap_from_shared"
    assert result.fragment_count > 0
    updated = mm.read_text(encoding="utf-8")
    assert "signal-detection and change-monitoring agent" in updated
    assert _START_MARKER in updated
    assert _END_MARKER in updated
    assert "Shared Cross-Agent Alerts" in updated


def test_repair_all_handles_main_and_agents(tmp_path: Path) -> None:
    main_home = tmp_path / "home"
    (main_home / "workspace" / "memory_system").mkdir(parents=True)
    (main_home / "workspace" / "memory_system" / "MAINMEMORY.md").write_text(
        "# Main Memory\n\n## Preferences\n- Main pref\n",
        encoding="utf-8",
    )
    agent_home = main_home / "agents" / "research"
    (agent_home / "workspace" / "memory_system").mkdir(parents=True)
    (agent_home / "workspace" / "memory_system" / "MAINMEMORY.md").write_text(
        "# Main Memory\n\n## Preferences\n- Agent pref\n",
        encoding="utf-8",
    )

    results = repair_all(main_home)

    assert {r.agent_name for r in results} == {"main", "research"}
