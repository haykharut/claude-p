"""Ledger convenience wrappers — thin facade over queries.py so callers
don't need to open a connection for simple reads.
"""

from __future__ import annotations

from pathlib import Path

from claude_p import queries
from claude_p.db import connect
from claude_p.models import JobRollup, WindowTotals


def window_totals(db_path: Path, hours: float) -> WindowTotals:
    with connect(db_path) as conn:
        return queries.window_totals(conn, hours)


def per_job_rollups(db_path: Path) -> list[JobRollup]:
    with connect(db_path) as conn:
        return queries.per_job_rollups(conn)


def weekly_budget(db_path: Path) -> float:
    with connect(db_path) as conn:
        return queries.get_weekly_budget(conn)


def set_weekly_budget(db_path: Path, amount: float) -> None:
    with connect(db_path) as conn:
        queries.set_weekly_budget(conn, amount)
