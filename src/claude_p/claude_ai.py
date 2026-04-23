"""
Unofficial claude.ai /usage scraper.

This is the one place in the codebase that talks to an **undocumented**
Anthropic endpoint (`claude.ai/api/organizations/<org>/usage`). Treat it
as best-effort: it can break on any Anthropic deploy, and it requires
the user to paste their live session cookie. Nothing else depends on
this working — the rest of the ledger uses the fully-supported
`rate_limit_event` stream.

Design:
- Credentials (session key, org id, optional cf_clearance) live in the
  `settings` table. Never logged, never printed.
- Poller ticks on a slow interval (default 5 min). On error, we record
  the message in `claude_ai_last_error` setting for the UI to show.
- Response is flattened into `claude_ai_usage` as one row per window
  key, plus a reserved `__extra_usage__` row for the credit-pool block.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from claude_p import queries
from claude_p.config import Config
from claude_p.db import connect, get_setting, set_setting
from claude_p.models import (
    CLAUDE_AI_ENABLED_SETTING,
    CLAUDE_AI_LAST_ERROR_SETTING,
    CLAUDE_AI_LAST_OK_AT_SETTING,
    CLAUDE_AI_ORG_ID_SETTING,
    CLAUDE_AI_SESSION_KEY_SETTING,
)

log = logging.getLogger(__name__)

EXTRA_USAGE_KEY = "__extra_usage__"
DEFAULT_POLL_SECONDS = 300  # 5 minutes


class ClaudeAiAuthError(Exception):
    """401/403 — caller should prompt the user to paste a fresh cookie."""


class ClaudeAiFetchError(Exception):
    """Any other error talking to the endpoint."""


async def fetch_usage(*, session_key: str, org_id: str, timeout: float = 15.0) -> dict[str, Any]:
    """Hit /api/organizations/<org>/usage and return the parsed JSON.

    Raises ClaudeAiAuthError on 401/403 so the caller can surface a
    "refresh your cookie" prompt to the user.
    """
    url = f"https://claude.ai/api/organizations/{org_id}/usage"
    cookies = {"sessionKey": session_key}
    headers = {
        "accept": "*/*",
        "referer": "https://claude.ai/settings/usage",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
        ),
        "anthropic-client-platform": "web_claude_ai",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, cookies=cookies, headers=headers)
    if r.status_code in (401, 403):
        raise ClaudeAiAuthError(
            f"HTTP {r.status_code} — session expired or Cloudflare challenge. "
            "Refresh cookies at https://claude.ai/settings/usage and update settings."
        )
    if r.status_code >= 400:
        raise ClaudeAiFetchError(f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as e:
        raise ClaudeAiFetchError(f"non-JSON response: {e}") from e


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def persist_usage_payload(conn, payload: dict[str, Any], observed_at: datetime) -> int:
    """Write the endpoint's JSON into claude_ai_usage. Returns # rows touched."""
    n = 0
    for key, value in payload.items():
        if value is None:
            # Some windows come back as null when not applicable. Skip them
            # rather than clobbering any existing data.
            continue
        if key == "extra_usage":
            if not isinstance(value, dict):
                continue
            queries.upsert_claude_ai_extra_usage(
                conn,
                observed_at=observed_at,
                is_enabled=bool(value.get("is_enabled")),
                monthly_limit=value.get("monthly_limit"),
                used_credits=value.get("used_credits"),
                utilization=value.get("utilization"),
                currency=value.get("currency"),
                raw_json=json.dumps(value),
            )
            n += 1
            continue
        if not isinstance(value, dict):
            continue
        utilization = value.get("utilization")
        resets_at = _parse_iso(value.get("resets_at"))
        queries.upsert_claude_ai_window(
            conn,
            window_key=key,
            utilization=utilization,
            resets_at=resets_at,
            observed_at=observed_at,
            raw_json=json.dumps(value),
        )
        n += 1
    return n


async def poll_once(cfg: Config) -> None:
    with connect(cfg.db_path) as conn:
        enabled = get_setting(conn, CLAUDE_AI_ENABLED_SETTING) == "1"
        session_key = get_setting(conn, CLAUDE_AI_SESSION_KEY_SETTING)
        org_id = get_setting(conn, CLAUDE_AI_ORG_ID_SETTING)
    if not enabled or not session_key or not org_id:
        return
    try:
        payload = await fetch_usage(session_key=session_key, org_id=org_id)
    except ClaudeAiAuthError as e:
        log.warning("claude.ai usage: auth error: %s", e)
        with connect(cfg.db_path) as conn:
            set_setting(conn, CLAUDE_AI_LAST_ERROR_SETTING, str(e))
        return
    except Exception as e:
        log.warning("claude.ai usage: fetch failed: %s", e)
        with connect(cfg.db_path) as conn:
            set_setting(conn, CLAUDE_AI_LAST_ERROR_SETTING, str(e)[:500])
        return

    now = datetime.now(UTC)
    with connect(cfg.db_path) as conn:
        n = persist_usage_payload(conn, payload, now)
        set_setting(conn, CLAUDE_AI_LAST_OK_AT_SETTING, now.isoformat())
        set_setting(conn, CLAUDE_AI_LAST_ERROR_SETTING, "")
    log.info("claude.ai usage: polled %d windows", n)


async def poller(cfg: Config, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await poll_once(cfg)
        except Exception:
            log.exception("claude.ai poller unhandled error")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=DEFAULT_POLL_SECONDS)
        except TimeoutError:
            pass
