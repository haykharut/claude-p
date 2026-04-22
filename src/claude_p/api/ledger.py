from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from claude_p import queries
from claude_p.claude_runner import build_claude_argv
from claude_p.db import connect
from claude_p.ledger import (
    model_usage_window,
    per_job_rollups,
    rate_limit_snapshots,
    set_weekly_budget,
    weekly_budget,
    window_totals,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/ledger", response_class=HTMLResponse)
async def ledger_page(request: Request):
    st = request.app.state.claude_p
    snapshots = rate_limit_snapshots(st.cfg.db_path)
    return st.templates.TemplateResponse(
        request,
        "ledger.html",
        {
            "w5h": window_totals(st.cfg.db_path, 5),
            "w24h": window_totals(st.cfg.db_path, 24),
            "w7d": window_totals(st.cfg.db_path, 24 * 7),
            "rollups": sorted(per_job_rollups(st.cfg.db_path), key=lambda r: r.slug),
            "model_usage_7d": model_usage_window(st.cfg.db_path, 24 * 7),
            "budget": weekly_budget(st.cfg.db_path),
            "rate_limits": snapshots,
            "now": datetime.now(timezone.utc),
            "active": "ledger",
        },
    )


@router.post("/ledger/budget")
async def ledger_set_budget(request: Request, amount: float = Form(...)):
    st = request.app.state.claude_p
    set_weekly_budget(st.cfg.db_path, amount)
    return RedirectResponse("/ledger", status_code=303)


@router.post("/ledger/probe")
async def ledger_probe(request: Request):
    """Fire a tiny `claude -p` call so the rate_limit_event comes back and we
    can populate rate_limit_snapshots without waiting for a real job run.
    Costs well under a cent — a single 'ok' reply.
    """
    st = request.app.state.claude_p
    asyncio.create_task(_probe_rate_limits(st.cfg))
    return RedirectResponse("/ledger", status_code=303)


async def _probe_rate_limits(cfg) -> None:
    run_id = f"probe-{uuid.uuid4().hex[:8]}"
    argv = build_claude_argv(
        "reply with a single word: ok",
        claude_cli=cfg.claude_cli,
        allowed_tools=[],
        permission_mode="dontAsk",
        max_budget_usd=0.01,
        max_turns=1,
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        log.warning("probe: claude CLI not found")
        return

    events: list[dict] = []
    import json as _json

    assert proc.stdout is not None
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").strip()
        if not line:
            continue
        try:
            event = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if event.get("type") == "rate_limit_event":
            info = event.get("rate_limit_info")
            if isinstance(info, dict):
                events.append(info)
    await proc.wait()

    if not events:
        log.info("probe: no rate_limit_event observed (claude version may not emit it)")
        return

    observed_at = datetime.now(timezone.utc)
    with connect(cfg.db_path) as conn:
        for info in events:
            rl_type = info.get("rateLimitType")
            status = info.get("status")
            resets_at_epoch = info.get("resetsAt")
            if not rl_type or not status or resets_at_epoch is None:
                continue
            try:
                resets_at = datetime.fromtimestamp(int(resets_at_epoch), tz=timezone.utc)
            except (TypeError, ValueError):
                continue
            overage_epoch = info.get("overageResetsAt")
            overage_resets_at = None
            if overage_epoch is not None:
                try:
                    overage_resets_at = datetime.fromtimestamp(
                        int(overage_epoch), tz=timezone.utc
                    )
                except (TypeError, ValueError):
                    overage_resets_at = None
            queries.upsert_rate_limit_snapshot(
                conn,
                rate_limit_type=rl_type,
                status=status,
                resets_at=resets_at,
                overage_status=info.get("overageStatus"),
                overage_resets_at=overage_resets_at,
                is_using_overage=bool(info.get("isUsingOverage")),
                observed_at=observed_at,
                observed_run_id=run_id,
            )
    log.info("probe: persisted %d rate limit snapshot(s)", len(events))
