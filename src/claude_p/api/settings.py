from __future__ import annotations

import logging
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from claude_p import claude_ai, queries
from claude_p.db import connect, get_setting, set_setting
from claude_p.models import (
    AUTO_5H_UTIL_DAY_LOW_SETTING,
    AUTO_5H_UTIL_DAY_NORMAL_SETTING,
    AUTO_5H_UTIL_NIGHT_LOW_SETTING,
    AUTO_5H_UTIL_NIGHT_NORMAL_SETTING,
    AUTO_COLDSTART_5H_UTIL_DELTA_SETTING,
    AUTO_COLDSTART_7D_UTIL_DELTA_SETTING,
    AUTO_COLDSTART_COST_USD_SETTING,
    AUTO_COLDSTART_MIN_SAMPLES_SETTING,
    AUTO_DAYTIME_END_LOCAL_SETTING,
    AUTO_DAYTIME_START_LOCAL_SETTING,
    AUTO_LOCAL_TZ_SETTING,
    AUTO_MIN_SECONDS_BETWEEN_FIRES_SETTING,
    AUTO_SAFETY_FACTOR_SETTING,
    AUTO_WEEKLY_BUDGET_GUARD_SETTING,
    AUTO_WEEKLY_SKIP_ABOVE_SETTING,
    CLAUDE_AI_ENABLED_SETTING,
    CLAUDE_AI_LAST_ERROR_SETTING,
    CLAUDE_AI_LAST_OK_AT_SETTING,
    CLAUDE_AI_ORG_ID_SETTING,
    CLAUDE_AI_SESSION_KEY_SETTING,
)
from claude_p.net import dashboard_urls, detect_host, webdav_urls

_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _validate_hhmm(s: str) -> str:
    if not _HHMM_RE.match(s):
        raise HTTPException(400, f"time must be HH:MM, got {s!r}")
    h, m = s.split(":")
    hi, mi = int(h), int(m)
    if not (0 <= hi <= 23 and 0 <= mi <= 59):
        raise HTTPException(400, f"time out of range: {s!r}")
    return f"{hi:02d}:{mi:02d}"


def _validate_tz(s: str) -> str:
    try:
        ZoneInfo(s)
    except ZoneInfoNotFoundError as e:
        raise HTTPException(400, f"unknown timezone: {s!r}") from e
    return s


def _validate_percent(s: str, field: str) -> str:
    try:
        v = float(s)
    except ValueError as e:
        raise HTTPException(400, f"{field}: not a number: {s!r}") from e
    if not (0.0 <= v <= 100.0):
        raise HTTPException(400, f"{field}: must be in [0, 100], got {v}")
    return str(v)


def _validate_nonneg_float(s: str, field: str) -> str:
    try:
        v = float(s)
    except ValueError as e:
        raise HTTPException(400, f"{field}: not a number: {s!r}") from e
    if v < 0:
        raise HTTPException(400, f"{field}: must be non-negative, got {v}")
    return str(v)


def _validate_pos_int(s: str, field: str) -> str:
    try:
        v = int(s)
    except ValueError as e:
        raise HTTPException(400, f"{field}: not an integer: {s!r}") from e
    if v < 0:
        raise HTTPException(400, f"{field}: must be non-negative, got {v}")
    return str(v)


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
            "backend": st.cfg.backend,
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


@router.get("/settings/auto")
async def settings_auto_get(request: Request) -> dict:
    st = request.app.state.claude_p
    with connect(st.cfg.db_path) as conn:
        return queries.load_auto_settings(conn).model_dump()


@router.post("/settings/auto")
async def settings_auto_save(
    request: Request,
    daytime_start_local: str = Form(""),
    daytime_end_local: str = Form(""),
    local_tz: str = Form(""),
    util_5h_day_normal: str = Form(""),
    util_5h_night_normal: str = Form(""),
    util_5h_day_low: str = Form(""),
    util_5h_night_low: str = Form(""),
    weekly_skip_above: str = Form(""),
    weekly_budget_guard: str = Form(""),
    min_seconds_between_fires: str = Form(""),
    safety_factor: str = Form(""),
    coldstart_5h_util_delta: str = Form(""),
    coldstart_7d_util_delta: str = Form(""),
    coldstart_cost_usd: str = Form(""),
    coldstart_min_samples: str = Form(""),
    _redirect: bool = True,
):
    """Partial-update: only non-empty fields are written. Validates tight
    so a bad value fails fast rather than silently corrupting the gate."""
    st = request.app.state.claude_p
    to_set: list[tuple[str, str]] = []

    def _add(key: str, raw: str, validator):
        if raw.strip() == "":
            return
        to_set.append((key, validator(raw.strip())))

    _add(AUTO_DAYTIME_START_LOCAL_SETTING, daytime_start_local, _validate_hhmm)
    _add(AUTO_DAYTIME_END_LOCAL_SETTING, daytime_end_local, _validate_hhmm)
    _add(AUTO_LOCAL_TZ_SETTING, local_tz, _validate_tz)
    for key, raw in [
        (AUTO_5H_UTIL_DAY_NORMAL_SETTING, util_5h_day_normal),
        (AUTO_5H_UTIL_NIGHT_NORMAL_SETTING, util_5h_night_normal),
        (AUTO_5H_UTIL_DAY_LOW_SETTING, util_5h_day_low),
        (AUTO_5H_UTIL_NIGHT_LOW_SETTING, util_5h_night_low),
        (AUTO_WEEKLY_SKIP_ABOVE_SETTING, weekly_skip_above),
    ]:
        _add(key, raw, lambda s, k=key: _validate_percent(s, k))
    for key, raw in [
        (AUTO_WEEKLY_BUDGET_GUARD_SETTING, weekly_budget_guard),
        (AUTO_MIN_SECONDS_BETWEEN_FIRES_SETTING, min_seconds_between_fires),
        (AUTO_SAFETY_FACTOR_SETTING, safety_factor),
        (AUTO_COLDSTART_5H_UTIL_DELTA_SETTING, coldstart_5h_util_delta),
        (AUTO_COLDSTART_7D_UTIL_DELTA_SETTING, coldstart_7d_util_delta),
        (AUTO_COLDSTART_COST_USD_SETTING, coldstart_cost_usd),
    ]:
        _add(key, raw, lambda s, k=key: _validate_nonneg_float(s, k))
    _add(
        AUTO_COLDSTART_MIN_SAMPLES_SETTING,
        coldstart_min_samples,
        lambda s: _validate_pos_int(s, AUTO_COLDSTART_MIN_SAMPLES_SETTING),
    )

    with connect(st.cfg.db_path) as conn:
        for key, value in to_set:
            set_setting(conn, key, value)

    if _redirect:
        return RedirectResponse("/settings", status_code=303)
    return {"updated": [k for k, _ in to_set]}
