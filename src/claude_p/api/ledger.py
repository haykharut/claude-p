from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from claude_p.ledger import per_job_rollups, set_weekly_budget, weekly_budget, window_totals

router = APIRouter()


@router.get("/ledger", response_class=HTMLResponse)
async def ledger_page(request: Request):
    st = request.app.state.claude_p
    return st.templates.TemplateResponse(
        request,
        "ledger.html",
        {
            "w5h": window_totals(st.cfg.db_path, 5),
            "w24h": window_totals(st.cfg.db_path, 24),
            "w7d": window_totals(st.cfg.db_path, 24 * 7),
            "rollups": sorted(per_job_rollups(st.cfg.db_path), key=lambda r: r.slug),
            "budget": weekly_budget(st.cfg.db_path),
            "active": "ledger",
        },
    )


@router.post("/ledger/budget")
async def ledger_set_budget(request: Request, amount: float = Form(...)):
    st = request.app.state.claude_p
    set_weekly_budget(st.cfg.db_path, amount)
    return RedirectResponse("/ledger", status_code=303)
