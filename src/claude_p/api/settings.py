from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from claude_p import claude_ai
from claude_p.db import connect, get_setting, set_setting
from claude_p.models import (
    CLAUDE_AI_ENABLED_SETTING,
    CLAUDE_AI_LAST_ERROR_SETTING,
    CLAUDE_AI_LAST_OK_AT_SETTING,
    CLAUDE_AI_ORG_ID_SETTING,
    CLAUDE_AI_SESSION_KEY_SETTING,
)
from claude_p.net import dashboard_urls, detect_host, webdav_urls

log = logging.getLogger(__name__)
router = APIRouter()


def _mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return value[:2] + "…"
    return value[:6] + "…" + value[-4:]


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    st = request.app.state.claude_p
    host = detect_host(st.cfg.bind_port)
    with connect(st.cfg.db_path) as conn:
        ctx = {
            "enabled": get_setting(conn, CLAUDE_AI_ENABLED_SETTING) == "1",
            "session_key_masked": _mask(get_setting(conn, CLAUDE_AI_SESSION_KEY_SETTING)),
            "org_id": get_setting(conn, CLAUDE_AI_ORG_ID_SETTING) or "",
            "last_ok_at": get_setting(conn, CLAUDE_AI_LAST_OK_AT_SETTING),
            "last_error": get_setting(conn, CLAUDE_AI_LAST_ERROR_SETTING),
        }
    return st.templates.TemplateResponse(
        request,
        "settings.html",
        {
            **ctx,
            "dashboard_urls": dashboard_urls(host),
            "webdav_urls": webdav_urls(host),
            "bind_host": st.cfg.bind_host,
            "active": "settings",
        },
    )


@router.post("/settings/claude-ai")
async def settings_save(
    request: Request,
    session_key: str = Form(""),
    org_id: str = Form(""),
    enabled: str = Form(""),
):
    st = request.app.state.claude_p
    with connect(st.cfg.db_path) as conn:
        if session_key.strip():
            set_setting(conn, CLAUDE_AI_SESSION_KEY_SETTING, session_key.strip())
        if org_id.strip():
            set_setting(conn, CLAUDE_AI_ORG_ID_SETTING, org_id.strip())
        set_setting(conn, CLAUDE_AI_ENABLED_SETTING, "1" if enabled == "on" else "0")
        set_setting(conn, CLAUDE_AI_LAST_ERROR_SETTING, "")

    # Synchronously probe the endpoint so the user gets immediate feedback
    # about whether what they pasted actually works.
    await claude_ai.poll_once(st.cfg)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/claude-ai/clear")
async def settings_clear(request: Request):
    st = request.app.state.claude_p
    with connect(st.cfg.db_path) as conn:
        for key in (
            CLAUDE_AI_SESSION_KEY_SETTING,
            CLAUDE_AI_LAST_ERROR_SETTING,
            CLAUDE_AI_LAST_OK_AT_SETTING,
        ):
            set_setting(conn, key, "")
        set_setting(conn, CLAUDE_AI_ENABLED_SETTING, "0")
    return RedirectResponse("/settings", status_code=303)
