from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from claude_p import queries
from claude_p.db import connect, get_setting
from claude_p.ledger import per_job_rollups, window_totals
from claude_p.models import CLAUDE_AI_ENABLED_SETTING

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/ledger", response_class=HTMLResponse)
async def ledger_page(request: Request):
    st = request.app.state.claude_p
    with connect(st.cfg.db_path) as conn:
        claude_ai_enabled = get_setting(conn, CLAUDE_AI_ENABLED_SETTING) == "1"
        claude_ai_windows = queries.list_claude_ai_windows(conn) if claude_ai_enabled else []
        claude_ai_extra = (
            queries.get_claude_ai_extra_usage(conn) if claude_ai_enabled else None
        )
    return st.templates.TemplateResponse(
        request,
        "ledger.html",
        {
            "w5h": window_totals(st.cfg.db_path, 5),
            "w24h": window_totals(st.cfg.db_path, 24),
            "w7d": window_totals(st.cfg.db_path, 24 * 7),
            "rollups": sorted(per_job_rollups(st.cfg.db_path), key=lambda r: r.slug),
            "claude_ai_enabled": claude_ai_enabled,
            "claude_ai_windows": claude_ai_windows,
            "claude_ai_extra": claude_ai_extra,
            "now": datetime.now(timezone.utc),
            "active": "ledger",
        },
    )
