from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from claude_p import queries
from claude_p.db import connect
from claude_p.models import RunSummary

router = APIRouter()


def _state(request: Request):
    return request.app.state.claude_p


@router.get("/", response_class=HTMLResponse)
async def jobs_list(request: Request):
    st = _state(request)
    with connect(st.cfg.db_path) as conn:
        states = queries.list_job_states(conn)
        schedules = queries.list_schedules(conn)
        last_runs = queries.last_runs_by_slug(conn)

    now = datetime.now(timezone.utc)
    summaries: list[RunSummary] = []
    for slug, entry in sorted(st.registry.entries.items()):
        state = states.get(slug)
        sched = schedules.get(slug)
        last = last_runs.get(slug)
        next_fire = sched.next_fire_at if sched else None
        summaries.append(
            RunSummary(
                slug=slug,
                description=entry.manifest.description if entry.manifest else "(invalid)",
                runtime=entry.manifest.runtime if entry.manifest else "—",
                schedule=sched.cron if sched else "—",
                next_fire_in=(
                    f"{int((next_fire - now).total_seconds())}s" if next_fire else "—"
                ),
                error=entry.error or (state.manifest_error if state else None),
                disabled=bool(state.disabled_reason) if state else False,
                last_run_id=last.id if last else None,
                last_run_exit=last.exit_code if last else None,
                last_run_cost=last.cost_usd if last else 0.0,
                last_run_at=last.started_at if last else None,
                running=st.scheduler.is_running(slug),
            )
        )
    return st.templates.TemplateResponse(
        request, "jobs_list.html", {"jobs": summaries, "active": "jobs"}
    )


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
