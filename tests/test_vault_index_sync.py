"""Tests for the neutral zhihaol index rebuild utility."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.vault_index_sync import (
    build_index,
    coverage_report,
    replace_index,
    scan_vault,
)


def test_scan_vault_excludes_runtime_dirs_and_reports_bad_files(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects").mkdir(parents=True)
    (vault / "projects" / "valid.md").write_text("# Current title\nBody", encoding="utf-8")
    (vault / "projects" / "nul.md").write_bytes(b"bad\x00content")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "hidden.md").write_text("hidden", encoding="utf-8")
    (vault / "node_modules").mkdir()
    (vault / "node_modules" / "readme.md").write_text("dependency", encoding="utf-8")
    (vault / "large.md").write_text("# Large\n" + "x" * 100, encoding="utf-8")

    scan = scan_vault(vault, max_content_bytes=32)

    assert scan.eligible_count == 3
    assert [record.path for record in scan.records] == ["large.md", "projects/valid.md"]
    assert scan.null_byte_paths == ("projects/nul.md",)
    assert scan.truncated_paths == ("large.md",)
    assert scan.records[1].title == "Current title"


def test_build_index_and_coverage_report(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "one.md").write_text("---\ntitle: One Note\n---\nBody", encoding="utf-8")
    scan = scan_vault(vault)
    index = tmp_path / "vault_index.db"

    build_index(index, scan)

    with sqlite3.connect(index) as conn:
        assert conn.execute("SELECT title, path FROM notes").fetchall() == [("One Note", "one.md")]
        assert conn.execute("SELECT COUNT(*) FROM notes_fts").fetchone() == (1,)
    assert coverage_report(scan, index)["missing_paths"] == []


def test_coverage_report_detects_content_changes_at_existing_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "one.md"
    note.write_text("# One\nOriginal", encoding="utf-8")
    index = tmp_path / "vault_index.db"
    build_index(index, scan_vault(vault))

    note.write_text("# One\nChanged", encoding="utf-8")
    report = coverage_report(scan_vault(vault), index)

    assert report["missing_paths"] == []
    assert report["stale_paths"] == []
    assert report["changed_paths"] == ["one.md"]


def test_scan_vault_hashes_full_file_with_bounded_content(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "large.md"
    note.write_text("# Large\n" + "x" * 128, encoding="utf-8")

    first = scan_vault(vault, max_content_bytes=32)
    note.write_text("# Large\n" + "x" * 127 + "y", encoding="utf-8")
    second = scan_vault(vault, max_content_bytes=32)

    assert len(first.records[0].content.encode()) <= 32
    assert first.records[0].checksum != second.records[0].checksum


def test_replace_index_backs_up_and_removes_stale_paths(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "current.md").write_text("# Current", encoding="utf-8")
    scan = scan_vault(vault)
    index = tmp_path / "runtime" / "vault_index.db"
    index.parent.mkdir()
    conn = sqlite3.connect(index)
    try:
        conn.execute("CREATE TABLE notes (path TEXT)")
        conn.execute("INSERT INTO notes VALUES ('stale.md')")
        conn.commit()
    finally:
        conn.close()

    backup = replace_index(index, scan, tmp_path / "archive")

    assert backup is not None
    assert backup.is_file()
    report = coverage_report(scan, index)
    assert report["missing_paths"] == []
    assert report["stale_paths"] == []
