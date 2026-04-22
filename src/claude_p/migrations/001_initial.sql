-- Initial schema for claude-p.
--
-- Runs are the append-log of every job execution (including scaffolder runs,
-- which use job_slug = '__scaffold__:<slug>').

CREATE TABLE runs (
    id                      TEXT PRIMARY KEY,
    job_slug                TEXT NOT NULL,
    started_at              TEXT NOT NULL,
    ended_at                TEXT,
    exit_code               INTEGER,
    trigger                 TEXT NOT NULL,          -- 'schedule' | 'manual' | 'scaffold'
    cost_usd                REAL NOT NULL DEFAULT 0,
    input_tokens            INTEGER NOT NULL DEFAULT 0,
    output_tokens           INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens   INTEGER NOT NULL DEFAULT 0,
    error                   TEXT,
    run_dir                 TEXT NOT NULL
);

CREATE INDEX idx_runs_job_slug   ON runs(job_slug, started_at DESC);
CREATE INDEX idx_runs_started_at ON runs(started_at DESC);

-- One row per slug currently (or ever) on disk. Kept even after the folder
-- disappears so we can show historical runs in the dashboard.
CREATE TABLE jobs_state (
    slug                    TEXT PRIMARY KEY,
    last_seen_at            TEXT NOT NULL,
    last_manifest_hash      TEXT,
    disabled_reason         TEXT,
    manifest_error          TEXT
);

-- Only rows for jobs that declare a cron schedule.
CREATE TABLE schedules (
    slug                    TEXT PRIMARY KEY,
    cron                    TEXT NOT NULL,
    next_fire_at            TEXT,
    last_fire_at            TEXT
);

CREATE TABLE settings (
    key                     TEXT PRIMARY KEY,
    value                   TEXT NOT NULL
);

CREATE TABLE secrets (
    name                    TEXT PRIMARY KEY,
    value_encrypted         TEXT NOT NULL,
    created_at              TEXT NOT NULL
);
