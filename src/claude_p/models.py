"""
Pydantic models for everything that crosses a module boundary.

The rule: if it moves between DB ↔ queries ↔ API ↔ templates, it's a model
here. Internal mutable state that never leaves a module (the stream-json
accumulator, the in-flight Scaffold object) stays as a plain dataclass in
its owning module.

Models mirror the SQL schema in `migrations/001_initial.sql`. When the
schema changes, update both together (new migration file + model diff).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from claude_p.manifest import Manifest

Trigger = Literal["schedule", "manual", "scaffold"]


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


class JobState(BaseModel):
    """One row of the `jobs_state` table."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    last_seen_at: datetime
    last_manifest_hash: str | None = None
    disabled_reason: str | None = None
    manifest_error: str | None = None


class Schedule(BaseModel):
    """One row of the `schedules` table."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    cron: str
    next_fire_at: datetime | None = None
    last_fire_at: datetime | None = None


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


class ScaffoldInfo(BaseModel):
    """Response shape when the SSE stream ends — surfaced to the client."""

    exit_code: int | None = None
    error: str | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class SettingKV(BaseModel):
    key: str
    value: str


WEEKLY_BUDGET_SETTING: str = "weekly_budget_usd"
DASHBOARD_PASSWORD_SETTING: str = "dashboard_password_hash"


def as_run(row) -> Run:
    """Adapt a sqlite3.Row (or dict) into a Run model."""
    return Run.model_validate(dict(row))


def as_job_state(row) -> JobState:
    return JobState.model_validate(dict(row))


def as_schedule(row) -> Schedule:
    return Schedule.model_validate(dict(row))
