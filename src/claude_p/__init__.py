"""claude-p: home server for Claude Code agent jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from claude_p.backends import get_backend
from claude_p.config import get_config
from claude_p.models import BackendEvent, BackendResult, RunOptions

__version__ = "0.1.0"

log = logging.getLogger(__name__)

# Sentinel distinguishing "caller passed nothing" from "caller passed None".
# Explicit-kwarg > injected-manifest-config > code-default means we must be
# able to tell the three states apart, which Python kwargs can't do with
# concrete defaults.
_UNSET: Any = object()


def run_claude(
    prompt: str,
    *,
    allowed_tools: Iterable[str] | None = _UNSET,
    permission_mode: str = _UNSET,
    max_budget_usd: float | None = _UNSET,
    max_turns: int | None = _UNSET,
    add_dir: list[str] | None = _UNSET,
    system_prompt: str | None = _UNSET,
    model: str | None = _UNSET,
    cwd: str | Path | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    timeout_seconds: float | None = _UNSET,
    claude_cli: str | None = _UNSET,
) -> BackendResult:
    """Synchronously invoke the configured backend.

    Resolution order for every tunable (backend selection, model,
    budget, tools, etc.):

        explicit kwargs  >  manifest llm block  >  code defaults

    The manifest `llm` block reaches this function via
    `runs/<run-id>/llm_config.json` — the executor writes it, this
    function reads it via the `CLAUDE_P_LLM_CONFIG` env var. When the
    job runs locally (no executor, no file), code defaults are used.

    When called inside a claude-p job process (env `CLAUDE_P_RUN_ID` +
    `CLAUDE_P_JOB_DIR` set), appends a ledger entry to the run's
    `claude_calls.jsonl` so the executor can roll up cost/tokens.

    Claude-CLI-specific kwargs (`allowed_tools`, `permission_mode`,
    `add_dir`, `claude_cli`) land in `RunOptions.backend_options` so
    the claude backend picks them up while other backends can ignore
    them. Prefer not passing them here at all — put them in the
    manifest's `llm.options` block so the same `main.py` works
    across backends.
    """
    injected = _load_injected_llm_config()

    # Top-level RunOptions fields.
    resolved_model = _resolve(model, injected, "model", None)
    resolved_system_prompt = _resolve(system_prompt, injected, "system_prompt", None)
    resolved_max_budget = _resolve(max_budget_usd, injected, "max_budget_usd", 1.0)
    resolved_max_turns = _resolve(max_turns, injected, "max_turns", None)
    resolved_timeout = _resolve(timeout_seconds, injected, "timeout_seconds", None)

    # Claude-CLI-specific options live under `options` in the injected
    # config. Manifest-side Options validation already filled defaults,
    # so these keys are present whenever `injected` is.
    injected_opts = (injected or {}).get("options", {})
    resolved_allowed_tools = _resolve(allowed_tools, injected_opts, "allowed_tools", None)
    resolved_permission_mode = _resolve(permission_mode, injected_opts, "permission_mode", "dontAsk")
    resolved_add_dir = _resolve(add_dir, injected_opts, "add_dir", None)
    resolved_claude_cli = _resolve(claude_cli, injected_opts, "claude_cli", None)

    # Backend selection: manifest llm.backend wins over cfg.backend.
    cfg = get_config()
    if injected and injected.get("backend"):
        cfg = cfg.model_copy(update={"backend": injected["backend"]})
    backend = get_backend(cfg)

    backend_options: dict[str, Any] = {}
    if resolved_allowed_tools is not None:
        backend_options["allowed_tools"] = list(resolved_allowed_tools)
    backend_options["permission_mode"] = resolved_permission_mode
    if resolved_add_dir is not None:
        backend_options["add_dir"] = list(resolved_add_dir)
    if resolved_claude_cli is not None:
        backend_options["claude_cli"] = resolved_claude_cli

    options = RunOptions(
        prompt=prompt,
        model=resolved_model,
        system_prompt=resolved_system_prompt,
        max_turns=resolved_max_turns,
        max_budget_usd=resolved_max_budget,
        timeout_seconds=resolved_timeout,
        cwd=cwd,
        backend_options=backend_options,
    )

    # on_event receives canonical `{kind, data}` dicts (not raw
    # stream-json). See CHANGELOG for the migration note.
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


def _resolve(kwarg_value: Any, source: dict | None, key: str, code_default: Any) -> Any:
    """Pick a value per the resolution order: explicit kwarg >
    injected manifest config > code default. `_UNSET` is the sentinel
    for "caller didn't pass this kwarg."""
    if kwarg_value is not _UNSET:
        return kwarg_value
    if source is not None and key in source and source[key] is not None:
        return source[key]
    return code_default


def _load_injected_llm_config() -> dict | None:
    """Read the executor-written `llm_config.json`, if any. A missing
    or malformed file is logged and ignored — local invocations of
    `run_claude()` (outside a claude-p job) rightly have no file."""
    path = os.environ.get("CLAUDE_P_LLM_CONFIG")
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        log.warning("CLAUDE_P_LLM_CONFIG=%s unreadable (%s); using code defaults", path, e)
        return None


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
