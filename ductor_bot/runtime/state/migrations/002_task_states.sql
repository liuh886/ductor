CREATE TABLE IF NOT EXISTS task_states (
    task_id TEXT PRIMARY KEY,
    storage_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    current_step INTEGER NOT NULL DEFAULT 0,
    total_steps INTEGER,
    step_label TEXT NOT NULL DEFAULT '',
    context_snapshot_json TEXT NOT NULL DEFAULT '{}',
    error_log TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT (unixepoch()),
    updated_at REAL NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (storage_key) REFERENCES sessions(storage_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_states_storage_key ON task_states(storage_key);
