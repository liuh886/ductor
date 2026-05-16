"""Memory integrity check for the fragment-backed memory files."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ductor_bot.runtime.state import MemoryFragmentRepository, RuntimeStateDB
from ductor_bot.workspace.paths import resolve_paths


@dataclass(frozen=True, slots=True)
class MemoryAuditRow:
    """One auditable memory source and its runtime fragment status."""

    name: str
    scope: str
    agent_name: str
    fragment_count: int
    conflict_count: int
    duplicate_groups: int
    file_status: str
    runtime_read: str
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemoryAuditReport:
    """Current memory audit report for root and sub-agent memory stores."""

    ductor_home: str
    rows: tuple[MemoryAuditRow, ...]

    @property
    def warning_count(self) -> int:
        return sum(len(row.warnings) for row in self.rows)


@dataclass(frozen=True, slots=True)
class _AuditSource:
    name: str
    file_path: Path
    home: Path
    scope: str
    agent_name: str = ""
    runtime_agent_name: str = ""
    runtime_read: str | None = None


def _repo_for(db_path: Path) -> MemoryFragmentRepository | None:
    if not db_path.exists():
        return None
    return MemoryFragmentRepository(RuntimeStateDB(db_path))


def _count_fragments(
    repo: MemoryFragmentRepository | None, scope: str, *, agent_name: str = ""
) -> int:
    if repo is None:
        return 0
    return len(repo.list_by_scope(scope, agent_name=agent_name))


def _count_conflicts(
    repo: MemoryFragmentRepository | None, scope: str, *, agent_name: str = ""
) -> int:
    if repo is None:
        return 0
    return len(repo.list_conflicts(scope, agent_name=agent_name))


def _rows_for_scope(
    repo: MemoryFragmentRepository | None,
    scope: str,
    *,
    agent_name: str = "",
) -> list[dict[str, object]]:
    if repo is None:
        return []
    return repo.list_by_scope(scope, agent_name=agent_name)


def _count_duplicate_groups(rows: list[dict[str, object]]) -> int:
    seen: dict[tuple[str, str, str, str], int] = {}
    for row in rows:
        key = (
            str(row.get("scope", "")).strip().lower(),
            str(row.get("agent_name", "")).strip().lower(),
            " ".join(str(row.get("title", "")).lower().split()),
            "\n".join(
                line.strip().lower()
                for line in str(row.get("body", "")).splitlines()
                if line.strip()
            ),
        )
        seen[key] = seen.get(key, 0) + 1
    return sum(1 for count in seen.values() if count > 1)


def _format_file_status(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    return f"EXISTS ({path.stat().st_size} bytes)"


def _render_runtime_status(
    home: Path,
    repo: MemoryFragmentRepository | None,
    *,
    agent_name: str,
) -> str:
    """Return a compact, read-only status for the prompt-facing memory read path."""
    paths = resolve_paths(ductor_home=home)
    main_rows = _rows_for_scope(repo, "mainmemory", agent_name=agent_name)
    shared_rows = _rows_for_scope(repo, "sharedmemory")
    fragment_chars = sum(len(str(row.get("body", ""))) for row in (*main_rows, *shared_rows))
    if main_rows or shared_rows:
        return f"FRAGMENTS ({fragment_chars} body chars)"

    try:
        main_text = paths.mainmemory_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        main_text = ""
    if main_text.strip():
        return f"FILE_ONLY ({len(main_text)} chars)"
    return "EMPTY"


def _latest_row_timestamp(rows: list[dict[str, object]]) -> float:
    latest = 0.0
    for row in rows:
        for key in ("updated_at", "created_at"):
            value = row.get(key, 0.0)
            if not isinstance(value, (str, bytes, bytearray, int, float)):
                continue
            try:
                latest = max(latest, float(value))
            except (TypeError, ValueError):
                continue
    return latest


def _projection_warnings(
    file_path: Path,
    rows: list[dict[str, object]],
    *,
    runtime_read: str,
    conflicts: int,
    duplicate_groups: int,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if conflicts > 0:
        warnings.append("CONFLICTS")
    if duplicate_groups > 0:
        warnings.append("DUPLICATES")
    if runtime_read.startswith("FILE_ONLY"):
        warnings.append("FILE_ONLY_READ")
    warnings.extend(_projection_freshness_warnings(file_path, rows))
    return tuple(warnings)


def _projection_freshness_warnings(
    file_path: Path,
    rows: list[dict[str, object]],
) -> list[str]:
    if not file_path.exists():
        return ["PROJECTION_MISSING"] if rows else []
    if not rows:
        return ["NO_FRAGMENTS"] if file_path.stat().st_size > 0 else []

    try:
        file_mtime = file_path.stat().st_mtime
    except OSError:
        return ["FILE_STAT_FAILED"]

    latest_row_update = _latest_row_timestamp(rows)
    # Keep a small tolerance for coarse filesystem timestamp precision.
    if latest_row_update > file_mtime + 1:
        return ["PROJECTION_STALE"]
    if file_mtime > latest_row_update + 1:
        return ["FRAGMENTS_STALE"]
    return []


def _audit_row(
    source: _AuditSource,
    repo: MemoryFragmentRepository | None,
) -> MemoryAuditRow:
    rows = _rows_for_scope(repo, source.scope, agent_name=source.agent_name)
    conflicts = _count_conflicts(repo, source.scope, agent_name=source.agent_name)
    duplicate_groups = _count_duplicate_groups(rows)
    runtime_status = source.runtime_read
    if runtime_status is None:
        agent_name = source.runtime_agent_name or source.agent_name
        runtime_status = _render_runtime_status(source.home, repo, agent_name=agent_name)
    warnings = _projection_warnings(
        source.file_path,
        rows,
        runtime_read=runtime_status,
        conflicts=conflicts,
        duplicate_groups=duplicate_groups,
    )
    return MemoryAuditRow(
        name=source.name,
        scope=source.scope,
        agent_name=source.agent_name,
        fragment_count=len(rows),
        conflict_count=conflicts,
        duplicate_groups=duplicate_groups,
        file_status=_format_file_status(source.file_path),
        runtime_read=runtime_status,
        warnings=warnings,
    )


def build_integrity_report(ductor_home: Path | str | None = None) -> MemoryAuditReport:
    """Build fragment/file alignment report for root and sub-agent memory stores."""
    paths = resolve_paths(ductor_home=ductor_home)
    root_home = (
        paths.ductor_home.parent.parent
        if paths.ductor_home.parent.name == "agents"
        else paths.ductor_home
    )

    root_repo = _repo_for(root_home / "state.db")
    report_rows: list[MemoryAuditRow] = []

    shared_file = root_home / "SHAREDMEMORY.md"
    report_rows.append(
        _audit_row(
            _AuditSource(
                name="GLOBAL/sharedmemory",
                file_path=shared_file,
                home=root_home,
                scope="sharedmemory",
                runtime_agent_name="main",
                runtime_read="N/A",
            ),
            root_repo,
        )
    )

    mainmemory_file = root_home / "workspace" / "memory_system" / "MAINMEMORY.md"
    report_rows.append(
        _audit_row(
            _AuditSource(
                name="main/mainmemory",
                file_path=mainmemory_file,
                home=root_home,
                scope="mainmemory",
                agent_name="main",
                runtime_agent_name="main",
            ),
            root_repo,
        )
    )

    agents_dir = root_home / "agents"
    if agents_dir.exists():
        for agent_dir in sorted(path for path in agents_dir.iterdir() if path.is_dir()):
            repo = _repo_for(agent_dir / "state.db")
            main_file = agent_dir / "workspace" / "memory_system" / "MAINMEMORY.md"
            report_rows.append(
                _audit_row(
                    _AuditSource(
                        name=agent_dir.name + "/mainmemory",
                        file_path=main_file,
                        home=agent_dir,
                        scope="mainmemory",
                        agent_name=agent_dir.name,
                        runtime_agent_name=agent_dir.name,
                    ),
                    repo,
                )
            )

    return MemoryAuditReport(ductor_home=str(root_home), rows=tuple(report_rows))


def render_integrity_report(
    report: MemoryAuditReport,
    *,
    warnings_only: bool = True,
) -> str:
    """Render a short prompt-safe integrity summary without memory bodies."""
    total_fragments = sum(row.fragment_count for row in report.rows)
    total_conflicts = sum(row.conflict_count for row in report.rows)
    total_duplicate_groups = sum(row.duplicate_groups for row in report.rows)
    issue_rows = [
        row
        for row in report.rows
        if row.warnings or row.conflict_count > 0 or row.duplicate_groups > 0
    ]

    if warnings_only and not issue_rows:
        return (
            "Memory integrity: OK "
            f"(sources={len(report.rows)} fragments={total_fragments} "
            f"conflicts={total_conflicts} duplicate_groups={total_duplicate_groups} "
            f"warnings={report.warning_count})"
        )

    rows = issue_rows if warnings_only else list(report.rows)
    if not rows:
        return (
            "Memory integrity: OK "
            f"(sources={len(report.rows)} fragments={total_fragments} "
            f"conflicts={total_conflicts} duplicate_groups={total_duplicate_groups} "
            f"warnings={report.warning_count})"
        )

    lines = ["Memory integrity warnings:" if warnings_only else "Memory integrity report:"]
    for row in rows:
        warning_text = ",".join(row.warnings) if row.warnings else "OK"
        agent = row.agent_name or "global"
        lines.append(
            f"- {row.name} scope={row.scope} agent={agent} "
            f"fragments={row.fragment_count} conflicts={row.conflict_count} "
            f"duplicate_groups={row.duplicate_groups} file={row.file_status} "
            f"runtime={row.runtime_read} warnings={warning_text}"
        )
    return "\n".join(lines)


def _print_table(report: MemoryAuditReport) -> None:
    print(
        f"{'Agent/Scope':<30} | {'Fragments':<10} | {'Conflicts':<10} | "
        f"{'Dupes':<6} | {'File Status':<20} | {'Runtime Read':<22} | Warnings"
    )
    print("-" * 112)
    for row in report.rows:
        print(
            f"{row.name:<30} | {row.fragment_count:<10} | {row.conflict_count:<10} | "
            f"{row.duplicate_groups:<6} | {row.file_status:<20} | "
            f"{row.runtime_read:<22} | {', '.join(row.warnings) or 'OK'}"
        )


def check_integrity(ductor_home: Path | str | None = None, *, json_output: bool = False) -> None:
    """Print fragment/file alignment for root and sub-agent memory stores."""
    report = build_integrity_report(ductor_home)
    if json_output:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        return
    _print_table(report)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Audit ductor memory fragment/file alignment.")
    parser.add_argument("--ductor-home", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()
    check_integrity(args.ductor_home, json_output=args.json_output)


if __name__ == "__main__":
    _main()
