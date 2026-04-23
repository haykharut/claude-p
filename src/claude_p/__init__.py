"""claude-p: home server for Claude Code agent jobs."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from claude_p.backends import get_backend
from claude_p.config import get_config
from claude_p.models import BackendEvent, BackendResult, RunOptions

__version__ = "0.1.0"


def run_claude(
    prompt: str,
    *,
    # Common options (forwarded to RunOptions). Signature preserved from
    # the pre-refactor helper so existing user jobs keep working.
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
) -> BackendResult:
    """Synchronously invoke the configured backend.

    When called inside a claude-p job process (env `CLAUDE_P_RUN_ID` +
    `CLAUDE_P_JOB_DIR` set), appends a ledger entry to the run's
    `claude_calls.jsonl` so the executor can roll up cost/tokens.

    The claude-CLI-specific kwargs (`allowed_tools`, `permission_mode`,
    `add_dir`, `claude_cli`) land in `RunOptions.backend_options` so the
    claude backend picks them up while other backends are free to ignore.
    """
    cfg = get_config()
    backend = get_backend(cfg)

    backend_options: dict[str, Any] = {}
    if allowed_tools is not None:
        backend_options["allowed_tools"] = list(allowed_tools)
    backend_options["permission_mode"] = permission_mode
    if add_dir is not None:
        backend_options["add_dir"] = list(add_dir)
    if claude_cli is not None:
        backend_options["claude_cli"] = claude_cli

    options = RunOptions(
        prompt=prompt,
        model=model,
        system_prompt=system_prompt,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        backend_options=backend_options,
    )

    # The on_event callback receives canonical events as `{kind, data}`
    # dicts (not raw stream-json). Pre-refactor this was the raw
    # stream-json dict; see CHANGELOG for the migration note.
    def _bridge_on_event(ev: BackendEvent) -> None:
        if on_event is None:
            return
        try:
            on_event(ev.model_dump())
        except Exception:
            pass

    result = asyncio.run(backend.run_async(options, on_event=_bridge_on_event))

    _maybe_write_ledger(result)
    return result


def _maybe_write_ledger(result: BackendResult) -> None:
    run_id = os.environ.get("CLAUDE_P_RUN_ID")
    job_dir = os.environ.get("CLAUDE_P_JOB_DIR")
    if not run_id or not job_dir:
        return
    run_dir = Path(job_dir) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "claude_calls.jsonl").open("a").write(
        json.dumps(
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
            }
        )
        + "\n"
    )
    if result.rate_limit_events:
        with (run_dir / "claude_rate_limits.jsonl").open("a") as f:
            for ev in result.rate_limit_events:
                f.write(json.dumps(ev) + "\n")


__all__ = ["run_claude", "BackendResult", "__version__"]
