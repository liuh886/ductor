"""File storage helpers: sanitization, destination preparation, and indexing."""

from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_UNSAFE_CHARS_RE = re.compile(r'[/\\<>:"|?*\x00]')
_INDEX_SKIP = frozenset({"_index.yaml", "CLAUDE.md", "AGENTS.md"})
_INDEX_LOCK = threading.RLock()


def sanitize_filename(name: str) -> str:
    r"""Remove path separators, null bytes, and OS-illegal characters.

    Strips characters forbidden on Windows (``< > : " | ? *``),
    path separators (``/`` ``\\``), and null bytes on all platforms.
    """
    name = _UNSAFE_CHARS_RE.sub("_", name)
    while "__" in name:
        name = name.replace("__", "_")
    return name.strip("_. ")[:120] or "file"


def prepare_destination(base_dir: Path, file_name: str) -> Path:
    """Create date directory and return a non-colliding destination path."""
    day_dir = base_dir / datetime.now(tz=UTC).strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    dest = day_dir / file_name
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = day_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    return dest


def _is_date_dir_name(name: str) -> bool:
    return len(name) == 10 and name[4] == "-"


def _file_entry(path: Path) -> dict[str, object]:
    """Build one index entry for *path* (stat + MIME sniff)."""
    from ductor_bot.files.tags import guess_mime

    stat = path.stat()
    return {
        "name": path.name,
        "type": guess_mime(path),
        "size": stat.st_size,
        "received": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    }


def _write_index(index_path: Path, tree: dict[str, list[dict[str, object]]]) -> None:
    """Serialize *tree* to ``_index.yaml`` and log the result."""
    import yaml

    total = sum(len(files) for files in tree.values())
    index = {
        "last_updated": datetime.now(tz=UTC).isoformat(),
        "total_files": total,
        "tree": tree,
    }
    index_path.write_text(
        yaml.safe_dump(index, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    logger.debug("Index updated: %d files across %d days", total, len(tree))


def update_index(base_dir: Path) -> None:
    """Rebuild ``_index.yaml`` by scanning all date subdirectories.

    Works for any transport's file directory (Telegram, Matrix, API).
    """
    tree: dict[str, list[dict[str, object]]] = {}

    with _INDEX_LOCK:
        for entry in sorted(base_dir.iterdir()):
            if not entry.is_dir() or not _is_date_dir_name(entry.name):
                continue
            files: list[dict[str, object]] = []
            for f in sorted(entry.iterdir()):
                if not f.is_file() or f.name in _INDEX_SKIP:
                    continue
                files.append(_file_entry(f))
            if files:
                tree[entry.name] = files

        _write_index(base_dir / "_index.yaml", tree)


def update_index_entry(base_dir: Path, file_path: Path) -> None:
    """Insert or replace a single *file_path* entry in ``_index.yaml``.

    Avoids the full-directory rescan (stat + MIME sniff for every stored file)
    that ``update_index`` performs, which grows with the media history. Falls
    back to a full rebuild whenever the index is missing, unreadable, or the
    file does not live in a dated subdirectory of *base_dir*.
    """
    import yaml

    day_dir = file_path.parent
    usable = file_path.is_file() and _is_date_dir_name(day_dir.name) and day_dir.parent == base_dir
    if not usable:
        update_index(base_dir)
        return

    index_path = base_dir / "_index.yaml"
    with _INDEX_LOCK:
        tree: dict[str, list[dict[str, object]]] | None = None
        try:
            raw = yaml.safe_load(index_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            raw = None
        if isinstance(raw, dict) and isinstance(raw.get("tree"), dict):
            tree = {}
            for day, rows in raw["tree"].items():
                if not isinstance(day, str) or not isinstance(rows, list):
                    tree = None
                    break
                tree[day] = [row for row in rows if isinstance(row, dict)]
        if tree is None:
            update_index(base_dir)
            return

        bucket = tree.setdefault(day_dir.name, [])
        entry = _file_entry(file_path)
        for index, existing in enumerate(bucket):
            if existing.get("name") == file_path.name:
                bucket[index] = entry
                break
        else:
            bucket.append(entry)
        tree[day_dir.name] = sorted(bucket, key=lambda item: str(item.get("name", "")))

        _write_index(index_path, tree)
