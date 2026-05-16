"""Initialize or update the runtime SQLite database schema."""

from contextlib import closing
from pathlib import Path

from ductor_bot.runtime.state.db import RuntimeStateDB


def main() -> None:
    db_path = Path("~/.ductor/state.db").expanduser()
    print(f"Initializing database at: {db_path}")

    db = RuntimeStateDB(db_path)

    # Verify tables
    with closing(db.connect()) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"Existing tables: {', '.join(tables)}")

        if "task_states" in tables:
            print("[PASS] task_states table successfully created/verified.")
        else:
            print("[FAIL] task_states table missing!")

if __name__ == "__main__":
    main()
