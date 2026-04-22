from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from claude_p.db import connect

router = APIRouter()


def _state(request: Request):
    return request.app.state.claude_p


def _run_row(db_path: Path, run_id: str) -> dict | None:
    with connect(db_path) as conn:
        r = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(r) if r else None


def _safe_file(base: Path, rel: str) -> Path:
    target = (base / rel).resolve()
    if not str(target).startswith(str(base.resolve()) + "/") and target != base.resolve():
        raise HTTPException(400, "bad path")
    return target


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(run_id: str, request: Request):
    st = _state(request)
    row = _run_row(st.cfg.db_path, run_id)
    if not row:
        raise HTTPException(404, "run not found")
    run_dir = Path(row["run_dir"])
    stdout = (run_dir / "stdout.log").read_text() if (run_dir / "stdout.log").exists() else ""
    stderr = (run_dir / "stderr.log").read_text() if (run_dir / "stderr.log").exists() else ""
    trace_path = run_dir / "trace.jsonl"
    trace = trace_path.read_text() if trace_path.exists() else ""
    output_dir = run_dir / "output"
    outputs: list[str] = []
    if output_dir.exists():
        outputs = sorted(
            str(p.relative_to(output_dir)) for p in output_dir.rglob("*") if p.is_file()
        )
    return st.templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run": row,
            "stdout": stdout,
            "stderr": stderr,
            "trace": trace,
            "outputs": outputs,
            "active": "jobs",
        },
    )


@router.get("/runs/{run_id}/stdout", response_class=PlainTextResponse)
async def run_stdout(run_id: str, request: Request):
    row = _run_row(_state(request).cfg.db_path, run_id)
    if not row:
        raise HTTPException(404)
    p = Path(row["run_dir"]) / "stdout.log"
    return PlainTextResponse(p.read_text() if p.exists() else "")


@router.get("/runs/{run_id}/stderr", response_class=PlainTextResponse)
async def run_stderr(run_id: str, request: Request):
    row = _run_row(_state(request).cfg.db_path, run_id)
    if not row:
        raise HTTPException(404)
    p = Path(row["run_dir"]) / "stderr.log"
    return PlainTextResponse(p.read_text() if p.exists() else "")


@router.get("/runs/{run_id}/trace", response_class=PlainTextResponse)
async def run_trace(run_id: str, request: Request):
    row = _run_row(_state(request).cfg.db_path, run_id)
    if not row:
        raise HTTPException(404)
    p = Path(row["run_dir"]) / "trace.jsonl"
    return PlainTextResponse(p.read_text() if p.exists() else "", media_type="application/x-ndjson")


@router.get("/runs/{run_id}/output/{rel:path}")
async def run_output(run_id: str, rel: str, request: Request):
    row = _run_row(_state(request).cfg.db_path, run_id)
    if not row:
        raise HTTPException(404)
    output_root = Path(row["run_dir"]) / "output"
    target = _safe_file(output_root, rel)
    if not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)
