"""
Wrapper around `claude -p` with stream-json parsing.

Two public surfaces:

- `run_claude(...)`: synchronous helper that user jobs import
  (`from claude_p import run_claude`). Blocks, returns a `ClaudeResult`,
  and — when invoked inside a claude-p job process — appends a ledger
  entry to `<run_dir>/claude_calls.jsonl` so the executor can roll up
  cost/tokens for the whole run.

- `build_claude_argv()` and `parse_event()`: lower-level primitives used
  by the daemon's async executor and scaffolder. Kept here so there's
  exactly one place that knows the stream-json schema and one place that
  decides the CLI flags.

Rule: **never pass `--bare`**. Research confirmed `--bare` ignores both
OAuth creds from `~/.claude/` and `CLAUDE_CODE_OAUTH_TOKEN` — if we ever
pass it, auth silently breaks.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

DEFAULT_ALLOWED_TOOLS = ["Read", "Write", "Bash", "WebFetch"]


@dataclass
class ClaudeResult:
    text: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    session_id: str | None = None
    num_turns: int = 0
    is_error: bool = False
    stop_reason: str | None = None
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    # per-model breakdown from the final `result` event's `modelUsage` field.
    # Keys are model names (e.g. "claude-opus-4-7[1m]"); values are the
    # original dict from stream-json (costUSD, inputTokens, outputTokens, ...)
    model_usage: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Every rate_limit_event observed during the run. We only persist the
    # latest one per window type, but we keep all of them here in case a
    # caller wants to inspect the trajectory.
    rate_limit_events: list[dict[str, Any]] = field(default_factory=list)


def build_claude_argv(
    prompt: str,
    *,
    claude_cli: str = "claude",
    allowed_tools: Iterable[str] | None = None,
    permission_mode: str = "dontAsk",
    max_budget_usd: float | None = None,
    max_turns: int | None = None,
    add_dir: list[str] | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
) -> list[str]:
    tools = list(allowed_tools) if allowed_tools is not None else list(DEFAULT_ALLOWED_TOOLS)
    argv: list[str] = [
        claude_cli,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        permission_mode,
    ]
    if tools:
        argv += ["--allowedTools", ",".join(tools)]
    if max_budget_usd is not None:
        argv += ["--max-budget-usd", f"{max_budget_usd:g}"]
    if max_turns is not None:
        argv += ["--max-turns", str(max_turns)]
    if add_dir:
        argv += ["--add-dir", *add_dir]
    if system_prompt is not None:
        argv += ["--append-system-prompt", system_prompt]
    if model is not None:
        argv += ["--model", model]
    argv.append(prompt)
    return argv


def parse_event(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def apply_event(result: ClaudeResult, event: dict[str, Any]) -> None:
    """Mutates `result` in-place based on a single stream-json event."""
    result.raw_events.append(event)
    etype = event.get("type")

    if etype == "system" and event.get("subtype") == "init":
        result.session_id = event.get("session_id")

    elif etype == "assistant":
        # Final assistant message text accumulates here; we pick up the
        # last chunk since the `result` event below is authoritative for
        # the overall output.
        msg = event.get("message") or {}
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                result.text = block.get("text", result.text)

    elif etype == "result":
        result.is_error = bool(event.get("is_error"))
        result.stop_reason = event.get("stop_reason")
        result.num_turns = int(event.get("num_turns") or 0)
        result.cost_usd = float(event.get("total_cost_usd") or 0.0)
        if event.get("result"):
            result.text = event["result"]
        usage = event.get("usage") or {}
        if isinstance(usage, dict):
            result.input_tokens = int(usage.get("input_tokens") or 0)
            result.output_tokens = int(usage.get("output_tokens") or 0)
            result.cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
            result.cache_creation_tokens = int(usage.get("cache_creation_input_tokens") or 0)
        mu = event.get("modelUsage") or {}
        if isinstance(mu, dict):
            result.model_usage = mu

    elif etype == "rate_limit_event":
        info = event.get("rate_limit_info") or {}
        if isinstance(info, dict):
            result.rate_limit_events.append(info)


def _append_ledger(run_dir: Path, call: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "claude_calls.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(call) + "\n")


def run_claude(
    prompt: str,
    *,
    allowed_tools: Iterable[str] | None = None,
    permission_mode: str = "dontAsk",
    max_budget_usd: float | None = 1.0,
    max_turns: int | None = None,
    add_dir: list[str] | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
    cwd: str | Path | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    timeout_seconds: float | None = None,
    claude_cli: str | None = None,
) -> ClaudeResult:
    """Invoke `claude -p` synchronously and parse stream-json.

    Intended to be called from inside user jobs. When invoked inside a
    claude-p job process (env `CLAUDE_P_RUN_ID` + `CLAUDE_P_JOB_DIR` are
    set), appends token/cost to the run's `claude_calls.jsonl` so the
    daemon can aggregate.
    """
    cli = claude_cli or os.environ.get("CLAUDE_P_CLAUDE_CLI") or "claude"
    argv = build_claude_argv(
        prompt,
        claude_cli=cli,
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        max_budget_usd=max_budget_usd,
        max_turns=max_turns,
        add_dir=add_dir,
        system_prompt=system_prompt,
        model=model,
    )

    result = ClaudeResult()

    # We pipe stdout line-by-line and stream to on_event for progress.
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(cwd) if cwd is not None else None,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            event = parse_event(line)
            if event is None:
                continue
            apply_event(result, event)
            if on_event is not None:
                try:
                    on_event(event)
                except Exception:
                    pass
        proc.wait(timeout=timeout_seconds)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    if proc.returncode != 0 and not result.is_error:
        stderr = proc.stderr.read() if proc.stderr else ""
        result.is_error = True
        result.stop_reason = result.stop_reason or f"claude exit={proc.returncode}: {stderr[:500]}"

    # If we're running inside a claude-p job, append a ledger record +
    # model usage + rate-limit observations for the executor to pick up.
    run_id = os.environ.get("CLAUDE_P_RUN_ID")
    job_dir = os.environ.get("CLAUDE_P_JOB_DIR")
    if run_id and job_dir:
        run_dir = Path(job_dir) / "runs" / run_id
        _append_ledger(
            run_dir,
            {
                "cost_usd": result.cost_usd,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cache_read_tokens": result.cache_read_tokens,
                "cache_creation_tokens": result.cache_creation_tokens,
                "num_turns": result.num_turns,
                "session_id": result.session_id,
                "is_error": result.is_error,
                "model_usage": result.model_usage,
            },
        )
        if result.rate_limit_events:
            _append_rate_limits(run_dir, result.rate_limit_events)

    return result


def _append_rate_limits(run_dir: Path, events: list[dict[str, Any]]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "claude_rate_limits.jsonl"
    with path.open("a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
