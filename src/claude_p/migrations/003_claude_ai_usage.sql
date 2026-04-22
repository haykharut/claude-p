-- Per-window utilization from the (unofficial) claude.ai web endpoint
-- /api/organizations/<org>/usage. Populated by a poller that uses the
-- user's pasted sessionKey cookie. Keep only the latest snapshot per
-- window_key — old data is noise.
--
-- The endpoint returns both per-window utilization (five_hour,
-- seven_day, seven_day_sonnet, ...) and an extra_usage object
-- (monthly_limit, used_credits, currency). We flatten both into this
-- single table using a reserved window_key = '__extra_usage__' row for
-- the latter, leaving the window-specific columns null for extra_usage
-- and the extra_usage columns null for the windows.
CREATE TABLE claude_ai_usage (
    window_key      TEXT PRIMARY KEY,   -- 'five_hour' | 'seven_day' | ... | '__extra_usage__'
    utilization     REAL,               -- 0.0–100.0
    resets_at       TEXT,               -- ISO datetime (may be null)
    monthly_limit   INTEGER,            -- extra_usage only
    used_credits    REAL,               -- extra_usage only
    currency        TEXT,               -- extra_usage only
    is_enabled      INTEGER,            -- extra_usage only
    raw_json        TEXT,               -- preserve full payload for debugging
    observed_at     TEXT NOT NULL
);
