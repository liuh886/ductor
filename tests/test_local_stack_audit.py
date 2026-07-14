"""Tests for the minimal local-stack audit."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import local_stack_audit as audit


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_git_checks_report_clean_linear_stack(tmp_path: Path, monkeypatch) -> None:
    outputs = {
        ("branch", "--show-current"): "codex/local-stack-v019-rebuild",
        ("status", "--porcelain"): "",
        ("rev-parse", "HEAD"): "head",
        ("rev-parse", audit.UPSTREAM_BASE): "base",
        ("rev-parse", "main"): "base",
        ("rev-parse", "origin/main"): "base",
        ("rev-list", "--count", f"{audit.UPSTREAM_BASE}..HEAD"): "24",
        ("rev-list", "--count", f"HEAD..{audit.UPSTREAM_BASE}"): "0",
    }
    monkeypatch.setattr(audit, "_git_output", lambda _repo, *args: outputs.get(args, ""))
    monkeypatch.setattr(audit, "_run_git", lambda _repo, *_args: (0, ""))

    results = audit.git_checks(tmp_path)

    assert all(result.ok for result in results)


def test_runtime_config_checks_reject_enabled_automatic_memory(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "config" / "config.json",
        {"memory_flush": {"enabled": True}, "heartbeat": {"enabled": False}},
    )
    vault_index = tmp_path / "workspace" / "memory_system" / "vault_index.db"
    vault_index.parent.mkdir(parents=True)
    vault_index.touch()

    results = audit.runtime_config_checks(tmp_path)
    by_label = {result.label: result for result in results}

    assert by_label["memory_flush.enabled"].ok is False
    assert by_label["heartbeat.enabled"].ok is True
    assert by_label["memory_context.enabled"].ok is True
    assert by_label["append_system_prompt_files"].ok is True
    assert by_label["vault index"].ok is True


def test_runtime_config_checks_reject_ambient_prompt_files(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "config" / "config.json",
        {"append_system_prompt_files": ["PERSONA.md"]},
    )
    vault_index = tmp_path / "workspace" / "memory_system" / "vault_index.db"
    vault_index.parent.mkdir(parents=True)
    vault_index.touch()

    results = audit.runtime_config_checks(tmp_path)
    by_label = {result.label: result for result in results}

    assert by_label["append_system_prompt_files"].ok is False
    assert by_label["append_system_prompt_files"].detail == "configured=1"


def test_legacy_session_checks_scan_only_active_agents(tmp_path: Path) -> None:
    _write_json(tmp_path / "sessions.json", {"chat": {"lineage_id": "old"}})
    _write_json(
        tmp_path / "agents.json",
        {"not": "a list"},
    )

    assert audit.legacy_session_checks(tmp_path)[0].ok is False

    (tmp_path / "agents.json").write_text(json.dumps([{"name": "active"}]), encoding="utf-8")
    _write_json(tmp_path / "agents" / "active" / "sessions.json", {"chat": {"model": "x"}})
    _write_json(
        tmp_path / "agents" / "retired" / "sessions.json",
        {"chat": {"lineage_parent": "ignored"}},
    )

    result = audit.legacy_session_checks(tmp_path)[0]
    assert result.ok is False
    assert result.detail == "fields=1 files=2"

    _write_json(tmp_path / "sessions.json", {"chat": {"model": "x"}})
    result = audit.legacy_session_checks(tmp_path)[0]
    assert result.ok is True
    assert result.detail == "fields=0 files=2"


def test_legacy_memory_surface_checks_reject_retired_state(tmp_path: Path) -> None:
    (tmp_path / "agents.json").write_text(json.dumps([{"name": "active"}]), encoding="utf-8")
    _write_json(tmp_path / "config" / "config.json", {"state_backend": "dual"})
    _write_json(tmp_path / "agents" / "active" / "config" / "config.json", {})
    stale_tool = tmp_path / "agents" / "active" / "workspace" / "tools" / "memory"
    stale_tool.mkdir(parents=True)

    results = audit.legacy_memory_surface_checks(tmp_path)
    by_label = {result.label: result for result in results}

    assert by_label["legacy memory surfaces"].ok is False
    assert by_label["legacy memory surfaces"].detail == "paths=1 homes=2"
    assert by_label["legacy memory config"].ok is False
    assert by_label["legacy memory config"].detail == "keys=1 files=2"

    stale_tool.rmdir()
    _write_json(tmp_path / "config" / "config.json", {})
    results = audit.legacy_memory_surface_checks(tmp_path)
    assert all(result.ok for result in results)


def test_legacy_memory_surface_checks_reject_shared_memory_file(tmp_path: Path) -> None:
    (tmp_path / "agents.json").write_text("[]", encoding="utf-8")
    _write_json(tmp_path / "config" / "config.json", {})
    (tmp_path / "SHAREDMEMORY.md").write_text("# Legacy", encoding="utf-8")

    result = audit.legacy_memory_surface_checks(tmp_path)[0]

    assert result.ok is False
    assert result.detail == "paths=1 homes=1"


def test_runtime_identity_checks_detect_stale_checkout(tmp_path: Path, monkeypatch) -> None:
    _write_json(
        tmp_path / audit.RUNTIME_IDENTITY_FILENAME,
        {
            "framework_root": str(tmp_path),
            "git_head": "old-head",
            "pid": 123,
            "started_at": "2026-07-13T00:00:00+00:00",
        },
    )
    (tmp_path / "bot.pid").write_text("123", encoding="utf-8")
    monkeypatch.setattr(audit, "_git_output", lambda *_args: "new-head")

    results = audit.runtime_identity_checks(tmp_path, tmp_path)
    by_label = {result.label: result for result in results}

    assert by_label["runtime checkout"].ok is True
    assert by_label["runtime commit"].ok is False
    assert by_label["runtime pid lock"].ok is True
    assert by_label["runtime start time"].ok is True


def test_runtime_identity_checks_fail_when_missing(tmp_path: Path) -> None:
    results = audit.runtime_identity_checks(tmp_path, tmp_path)

    assert len(results) == 1
    assert results[0].ok is False


def test_maintenance_file_checks_require_portable_pm2_config(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "local-stack-maintenance.md").touch()
    (tmp_path / "docs" / "local-stack-origin-main-inventory.md").touch()
    (tmp_path / "ecosystem.config.js").write_text(
        "cwd: __dirname\ndisable_logs: true\n", encoding="utf-8"
    )

    assert all(result.ok for result in audit.maintenance_file_checks(tmp_path))


def test_render_marks_failures() -> None:
    output = audit.render(
        [
            audit.CheckResult(True, "good", "ok"),
            audit.CheckResult(False, "bad", "stale"),
        ]
    )

    assert "[PASS] good: ok" in output
    assert "[FAIL] bad: stale" in output
