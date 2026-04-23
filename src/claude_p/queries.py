"""
Typed query helpers. Raw SQL in, Pydantic models out.

Every function here takes a sqlite3.Connection and returns a model or list
of models from `claude_p.models`. Callers stay in control of transaction
boundaries — we don't commit, connect, or retry at this layer.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from claude_p.models import (
    WEEKLY_BUDGET_SETTING,
    AutoSettings,
    ClaudeAiExtraUsage,
    ClaudeAiUsageWindow,
    JobCostEstimate,
    JobRollup,
    JobState,
    ModelUsage,
    RateLimitSnapshot,
    Run,
    Schedule,
    WindowTotals,
    as_job_state,
    as_rate_limit_snapshot,
    as_run,
    as_schedule,
)


def get_run(conn: sqlite3.Connection, run_id: str) -> Run | None:
    row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return as_run(row) if row else None


def list_runs_for_job(conn: sqlite3.Connection, slug: str, limit: int = 50) -> list[Run]:
    rows = conn.execute(
        "SELECT * FROM runs WHERE job_slug=? ORDER BY started_at DESC LIMIT ?",
        (slug, limit),
    ).fetchall()
    return [as_run(r) for r in rows]


def last_runs_by_slug(conn: sqlite3.Connection) -> dict[str, Run]:
    """Latest run for each job_slug."""
    rows = conn.execute(
        """
        SELECT r.* FROM runs r
        JOIN (SELECT job_slug, MAX(started_at) AS ts FROM runs GROUP BY job_slug) m
          ON m.job_slug = r.job_slug AND m.ts = r.started_at
        """
    ).fetchall()
    return {r["job_slug"]: as_run(r) for r in rows}


def list_job_states(conn: sqlite3.Connection) -> dict[str, JobState]:
    return {r["slug"]: as_job_state(r) for r in conn.execute("SELECT * FROM jobs_state").fetchall()}


def get_job_state(conn: sqlite3.Connection, slug: str) -> JobState | None:
    row = conn.execute("SELECT * FROM jobs_state WHERE slug=?", (slug,)).fetchone()
    return as_job_state(row) if row else None


def list_schedules(conn: sqlite3.Connection) -> dict[str, Schedule]:
    return {r["slug"]: as_schedule(r) for r in conn.execute("SELECT * FROM schedules").fetchall()}


def get_schedule(conn: sqlite3.Connection, slug: str) -> Schedule | None:
    row = conn.execute("SELECT * FROM schedules WHERE slug=?", (slug,)).fetchone()
    return as_schedule(row) if row else None


def insert_run_pending(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    job_slug: str,
    started_at: datetime,
    trigger: str,
    run_dir: Path,
) -> None:
    conn.execute(
        "INSERT INTO runs(id, job_slug, started_at, trigger, run_dir) VALUES(?,?,?,?,?)",
        (run_id, job_slug, started_at.isoformat(), trigger, str(run_dir)),
    )


def update_run_result(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ended_at: datetime,
    exit_code: int | None,
    cost_usd: float,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    error: str | None,
) -> None:
    conn.execute(
        """
        UPDATE runs SET
            ended_at=?, exit_code=?, cost_usd=?, input_tokens=?, output_tokens=?,
            cache_read_tokens=?, cache_creation_tokens=?, error=?
        WHERE id=?
        """,
        (
            ended_at.isoformat(),
            exit_code,
            cost_usd,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_creation_tokens,
            error,
            run_id,
        ),
    )


def bump_schedule(
    conn: sqlite3.Connection, slug: str, last_fire_at: datetime, next_fire_at: datetime
) -> None:
    conn.execute(
        "UPDATE schedules SET last_fire_at=?, next_fire_at=? WHERE slug=?",
        (last_fire_at.isoformat(), next_fire_at.isoformat(), slug),
    )


def upsert_job_state(
    conn: sqlite3.Connection,
    *,
    slug: str,
    last_seen_at: datetime,
    manifest_hash: str | None,
    manifest_error: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO jobs_state(slug, last_seen_at, last_manifest_hash, manifest_error, disabled_reason)
        VALUES(?,?,?,?,NULL)
        ON CONFLICT(slug) DO UPDATE SET
            last_seen_at       = excluded.last_seen_at,
            last_manifest_hash = excluded.last_manifest_hash,
            manifest_error     = excluded.manifest_error,
            disabled_reason    = NULL
        """,
        (slug, last_seen_at.isoformat(), manifest_hash, manifest_error),
    )


