from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from claude_p import queries
from claude_p.db import connect
from claude_p.models import Run

router = APIRouter()


def _state(request: Request):
    return request.app.state.claude_p


def _load_run(request: Request, run_id: str) -> Run:
    with connect(_state(request).cfg.db_path) as conn:
        run = queries.get_run(conn, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


def _safe_file(base: Path, rel: str) -> Path:
    target = (base / rel).resolve()
    if not str(target).startswith(str(base.resolve()) + "/") and target != base.resolve():
        raise HTTPException(400, "bad path")
    return target


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(run_id: str, request: Request):
    st = _state(request)
    run = _load_run(request, run_id)
    run_dir = Path(run.run_dir)
    stdout = (run_dir / "stdout.log").read_text() if (run_dir / "stdout.log").exists() else ""
    stderr = (run_dir / "stderr.log").read_text() if (run_dir / "stderr.log").exists() else ""
    trace_path = run_dir / "trace.jsonl"
    trace = trace_path.read_text() if trace_path.exists() else ""
    output_dir = run_dir / "output"
    outputs: list[str] = []
    if output_dir.exists():
        outputs = sorted(str(p.relative_to(output_dir)) for p in output_dir.rglob("*") if p.is_file())
    return st.templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run": run,
            "stdout": stdout,
            "stderr": stderr,
            "trace": trace,
            "outputs": outputs,
            "active": "jobs",
        },
    )


@router.get("/runs/{run_id}/stdout", response_class=PlainTextResponse)
async def run_stdout(run_id: str, request: Request):
    run = _load_run(request, run_id)
    p = Path(run.run_dir) / "stdout.log"
    return PlainTextResponse(p.read_text() if p.exists() else "")


@router.get("/runs/{run_id}/stderr", response_class=PlainTextResponse)
async def run_stderr(run_id: str, request: Request):
    run = _load_run(request, run_id)
    p = Path(run.run_dir) / "stderr.log"
    return PlainTextResponse(p.read_text() if p.exists() else "")


@router.get("/runs/{run_id}/trace", response_class=PlainTextResponse)
async def run_trace(run_id: str, request: Request):
    run = _load_run(request, run_id)
    p = Path(run.run_dir) / "trace.jsonl"
    return PlainTextResponse(p.read_text() if p.exists() else "", media_type="application/x-ndjson")


@router.get("/runs/{run_id}/output/{rel:path}")
async def run_output(run_id: str, rel: str, request: Request):
    run = _load_run(request, run_id)
    output_root = Path(run.run_dir) / "output"
    target = _safe_file(output_root, rel)
    if not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)
