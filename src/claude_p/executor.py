from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import signal
import uuid
from datetime import UTC, datetime
from pathlib import Path

from croniter import croniter

from claude_p import queries
from claude_p.backends import resolve_backend_class
from claude_p.config import Config
from claude_p.db import connect
from claude_p.manifest import LlmConfig, Manifest

log = logging.getLogger(__name__)


def _encode_param(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _effective_llm_config(manifest: Manifest, cfg: Config) -> dict:
    """Merge the job's `llm:` block with daemon defaults into the dict
    we write to `runs/<id>/llm_config.json`. `run_claude()` in user
    jobs reads that file and uses the values as defaults — explicit
    kwargs still win.

    The `options` block is run through the selected backend's
    `Options` Pydantic model so schema defaults land in the file too;
    `run_claude()` then only has to handle "present" vs "absent",
    never "field exists but some of its subdefaults are missing".
    """
    llm = manifest.llm or LlmConfig()
    backend_name = llm.backend or cfg.backend
    backend_cls = resolve_backend_class(backend_name)
    options_model = backend_cls.Options.model_validate(llm.options)
    return {
        "backend": backend_name,
        "model": llm.model,
        "max_budget_usd": llm.max_budget_usd,
        "max_turns": llm.max_turns,
        "timeout_seconds": llm.timeout_seconds,
        "system_prompt": llm.system_prompt,
        "options": options_model.model_dump(),
    }


def _write_llm_config(run_dir: Path, manifest: Manifest, cfg: Config) -> Path:
    path = run_dir / "llm_config.json"
    path.write_text(json.dumps(_effective_llm_config(manifest, cfg), indent=2))
    return path


def _build_env(
    cfg: Config,
    manifest: Manifest,
    run_id: str,
    job_dir: Path,
    workspace_dir: Path,
    extra_params: dict,
    secrets: dict[str, str],
    llm_config_path: Path,
) -> dict[str, str]:
    env = os.environ.copy()
    env["CLAUDE_P_RUN_ID"] = run_id
    env["CLAUDE_P_JOB_DIR"] = str(job_dir)
    env["CLAUDE_P_WORKSPACE_DIR"] = str(workspace_dir)
    env["CLAUDE_P_CLAUDE_CLI"] = cfg.claude_cli
    # Pointer to the merged llm config for this run. `run_claude()`
    # reads it to fill in defaults (backend, model, budget, options).
    env["CLAUDE_P_LLM_CONFIG"] = str(llm_config_path)
    for key, spec in manifest.params.items():
        value = extra_params.get(key, spec.default)
        if value is None:
            continue
        env[f"CLAUDE_P_PARAM_{key.upper()}"] = _encode_param(value)
    for name in manifest.env:
        if name in secrets:
            env[name] = secrets[name]
    return env


def _resolve_entrypoint(job_dir: Path, entrypoint: str) -> Path:
    p = (job_dir / entrypoint).resolve()
    if not str(p).startswith(str(job_dir.resolve()) + os.sep) and p != job_dir.resolve():
        raise ValueError(f"entrypoint escapes job directory: {entrypoint}")
    return p


def _aggregate_claude_calls(
    run_dir: Path,
) -> tuple[dict[str, float | int], dict[str, dict[str, float | int]]]:
    """Return (totals, per_model_totals) by scanning claude_calls.jsonl.

    per_model_totals maps model-name → accumulated {cost_usd, input_tokens,
    output_tokens, cache_read_tokens, cache_creation_tokens}.
    """
    path = run_dir / "claude_calls.jsonl"
    totals: dict[str, float | int] = {
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    per_model: dict[str, dict[str, float | int]] = {}
    if not path.exists():
        return totals, per_model
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
        mu = row.get("model_usage") or {}
        if isinstance(mu, dict):
            for model, m in mu.items():
                if not isinstance(m, dict):
                    continue
                bucket = per_model.setdefault(
                    model,
                    {
                        "cost_usd": 0.0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_creation_tokens": 0,
                    },
                )
                bucket["cost_usd"] += float(m.get("costUSD") or 0)
                bucket["input_tokens"] += int(m.get("inputTokens") or 0)
                bucket["output_tokens"] += int(m.get("outputTokens") or 0)
                bucket["cache_read_tokens"] += int(m.get("cacheReadInputTokens") or 0)
                bucket["cache_creation_tokens"] += int(m.get("cacheCreationInputTokens") or 0)
    return totals, per_model


def _read_rate_limit_events(run_dir: Path) -> list[dict]:
    path = run_dir / "claude_rate_limits.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


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

    started = datetime.now(UTC)
    with connect(cfg.db_path) as conn:
        queries.insert_run_pending(
            conn,
            run_id=run_id,
            job_slug=manifest.name,
            started_at=started,
            trigger=trigger,
            run_dir=run_dir,
        )

    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    result_path = run_dir / "result.json"
    error: str | None = None
    exit_code: int | None = None

    try:
        argv = _build_argv(cfg, manifest, job_dir)
        llm_config_path = _write_llm_config(run_dir, manifest, cfg)
        env = _build_env(
            cfg, manifest, run_id, job_dir, workspace_dir, extra_params, secrets, llm_config_path
        )

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
    except TimeoutError:
        error = f"timeout after {manifest.timeout}"
        exit_code = -1
    except Exception as e:
        log.exception("executor error for %s/%s", manifest.name, run_id)
        error = str(e)
        exit_code = -1

    ended = datetime.now(UTC)
    ledger, per_model = _aggregate_claude_calls(run_dir)
    rate_limit_events = _read_rate_limit_events(run_dir)
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
                "started_at": started.isoformat(),
                "ended_at": ended.isoformat(),
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
        queries.update_run_result(
            conn,
            run_id=run_id,
            ended_at=ended,
            exit_code=exit_code,
            cost_usd=float(ledger["cost_usd"]),
            input_tokens=int(ledger["input_tokens"]),
            output_tokens=int(ledger["output_tokens"]),
            cache_read_tokens=int(ledger["cache_read_tokens"]),
            cache_creation_tokens=int(ledger["cache_creation_tokens"]),
            error=error,
        )
        _persist_model_usage(conn, run_id, per_model)
        _persist_rate_limits(conn, run_id, ended, rate_limit_events)
        if manifest.schedule:
            next_fire = croniter(manifest.schedule, ended).get_next(datetime)
            queries.bump_schedule(conn, manifest.name, ended, next_fire)

    return run_id


def _persist_model_usage(conn, run_id: str, per_model: dict[str, dict[str, float | int]]) -> None:
    for model, totals in per_model.items():
        queries.upsert_run_model_usage(
            conn,
            run_id,
            model,
            cost_usd=float(totals["cost_usd"]),
            input_tokens=int(totals["input_tokens"]),
            output_tokens=int(totals["output_tokens"]),
            cache_read_tokens=int(totals["cache_read_tokens"]),
            cache_creation_tokens=int(totals["cache_creation_tokens"]),
        )


def _persist_rate_limits(conn, run_id: str, observed_at: datetime, events: list[dict]) -> None:
    """Fold a list of rate_limit_info dicts into rate_limit_snapshots.

    The event shape mirrors stream-json: rateLimitType, status, resetsAt
    (unix epoch seconds), overageStatus, overageResetsAt, isUsingOverage.
    """
    for info in events:
        rl_type = info.get("rateLimitType")
        status = info.get("status")
        resets_at_epoch = info.get("resetsAt")
        if not rl_type or not status or resets_at_epoch is None:
            continue
        try:
            resets_at = datetime.fromtimestamp(int(resets_at_epoch), tz=UTC)
        except (TypeError, ValueError):
            continue
        overage_epoch = info.get("overageResetsAt")
        overage_resets_at = None
        if overage_epoch is not None:
            try:
                overage_resets_at = datetime.fromtimestamp(int(overage_epoch), tz=UTC)
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
    assert proc.stdout is not None and proc.stderr is not None  # PIPE set above

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
    except TimeoutError:
        try:
            proc.send_signal(signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        raise
