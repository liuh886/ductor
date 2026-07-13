# ruff: noqa: INP001

"""Audit or atomically rebuild the read-only zhihaol vault index."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SKIP_DIR_NAMES = frozenset({"node_modules", "__pycache__"})
_DEFAULT_MAX_CONTENT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class VaultRecord:
    ulid: str
    title: str
    content: str
    path: str
    created_at: str
    checksum: str
    active_project: str | None


@dataclass(frozen=True)
class VaultScan:
    records: tuple[VaultRecord, ...]
    eligible_count: int
    null_byte_paths: tuple[str, ...]
    decode_error_paths: tuple[str, ...]
    truncated_paths: tuple[str, ...]


def _is_eligible(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return not any(
        part.startswith(".") or part.casefold() in _SKIP_DIR_NAMES for part in relative.parts[:-1]
    )


def _extract_title(content: str, fallback: str) -> str:
    frontmatter = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", content, re.DOTALL)
    if frontmatter:
        title_match = re.search(
            r"(?im)^title\s*:\s*[\"']?(.*?)[\"']?\s*$",
            frontmatter.group(1),
        )
        if title_match and title_match.group(1).strip():
            return title_match.group(1).strip()
    heading = re.search(r"(?m)^#\s+(.+?)\s*$", content)
    return heading.group(1).strip() if heading else fallback


def _stable_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:26].upper()


def scan_vault(
    vault_root: Path,
    *,
    max_content_bytes: int = _DEFAULT_MAX_CONTENT_BYTES,
) -> VaultScan:
    """Read eligible Markdown files without modifying the vault."""
    records: list[VaultRecord] = []
    null_byte_paths: list[str] = []
    decode_error_paths: list[str] = []
    truncated_paths: list[str] = []
    eligible_count = 0
    for path in sorted(vault_root.rglob("*.md")):
        if not path.is_file() or not _is_eligible(path, vault_root):
            continue
        eligible_count += 1
        relative = path.relative_to(vault_root).as_posix()
        try:
            raw = path.read_bytes()
        except OSError:
            decode_error_paths.append(relative)
            continue
        if b"\x00" in raw:
            null_byte_paths.append(relative)
            continue
        try:
            full_content = raw.decode("utf-8")
        except UnicodeDecodeError:
            decode_error_paths.append(relative)
            continue
        content = full_content
        if len(raw) > max_content_bytes:
            content = raw[:max_content_bytes].decode("utf-8", errors="ignore")
            truncated_paths.append(relative)
        stat = path.stat()
        parts = Path(relative).parts
        active_project = parts[1] if len(parts) > 1 and parts[0] == "100_Project" else None
        records.append(
            VaultRecord(
                ulid=_stable_id(relative),
                title=_extract_title(full_content[:65536], path.stem),
                content=content,
                path=relative,
                created_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                checksum=hashlib.sha256(raw).hexdigest(),
                active_project=active_project,
            )
        )
    return VaultScan(
        records=tuple(records),
        eligible_count=eligible_count,
        null_byte_paths=tuple(null_byte_paths),
        decode_error_paths=tuple(decode_error_paths),
        truncated_paths=tuple(truncated_paths),
    )


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE notes (
            ulid TEXT PRIMARY KEY,
            title TEXT,
            content TEXT,
            path TEXT UNIQUE,
            created_at TEXT,
            importance_score REAL,
            tags TEXT,
            active_project TEXT,
            role TEXT,
            checksum TEXT,
            task_ulid TEXT
        );
        CREATE INDEX idx_active_project ON notes(active_project);
        CREATE INDEX idx_project_role ON notes(active_project, role);
        CREATE INDEX idx_role ON notes(role);
        CREATE VIRTUAL TABLE notes_fts USING fts5(
            ulid UNINDEXED,
            title,
            content,
            tokenize='porter unicode61'
        );
        CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
            INSERT INTO notes_fts(ulid, title, content) VALUES (new.ulid, new.title, new.content);
        END;
        CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
            DELETE FROM notes_fts WHERE ulid = old.ulid;
        END;
        CREATE TRIGGER notes_au AFTER UPDATE ON notes BEGIN
            DELETE FROM notes_fts WHERE ulid = old.ulid;
            INSERT INTO notes_fts(ulid, title, content) VALUES (new.ulid, new.title, new.content);
        END;
        """
    )


