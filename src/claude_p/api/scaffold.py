from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sse_starlette.sse import EventSourceResponse

from claude_p import queries
from claude_p.claude_runner import ClaudeResult, apply_event, build_claude_argv
from claude_p.db import connect

log = logging.getLogger(__name__)
router = APIRouter()

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{0,62}$")
SCAFFOLDER_PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "scaffolder.md"


@dataclass
class Scaffold:
    """In-flight state for a single scaffold operation. Not persisted
    between daemon restarts — if the process dies mid-scaffold, the
    partial job folder stays on disk and is what the user sees.
    """

    id: str
    slug: str
    job_dir: Path
    description: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    exit_code: int | None = None
    error: str | None = None
    result: ClaudeResult = field(default_factory=ClaudeResult)


def _state(request: Request):
    return request.app.state.claude_p


@router.get("/scaffold", response_class=HTMLResponse)
async def scaffold_form(request: Request):
    st = _state(request)
    return st.templates.TemplateResponse(
        request, "scaffold.html", {"active": "scaffold"}
    )


@router.post("/scaffold")
async def scaffold_start(
    request: Request,
    description: str = Form(...),
    slug: str = Form(...),
):
    st = _state(request)
    slug = slug.strip()
    if not SLUG_RE.match(slug):
        raise HTTPException(
            400,
            "slug must be lowercase letters/digits/-/_, starting with letter or digit (max 63 chars)",
        )
    job_dir = st.cfg.jobs_dir / slug
    if job_dir.exists():
        raise HTTPException(409, f"job '{slug}' already exists")
    job_dir.mkdir(parents=True)
    (job_dir / "workspace").mkdir(exist_ok=True)

    scaffold_id = uuid.uuid4().hex[:12]
    scaffold = Scaffold(id=scaffold_id, slug=slug, job_dir=job_dir, description=description)
    _scaffolds()[scaffold_id] = scaffold

    started = datetime.now(timezone.utc)
    with connect(st.cfg.db_path) as conn:
        queries.insert_run_pending(
            conn,
            run_id=scaffold_id,
            job_slug=f"__scaffold__:{slug}",
            started_at=started,
            trigger="scaffold",
            run_dir=job_dir,
        )

    asyncio.create_task(_run_scaffold(st.cfg, scaffold))
    return RedirectResponse(f"/scaffold/{scaffold_id}", status_code=303)


@router.get("/scaffold/{scaffold_id}", response_class=HTMLResponse)
async def scaffold_view(scaffold_id: str, request: Request):
    st = _state(request)
    scaffold = _scaffolds().get(scaffold_id)
    if scaffold is None:
        raise HTTPException(404)
    return st.templates.TemplateResponse(
        request,
        "scaffold_view.html",
        {"scaffold": scaffold, "active": "scaffold"},
    )


@router.get("/scaffold/{scaffold_id}/stream")
async def scaffold_stream(scaffold_id: str):
    scaffold = _scaffolds().get(scaffold_id)
    if scaffold is None:
        raise HTTPException(404)

    async def gen():
        while True:
            if scaffold.done.is_set() and scaffold.queue.empty():
                yield {
                    "event": "end",
                    "data": json.dumps(
                        {"exit_code": scaffold.exit_code, "error": scaffold.error}
                    ),
                }
                return
            try:
                event = await asyncio.wait_for(scaffold.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": ""}
                continue
            yield {"event": "trace", "data": json.dumps(event)}

    return EventSourceResponse(gen())


def _scaffolds() -> dict[str, Scaffold]:
    if not hasattr(_scaffolds, "_store"):
        _scaffolds._store = {}
    return _scaffolds._store


async def _run_scaffold(cfg, scaffold: Scaffold) -> None:
    log.info("scaffolding %s in %s", scaffold.slug, scaffold.job_dir)
    system_prompt = (
        SCAFFOLDER_PROMPT_PATH.read_text() if SCAFFOLDER_PROMPT_PATH.exists() else ""
    )
    prompt = (
        f"Your job folder is `{scaffold.job_dir}`. The slug is `{scaffold.slug}`.\n\n"
        f"User request:\n\n{scaffold.description}\n\n"
        "Create the complete job folder now. When done, summarize what you built in a final message."
    )

    argv = build_claude_argv(
        prompt,
        claude_cli=cfg.claude_cli,
        allowed_tools=["Read", "Write", "Edit", "Bash", "WebFetch"],
        permission_mode="dontAsk",
        max_budget_usd=cfg.scaffolder_max_budget_usd,
        add_dir=[str(scaffold.job_dir)],
        system_prompt=system_prompt,
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(scaffold.job_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        scaffold.error = f"claude CLI not found: {e}"
        scaffold.done.set()
        return

    async def pump_stdout():
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                apply_event(scaffold.result, event)
            except json.JSONDecodeError:
                event = {"type": "raw", "text": line}
            await scaffold.queue.put(event)

    async def pump_stderr():
        assert proc.stderr is not None
        buf = b""
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                break
            buf += chunk
        if buf:
            scaffold.error = (buf.decode(errors="replace") or "").strip()[:1000] or None

    try:
        await asyncio.gather(pump_stdout(), pump_stderr())
        scaffold.exit_code = await proc.wait()
    except Exception as e:
        scaffold.error = str(e)
        scaffold.exit_code = -1
    finally:
        ended = datetime.now(timezone.utc)
        r = scaffold.result
        with connect(cfg.db_path) as conn:
            queries.update_run_result(
                conn,
                run_id=scaffold.id,
                ended_at=ended,
                exit_code=scaffold.exit_code,
                cost_usd=r.cost_usd,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cache_read_tokens=r.cache_read_tokens,
                cache_creation_tokens=r.cache_creation_tokens,
                error=scaffold.error,
            )
        scaffold.done.set()
