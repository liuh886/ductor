"""Schema helpers for the runtime SQLite state layer."""

from __future__ import annotations

from pathlib import Path

_SCHEMA_DIR = Path(__file__).resolve().parent / "migrations"
_INITIAL_SCHEMA = _SCHEMA_DIR / "001_initial.sql"


def load_schema_sql() -> str:
    """Return the combined runtime-state schema SQL from all migration files."""
    sql_files = sorted(_SCHEMA_DIR.glob("*.sql"))
    parts = [f.read_text(encoding="utf-8") for f in sql_files]
    return "\n\n".join(parts)