def build_index(index_path: Path, scan: VaultScan) -> None:
    """Create a complete index at a new path."""
    if index_path.exists():
        raise FileExistsError(index_path)
    conn = sqlite3.connect(index_path)
    try:
        _create_schema(conn)
        conn.executemany(
            """
            INSERT INTO notes (
                ulid, title, content, path, created_at, importance_score,
                tags, active_project, role, checksum, task_ulid
            ) VALUES (?, ?, ?, ?, ?, 0.0, '[]', ?, '', ?, NULL)
            """,
            (
                (
                    record.ulid,
                    record.title,
                    record.content,
                    record.path,
                    record.created_at,
                    record.active_project,
                    record.checksum,
                )
                for record in scan.records
            ),
        )
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        note_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0]
        if integrity != ("ok",) or note_count != len(scan.records) or fts_count != note_count:
            raise RuntimeError(
                f"index verification failed: integrity={integrity} notes={note_count} fts={fts_count}"
            )
        conn.commit()
    finally:
        conn.close()


def index_paths(index_path: Path) -> set[str]:
    if not index_path.is_file():
        return set()
    uri = f"file:{index_path.resolve().as_posix()}?mode=ro"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        return {str(row[0]).replace("\\", "/") for row in conn.execute("SELECT path FROM notes")}
    except sqlite3.Error:
        return set()
    finally:
        if conn is not None:
            conn.close()


def coverage_report(scan: VaultScan, index_path: Path) -> dict[str, object]:
    source_paths = {record.path for record in scan.records}
    indexed_paths = index_paths(index_path)
    return {
        "eligible_markdown": scan.eligible_count,
        "indexable_markdown": len(source_paths),
        "indexed_notes": len(indexed_paths),
        "missing_paths": sorted(source_paths - indexed_paths),
        "stale_paths": sorted(indexed_paths - source_paths),
        "null_byte_paths": list(scan.null_byte_paths),
        "decode_error_paths": list(scan.decode_error_paths),
        "truncated_paths": list(scan.truncated_paths),
    }


def replace_index(index_path: Path, scan: VaultScan, backup_root: Path) -> Path | None:
    """Build beside the live DB, verify, back up, then atomically replace it."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = index_path.with_name(f".{index_path.name}.{os.getpid()}.tmp")
    backup_path: Path | None = None
    try:
        build_index(temporary, scan)
        if index_path.exists():
            stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
            backup_dir = backup_root / f"vault-index-{stamp}"
            backup_dir.mkdir(parents=True, exist_ok=False)
            backup_path = backup_dir / index_path.name
            shutil.copy2(index_path, backup_path)
        temporary.replace(index_path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument(
        "--index",
        type=Path,
        default=Path("~/.ductor/workspace/memory_system/vault_index.db").expanduser(),
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=Path("~/.ductor/archive").expanduser(),
    )
    parser.add_argument("--max-content-bytes", type=int, default=_DEFAULT_MAX_CONTENT_BYTES)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    vault = args.vault.expanduser().resolve()
    index = args.index.expanduser().resolve()
    if not vault.is_dir():
        parser.error(f"vault directory does not exist: {vault}")
    scan = scan_vault(vault, max_content_bytes=args.max_content_bytes)
    before = coverage_report(scan, index)
    backup: Path | None = None
    if args.apply:
        backup = replace_index(index, scan, args.backup_root.expanduser().resolve())
    report = {
        "vault": str(vault),
        "index": str(index),
        "applied": args.apply,
        "backup": str(backup) if backup else None,
        "before": before,
        "after": coverage_report(scan, index),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    after = report["after"]
    assert isinstance(after, dict)
    return 0 if not after["missing_paths"] and not after["stale_paths"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
