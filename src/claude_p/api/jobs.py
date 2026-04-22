from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from claude_p.db import connect

router = APIRouter()


def _state(request: Request):
    return request.app.state.claude_p


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse)
async def jobs_list(request: Request):
    st = _state(request)
    with connect(st.cfg.db_path) as conn:
        state_rows = {
            r["slug"]: dict(r)
            for r in conn.execute(
                "SELECT slug, last_seen_at, disabled_reason, manifest_error FROM jobs_state"
            ).fetchall()
        }
        schedule_rows = {
            r["slug"]: dict(r)
            for r in conn.execute(
                "SELECT slug, cron, next_fire_at, last_fire_at FROM schedules"
            ).fetchall()
        }
        last_runs = {
            r["job_slug"]: dict(r)
            for r in conn.execute(
                """
                SELECT r.job_slug, r.id, r.started_at, r.ended_at, r.exit_code, r.cost_usd
                FROM runs r
                JOIN (
                    SELECT job_slug, MAX(started_at) as ts FROM runs GROUP BY job_slug
                ) m ON m.job_slug = r.job_slug AND m.ts = r.started_at
                """
            ).fetchall()
        }

    now = datetime.now(timezone.utc)
    entries = []
    for slug, entry in sorted(st.registry.entries.items()):
        state = state_rows.get(slug, {})
        sched = schedule_rows.get(slug, {})
        last = last_runs.get(slug, {})
        next_fire = _dt(sched.get("next_fire_at"))
        entries.append(
            {
                "slug": slug,
                "description": entry.manifest.description if entry.manifest else "(invalid)",
                "runtime": entry.manifest.runtime if entry.manifest else "—",
                "schedule": sched.get("cron") or "—",
                "next_fire_in": (
                    f"{int((next_fire - now).total_seconds())}s"
                    if next_fire
                    else "—"
                ),
                "error": entry.error or state.get("manifest_error"),
                "disabled": bool(state.get("disabled_reason")),
                "last_run_id": last.get("id"),
                "last_run_exit": last.get("exit_code"),
                "last_run_cost": last.get("cost_usd") or 0,
                "last_run_at": last.get("started_at"),
                "running": st.scheduler.is_running(slug),
            }
        )
    return st.templates.TemplateResponse(
        request, "jobs_list.html", {"jobs": entries, "active": "jobs"}
    )


@router.get("/jobs/{slug}", response_class=HTMLResponse)
async def job_detail(slug: str, request: Request):
    st = _state(request)
    entry = st.registry.entries.get(slug)
    if entry is None:
        raise HTTPException(404, "job not found")
    with connect(st.cfg.db_path) as conn:
        runs = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, started_at, ended_at, exit_code, trigger, cost_usd,
                       input_tokens, output_tokens, error
                FROM runs WHERE job_slug=? ORDER BY started_at DESC LIMIT 50
                """,
                (slug,),
            ).fetchall()
        ]
        schedule = conn.execute(
            "SELECT * FROM schedules WHERE slug=?", (slug,)
        ).fetchone()
        state = conn.execute(
            "SELECT * FROM jobs_state WHERE slug=?", (slug,)
        ).fetchone()

    manifest_text = (entry.path / "job.yaml").read_text() if (entry.path / "job.yaml").exists() else ""
    return st.templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "slug": slug,
            "entry": entry,
            "runs": runs,
            "schedule": dict(schedule) if schedule else None,
            "state": dict(state) if state else None,
            "manifest_text": manifest_text,
            "running": st.scheduler.is_running(slug),
            "active": "jobs",
        },
    )


@router.post("/jobs/{slug}/run")
async def job_run_now(slug: str, request: Request):
    st = _state(request)
    entry = st.registry.entries.get(slug)
    if entry is None or entry.manifest is None:
        raise HTTPException(404, "job not available")
    # Kick off asynchronously so we can redirect immediately.
    import asyncio

    asyncio.create_task(st.scheduler.trigger(slug, "manual"))
    return RedirectResponse(f"/jobs/{slug}", status_code=303)


@router.post("/jobs/{slug}/disable")
async def job_disable(slug: str, request: Request):
    st = _state(request)
    with connect(st.cfg.db_path) as conn:
        conn.execute(
            "UPDATE jobs_state SET disabled_reason=? WHERE slug=?",
            ("manually disabled", slug),
        )
    return RedirectResponse(f"/jobs/{slug}", status_code=303)


@router.post("/jobs/{slug}/enable")
async def job_enable(slug: str, request: Request):
    st = _state(request)
    with connect(st.cfg.db_path) as conn:
        conn.execute("UPDATE jobs_state SET disabled_reason=NULL WHERE slug=?", (slug,))
    return RedirectResponse(f"/jobs/{slug}", status_code=303)
