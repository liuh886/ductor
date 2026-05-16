CREATE TABLE IF NOT EXISTS outcome_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT '',
    session_storage_key TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    process_id INTEGER,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    flow TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    failure_class TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    empty_result INTEGER NOT NULL DEFAULT 0,
    recovery_count INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL,
    confidence REAL NOT NULL DEFAULT 1.0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    learned INTEGER NOT NULL DEFAULT 0,
    learned_at REAL,
    created_at REAL NOT NULL DEFAULT (unixepoch()),
    updated_at REAL NOT NULL DEFAULT (unixepoch()),
    UNIQUE (source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_outcome_events_learned_updated
    ON outcome_events(learned, updated_at);

CREATE INDEX IF NOT EXISTS idx_outcome_events_learning
    ON outcome_events(learned, outcome, failure_class, created_at);

CREATE INDEX IF NOT EXISTS idx_outcome_events_session
    ON outcome_events(session_storage_key, created_at);