def set_job_disabled(conn: sqlite3.Connection, slug: str, reason: str | None) -> None:
    conn.execute("UPDATE jobs_state SET disabled_reason=? WHERE slug=?", (reason, slug))


def delete_schedule(conn: sqlite3.Connection, slug: str) -> None:
    conn.execute("DELETE FROM schedules WHERE slug=?", (slug,))


def upsert_schedule(conn: sqlite3.Connection, slug: str, cron: str, next_fire_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO schedules(slug, cron, next_fire_at, last_fire_at,
                              mode, auto_config_json, deferred_since)
        VALUES(?,?,?,NULL,'cron',NULL,NULL)
        ON CONFLICT(slug) DO UPDATE SET
            cron             = excluded.cron,
            next_fire_at     = CASE
                WHEN schedules.cron = excluded.cron THEN schedules.next_fire_at
                ELSE excluded.next_fire_at
            END,
            mode             = 'cron',
            auto_config_json = NULL,
            deferred_since   = NULL
        """,
        (slug, cron, next_fire_at.isoformat()),
    )


def upsert_auto_schedule(conn: sqlite3.Connection, slug: str, auto_config_json: str) -> None:
    """Persist an auto-mode schedule. Preserves `last_fire_at` on conflict so
    cadence continues across manifest reloads. Clears `deferred_since` when
    the auto config changes (new cadence ⇒ re-evaluate from scratch)."""
    conn.execute(
        """
        INSERT INTO schedules(slug, cron, next_fire_at, last_fire_at,
                              mode, auto_config_json, deferred_since)
        VALUES(?, NULL, NULL, NULL, 'auto', ?, NULL)
        ON CONFLICT(slug) DO UPDATE SET
            cron             = NULL,
            next_fire_at     = NULL,
            mode             = 'auto',
            auto_config_json = excluded.auto_config_json,
            deferred_since   = CASE
                WHEN schedules.auto_config_json = excluded.auto_config_json
                  THEN schedules.deferred_since
                ELSE NULL
            END
        """,
        (slug, auto_config_json),
    )


def due_job_slugs(conn: sqlite3.Connection, now: datetime) -> list[str]:
    rows = conn.execute(
        """
        SELECT s.slug FROM schedules s
        JOIN jobs_state j ON j.slug = s.slug
        WHERE s.next_fire_at IS NOT NULL
          AND s.next_fire_at <= ?
          AND j.disabled_reason IS NULL
        """,
        (now.isoformat(),),
    ).fetchall()
    return [r["slug"] for r in rows]


# -- Ledger queries ---------------------------------------------------------


def _window_start(hours: float) -> str:
    from datetime import timedelta

    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def window_totals(conn: sqlite3.Connection, hours: float) -> WindowTotals:
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(cost_usd),0)              AS cost_usd,
            COALESCE(SUM(input_tokens),0)          AS input_tokens,
            COALESCE(SUM(output_tokens),0)         AS output_tokens,
            COALESCE(SUM(cache_read_tokens),0)     AS cache_read_tokens,
            COALESCE(SUM(cache_creation_tokens),0) AS cache_creation_tokens,
            COUNT(*)                               AS run_count
        FROM runs
        WHERE started_at >= ?
        """,
        (_window_start(hours),),
    ).fetchone()
    return WindowTotals.model_validate(dict(row))


