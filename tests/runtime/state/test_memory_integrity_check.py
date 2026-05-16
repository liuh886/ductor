from __future__ import annotations

from pathlib import Path

from ductor_bot.runtime.memory import MemoryFragment
from ductor_bot.runtime.state import MemoryFragmentRepository, RuntimeStateDB
from ductor_bot.scripts.memory_integrity_check import (
    MemoryAuditReport,
    MemoryAuditRow,
    build_integrity_report,
    check_integrity,
    render_integrity_report,
)


def test_render_integrity_report_ok_summary() -> None:
    report = MemoryAuditReport(
        ductor_home="home",
        rows=(
            MemoryAuditRow(
                name="main/mainmemory",
                scope="mainmemory",
                agent_name="main",
                fragment_count=2,
                conflict_count=0,
                duplicate_groups=0,
                file_status="EXISTS (10 bytes)",
                runtime_read="FRAGMENTS (20 body chars)",
                warnings=(),
            ),
        ),
    )

    rendered = render_integrity_report(report)

    assert rendered == (
        "Memory integrity: OK "
        "(sources=1 fragments=2 conflicts=0 duplicate_groups=0 warnings=0)"
    )


def test_render_integrity_report_warning_only_lists_issue_rows() -> None:
    report = MemoryAuditReport(
        ductor_home="home",
        rows=(
            MemoryAuditRow(
                name="main/mainmemory",
                scope="mainmemory",
                agent_name="main",
                fragment_count=2,
                conflict_count=0,
                duplicate_groups=0,
                file_status="EXISTS (10 bytes)",
                runtime_read="FRAGMENTS (20 body chars)",
                warnings=(),
            ),
            MemoryAuditRow(
                name="bot3-writer/mainmemory",
                scope="mainmemory",
                agent_name="bot3-writer",
                fragment_count=3,
                conflict_count=1,
                duplicate_groups=1,
                file_status="EXISTS (12 bytes)",
                runtime_read="FRAGMENTS (30 body chars)",
                warnings=("CONFLICTS", "DUPLICATES"),
            ),
        ),
    )

    rendered = render_integrity_report(report)

    assert "Memory integrity warnings:" in rendered
    assert "bot3-writer/mainmemory" in rendered
    assert "scope=mainmemory" in rendered
    assert "agent=bot3-writer" in rendered
    assert "conflicts=1" in rendered
    assert "duplicate_groups=1" in rendered
    assert "warnings=CONFLICTS,DUPLICATES" in rendered
    assert "main/mainmemory" not in rendered


def test_check_integrity_prints_fragment_and_conflict_counts(
    tmp_path: Path, capsys: object
) -> None:
    root = tmp_path / "home"
    (root / "workspace" / "memory_system").mkdir(parents=True)
    (root / "workspace" / "memory_system" / "MAINMEMORY.md").write_text(
        "# Main Memory\n", encoding="utf-8"
    )
    (root / "SHAREDMEMORY.md").write_text("## Shared\n", encoding="utf-8")
    agent_home = root / "agents" / "bot3-writer"
    (agent_home / "workspace" / "memory_system").mkdir(parents=True)
    (agent_home / "workspace" / "memory_system" / "MAINMEMORY.md").write_text(
        "# Main Memory\n", encoding="utf-8"
    )

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
    assert "Dupes" in captured.out
    assert "bot3-writer/mainmemory" in captured.out


def test_build_integrity_report_marks_file_only_read_without_state_db(tmp_path: Path) -> None:
    root = tmp_path / "home"
    (root / "workspace" / "memory_system").mkdir(parents=True)
    (root / "workspace" / "memory_system" / "MAINMEMORY.md").write_text(
        "# Main Memory\n\n- Keep answers short\n",
        encoding="utf-8",
    )
    (root / "SHAREDMEMORY.md").write_text("", encoding="utf-8")

    report = build_integrity_report(root)
    main_row = next(row for row in report.rows if row.name == "main/mainmemory")

    assert main_row.runtime_read.startswith("FILE_ONLY")
    assert "FILE_ONLY_READ" in main_row.warnings
    assert "NO_FRAGMENTS" in main_row.warnings


def test_build_integrity_report_marks_stale_projection(tmp_path: Path) -> None:
    root = tmp_path / "home"
    memory_file = root / "workspace" / "memory_system" / "MAINMEMORY.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("# Main Memory\n\n- Old projection\n", encoding="utf-8")
    (root / "SHAREDMEMORY.md").write_text("", encoding="utf-8")

    repo = MemoryFragmentRepository(RuntimeStateDB(root / "state.db"))
    future_timestamp = memory_file.stat().st_mtime + 10
    repo.replace_for_scope(
        "mainmemory",
        [
            MemoryFragment(
                title="Main Memory",
                body="- Newer runtime fact",
                scope="mainmemory",
                agent_name="main",
                ulid="mf_newer",
                created_at=future_timestamp,
                updated_at=future_timestamp,
            )
        ],
        agent_name="main",
    )

    report = build_integrity_report(root)
    main_row = next(row for row in report.rows if row.name == "main/mainmemory")

    assert main_row.runtime_read.startswith("FRAGMENTS")
    assert "PROJECTION_STALE" in main_row.warnings


def test_check_integrity_can_print_json(tmp_path: Path, capsys: object) -> None:
    root = tmp_path / "home"
    (root / "workspace" / "memory_system").mkdir(parents=True)
    (root / "workspace" / "memory_system" / "MAINMEMORY.md").write_text(
        "# Main\n", encoding="utf-8"
    )
    (root / "SHAREDMEMORY.md").write_text("", encoding="utf-8")

    check_integrity(root, json_output=True)
    captured = capsys.readouterr()

    assert '"ductor_home"' in captured.out
    assert '"rows"' in captured.out
