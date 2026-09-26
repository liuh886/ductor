"""Tests for shared file storage utilities."""

from __future__ import annotations

from pathlib import Path

import yaml

from ductor_bot.files.storage import (
    prepare_destination,
    sanitize_filename,
    update_index,
    update_index_entry,
)


class TestSanitizeFilename:
    def test_removes_slashes(self) -> None:
        assert sanitize_filename("path/to/file.txt") == "path_to_file.txt"

    def test_removes_null_bytes(self) -> None:
        assert sanitize_filename("file\x00name.txt") == "file_name.txt"

    def test_collapses_underscores(self) -> None:
        assert sanitize_filename("a___b.txt") == "a_b.txt"

    def test_truncates_long_names(self) -> None:
        result = sanitize_filename("x" * 200)
        assert len(result) <= 120

    def test_empty_returns_file(self) -> None:
        assert sanitize_filename("...") == "file"

    def test_backslashes(self) -> None:
        assert sanitize_filename("C:\\Users\\file.txt") == "C_Users_file.txt"

    def test_windows_illegal_chars(self) -> None:
        assert sanitize_filename("file<script>.txt") == "file_script_.txt"
        assert sanitize_filename("data|pipe.csv") == "data_pipe.csv"
        assert sanitize_filename('note"s.txt') == "note_s.txt"
        assert sanitize_filename("who?.txt") == "who_.txt"
        assert sanitize_filename("star*.log") == "star_.log"


class TestPrepareDestination:
    def test_creates_date_dir(self, tmp_path: Path) -> None:
        dest = prepare_destination(tmp_path, "test.jpg")
        assert dest.parent.exists()
        assert len(dest.parent.name) == 10
        assert dest.parent.name[4] == "-"

    def test_collision_avoidance(self, tmp_path: Path) -> None:
        dest1 = prepare_destination(tmp_path, "test.jpg")
        dest1.touch()

        dest2 = prepare_destination(tmp_path, "test.jpg")
        assert dest2 != dest1
        assert "test_1.jpg" in dest2.name

    def test_multiple_collisions(self, tmp_path: Path) -> None:
        dest1 = prepare_destination(tmp_path, "file.pdf")
        dest1.touch()

        dest2 = prepare_destination(tmp_path, "file.pdf")
        dest2.touch()

        dest3 = prepare_destination(tmp_path, "file.pdf")
        assert dest3.name == "file_2.pdf"


def _read_index(base_dir: Path) -> dict[str, object]:
    return yaml.safe_load((base_dir / "_index.yaml").read_text(encoding="utf-8"))


class TestUpdateIndexEntry:
    def test_appends_new_file_keeping_existing_entries(self, tmp_path: Path) -> None:
        day = tmp_path / "2025-06-15"
        day.mkdir()
        (day / "a.jpg").write_bytes(b"x" * 10)
        update_index(tmp_path)

        (day / "b.ogg").write_bytes(b"y" * 20)
        update_index_entry(tmp_path, day / "b.ogg")

        data = _read_index(tmp_path)
        names = {entry["name"] for entry in data["tree"]["2025-06-15"]}
        assert names == {"a.jpg", "b.ogg"}
        assert data["total_files"] == 2

    def test_replaces_existing_entry_without_duplicating(self, tmp_path: Path) -> None:
        day = tmp_path / "2025-06-15"
        day.mkdir()
        target = day / "a.jpg"
        target.write_bytes(b"x" * 10)
        update_index(tmp_path)

        target.write_bytes(b"x" * 30)
        update_index_entry(tmp_path, target)

        data = _read_index(tmp_path)
        entries = data["tree"]["2025-06-15"]
        assert len(entries) == 1
        assert entries[0]["size"] == 30
        assert data["total_files"] == 1

    def test_builds_index_when_missing(self, tmp_path: Path) -> None:
        day = tmp_path / "2025-06-15"
        day.mkdir()
        target = day / "a.jpg"
        target.write_bytes(b"x" * 10)

        update_index_entry(tmp_path, target)

        data = _read_index(tmp_path)
        assert data["total_files"] == 1
        assert data["tree"]["2025-06-15"][0]["name"] == "a.jpg"

    def test_non_date_dir_falls_back_to_rebuild(self, tmp_path: Path) -> None:
        odd = tmp_path / "random"
        odd.mkdir()
        target = odd / "x.txt"
        target.write_text("x", encoding="utf-8")

        update_index_entry(tmp_path, target)

        data = _read_index(tmp_path)
        assert data["tree"] == {}
        assert data["total_files"] == 0
