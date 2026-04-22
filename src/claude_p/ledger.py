from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class WindowTotals:
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    run_count: int


@dataclass
class JobRollup:
    slug: str
    runs_last_10: int
    avg_cost_usd: float
    avg_input_tokens: float
    avg_output_tokens: float
    last_run_at: str | None


def _window_start(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def window_totals(db_path: Path, hours: float) -> WindowTotals:
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(cost_usd),0) as cost,
                COALESCE(SUM(input_tokens),0) as in_t,
                COALESCE(SUM(output_tokens),0) as out_t,
                COALESCE(SUM(cache_read_tokens),0) as cr_t,
                COALESCE(SUM(cache_creation_tokens),0) as cc_t,
                COUNT(*) as n
            FROM runs
            WHERE started_at >= ?
            """,
            (_window_start(hours),),
        ).fetchone()
    return WindowTotals(
        cost_usd=row["cost"] or 0.0,
        input_tokens=row["in_t"] or 0,
        output_tokens=row["out_t"] or 0,
        cache_read_tokens=row["cr_t"] or 0,
        cache_creation_tokens=row["cc_t"] or 0,
        run_count=row["n"] or 0,
    )


def per_job_rollups(db_path: Path) -> list[JobRollup]:
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        slugs = [r["slug"] for r in conn.execute("SELECT DISTINCT slug FROM jobs_state").fetchall()]
        out: list[JobRollup] = []
        for slug in slugs:
            rows = conn.execute(
                """
                SELECT cost_usd, input_tokens, output_tokens, started_at
                FROM runs
                WHERE job_slug = ?
                ORDER BY started_at DESC
                LIMIT 10
                """,
                (slug,),
            ).fetchall()
            if not rows:
                out.append(JobRollup(slug, 0, 0.0, 0.0, 0.0, None))
                continue
            n = len(rows)
            out.append(
                JobRollup(
                    slug=slug,
                    runs_last_10=n,
                    avg_cost_usd=sum(r["cost_usd"] or 0 for r in rows) / n,
                    avg_input_tokens=sum(r["input_tokens"] or 0 for r in rows) / n,
                    avg_output_tokens=sum(r["output_tokens"] or 0 for r in rows) / n,
                    last_run_at=rows[0]["started_at"],
                )
            )
        return out


def weekly_budget(db_path: Path) -> float:
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT value FROM settings WHERE key='weekly_budget_usd'").fetchone()
        return float(row["value"]) if row else 0.0


def set_weekly_budget(db_path: Path, amount: float) -> None:
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('weekly_budget_usd', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(amount),),
        )
        conn.commit()
