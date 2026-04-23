"""
Pydantic models for everything that crosses a module boundary.

The rule: if it moves between DB ↔ queries ↔ API ↔ templates, it's a model
here. Internal mutable state that never leaves a module (e.g. a stream-json
accumulator) stays as a plain dataclass in its owning module.

Models mirror the SQL schema in `migrations/001_initial.sql`. When the
schema changes, update both together (new migration file + model diff).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from claude_p.manifest import Manifest

Trigger = Literal["schedule", "manual"]

BackendEventKind = Literal[
    "session_start",
    "assistant_text_delta",
    "tool_use",
    "tool_result",
    "rate_limit",
    "result",
    "raw",
]


class BackendEvent(BaseModel):
    """One canonical event emitted by a `Backend.stream()` iterator.

    Every backend (claude CLI, codex CLI, HTTP API, …) converts its native
    stream into these. The folding helper in `backends/base.py` turns them
    into a `BackendResult`.
    """

    kind: BackendEventKind
    data: dict[str, Any] = Field(default_factory=dict)


class BackendResult(BaseModel):
    """Accumulated outcome of one Backend invocation. Replaces the old
    `ClaudeResult` dataclass. Folded from the `result` event (authoritative
    for cost/tokens) plus running accumulation from other events."""

    text: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    session_id: str | None = None
    num_turns: int = 0
    is_error: bool = False
    stop_reason: str | None = None
    # Per-model breakdown, keyed by model name. Values are the raw dict
    # from the backend's `result` event (costUSD, inputTokens, …) — the
    # executor-side persistence code reads these keys by name.
    model_usage: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Every rate-limit observation during the run. Persisted as snapshots
    # by the executor so the dashboard can show them.
    rate_limit_events: list[dict[str, Any]] = Field(default_factory=list)


class RunOptions(BaseModel):
    """Input to `Backend.stream()`. Common params on the struct; anything
    backend-native goes into `backend_options` so the shared surface stays
    small and HTTP backends don't have to carry CLI-isms."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    prompt: str
    model: str | None = None
    system_prompt: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = 1.0
    timeout_seconds: float | None = None
    cwd: str | Path | None = None
    backend_options: dict[str, Any] = Field(default_factory=dict)


class Run(BaseModel):
    """One row of the `runs` table."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    job_slug: str
    started_at: datetime
    ended_at: datetime | None = None
    exit_code: int | None = None
    trigger: Trigger
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    error: str | None = None
    run_dir: str
    # claude.ai poller snapshots at run boundaries. NULL when poller off.
    five_hour_util_at_start: float | None = None
    five_hour_util_at_end: float | None = None
    seven_day_util_at_start: float | None = None
    seven_day_util_at_end: float | None = None


class JobState(BaseModel):
    """One row of the `jobs_state` table."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    last_seen_at: datetime
    last_manifest_hash: str | None = None
    disabled_reason: str | None = None
    manifest_error: str | None = None


ScheduleMode = Literal["cron", "auto"]


class Schedule(BaseModel):
    """One row of the `schedules` table."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    cron: str | None = None
    next_fire_at: datetime | None = None
    last_fire_at: datetime | None = None
    mode: ScheduleMode = "cron"
    auto_config_json: str | None = None
    deferred_since: datetime | None = None


class WindowTotals(BaseModel):
    """Aggregated token/cost usage over a rolling time window."""

    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    run_count: int = 0


class JobRollup(BaseModel):
    """Per-job aggregation across the job's most-recent runs."""

    slug: str
    runs_last_10: int = 0
    avg_cost_usd: float = 0.0
    avg_input_tokens: float = 0.0
    avg_output_tokens: float = 0.0
    last_run_at: datetime | None = None


class JobCostEstimate(BaseModel):
    """Empirical per-job footprint, used by the auto-schedule algorithm to
    predict "if I fire this job now, how much does it cost?" before firing."""

    slug: str
    sample_size: int = 0
    avg_cost_usd: float = 0.0
    p90_cost_usd: float = 0.0
    median_5h_util_delta: float | None = None
    median_7d_util_delta: float | None = None
    is_cold_start: bool = True


