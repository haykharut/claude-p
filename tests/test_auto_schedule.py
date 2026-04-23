"""Decision-algorithm tests for `schedule: auto` jobs.

These tests use the pure `decide_one` / `decide_batch` functions — no DB,
no claude.ai calls, no time freezing. The DB-integrated pieces
(`auto_job_cost_estimate`, state writes) are covered separately below.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from claude_p import db, queries
from claude_p.auto_schedule import (
    AutoInputs,
    decide_batch,
    decide_one,
    is_nighttime,
)
from claude_p.manifest import AutoConfig
from claude_p.models import AutoSettings, JobCostEstimate, Schedule


def _settings(**overrides) -> AutoSettings:
    base = AutoSettings()
    return base.model_copy(update=overrides)


def _schedule(
    slug: str = "j",
    *,
    last_fire_at: datetime | None = None,
    deferred_since: datetime | None = None,
) -> Schedule:
    return Schedule(
        slug=slug,
        mode="auto",
        last_fire_at=last_fire_at,
        deferred_since=deferred_since,
        auto_config_json='{"every":"1h"}',
    )


def _auto(
    every: str = "1h",
    deadline: str | None = None,
    priority: Literal["low", "normal"] = "normal",
) -> AutoConfig:
    return AutoConfig(every=every, deadline=deadline, priority=priority)


def _estimate(
    *,
    cost: float = 0.0,
    d5: float | None = 0.0,
    d7: float | None = 0.0,
    samples: int = 10,
    cold: bool = False,
) -> JobCostEstimate:
    return JobCostEstimate(
        slug="j",
        sample_size=samples,
        avg_cost_usd=cost,
        p90_cost_usd=cost,
        median_5h_util_delta=d5,
        median_7d_util_delta=d7,
        is_cold_start=cold,
    )


NOW = datetime(2026, 4, 23, 3, 0, tzinfo=UTC)  # 03:00 UTC — nighttime under defaults


# -- decide_one: cadence / deadline ---------------------------------------


def test_first_fire_ever_is_due():
    d = decide_one(
        schedule=_schedule(last_fire_at=None),
        auto_config=_auto("1h"),
        estimate=_estimate(),
        now_utc=NOW,
        five_hour_util=0.0,
        seven_day_util=0.0,
        spend_7d=0.0,
        weekly_budget=0.0,
        settings=_settings(),
    )
    assert d.verdict == "fire"


def test_not_due_yet_returns_skip():
    d = decide_one(
        schedule=_schedule(last_fire_at=NOW - timedelta(minutes=30)),
        auto_config=_auto("1h"),
        estimate=_estimate(),
        now_utc=NOW,
        five_hour_util=0.0,
        seven_day_util=0.0,
        spend_7d=0.0,
        weekly_budget=0.0,
        settings=_settings(),
    )
    assert d.verdict == "skip"
    assert "not due yet" in d.reason


def test_past_deadline_forces_fire_even_with_hot_quotas():
    """Soft-deadline semantics: when elapsed >= deadline_s, fire no matter
    how hot the windows are."""
    d = decide_one(
        schedule=_schedule(last_fire_at=NOW - timedelta(hours=5)),
        auto_config=_auto("1h"),  # deadline defaults to 2h
        estimate=_estimate(d5=50.0, d7=50.0),
        now_utc=NOW,
        five_hour_util=99.0,
        seven_day_util=99.0,
        spend_7d=9999.0,
        weekly_budget=1.0,
        settings=_settings(),
    )
    assert d.verdict == "fire"
    assert "past deadline" in d.reason


# -- decide_one: quota defers --------------------------------------------


def test_weekly_util_would_exceed_skips_cap():
    d = decide_one(
        schedule=_schedule(last_fire_at=None),
        auto_config=_auto(),
        estimate=_estimate(d7=20.0),
        now_utc=NOW,
        five_hour_util=0.0,
        seven_day_util=75.0,  # 75 + 20*1.25 = 100 >= 90
        spend_7d=0.0,
        weekly_budget=0.0,
        settings=_settings(weekly_skip_above=90.0),
    )
    assert d.verdict == "defer"
    assert "weekly util" in d.reason


def test_budget_guard_uses_expected_cost():
    """Firing this job would push us to 95% of budget, guard is 0.9 → defer."""
    d = decide_one(
        schedule=_schedule(last_fire_at=None),
        auto_config=_auto(),
        estimate=_estimate(cost=0.30),  # safety 1.25 → expected 0.375
        now_utc=NOW,
        five_hour_util=0.0,
        seven_day_util=0.0,
        spend_7d=0.55,
        weekly_budget=1.0,
        settings=_settings(weekly_budget_guard=0.9, safety_factor=1.25),
    )
    # spend + expected_cost = 0.55 + 0.375 = 0.925 → 92.5% → >= 90%
    assert d.verdict == "defer"
    assert "weekly budget" in d.reason


def test_five_hour_threshold_day_vs_night():
    # Daytime in UTC: 12:00.
    noon = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    settings = _settings(util_5h_day_normal=60.0, util_5h_night_normal=85.0)

    # Daytime, 50% current + predicted +15% = 65% → exceeds 60% day cap.
    d_day = decide_one(
        schedule=_schedule(last_fire_at=None),
        auto_config=_auto(priority="normal"),
        estimate=_estimate(d5=12.0),  # 12 * 1.25 = 15
        now_utc=noon,
        five_hour_util=50.0,
        seven_day_util=0.0,
        spend_7d=0.0,
        weekly_budget=0.0,
        settings=settings,
    )
    assert d_day.verdict == "defer"
    assert "5h util" in d_day.reason

    # Same inputs at night (03:00 UTC), night cap 85% is permissive.
    d_night = decide_one(
        schedule=_schedule(last_fire_at=None),
        auto_config=_auto(priority="normal"),
        estimate=_estimate(d5=12.0),
        now_utc=NOW,  # 03:00 UTC
        five_hour_util=50.0,
        seven_day_util=0.0,
        spend_7d=0.0,
        weekly_budget=0.0,
        settings=settings,
    )
    assert d_night.verdict == "fire"


def test_low_priority_has_stricter_thresholds():
    """At the same util, a normal-priority job fires but a low-priority job
    defers, purely from threshold asymmetry."""
    settings = _settings(util_5h_night_normal=80.0, util_5h_night_low=30.0)
    # 5h at 40%, estimate 0% delta. Normal cap 80%, low cap 30%.
    sched = _schedule(last_fire_at=None)
    est = _estimate()
    d_normal = decide_one(
        schedule=sched,
        auto_config=_auto(priority="normal"),
        estimate=est,
        now_utc=NOW,
        five_hour_util=40.0,
        seven_day_util=0.0,
        spend_7d=0.0,
        weekly_budget=0.0,
        settings=settings,
    )
    d_low = decide_one(
        schedule=sched,
        auto_config=_auto(priority="low"),
        estimate=est,
        now_utc=NOW,
        five_hour_util=40.0,
        seven_day_util=0.0,
        spend_7d=0.0,
        weekly_budget=0.0,
        settings=settings,
    )
    assert d_normal.verdict == "fire"
    assert d_low.verdict == "defer"


# -- decide_one: fallback when poller off ---------------------------------


def test_no_poller_data_still_works():
    """With no claude.ai utilization, fire decisions are based on ledger +
    cadence only."""
    d = decide_one(
        schedule=_schedule(last_fire_at=None),
        auto_config=_auto(),
        estimate=_estimate(cost=0.10),
        now_utc=NOW,
        five_hour_util=None,
        seven_day_util=None,
        spend_7d=0.0,
        weekly_budget=1.0,
        settings=_settings(),
    )
    assert d.verdict == "fire"


def test_no_poller_still_respects_budget():
    d = decide_one(
        schedule=_schedule(last_fire_at=None),
        auto_config=_auto(),
        estimate=_estimate(cost=1.0),
        now_utc=NOW,
        five_hour_util=None,
        seven_day_util=None,
        spend_7d=0.0,
        weekly_budget=1.0,
        settings=_settings(weekly_budget_guard=1.0),
    )
    assert d.verdict == "defer"


# -- is_nighttime ---------------------------------------------------------


def test_is_nighttime_default_window():
    s = _settings(daytime_start_local="07:00", daytime_end_local="22:00", local_tz="UTC")
    assert is_nighttime(datetime(2026, 4, 23, 3, 0, tzinfo=UTC), s) is True
    assert is_nighttime(datetime(2026, 4, 23, 14, 0, tzinfo=UTC), s) is False
    assert is_nighttime(datetime(2026, 4, 23, 22, 0, tzinfo=UTC), s) is True
    assert is_nighttime(datetime(2026, 4, 23, 6, 59, tzinfo=UTC), s) is True


def test_is_nighttime_unknown_tz_falls_back_to_utc():
    s = _settings(local_tz="Not/A/Real/Zone")
    # 03:00 UTC is nighttime under default 07:00-22:00 window.
    assert is_nighttime(datetime(2026, 4, 23, 3, 0, tzinfo=UTC), s) is True


# -- decide_batch: fleet cooldown + per-tick cap --------------------------


def test_decide_batch_only_one_fire_per_tick():
    schedules = [_schedule(f"j{i}", last_fire_at=None) for i in range(3)]
    auto_configs = {s.slug: _auto("1h") for s in schedules}
    estimates = {s.slug: _estimate() for s in schedules}
    result = decide_batch(
        AutoInputs(
            now_utc=NOW,
            schedules=schedules,
            auto_configs=auto_configs,
            estimates=estimates,
            five_hour_util=0.0,
            seven_day_util=0.0,
            spend_7d=0.0,
            weekly_budget=0.0,
            settings=_settings(),
        )
    )
    fires = [s for s, d in result.items() if d.verdict == "fire"]
    defers = [s for s, d in result.items() if d.verdict == "defer"]
    assert len(fires) == 1
    assert len(defers) == 2
    for s in defers:
        assert "rate-limited" in result[s].reason


def test_decide_batch_fleet_cooldown_blocks_all_fires():
    schedules = [_schedule("j1", last_fire_at=None)]
    result = decide_batch(
        AutoInputs(
            now_utc=NOW,
            schedules=schedules,
            auto_configs={"j1": _auto("1h")},
            estimates={"j1": _estimate()},
            five_hour_util=0.0,
            seven_day_util=0.0,
            spend_7d=0.0,
            weekly_budget=0.0,
            settings=_settings(min_seconds_between_fires=600.0),
            last_auto_fire_at=NOW - timedelta(seconds=60),
        )
    )
    assert result["j1"].verdict == "defer"
    assert "fleet cooldown" in result["j1"].reason


def test_decide_batch_skips_in_flight():
    schedules = [_schedule("j1", last_fire_at=None)]
    result = decide_batch(
        AutoInputs(
            now_utc=NOW,
            schedules=schedules,
            auto_configs={"j1": _auto("1h")},
            estimates={"j1": _estimate()},
            five_hour_util=0.0,
            seven_day_util=0.0,
            spend_7d=0.0,
            weekly_budget=0.0,
            settings=_settings(),
            in_flight={"j1"},
        )
    )
    assert result["j1"].verdict == "skip"
    assert "already running" in result["j1"].reason


# -- DB-integrated: auto_job_cost_estimate --------------------------------


def _setup_db(tmp_path: Path) -> Path:
    p = tmp_path / "claude-p.db"
    db.init_db(p)
    return p


def _insert_completed_run(
    conn,
    *,
    run_id: str,
    slug: str,
    cost: float,
    started: datetime,
    util_5h_start: float | None = None,
    util_5h_end: float | None = None,
    util_7d_start: float | None = None,
    util_7d_end: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO runs(
            id, job_slug, started_at, ended_at, exit_code, trigger,
            cost_usd, run_dir,
            five_hour_util_at_start, five_hour_util_at_end,
            seven_day_util_at_start, seven_day_util_at_end
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            slug,
            started.isoformat(),
            (started + timedelta(minutes=5)).isoformat(),
            0,
            "schedule",
            cost,
            "/tmp/x",
            util_5h_start,
            util_5h_end,
            util_7d_start,
            util_7d_end,
        ),
    )


def test_cost_estimate_cold_start_under_min_samples(tmp_path: Path):
    p = _setup_db(tmp_path)
    with db.connect(p) as conn:
        _insert_completed_run(conn, run_id="r1", slug="j", cost=9.99, started=NOW - timedelta(hours=1))
        settings = AutoSettings(coldstart_min_samples=3, coldstart_cost_usd=0.25)
        est = queries.auto_job_cost_estimate(conn, "j", settings)
    assert est.is_cold_start is True
    assert est.sample_size == 1
    assert est.avg_cost_usd == 0.25  # cold-start default, learned cost ignored


def test_cost_estimate_learned_from_history(tmp_path: Path):
    p = _setup_db(tmp_path)
    with db.connect(p) as conn:
        for i, cost in enumerate([0.10, 0.20, 0.30, 0.40, 0.50]):
            _insert_completed_run(
                conn,
                run_id=f"r{i}",
                slug="j",
                cost=cost,
                started=NOW - timedelta(hours=i + 1),
                util_5h_start=10.0,
                util_5h_end=10.0 + (i + 1) * 2,  # deltas: 2, 4, 6, 8, 10
                util_7d_start=5.0,
                util_7d_end=5.0 + (i + 1) * 0.5,  # deltas: 0.5, 1.0, 1.5, 2.0, 2.5
            )
        settings = AutoSettings(coldstart_min_samples=3)
        est = queries.auto_job_cost_estimate(conn, "j", settings)
    assert est.is_cold_start is False
    assert est.sample_size == 5
    assert est.avg_cost_usd == pytest.approx(0.30)
    assert est.median_5h_util_delta == 6.0  # median of [2,4,6,8,10]
    assert est.median_7d_util_delta == 1.5  # median of [.5,1,1.5,2,2.5]


def test_cost_estimate_missing_snapshots_fall_back_to_coldstart_delta(
    tmp_path: Path,
):
    """Enough samples to not cold-start overall, but the util-delta medians
    are None because no run has a snapshot pair. Each delta field
    independently falls back to its cold-start default."""
    p = _setup_db(tmp_path)
    with db.connect(p) as conn:
        for i in range(5):
            _insert_completed_run(
                conn,
                run_id=f"r{i}",
                slug="j",
                cost=0.10,
                started=NOW - timedelta(hours=i + 1),
                # All snapshots NULL (poller was off).
            )
        settings = AutoSettings(
            coldstart_min_samples=3,
            coldstart_5h_util_delta=7.0,
            coldstart_7d_util_delta=1.0,
        )
        est = queries.auto_job_cost_estimate(conn, "j", settings)
    assert est.is_cold_start is False
    assert est.median_5h_util_delta == 7.0
    assert est.median_7d_util_delta == 1.0
    assert est.avg_cost_usd == pytest.approx(0.10)


def test_load_auto_settings_applies_defaults(tmp_path: Path):
    p = _setup_db(tmp_path)
    with db.connect(p) as conn:
        s = queries.load_auto_settings(conn)
    # Migration 005 seeds defaults, so we should get the seeded values.
    assert s.daytime_start_local == "07:00"
    assert s.daytime_end_local == "22:00"
    assert s.util_5h_day_normal == 60.0
    assert s.coldstart_min_samples == 3
