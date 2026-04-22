"""
Typed query helpers. Raw SQL in, Pydantic models out.

Every function here takes a sqlite3.Connection and returns a model or list
of models from `claude_p.models`. Callers stay in control of transaction
boundaries — we don't commit, connect, or retry at this layer.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from claude_p.models import (
    ClaudeAiExtraUsage,
    ClaudeAiUsageWindow,
    JobRollup,
    JobState,
    ModelUsage,
    RateLimitSnapshot,
    Run,
    Schedule,
    WEEKLY_BUDGET_SETTING,
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
    return {
        r["slug"]: as_job_state(r) for r in conn.execute("SELECT * FROM jobs_state").fetchall()
    }


def get_job_state(conn: sqlite3.Connection, slug: str) -> JobState | None:
    row = conn.execute("SELECT * FROM jobs_state WHERE slug=?", (slug,)).fetchone()
    return as_job_state(row) if row else None


def list_schedules(conn: sqlite3.Connection) -> dict[str, Schedule]:
    return {
        r["slug"]: as_schedule(r)
        for r in conn.execute("SELECT * FROM schedules").fetchall()
    }


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


def upsert_schedule(
    conn: sqlite3.Connection, slug: str, cron: str, next_fire_at: datetime
) -> None:
    conn.execute(
        """
        INSERT INTO schedules(slug, cron, next_fire_at, last_fire_at)
        VALUES(?,?,?,NULL)
        ON CONFLICT(slug) DO UPDATE SET
            cron         = excluded.cron,
            next_fire_at = CASE
                WHEN schedules.cron = excluded.cron THEN schedules.next_fire_at
                ELSE excluded.next_fire_at
            END
        """,
        (slug, cron, next_fire_at.isoformat()),
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

    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


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
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?", (WEEKLY_BUDGET_SETTING,)
    ).fetchone()
    return float(row["value"]) if row else 0.0


def set_weekly_budget(conn: sqlite3.Connection, amount: float) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
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
    rows = conn.execute(
        "SELECT * FROM rate_limit_snapshots ORDER BY rate_limit_type"
    ).fetchall()
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