class RegistryEntry(BaseModel):
    """Current state of one job folder known to the registry."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    slug: str
    path: Path
    manifest: Manifest | None = None
    error: str | None = None
    manifest_hash: str | None = None


class RunSummary(BaseModel):
    """Lightweight view model used by the jobs-list page."""

    slug: str
    description: str
    runtime: str
    schedule: str
    next_fire_in: str
    error: str | None = None
    disabled: bool = False
    last_run_id: str | None = None
    last_run_exit: int | None = None
    last_run_cost: float = 0.0
    last_run_at: datetime | None = None
    running: bool = False
    # Auto-schedule state. `mode` is 'cron', 'auto', or 'manual' (no schedule).
    mode: Literal["cron", "auto", "manual"] = "manual"
    deferred_since: datetime | None = None


class SettingKV(BaseModel):
    key: str
    value: str


class RateLimitSnapshot(BaseModel):
    """One row of `rate_limit_snapshots`. Mirrors the `rate_limit_event` in
    stream-json output — the only signal Claude gives us about subscription
    limits from outside the interactive `/usage` command."""

    model_config = ConfigDict(from_attributes=True)

    rate_limit_type: str
    status: str
    resets_at: datetime
    overage_status: str | None = None
    overage_resets_at: datetime | None = None
    is_using_overage: bool = False
    observed_at: datetime
    observed_run_id: str | None = None


class ModelUsage(BaseModel):
    """Per-model cost/token breakdown — aggregated from `run_model_usage`."""

    model: str
    runs: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


def as_rate_limit_snapshot(row) -> RateLimitSnapshot:
    d = dict(row)
    if "is_using_overage" in d and d["is_using_overage"] is not None:
        d["is_using_overage"] = bool(d["is_using_overage"])
    return RateLimitSnapshot.model_validate(d)


class ClaudeAiUsageWindow(BaseModel):
    """One window-keyed row from claude_ai_usage (excluding __extra_usage__)."""

    model_config = ConfigDict(from_attributes=True)

    window_key: str
    utilization: float | None = None
    resets_at: datetime | None = None
    observed_at: datetime


class ClaudeAiExtraUsage(BaseModel):
    """The __extra_usage__ singleton — credit-pool data for subscriptions
    that have extra-usage enabled (e.g. the Max plan's credit top-up)."""

    model_config = ConfigDict(from_attributes=True)

    is_enabled: bool = False
    monthly_limit: int | None = None
    used_credits: float | None = None
    utilization: float | None = None
    currency: str | None = None
    observed_at: datetime


# Settings keys for claude.ai integration (stored in the `settings` table).
CLAUDE_AI_SESSION_KEY_SETTING: str = "claude_ai_session_key"
CLAUDE_AI_ORG_ID_SETTING: str = "claude_ai_org_id"
CLAUDE_AI_CF_CLEARANCE_SETTING: str = "claude_ai_cf_clearance"
CLAUDE_AI_ENABLED_SETTING: str = "claude_ai_enabled"
CLAUDE_AI_LAST_ERROR_SETTING: str = "claude_ai_last_error"
CLAUDE_AI_LAST_OK_AT_SETTING: str = "claude_ai_last_ok_at"


WEEKLY_BUDGET_SETTING: str = "weekly_budget_usd"
DASHBOARD_PASSWORD_SETTING: str = "dashboard_password_hash"

# claude.ai /usage window keys the auto-schedule algorithm reads. If a
# future Anthropic deploy moves the signal (e.g. `claude -p` consumption
# lands in a different window_key), change these two constants only.
# See the plan's "Empirical check" note for the verification procedure.
FIVE_HOUR_WINDOW_KEY: str = "five_hour"
SEVEN_DAY_WINDOW_KEY: str = "seven_day"

# Auto-schedule settings (migration 005 seeds defaults).
AUTO_DAYTIME_START_LOCAL_SETTING: str = "auto_daytime_start_local"
AUTO_DAYTIME_END_LOCAL_SETTING: str = "auto_daytime_end_local"
AUTO_LOCAL_TZ_SETTING: str = "auto_local_tz"
AUTO_5H_UTIL_DAY_NORMAL_SETTING: str = "auto_5h_util_day_normal"
AUTO_5H_UTIL_NIGHT_NORMAL_SETTING: str = "auto_5h_util_night_normal"
AUTO_5H_UTIL_DAY_LOW_SETTING: str = "auto_5h_util_day_low"
AUTO_5H_UTIL_NIGHT_LOW_SETTING: str = "auto_5h_util_night_low"
AUTO_WEEKLY_SKIP_ABOVE_SETTING: str = "auto_weekly_skip_above"
AUTO_WEEKLY_BUDGET_GUARD_SETTING: str = "auto_weekly_budget_guard"
AUTO_MIN_SECONDS_BETWEEN_FIRES_SETTING: str = "auto_min_seconds_between_fires"
AUTO_SAFETY_FACTOR_SETTING: str = "auto_safety_factor"
AUTO_COLDSTART_5H_UTIL_DELTA_SETTING: str = "auto_coldstart_5h_util_delta"
AUTO_COLDSTART_7D_UTIL_DELTA_SETTING: str = "auto_coldstart_7d_util_delta"
AUTO_COLDSTART_COST_USD_SETTING: str = "auto_coldstart_cost_usd"
AUTO_COLDSTART_MIN_SAMPLES_SETTING: str = "auto_coldstart_min_samples"


class AutoSettings(BaseModel):
    """Flat snapshot of all auto_* settings, loaded once per scheduler tick."""

    daytime_start_local: str = "07:00"
    daytime_end_local: str = "22:00"
    local_tz: str = "UTC"
    util_5h_day_normal: float = 60.0
    util_5h_night_normal: float = 85.0
    util_5h_day_low: float = 30.0
    util_5h_night_low: float = 70.0
    weekly_skip_above: float = 90.0
    weekly_budget_guard: float = 1.0
    min_seconds_between_fires: float = 120.0
    safety_factor: float = 1.25
    coldstart_5h_util_delta: float = 10.0
    coldstart_7d_util_delta: float = 2.0
    coldstart_cost_usd: float = 0.25
    coldstart_min_samples: int = 3

    def threshold_5h(self, *, nighttime: bool, priority: str) -> float:
        if priority == "low":
            return self.util_5h_night_low if nighttime else self.util_5h_day_low
        return self.util_5h_night_normal if nighttime else self.util_5h_day_normal


def as_run(row) -> Run:
    """Adapt a sqlite3.Row (or dict) into a Run model."""
    return Run.model_validate(dict(row))


def as_job_state(row) -> JobState:
    return JobState.model_validate(dict(row))


def as_schedule(row) -> Schedule:
    return Schedule.model_validate(dict(row))
