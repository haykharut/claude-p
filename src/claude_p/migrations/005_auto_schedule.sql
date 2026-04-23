-- Auto schedule mode.
--
-- Rebuilds `schedules` to make `cron` nullable so auto jobs can live there
-- without a fake cron string. Adds per-run utilization snapshots on `runs`
-- so the decision function can learn each job's historical footprint.
-- Seeds default values for the new `auto_*` settings keys.

-- SQLite can't ALTER a column's NOT NULL in place. Full table rebuild.
CREATE TABLE schedules_new (
    slug                 TEXT PRIMARY KEY,
    cron                 TEXT,                     -- NULL when mode='auto'
    next_fire_at         TEXT,
    last_fire_at         TEXT,
    mode                 TEXT NOT NULL DEFAULT 'cron',   -- 'cron' | 'auto'
    auto_config_json     TEXT,                     -- serialized AutoConfig when mode='auto'
    deferred_since       TEXT                      -- first tick this cadence period became due
);

INSERT INTO schedules_new (slug, cron, next_fire_at, last_fire_at, mode, auto_config_json, deferred_since)
SELECT slug, cron, next_fire_at, last_fire_at, 'cron', NULL, NULL
FROM schedules;

DROP TABLE schedules;
ALTER TABLE schedules_new RENAME TO schedules;

-- Per-run claude.ai window snapshots, read at run start/end. NULL when the
-- claude.ai poller isn't configured or the window hasn't been observed yet.
ALTER TABLE runs ADD COLUMN five_hour_util_at_start REAL;
ALTER TABLE runs ADD COLUMN five_hour_util_at_end   REAL;
ALTER TABLE runs ADD COLUMN seven_day_util_at_start REAL;
ALTER TABLE runs ADD COLUMN seven_day_util_at_end   REAL;

-- Default values for the new auto_* settings. Safe to re-run via INSERT OR
-- IGNORE if a user has already set a value.
INSERT OR IGNORE INTO settings(key, value) VALUES
    ('auto_daytime_start_local',      '07:00'),
    ('auto_daytime_end_local',        '22:00'),
    ('auto_local_tz',                 'UTC'),
    ('auto_5h_util_day_normal',       '60'),
    ('auto_5h_util_night_normal',     '85'),
    ('auto_5h_util_day_low',          '30'),
    ('auto_5h_util_night_low',        '70'),
    ('auto_weekly_skip_above',        '90'),
    ('auto_weekly_budget_guard',      '1.0'),
    ('auto_min_seconds_between_fires','120'),
    ('auto_safety_factor',            '1.25'),
    ('auto_coldstart_5h_util_delta',  '10.0'),
    ('auto_coldstart_7d_util_delta',  '2.0'),
    ('auto_coldstart_cost_usd',       '0.25'),
    ('auto_coldstart_min_samples',    '3');
