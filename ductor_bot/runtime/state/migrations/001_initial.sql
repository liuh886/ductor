CREATE TABLE IF NOT EXISTS sessions (
    storage_key TEXT PRIMARY KEY,
    transport TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    topic_id INTEGER,
    topic_name TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_active TEXT NOT NULL,
    lineage_id TEXT NOT NULL DEFAULT '',
    lineage_root TEXT NOT NULL DEFAULT '',
    lineage_parent TEXT NOT NULL DEFAULT '',
    lineage_depth INTEGER NOT NULL DEFAULT 0,
    lineage_reason TEXT NOT NULL DEFAULT '',
    lineage_created_at TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS session_provider_state (
    storage_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    session_id TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    total_cost_usd REAL NOT NULL,
    total_tokens INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (storage_key, provider),
    FOREIGN KEY (storage_key) REFERENCES sessions(storage_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS named_sessions (
    chat_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    transport TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    session_id TEXT NOT NULL,
    prompt_preview TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    message_count INTEGER NOT NULL,
    last_prompt TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (chat_id, name)
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    parent_agent TEXT NOT NULL,
    name TEXT NOT NULL,
    prompt_preview TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    session_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    completed_at REAL NOT NULL,
    elapsed_seconds REAL NOT NULL,
    error TEXT NOT NULL,
    result_preview TEXT NOT NULL,
    question_count INTEGER NOT NULL,
    num_turns INTEGER NOT NULL,
    last_question TEXT NOT NULL,
    original_prompt TEXT NOT NULL,
    thinking TEXT NOT NULL,
    tasks_dir TEXT NOT NULL,
    thread_id INTEGER,
    payload_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS task_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    question_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'asked',
    asked_at REAL NOT NULL DEFAULT (unixepoch()),
    answered_at REAL,
    answer_text TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS processes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_label TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    topic_id INTEGER,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    session_storage_key TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL DEFAULT (unixepoch()),
    ended_at REAL,
    exit_code INTEGER,
    abort_reason TEXT NOT NULL DEFAULT '',
    timed_out INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_storage_key TEXT NOT NULL,
    message_id INTEGER,
    provider TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL,
    tool_namespace TEXT NOT NULL DEFAULT '',
    arguments_json TEXT NOT NULL DEFAULT '{}',
    result_preview TEXT NOT NULL DEFAULT '',
    latency_ms REAL NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 1,
    sensitive INTEGER NOT NULL DEFAULT 0,
    compressible INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_storage_key TEXT NOT NULL,
    turn_index INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'normal',
    content_text TEXT NOT NULL DEFAULT '',
    content_json TEXT NOT NULL DEFAULT '{}',
    token_count INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    is_compressed INTEGER NOT NULL DEFAULT 0,
    protected INTEGER NOT NULL DEFAULT 0,
    tool_call_id INTEGER,
    process_id INTEGER,
    created_at REAL NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS session_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_storage_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    coverage_from_message_id INTEGER,
    coverage_to_message_id INTEGER,
    model TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS memory_fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL,
    source_path TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    importance REAL NOT NULL DEFAULT 0,
    last_verified_at REAL,
    stale_after REAL,
    created_at REAL NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS inflight_turns (
    chat_id INTEGER PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