def per_job_rollups(conn: sqlite3.Connection) -> list[JobRollup]:
    slugs = [r["slug"] for r in conn.execute("SELECT DISTINCT slug FROM jobs_state").fetchall()]
    out: list[JobRollup] = []
    for slug in slugs:
        rows = conn.execute(
            """
            SELECT cost_usd, input_tokens, output_tokens, started_at
            FROM runs WHERE job_slug=? ORDER BY started_at DESC LIMIT 10
            """,
            (slug,),
        ).fetchall()
        if not rows:
            out.append(JobRollup(slug=slug))
            continue
        n = len(rows)
        out.append(
            JobRollup(
                slug=slug,
                runs_last_10=n,
                avg_cost_usd=sum((r["cost_usd"] or 0) for r in rows) / n,
                avg_input_tokens=sum((r["input_tokens"] or 0) for r in rows) / n,
                avg_output_tokens=sum((r["output_tokens"] or 0) for r in rows) / n,
                last_run_at=datetime.fromisoformat(rows[0]["started_at"]),
            )
        )
    return out


def get_weekly_budget(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (WEEKLY_BUDGET_SETTING,)).fetchone()
    return float(row["value"]) if row else 0.0


def set_weekly_budget(conn: sqlite3.Connection, amount: float) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (WEEKLY_BUDGET_SETTING, str(amount)),
    )


# -- Rate limits + model usage ---------------------------------------------


