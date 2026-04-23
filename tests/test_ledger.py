import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from claude_p.db import init_db
from claude_p.ledger import per_job_rollups, set_weekly_budget, weekly_budget, window_totals


def _insert_run(db, run_id, slug, started, cost, in_t, out_t):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO runs(id,job_slug,started_at,trigger,run_dir,cost_usd,input_tokens,output_tokens) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (run_id, slug, started, "manual", "/tmp", cost, in_t, out_t),
        )


def test_window_totals(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    now = datetime.now(UTC)
    _insert_run(db, "r1", "a", (now - timedelta(hours=1)).isoformat(), 0.01, 100, 50)
    _insert_run(db, "r2", "a", (now - timedelta(hours=3)).isoformat(), 0.02, 200, 80)
    _insert_run(db, "r3", "b", (now - timedelta(hours=10)).isoformat(), 0.10, 300, 150)

    w5 = window_totals(db, 5)
    assert w5.run_count == 2
    assert abs(w5.cost_usd - 0.03) < 1e-9

    w24 = window_totals(db, 24)
    assert w24.run_count == 3
    assert abs(w24.cost_usd - 0.13) < 1e-9


def test_per_job_rollups_requires_jobs_state_row(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO jobs_state(slug, last_seen_at) VALUES(?,?)",
            ("a", now),
        )
    _insert_run(db, "r1", "a", now, 0.01, 100, 50)
    _insert_run(db, "r2", "a", now, 0.03, 300, 100)

    rollups = {r.slug: r for r in per_job_rollups(db)}
    assert "a" in rollups
    assert rollups["a"].runs_last_10 == 2
    assert abs(rollups["a"].avg_cost_usd - 0.02) < 1e-9


def test_weekly_budget_roundtrip(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    assert weekly_budget(db) == 0.0
    set_weekly_budget(db, 12.50)
    assert weekly_budget(db) == 12.50
