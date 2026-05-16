CREATE TABLE IF NOT EXISTS memory_promotion_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL,
    session_storage_key TEXT NOT NULL,
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    agent_name TEXT NOT NULL DEFAULT '',
    target_scope TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    verification_json TEXT NOT NULL DEFAULT '{}',
    promoted_fragment_ulid TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT (unixepoch()),
    updated_at REAL NOT NULL DEFAULT (unixepoch()),
    UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_memory_promotion_journal_idempotency
    ON memory_promotion_journal(idempotency_key);

CREATE INDEX IF NOT EXISTS idx_memory_promotion_journal_pending
    ON memory_promotion_journal(status, target_scope, agent_name, created_at);