def upsert_rate_limit_snapshot(
    conn: sqlite3.Connection,
    *,
    rate_limit_type: str,
    status: str,
    resets_at: datetime,
    overage_status: str | None,
    overage_resets_at: datetime | None,
    is_using_overage: bool,
    observed_at: datetime,
    observed_run_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO rate_limit_snapshots(
            rate_limit_type, status, resets_at, overage_status,
            overage_resets_at, is_using_overage, observed_at, observed_run_id
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(rate_limit_type) DO UPDATE SET
            status             = excluded.status,
            resets_at          = excluded.resets_at,
            overage_status     = excluded.overage_status,
            overage_resets_at  = excluded.overage_resets_at,
            is_using_overage   = excluded.is_using_overage,
            observed_at        = excluded.observed_at,
            observed_run_id    = excluded.observed_run_id
        """,
        (
            rate_limit_type,
            status,
            resets_at.isoformat(),
            overage_status,
            overage_resets_at.isoformat() if overage_resets_at else None,
            1 if is_using_overage else 0,
            observed_at.isoformat(),
            observed_run_id,
        ),
    )


def list_rate_limit_snapshots(conn: sqlite3.Connection) -> list[RateLimitSnapshot]:
    rows = conn.execute("SELECT * FROM rate_limit_snapshots ORDER BY rate_limit_type").fetchall()
    return [as_rate_limit_snapshot(r) for r in rows]


def upsert_run_model_usage(
    conn: sqlite3.Connection,
    run_id: str,
    model: str,
    *,
    cost_usd: float,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
) -> None:
    conn.execute(
        """
        INSERT INTO run_model_usage(
            run_id, model, cost_usd, input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(run_id, model) DO UPDATE SET
            cost_usd             = cost_usd + excluded.cost_usd,
            input_tokens         = input_tokens + excluded.input_tokens,
            output_tokens        = output_tokens + excluded.output_tokens,
            cache_read_tokens    = cache_read_tokens + excluded.cache_read_tokens,
            cache_creation_tokens= cache_creation_tokens + excluded.cache_creation_tokens
        """,
        (
            run_id,
            model,
            cost_usd,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_creation_tokens,
        ),
    )


def model_usage_window(conn: sqlite3.Connection, hours: float) -> list[ModelUsage]:
    start = _window_start(hours)
    rows = conn.execute(
        """
        SELECT
            mu.model                                AS model,
            COUNT(DISTINCT mu.run_id)               AS runs,
            COALESCE(SUM(mu.cost_usd), 0)           AS cost_usd,
            COALESCE(SUM(mu.input_tokens), 0)       AS input_tokens,
            COALESCE(SUM(mu.output_tokens), 0)      AS output_tokens,
            COALESCE(SUM(mu.cache_read_tokens), 0)  AS cache_read_tokens,
            COALESCE(SUM(mu.cache_creation_tokens), 0) AS cache_creation_tokens
        FROM run_model_usage mu
        JOIN runs r ON r.id = mu.run_id
        WHERE r.started_at >= ?
        GROUP BY mu.model
        ORDER BY cost_usd DESC
        """,
        (start,),
    ).fetchall()
    return [ModelUsage.model_validate(dict(r)) for r in rows]


# -- claude.ai usage -------------------------------------------------------


def upsert_claude_ai_window(
    conn: sqlite3.Connection,
    *,
    window_key: str,
    utilization: float | None,
    resets_at: datetime | None,
    observed_at: datetime,
    raw_json: str,
) -> None:
    conn.execute(
        """
        INSERT INTO claude_ai_usage(
            window_key, utilization, resets_at, raw_json, observed_at
        ) VALUES(?,?,?,?,?)
        ON CONFLICT(window_key) DO UPDATE SET
            utilization = excluded.utilization,
            resets_at   = excluded.resets_at,
            raw_json    = excluded.raw_json,
            observed_at = excluded.observed_at
        """,
        (
            window_key,
            utilization,
            resets_at.isoformat() if resets_at else None,
            raw_json,
            observed_at.isoformat(),
        ),
    )


def upsert_claude_ai_extra_usage(
    conn: sqlite3.Connection,
    *,
    observed_at: datetime,
    is_enabled: bool,
    monthly_limit: int | None,
    used_credits: float | None,
    utilization: float | None,
    currency: str | None,
    raw_json: str,
) -> None:
    from claude_p.claude_ai import EXTRA_USAGE_KEY

    conn.execute(
        """
        INSERT INTO claude_ai_usage(
            window_key, utilization, monthly_limit, used_credits,
            currency, is_enabled, raw_json, observed_at
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(window_key) DO UPDATE SET
            utilization   = excluded.utilization,
            monthly_limit = excluded.monthly_limit,
            used_credits  = excluded.used_credits,
            currency      = excluded.currency,
            is_enabled    = excluded.is_enabled,
            raw_json      = excluded.raw_json,
            observed_at   = excluded.observed_at
        """,
        (
            EXTRA_USAGE_KEY,
            utilization,
            monthly_limit,
            used_credits,
            currency,
            1 if is_enabled else 0,
            raw_json,
            observed_at.isoformat(),
        ),
    )


def list_claude_ai_windows(conn: sqlite3.Connection) -> list[ClaudeAiUsageWindow]:
    from claude_p.claude_ai import EXTRA_USAGE_KEY

    rows = conn.execute(
        "SELECT window_key, utilization, resets_at, observed_at FROM claude_ai_usage "
        "WHERE window_key != ? ORDER BY window_key",
        (EXTRA_USAGE_KEY,),
    ).fetchall()
    return [ClaudeAiUsageWindow.model_validate(dict(r)) for r in rows]


def get_claude_ai_extra_usage(conn: sqlite3.Connection) -> ClaudeAiExtraUsage | None:
    from claude_p.claude_ai import EXTRA_USAGE_KEY

    row = conn.execute(
        "SELECT is_enabled, monthly_limit, used_credits, utilization, currency, observed_at "
        "FROM claude_ai_usage WHERE window_key=?",
        (EXTRA_USAGE_KEY,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["is_enabled"] = bool(d["is_enabled"]) if d["is_enabled"] is not None else False
    return ClaudeAiExtraUsage.model_validate(d)


def fetch_window_util(conn: sqlite3.Connection, window_key: str) -> float | None:
    """Current utilization for one claude.ai window_key, or None if no row."""
    row = conn.execute(
        "SELECT utilization FROM claude_ai_usage WHERE window_key=?",
        (window_key,),
    ).fetchone()
    if row is None:
        return None
    return row["utilization"]


def list_auto_schedules(conn: sqlite3.Connection) -> list[Schedule]:
    """All schedules in 'auto' mode where the job isn't disabled.

    Order: oldest `last_fire_at` first (never-fired jobs sort first), with
    slug as a stable tiebreaker. `decide_batch` fires at most one per tick,
    so this ordering is the fairness policy — no job gets starved under
    tight quotas by losing contention to a later-registered sibling."""
    rows = conn.execute(
        """
        SELECT s.* FROM schedules s
        JOIN jobs_state j ON j.slug = s.slug
        WHERE s.mode = 'auto' AND j.disabled_reason IS NULL
        ORDER BY COALESCE(s.last_fire_at, '0000-00-00') ASC, s.slug ASC
        """,
    ).fetchall()
    return [as_schedule(r) for r in rows]


def set_schedule_fired(conn: sqlite3.Connection, slug: str, last_fire_at: datetime) -> None:
    """Auto-mode: mark a fire by bumping last_fire_at and clearing deferred_since."""
    conn.execute(
        "UPDATE schedules SET last_fire_at=?, deferred_since=NULL WHERE slug=?",
        (last_fire_at.isoformat(), slug),
    )


def set_schedule_deferred(conn: sqlite3.Connection, slug: str, deferred_since: datetime) -> None:
    """Auto-mode: stamp deferred_since the first tick this cadence became due."""
    conn.execute(
        "UPDATE schedules SET deferred_since=? WHERE slug=? AND deferred_since IS NULL",
        (deferred_since.isoformat(), slug),
    )


def latest_auto_fire_at(conn: sqlite3.Connection) -> datetime | None:
    """Most recent last_fire_at across all auto schedules. Drives the
    fleet-wide `min_seconds_between_fires` cooldown."""
    row = conn.execute("SELECT MAX(last_fire_at) AS m FROM schedules WHERE mode='auto'").fetchone()
    if row is None or row["m"] is None:
        return None
    return datetime.fromisoformat(row["m"])


def auto_job_cost_estimate(conn: sqlite3.Connection, slug: str, settings: AutoSettings) -> JobCostEstimate:
    """Empirical per-job footprint from the last 10 completed runs.

    Returns cold-start values when the job has fewer than
    `coldstart_min_samples` completed runs. Util deltas individually fall
    back to cold-start values when no run had a non-null snapshot pair
    (e.g. poller was disabled during those runs)."""
    rows = conn.execute(
        """
        SELECT cost_usd,
               five_hour_util_at_start, five_hour_util_at_end,
               seven_day_util_at_start, seven_day_util_at_end
        FROM runs
        WHERE job_slug=? AND ended_at IS NOT NULL
        ORDER BY started_at DESC
        LIMIT 10
        """,
        (slug,),
    ).fetchall()

    sample_size = len(rows)
    if sample_size < settings.coldstart_min_samples:
        return JobCostEstimate(
            slug=slug,
            sample_size=sample_size,
            avg_cost_usd=settings.coldstart_cost_usd,
            p90_cost_usd=settings.coldstart_cost_usd,
            median_5h_util_delta=settings.coldstart_5h_util_delta,
            median_7d_util_delta=settings.coldstart_7d_util_delta,
            is_cold_start=True,
        )

    costs = sorted(float(r["cost_usd"] or 0.0) for r in rows)
    avg_cost = sum(costs) / len(costs)
    # Nearest-rank p90. For N=10 this picks index 8 (the 9th value).
    p90_idx = max(0, min(len(costs) - 1, int(round(len(costs) * 0.9)) - 1))
    p90_cost = costs[p90_idx]

    def _delta(s: float | None, e: float | None) -> float | None:
        if s is None or e is None:
            return None
        return e - s

    def _median(xs: list[float]) -> float | None:
        if not xs:
            return None
        xs = sorted(xs)
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    deltas_5h = [
        d
        for d in (_delta(r["five_hour_util_at_start"], r["five_hour_util_at_end"]) for r in rows)
        if d is not None
    ]
    deltas_7d = [
        d
        for d in (_delta(r["seven_day_util_at_start"], r["seven_day_util_at_end"]) for r in rows)
        if d is not None
    ]

    m5 = _median(deltas_5h)
    m7 = _median(deltas_7d)
    return JobCostEstimate(
        slug=slug,
        sample_size=sample_size,
        avg_cost_usd=avg_cost,
        p90_cost_usd=p90_cost,
        median_5h_util_delta=settings.coldstart_5h_util_delta if m5 is None else m5,
        median_7d_util_delta=settings.coldstart_7d_util_delta if m7 is None else m7,
        is_cold_start=False,
    )


def update_run_util_at_start(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    five_hour: float | None,
    seven_day: float | None,
) -> None:
    conn.execute(
        "UPDATE runs SET five_hour_util_at_start=?, seven_day_util_at_start=? WHERE id=?",
        (five_hour, seven_day, run_id),
    )


def update_run_util_at_end(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    five_hour: float | None,
    seven_day: float | None,
) -> None:
    conn.execute(
        "UPDATE runs SET five_hour_util_at_end=?, seven_day_util_at_end=? WHERE id=?",
        (five_hour, seven_day, run_id),
    )


def load_auto_settings(conn: sqlite3.Connection) -> AutoSettings:
    """Load all auto_* setting rows in one sweep with defaults applied."""
    from claude_p.models import (
        AUTO_5H_UTIL_DAY_LOW_SETTING,
        AUTO_5H_UTIL_DAY_NORMAL_SETTING,
        AUTO_5H_UTIL_NIGHT_LOW_SETTING,
        AUTO_5H_UTIL_NIGHT_NORMAL_SETTING,
        AUTO_COLDSTART_5H_UTIL_DELTA_SETTING,
        AUTO_COLDSTART_7D_UTIL_DELTA_SETTING,
        AUTO_COLDSTART_COST_USD_SETTING,
        AUTO_COLDSTART_MIN_SAMPLES_SETTING,
        AUTO_DAYTIME_END_LOCAL_SETTING,
        AUTO_DAYTIME_START_LOCAL_SETTING,
        AUTO_LOCAL_TZ_SETTING,
        AUTO_MIN_SECONDS_BETWEEN_FIRES_SETTING,
        AUTO_SAFETY_FACTOR_SETTING,
        AUTO_WEEKLY_BUDGET_GUARD_SETTING,
        AUTO_WEEKLY_SKIP_ABOVE_SETTING,
    )

    rows = {
        r["key"]: r["value"]
        for r in conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'auto\\_%' ESCAPE '\\'"
        ).fetchall()
    }

    def _f(key: str, default: float) -> float:
        try:
            return float(rows[key]) if key in rows else default
        except (TypeError, ValueError):
            return default

    def _i(key: str, default: int) -> int:
        try:
            return int(float(rows[key])) if key in rows else default
        except (TypeError, ValueError):
            return default

    return AutoSettings(
        daytime_start_local=rows.get(AUTO_DAYTIME_START_LOCAL_SETTING, "07:00"),
        daytime_end_local=rows.get(AUTO_DAYTIME_END_LOCAL_SETTING, "22:00"),
        local_tz=rows.get(AUTO_LOCAL_TZ_SETTING, "UTC"),
        util_5h_day_normal=_f(AUTO_5H_UTIL_DAY_NORMAL_SETTING, 60.0),
        util_5h_night_normal=_f(AUTO_5H_UTIL_NIGHT_NORMAL_SETTING, 85.0),
        util_5h_day_low=_f(AUTO_5H_UTIL_DAY_LOW_SETTING, 30.0),
        util_5h_night_low=_f(AUTO_5H_UTIL_NIGHT_LOW_SETTING, 70.0),
        weekly_skip_above=_f(AUTO_WEEKLY_SKIP_ABOVE_SETTING, 90.0),
        weekly_budget_guard=_f(AUTO_WEEKLY_BUDGET_GUARD_SETTING, 1.0),
        min_seconds_between_fires=_f(AUTO_MIN_SECONDS_BETWEEN_FIRES_SETTING, 120.0),
        safety_factor=_f(AUTO_SAFETY_FACTOR_SETTING, 1.25),
        coldstart_5h_util_delta=_f(AUTO_COLDSTART_5H_UTIL_DELTA_SETTING, 10.0),
        coldstart_7d_util_delta=_f(AUTO_COLDSTART_7D_UTIL_DELTA_SETTING, 2.0),
        coldstart_cost_usd=_f(AUTO_COLDSTART_COST_USD_SETTING, 0.25),
        coldstart_min_samples=_i(AUTO_COLDSTART_MIN_SAMPLES_SETTING, 3),
    )
