"""Cleansing Script: Migrates operational state from MAINMEMORY to task_states table."""

import json
import os
import re
from contextlib import closing
from pathlib import Path

from ductor_bot.runtime.state.db import RuntimeStateDB

# Keywords that indicate operational state rather than long-term facts
STATE_KEYWORDS = [
    r"current step",
    r"progress:",
    r"working on",
    r"task state",
    r"steps done",
    r"currently processing",
    r"last error:",
    r"retry count"
]

def main():
    db_path = Path(os.path.expanduser("~/.ductor/state.db"))
    main_mem_path = Path(os.path.expanduser("~/.ductor/workspace/memory_system/MAINMEMORY.md"))

    if not main_mem_path.exists():
        print(f"MAINMEMORY.md not found at {main_mem_path}. Skipping.")
        return

    print(f"Scanning {main_mem_path} for operational state clutter...")

    content = main_mem_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    new_memory_lines = []
    migrated_states = []

    # Simple block-based heuristic: scan for sections or bullet points
    # In this implementation, we look for bullet points or paragraphs containing keywords
    for line in lines:
        is_state = any(re.search(kw, line, re.IGNORECASE) for kw in STATE_KEYWORDS)

        if is_state:
            print(f"Found state clutter: {line.strip()}")
            migrated_states.append(line.strip())
        else:
            new_memory_lines.append(line)

    if not migrated_states:
        print("No state clutter found in MAINMEMORY.md. Excellent.")
        return

    # 1. Update MAINMEMORY.md (Physical Cleansing)
    print(f"Cleansing {len(migrated_states)} items from MAINMEMORY.md...")
    main_mem_path.write_text("\n".join(new_memory_lines), encoding="utf-8")

    # 2. Database Record (Governance Compliance)
    db = RuntimeStateDB(db_path)
    with closing(db.connect()) as conn:
        for state_text in migrated_states:
            # We create a generic migration task entry for auditing
            # Real-world usage would use structured fields
            task_id = f"migrated-{os.urandom(4).hex()}"
            conn.execute(
                "INSERT INTO task_states (task_id, storage_key, status, step_label, context_snapshot_json) VALUES (?, ?, ?, ?, ?)",
                (task_id, "global", "MIGRATED", state_text[:50], json.dumps({"full_text": state_text}))
            )
        conn.commit()

    print(f"[SUCCESS] Cleaned {len(migrated_states)} items. Migrated to state.db audit log.")

if __name__ == "__main__":
    main()
