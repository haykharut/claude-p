"""Tests for the unofficial claude.ai usage poller.

We don't hit the real endpoint from tests — fetch_usage is covered by
manual probe via the Settings page. These tests verify the parsing /
persistence logic with a synthetic payload.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from claude_p.claude_ai import EXTRA_USAGE_KEY, persist_usage_payload
from claude_p.db import connect, init_db
from claude_p.queries import (
    get_claude_ai_extra_usage,
    list_claude_ai_windows,
)


SAMPLE_PAYLOAD = {
    "five_hour": {
        "utilization": 9.0,
        "resets_at": "2026-04-22T17:00:00+00:00",
    },
    "seven_day": {
        "utilization": 53.0,
        "resets_at": "2026-04-24T06:00:00+00:00",
    },
    "seven_day_oauth_apps": None,
    "seven_day_opus": None,
    "seven_day_sonnet": {
        "utilization": 6.0,
        "resets_at": "2026-04-24T16:00:00+00:00",
    },
    "seven_day_omelette": {
        "utilization": 0.0,
        "resets_at": None,
    },
    "extra_usage": {
        "is_enabled": True,
        "monthly_limit": 110000,
        "used_credits": 2949.0,
        "utilization": 2.68,
        "currency": "EUR",
    },
}


def test_persist_usage_payload_writes_windows_and_extra(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    with connect(db) as conn:
        n = persist_usage_payload(conn, SAMPLE_PAYLOAD, datetime.now(timezone.utc))
    # 5 rows: five_hour, seven_day, seven_day_sonnet, seven_day_omelette, __extra_usage__
    # (null entries skipped)
    assert n == 5

    with connect(db) as conn:
        windows = {w.window_key: w for w in list_claude_ai_windows(conn)}
        extra = get_claude_ai_extra_usage(conn)

    assert set(windows.keys()) == {
        "five_hour",
        "seven_day",
        "seven_day_sonnet",
        "seven_day_omelette",
    }
    assert windows["five_hour"].utilization == 9.0
    assert windows["seven_day_omelette"].resets_at is None
    assert extra is not None
    assert extra.is_enabled is True
    assert extra.monthly_limit == 110000
    assert extra.utilization == 2.68
    assert extra.currency == "EUR"


def test_persist_is_idempotent(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    obs = datetime.now(timezone.utc)
    with connect(db) as conn:
        persist_usage_payload(conn, SAMPLE_PAYLOAD, obs)
        persist_usage_payload(conn, SAMPLE_PAYLOAD, obs)
    with sqlite3.connect(db) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM claude_ai_usage").fetchone()
    assert count == 5  # upserts, not duplicates


def test_null_windows_do_not_erase_existing_data(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    obs = datetime.now(timezone.utc)
    with connect(db) as conn:
        persist_usage_payload(conn, SAMPLE_PAYLOAD, obs)
    # Second payload with seven_day set to null — should NOT clobber the
    # existing row (real endpoint sometimes returns null temporarily).
    partial = dict(SAMPLE_PAYLOAD)
    partial["seven_day"] = None
    with connect(db) as conn:
        persist_usage_payload(conn, partial, obs)
        windows = {w.window_key: w for w in list_claude_ai_windows(conn)}
    assert "seven_day" in windows
    assert windows["seven_day"].utilization == 53.0
