from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import signal
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from claude_p.config import Config
from claude_p.db import connect
from claude_p.manifest import Manifest

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_param(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _build_env(
    cfg: Config,
    manifest: Manifest,
    run_id: str,
    job_dir: Path,
    workspace_dir: Path,
    extra_params: dict,
    secrets: dict[str, str],
) -> dict[str, str]:
    env = os.environ.copy()
    env["CLAUDE_P_RUN_ID"] = run_id
    env["CLAUDE_P_JOB_DIR"] = str(job_dir)
    env["CLAUDE_P_WORKSPACE_DIR"] = str(workspace_dir)
    env["CLAUDE_P_CLAUDE_CLI"] = cfg.claude_cli
    for key, spec in manifest.params.items():
        value = extra_params.get(key, spec.default)
        if value is None:
            continue
        env[f"CLAUDE_P_PARAM_{key.upper()}"] = _encode_param(value)
    for name in manifest.env:
        if name in secrets:
            env[name] = secrets[name]
    return env


async def _stream_to_file(stream: asyncio.StreamReader, path: Path) -> None:
    with path.open("wb") as f:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            f.write(chunk)
            f.flush()


def _resolve_entrypoint(job_dir: Path, entrypoint: str) -> Path:
    p = (job_dir / entrypoint).resolve()
    if not str(p).startswith(str(job_dir.resolve()) + os.sep) and p != job_dir.resolve():
        raise ValueError(f"entrypoint escapes job directory: {entrypoint}")
    return p


def _aggregate_claude_calls(run_dir: Path) -> dict:
    path = run_dir / "claude_calls.jsonl"
    totals = {
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    if not path.exists():
        return totals
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        for k in totals:
            totals[k] += row.get(k, 0) or 0
    return totals


def _copy_outputs(manifest: Manifest, workspace_dir: Path, run_dir: Path) -> list[str]:
    copied: list[str] = []
    if not manifest.output_globs:
        return copied
    out_dir = run_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    for pattern in manifest.output_globs:
        for src in workspace_dir.glob(pattern):
            if not src.is_file():
                continue
            rel = src.relative_to(workspace_dir)
            dst = out_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(str(rel))
    return copied


async def execute_run(
    cfg: Config,
    manifest: Manifest,
    job_dir: Path,
    trigger: str,
    secrets: dict[str, str] | None = None,
    extra_params: dict | None = None,
) -> str:
    """Run a job once. Returns the run_id. Blocks until completion."""
    secrets = secrets or {}
    extra_params = extra_params or {}

    run_id = uuid.uuid4().hex[:12]
    run_dir = job_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = job_dir / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    started = _now_iso()
    with connect(cfg.db_path) as conn:
        conn.execute(
            """
            INSERT INTO runs(id, job_slug, started_at, trigger, run_dir)
            VALUES(?,?,?,?,?)
            """,
            (run_id, manifest.name, started, trigger, str(run_dir)),
        )

    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    result_path = run_dir / "result.json"
    error: str | None = None
    exit_code: int | None = None

    try:
        argv = _build_argv(cfg, manifest, job_dir)
        env = _build_env(cfg, manifest, run_id, job_dir, workspace_dir, extra_params, secrets)

        # uv sync before execution if pyproject.toml exists and runtime=uv
        if manifest.runtime == "uv" and (job_dir / "pyproject.toml").exists():
            await _run_and_log(
                [cfg.uv_cli, "sync", "--project", str(job_dir)],
                cwd=workspace_dir,
                env=env,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                timeout=300,
                append=False,
            )

        exit_code = await _run_and_log(
            argv,
            cwd=workspace_dir,
            env=env,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            timeout=manifest.timeout_seconds,
            append=True,
        )
    except asyncio.TimeoutError:
        error = f"timeout after {manifest.timeout}"
        exit_code = -1
    except Exception as e:
        log.exception("executor error for %s/%s", manifest.name, run_id)
        error = str(e)
        exit_code = -1

    ended = _now_iso()
    ledger = _aggregate_claude_calls(run_dir)
    copied_outputs: list[str] = []
    try:
        copied_outputs = _copy_outputs(manifest, workspace_dir, run_dir)
    except Exception as e:
        log.warning("output copy failed for %s: %s", run_id, e)

    result_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "job_slug": manifest.name,
                "started_at": started,
                "ended_at": ended,
                "exit_code": exit_code,
                "trigger": trigger,
                "error": error,
                "outputs": copied_outputs,
                **ledger,
            },
            indent=2,
        )
    )

    with connect(cfg.db_path) as conn:
        conn.execute(
            """
            UPDATE runs SET
                ended_at=?, exit_code=?, cost_usd=?, input_tokens=?, output_tokens=?,
                cache_read_tokens=?, cache_creation_tokens=?, error=?
            WHERE id=?
            """,
            (
                ended,
                exit_code,
                ledger["cost_usd"],
                ledger["input_tokens"],
                ledger["output_tokens"],
                ledger["cache_read_tokens"],
                ledger["cache_creation_tokens"],
                error,
                run_id,
            ),
        )
        if manifest.schedule:
            _bump_schedule(conn, manifest.name, manifest.schedule)

    return run_id


def _bump_schedule(conn: sqlite3.Connection, slug: str, cron: str) -> None:
    from croniter import croniter

    now = datetime.now(timezone.utc)
    next_fire = croniter(cron, now).get_next(datetime).isoformat()
    conn.execute(
        "UPDATE schedules SET last_fire_at=?, next_fire_at=? WHERE slug=?",
        (now.isoformat(), next_fire, slug),
    )


def _build_argv(cfg: Config, manifest: Manifest, job_dir: Path) -> list[str]:
    entrypoint = _resolve_entrypoint(job_dir, manifest.entrypoint)
    if manifest.runtime == "uv":
        if (job_dir / "pyproject.toml").exists():
            return [
                cfg.uv_cli,
                "run",
                "--project",
                str(job_dir),
                "--no-sync",
                "--",
                "python",
                str(entrypoint),
            ]
        return [cfg.uv_cli, "run", "--", "python", str(entrypoint)]
    if manifest.runtime == "shell":
        if os.access(entrypoint, os.X_OK):
            return [str(entrypoint)]
        return ["/bin/sh", str(entrypoint)]
    raise ValueError(f"unsupported runtime: {manifest.runtime}")


async def _run_and_log(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
    timeout: float,
    append: bool,
) -> int:
    log.info("run: %s (cwd=%s)", shlex.join(argv), cwd)
    mode = "a" if append else "w"
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _copy(stream: asyncio.StreamReader, path: Path) -> None:
        with path.open(mode + "b") as f:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                f.write(chunk)
                f.flush()

    try:
        await asyncio.wait_for(
            asyncio.gather(_copy(proc.stdout, stdout_path), _copy(proc.stderr, stderr_path)),
            timeout=timeout,
        )
        return await proc.wait()
    except asyncio.TimeoutError:
        try:
            proc.send_signal(signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        raise
