-- Latest observed rate limit state per window type (5-hour, weekly, ...).
-- Upserted every time we see a `rate_limit_event` in stream-json output.
-- We keep only the most recent snapshot per window — older data is noise.
CREATE TABLE rate_limit_snapshots (
    rate_limit_type     TEXT PRIMARY KEY,   -- 'five_hour' | 'weekly' | ...
    status              TEXT NOT NULL,      -- 'allowed' | ...
    resets_at           TEXT NOT NULL,      -- ISO datetime
    overage_status      TEXT,
    overage_resets_at   TEXT,
    is_using_overage    INTEGER,            -- 0/1
    observed_at         TEXT NOT NULL,      -- ISO datetime we saw it
    observed_run_id     TEXT
);

-- Per-run per-model breakdown from the `modelUsage` field of the result
-- event. One row per (run, model). Enables per-model cost rollups on the
-- ledger page without having to re-parse trace files.
CREATE TABLE run_model_usage (
    run_id              TEXT NOT NULL,
    model               TEXT NOT NULL,
    cost_usd            REAL NOT NULL DEFAULT 0,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, model)
);

CREATE INDEX idx_run_model_usage_model ON run_model_usage(model);
