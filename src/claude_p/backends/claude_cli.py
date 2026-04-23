"""
Claude Code CLI backend — wraps `claude -p` with stream-json parsing.

This is the reference backend. Other backends (codex_cli, anthropic_api)
should look at this file for the shape of a `Backend` implementation.

Rule: **never pass `--bare`**. Research confirmed `--bare` ignores both
OAuth creds from `~/.claude/` and `CLAUDE_CODE_OAUTH_TOKEN` — if we ever
pass it, auth silently breaks. `build_claude_argv()` is the single place
that constructs the CLI argv; go through it.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Iterable
from typing import Any

from claude_p.models import BackendEvent, RunOptions

from .base import Backend

DEFAULT_ALLOWED_TOOLS = ["Read", "Write", "Bash", "WebFetch"]


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


def _parse_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _to_canonical(stream_event: dict[str, Any]) -> list[BackendEvent]:
    """Translate one stream-json event into canonical BackendEvents.

    Most stream-json events map 1:1. An `assistant` event with multiple
    content blocks fans out to several canonical events (one per block).
    Anything we don't recognize passes through as `raw` so the SSE debug
    view can still show it.
    """
    etype = stream_event.get("type")
    events: list[BackendEvent] = []

    if etype == "system" and stream_event.get("subtype") == "init":
        sid = stream_event.get("session_id")
        events.append(
            BackendEvent(
                kind="session_start",
                data={"session_id": sid} if sid is not None else {},
            )
        )
        return events

    if etype == "assistant":
        msg = stream_event.get("message") or {}
        for block in msg.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                events.append(
                    BackendEvent(
                        kind="assistant_text_delta",
                        data={"text": block.get("text", "")},
                    )
                )
            elif btype == "tool_use":
                events.append(
                    BackendEvent(
                        kind="tool_use",
                        data={
                            "name": block.get("name", ""),
                            "input": block.get("input"),
                        },
                    )
                )
        if not events:
            # Assistant event with no recognizable blocks — passthrough.
            events.append(BackendEvent(kind="raw", data={"payload": stream_event}))
        return events

    if etype == "user":
        # User events in stream-json are tool results. We don't have a
        # structured name/output readily, so surface the first block's
        # text/content as a preview.
        msg = stream_event.get("message") or {}
        preview = ""
        name = ""
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, str):
                    preview = content[:500]
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            preview = (c.get("text") or "")[:500]
                            break
                break
        events.append(BackendEvent(kind="tool_result", data={"name": name, "output_preview": preview}))
        return events

    if etype == "rate_limit_event":
        info = stream_event.get("rate_limit_info") or {}
        if isinstance(info, dict):
            events.append(BackendEvent(kind="rate_limit", data={"info": info}))
        return events

    if etype == "result":
        data: dict[str, Any] = {
            "is_error": bool(stream_event.get("is_error")),
            "stop_reason": stream_event.get("stop_reason"),
            "num_turns": int(stream_event.get("num_turns") or 0),
            "cost_usd": float(stream_event.get("total_cost_usd") or 0.0),
        }
        if stream_event.get("result"):
            data["text"] = stream_event["result"]
        usage = stream_event.get("usage") or {}
        if isinstance(usage, dict):
            data["input_tokens"] = int(usage.get("input_tokens") or 0)
            data["output_tokens"] = int(usage.get("output_tokens") or 0)
            data["cache_read_tokens"] = int(usage.get("cache_read_input_tokens") or 0)
            data["cache_creation_tokens"] = int(usage.get("cache_creation_input_tokens") or 0)
        mu = stream_event.get("modelUsage") or {}
        if isinstance(mu, dict):
            data["model_usage"] = mu
        events.append(BackendEvent(kind="result", data=data))
        return events

    # Anything else — raw passthrough.
    events.append(BackendEvent(kind="raw", data={"payload": stream_event}))
    return events


class ClaudeCLIBackend(Backend):
    """Spawns `claude -p --output-format stream-json`, reads lines, emits
    canonical events."""

    name = "claude_cli"

    async def stream(self, options: RunOptions) -> AsyncIterator[BackendEvent]:
        bo = options.backend_options
        claude_cli = (
            bo.get("claude_cli") or self.cfg.claude_cli or os.environ.get("CLAUDE_P_CLAUDE_CLI") or "claude"
        )
        argv = build_claude_argv(
            options.prompt,
            claude_cli=claude_cli,
            allowed_tools=bo.get("allowed_tools"),
            permission_mode=bo.get("permission_mode", "dontAsk"),
            max_budget_usd=options.max_budget_usd,
            max_turns=options.max_turns,
            add_dir=bo.get("add_dir"),
            system_prompt=options.system_prompt,
            model=options.model,
        )

        cwd = str(options.cwd) if options.cwd is not None else None
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_buf = bytearray()
        saw_result = False

        async def _drain_stderr() -> None:
            assert proc.stderr is not None
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                stderr_buf.extend(chunk)

        stderr_task = asyncio.create_task(_drain_stderr())

        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace")
                parsed = _parse_line(line)
                if parsed is None:
                    continue
                for ev in _to_canonical(parsed):
                    if ev.kind == "result":
                        saw_result = True
                    yield ev

            try:
                await asyncio.wait_for(proc.wait(), timeout=options.timeout_seconds)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            await stderr_task

        # If the CLI exited nonzero without emitting a `result` event,
        # synthesize one so the caller's BackendResult carries an error.
        if proc.returncode != 0 and not saw_result:
            stderr_text = stderr_buf.decode(errors="replace").strip()
            yield BackendEvent(
                kind="result",
                data={
                    "is_error": True,
                    "stop_reason": f"claude exit={proc.returncode}: {stderr_text[:500]}",
                },
            )
