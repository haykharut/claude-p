"""
Backend protocol — the pluggable LLM execution surface.

A new backend (e.g. `codex_cli`, `anthropic_api`) implements **one method**:
`async def stream(self, options: RunOptions) -> AsyncIterator[BackendEvent]`.

Everything else — sync helpers, result accumulation, ledger writes —
happens in shared code here and in `claude_p.__init__`. So the whole of
"make claude-p drive a different LLM" is:

    1. Subclass `Backend` in a new file under `backends/`.
    2. Implement `stream()` by converting your native events into
       `BackendEvent(kind=..., data={...})`. Always yield a terminal event
       with `kind="result"` carrying the final totals.
    3. Register it in `backends/__init__.py` under a string key.
    4. Set `CLAUDE_P_BACKEND=<your-key>`.

The canonical event kinds are defined in `models.BackendEventKind` — keep
them semantic, not stream-json-shaped, so HTTP-style backends don't have
to fake being CLIs.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, ClassVar

from claude_p.config import Config
from claude_p.models import BackendEvent, BackendResult, RunOptions


class Backend(ABC):
    """Abstract base for an LLM backend."""

    name: ClassVar[str]

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    @abstractmethod
    def stream(self, options: RunOptions) -> AsyncIterator[BackendEvent]:
        """Yield canonical events as the model runs.

        Implementations should yield a terminal event with
        `kind="result"` carrying the final cost / token totals. Missing
        that, the folded `BackendResult` will have zero cost/tokens.
        """
        raise NotImplementedError

    async def run_async(
        self,
        options: RunOptions,
        on_event: Callable[[BackendEvent], None] | None = None,
    ) -> BackendResult:
        """Consume `stream()` and fold events into a `BackendResult`.

        Backends should not override this — the folder is shared so all
        backends produce identical result semantics.
        """
        result = BackendResult()
        async for event in self.stream(options):
            fold_event(result, event)
            if on_event is not None:
                try:
                    on_event(event)
                except Exception:
                    # An on_event callback raising must not crash the run.
                    pass
        return result

    def run_sync(self, options: RunOptions) -> BackendResult:
        """Blocking wrapper around `run_async`. Used by user job scripts
        (which are themselves sync subprocesses — asking every job author
        to learn asyncio would be a regression)."""
        return asyncio.run(self.run_async(options))


def fold_event(result: BackendResult, event: BackendEvent) -> None:
    """Apply one canonical event to a `BackendResult`, mutating in place.

    Canonical event shapes (keys in `event.data`):
      - session_start:        {session_id: str}
      - assistant_text_delta: {text: str}      — last chunk wins (result event is authoritative)
      - tool_use:             {name: str, input: Any}   (not folded into totals, passthrough)
      - tool_result:          {name: str, output_preview: str}  (passthrough)
      - rate_limit:           {info: dict}     — raw rate_limit_info appended to list
      - result:               {text, cost_usd, input_tokens, output_tokens,
                               cache_read_tokens, cache_creation_tokens,
                               num_turns, is_error, stop_reason, model_usage}
      - raw:                  {payload: dict}  — debug passthrough, ignored by folder
    """
    kind = event.kind
    data = event.data

    if kind == "session_start":
        sid = data.get("session_id")
        if isinstance(sid, str):
            result.session_id = sid

    elif kind == "assistant_text_delta":
        text = data.get("text")
        if isinstance(text, str) and text:
            # Keep the latest non-empty chunk; the `result` event will
            # overwrite with the authoritative final text if present.
            result.text = text

    elif kind == "rate_limit":
        info = data.get("info")
        if isinstance(info, dict):
            result.rate_limit_events.append(info)

    elif kind == "result":
        if "text" in data and isinstance(data["text"], str):
            result.text = data["text"]
        if "cost_usd" in data:
            result.cost_usd = float(data["cost_usd"] or 0.0)
        if "input_tokens" in data:
            result.input_tokens = int(data["input_tokens"] or 0)
        if "output_tokens" in data:
            result.output_tokens = int(data["output_tokens"] or 0)
        if "cache_read_tokens" in data:
            result.cache_read_tokens = int(data["cache_read_tokens"] or 0)
        if "cache_creation_tokens" in data:
            result.cache_creation_tokens = int(data["cache_creation_tokens"] or 0)
        if "num_turns" in data:
            result.num_turns = int(data["num_turns"] or 0)
        if "is_error" in data:
            result.is_error = bool(data["is_error"])
        if "stop_reason" in data:
            sr = data["stop_reason"]
            result.stop_reason = sr if isinstance(sr, str) else None
        mu = data.get("model_usage")
        if isinstance(mu, dict):
            result.model_usage = mu
