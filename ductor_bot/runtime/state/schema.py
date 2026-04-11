"""Schema helpers for the runtime SQLite state layer."""

from __future__ import annotations

from pathlib import Path

_SCHEMA_DIR = Path(__file__).resolve().parent / "migrations"
_INITIAL_SCHEMA = _SCHEMA_DIR / "001_initial.sql"


def load_schema_sql() -> str:
    """Return the initial runtime-state schema SQL."""
    return _INITIAL_SCHEMA.read_text(encoding="utf-8")

