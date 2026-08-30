PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    workspace_realpath TEXT NOT NULL,
    requires_recovery_ack INTEGER NOT NULL DEFAULT 0 CHECK (requires_recovery_ack IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    stop_reason TEXT,
    error_kind TEXT,
    cancellation_requested_at TEXT,
    config_snapshot_json TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    round_count INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    context_json TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    parts_json TEXT NOT NULL,
    status TEXT NOT NULL,
    tool_call_id TEXT,
    UNIQUE (session_id, seq),
    UNIQUE (tool_call_id)
);

CREATE TABLE IF NOT EXISTS model_requests (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    round_no INTEGER NOT NULL,
    kind TEXT NOT NULL,
    model TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_creation_input_tokens INTEGER,
    cache_read_input_tokens INTEGER,
    usage_source TEXT,
    network_retry_count INTEGER NOT NULL DEFAULT 0,
    total_wait_ms INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tool_executions (
    tool_call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    assistant_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    call_order INTEGER NOT NULL,
    name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    requires_approval INTEGER NOT NULL CHECK (requires_approval IN (0, 1)),
    approval_status TEXT NOT NULL,
    approval_decision TEXT,
    approval_decided_at TEXT,
    effect_started_at TEXT,
    baseline_sha256 TEXT,
    execution_state TEXT NOT NULL,
    result_json TEXT,
    duration_ms INTEGER,
    UNIQUE (assistant_message_id, call_order)
);

CREATE TABLE IF NOT EXISTS context_snapshots (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    covered_through_message_seq INTEGER NOT NULL,
    summary TEXT NOT NULL,
    model_config_json TEXT NOT NULL,
    token_estimate INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);

CREATE TABLE IF NOT EXISTS client_commands (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    client_command_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'completed')),
    resource_id TEXT,
    event_seq INTEGER,
    ack_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, client_command_id)
);

CREATE INDEX IF NOT EXISTS runs_by_session ON runs(session_id, started_at);
CREATE INDEX IF NOT EXISTS messages_by_session ON messages(session_id, seq);
CREATE INDEX IF NOT EXISTS tools_by_message ON tool_executions(assistant_message_id, call_order);
CREATE UNIQUE INDEX IF NOT EXISTS context_snapshot_current_by_session
    ON context_snapshots(session_id);

-- The script baseline stays at v1; SQLiteStore.initialize() upgrades existing
-- databases (and stamps fresh ones) to user_version 2 with the runs.context_json
-- column for the run's latest context estimate.
PRAGMA user_version = 1;
COMMIT;
