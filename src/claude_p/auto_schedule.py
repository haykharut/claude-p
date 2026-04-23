"""
Per-tick decision algorithm for `schedule: auto` jobs.

This module is pure Python — no DB, no I/O. `scheduler._tick()` gathers the
inputs (auto schedules, current claude.ai utilization, ledger spend, weekly
budget, per-job estimates, settings) and calls `decide_batch()`. The return
value tells the scheduler which jobs to fire, which to defer, and which to
stamp `deferred_since` on.

Keeping this module pure makes it trivially unit-testable — no temp DBs,
no time-freezing tricks, just dataclass inputs and dict outputs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from claude_p.manifest import AutoConfig
from claude_p.models import AutoSettings, JobCostEstimate, Schedule

log = logging.getLogger(__name__)

Verdict = Literal["fire", "defer", "skip"]


@dataclass
class AutoDecision:
    verdict: Verdict
    reason: str  # short human-readable, for logs + dashboard


@dataclass
class AutoInputs:
    now_utc: datetime
    schedules: list[Schedule]
    auto_configs: dict[str, AutoConfig]
    estimates: dict[str, JobCostEstimate]
    five_hour_util: float | None
    seven_day_util: float | None
    spend_7d: float
    weekly_budget: float
    settings: AutoSettings
    # Slugs currently executing — scheduler populates this so decide_batch
    # doesn't double-fire a job that's mid-run from the previous tick.
    in_flight: set[str] = field(default_factory=set)
    # Most recent last_fire_at across all auto jobs. Drives the fleet-wide
    # `min_seconds_between_fires` gate.
    last_auto_fire_at: datetime | None = None


def _load_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log.warning("auto: unknown timezone %r, falling back to UTC", name)
        return ZoneInfo("UTC")


def _parse_hhmm(s: str) -> int:
    """`HH:MM` → minutes since local midnight. 0 on parse failure."""
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return 0


def is_nighttime(now_utc: datetime, settings: AutoSettings) -> bool:
    tz = _load_tz(settings.local_tz)
    local = now_utc.astimezone(tz)
    minute_of_day = local.hour * 60 + local.minute
    start = _parse_hhmm(settings.daytime_start_local)
    end = _parse_hhmm(settings.daytime_end_local)
    if start == end:
        return False  # degenerate; treat as always-daytime
    if start < end:
        # Normal case: daytime is a contiguous span within one day.
        return minute_of_day < start or minute_of_day >= end
    # Wrap case: daytime crosses midnight (start > end, e.g. 22:00-06:00).
    return end <= minute_of_day < start


def decide_one(
    *,
    schedule: Schedule,
    auto_config: AutoConfig,
    estimate: JobCostEstimate,
    now_utc: datetime,
    five_hour_util: float | None,
    seven_day_util: float | None,
    spend_7d: float,
    weekly_budget: float,
    settings: AutoSettings,
) -> AutoDecision:
    """Pure decision for one auto job."""
    every_s = auto_config.every_seconds
    deadline_s = auto_config.deadline_seconds
    last_fire = schedule.last_fire_at

    if last_fire is not None:
        elapsed = (now_utc - last_fire).total_seconds()
        if elapsed < every_s:
            return AutoDecision(
                "skip",
                f"not due yet ({int(elapsed)}s since last fire, cadence {int(every_s)}s)",
            )
        if elapsed >= deadline_s:
            return AutoDecision(
                "fire",
                f"past deadline ({int(elapsed)}s >= {int(deadline_s)}s)",
            )
    # last_fire is None → job has never run; always due.

    # Predicted states after firing this specific job.
    sf = settings.safety_factor
    expected_cost = estimate.avg_cost_usd * sf
    predicted_5h = (five_hour_util or 0.0) + (estimate.median_5h_util_delta or 0.0) * sf
    predicted_7d = (seven_day_util or 0.0) + (estimate.median_7d_util_delta or 0.0) * sf

    if seven_day_util is not None and predicted_7d >= settings.weekly_skip_above:
        return AutoDecision(
            "defer",
            f"weekly util would hit {predicted_7d:.1f}% (cap {settings.weekly_skip_above:.0f})",
        )

    if weekly_budget > 0:
        projected = (spend_7d + expected_cost) / weekly_budget
        if projected >= settings.weekly_budget_guard:
            return AutoDecision(
                "defer",
                f"would use {projected * 100:.1f}% of weekly budget "
                f"(guard {settings.weekly_budget_guard:.2f})",
            )

    nighttime = is_nighttime(now_utc, settings)
    threshold = settings.threshold_5h(nighttime=nighttime, priority=auto_config.priority)
    if five_hour_util is not None and predicted_5h >= threshold:
        when = "night" if nighttime else "day"
        return AutoDecision(
            "defer",
            f"5h util would hit {predicted_5h:.1f}% ({when} cap {threshold:.0f})",
        )

    return AutoDecision("fire", "within thresholds")


def decide_batch(inputs: AutoInputs) -> dict[str, AutoDecision]:
    """Decide for every auto job. Enforces thundering-herd guards:
    - at most one "fire" verdict per tick;
    - no fires at all if `last_auto_fire_at` is within `min_seconds_between_fires`.
    In-flight jobs are skipped."""
    decisions: dict[str, AutoDecision] = {}
    min_gap = inputs.settings.min_seconds_between_fires
    fleet_cooling_down = (
        inputs.last_auto_fire_at is not None
        and (inputs.now_utc - inputs.last_auto_fire_at).total_seconds() < min_gap
    )
    fire_count = 0
    for sched in inputs.schedules:
        slug = sched.slug
        if slug in inputs.in_flight:
            decisions[slug] = AutoDecision("skip", "already running")
            continue
        cfg = inputs.auto_configs.get(slug)
        est = inputs.estimates.get(slug)
        if cfg is None or est is None:
            decisions[slug] = AutoDecision("skip", "no config or estimate available")
            continue
        d = decide_one(
            schedule=sched,
            auto_config=cfg,
            estimate=est,
            now_utc=inputs.now_utc,
            five_hour_util=inputs.five_hour_util,
            seven_day_util=inputs.seven_day_util,
            spend_7d=inputs.spend_7d,
            weekly_budget=inputs.weekly_budget,
            settings=inputs.settings,
        )
        if d.verdict == "fire":
            if fleet_cooling_down:
                decisions[slug] = AutoDecision(
                    "defer",
                    f"fleet cooldown ({int(min_gap)}s between auto fires)",
                )
                continue
            if fire_count >= 1:
                decisions[slug] = AutoDecision(
                    "defer",
                    "rate-limited (another auto job fired this tick)",
                )
                continue
            fire_count += 1
        decisions[slug] = d
    return decisions
