from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from claude_p import queries
from claude_p.db import connect
from claude_p.models import RunSummary

ScheduleMode = Literal["cron", "auto", "manual"]

router = APIRouter()


def _state(request: Request):
    return request.app.state.claude_p


def _describe_schedule(entry, sched) -> tuple[str, ScheduleMode]:
    """Return (schedule_text, mode) for the jobs-list row."""
    if sched is None:
        return "—", "manual"
    if sched.mode == "auto":
        every = entry.manifest.auto.every if entry and entry.manifest and entry.manifest.auto else "?"
        return f"auto (every {every})", "auto"
    return (sched.cron or "—", "cron")


def _next_fire_text(sched, now: datetime) -> str:
    if sched is None:
        return "—"
    if sched.mode == "auto":
        if sched.deferred_since is not None:
            age_s = int((now - sched.deferred_since).total_seconds())
            return f"deferred {age_s}s"
        return "on next tick"
    if sched.next_fire_at is not None:
        return f"{int((sched.next_fire_at - now).total_seconds())}s"
    return "—"


@router.get("/", response_class=HTMLResponse)
async def jobs_list(request: Request):
    st = _state(request)
    with connect(st.cfg.db_path) as conn:
        states = queries.list_job_states(conn)
        schedules = queries.list_schedules(conn)
        last_runs = queries.last_runs_by_slug(conn)

    now = datetime.now(UTC)
    summaries: list[RunSummary] = []
    for slug, entry in sorted(st.registry.entries.items()):
        state = states.get(slug)
        sched = schedules.get(slug)
        last = last_runs.get(slug)
        sched_text, mode = _describe_schedule(entry, sched)
        summaries.append(
            RunSummary(
                slug=slug,
                description=entry.manifest.description if entry.manifest else "(invalid)",
                runtime=entry.manifest.runtime if entry.manifest else "—",
                schedule=sched_text,
                next_fire_in=_next_fire_text(sched, now),
                error=entry.error or (state.manifest_error if state else None),
                disabled=bool(state.disabled_reason) if state else False,
                last_run_id=last.id if last else None,
                last_run_exit=last.exit_code if last else None,
                last_run_cost=last.cost_usd if last else 0.0,
                last_run_at=last.started_at if last else None,
                running=st.scheduler.is_running(slug),
                mode=mode,
                deferred_since=sched.deferred_since if sched else None,
            )
        )
    return st.templates.TemplateResponse(request, "jobs_list.html", {"jobs": summaries, "active": "jobs"})


@router.get("/jobs/{slug}", response_class=HTMLResponse)
async def job_detail(slug: str, request: Request):
    st = _state(request)
    entry = st.registry.entries.get(slug)
    if entry is None:
        raise HTTPException(404, "job not found")
    with connect(st.cfg.db_path) as conn:
        runs = queries.list_runs_for_job(conn, slug, limit=50)
        schedule = queries.get_schedule(conn, slug)
        state = queries.get_job_state(conn, slug)
        # Only compute an estimate for auto jobs — it's the only page that uses it.
        cost_estimate = None
        if schedule is not None and schedule.mode == "auto":
            settings = queries.load_auto_settings(conn)
            cost_estimate = queries.auto_job_cost_estimate(conn, slug, settings)

    manifest_path = entry.path / "job.yaml"
    manifest_text = manifest_path.read_text() if manifest_path.exists() else ""
    return st.templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "slug": slug,
            "entry": entry,
            "runs": runs,
            "schedule": schedule,
            "state": state,
            "manifest_text": manifest_text,
            "running": st.scheduler.is_running(slug),
            "cost_estimate": cost_estimate,
            "active": "jobs",
        },
    )


@router.post("/jobs/{slug}/run")
async def job_run_now(slug: str, request: Request):
    import asyncio

    st = _state(request)
    entry = st.registry.entries.get(slug)
    if entry is None or entry.manifest is None:
        raise HTTPException(404, "job not available")
    asyncio.create_task(st.scheduler.trigger(slug, "manual"))
    return RedirectResponse(f"/jobs/{slug}", status_code=303)


@router.post("/jobs/{slug}/disable")
async def job_disable(slug: str, request: Request):
    st = _state(request)
    with connect(st.cfg.db_path) as conn:
        queries.set_job_disabled(conn, slug, "manually disabled")
    return RedirectResponse(f"/jobs/{slug}", status_code=303)


@router.post("/jobs/{slug}/enable")
async def job_enable(slug: str, request: Request):
    st = _state(request)
    with connect(st.cfg.db_path) as conn:
        queries.set_job_disabled(conn, slug, None)
    return RedirectResponse(f"/jobs/{slug}", status_code=303)
