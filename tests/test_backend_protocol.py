"""
Protocol-level tests for the Backend ABC. Uses a FakeBackend that yields
scripted canonical events — this is the proof that a new backend only
has to implement `stream()` and all downstream behavior (result folding,
sync wrapper, on_event callback) comes for free.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from claude_p.backends.base import Backend, fold_event
from claude_p.config import Config
from claude_p.models import BackendEvent, BackendResult, RunOptions


class FakeBackend(Backend):
    name = "fake"

    def __init__(self, cfg: Config, script: list[BackendEvent]) -> None:
        super().__init__(cfg)
        self._script = script

    async def stream(self, options: RunOptions) -> AsyncIterator[BackendEvent]:
        for ev in self._script:
            yield ev


SCRIPT = [
    BackendEvent(kind="session_start", data={"session_id": "sess-123"}),
    BackendEvent(kind="assistant_text_delta", data={"text": "partial"}),
    BackendEvent(kind="tool_use", data={"name": "Read", "input": {"path": "/a"}}),
    BackendEvent(
        kind="rate_limit",
        data={"info": {"rateLimitType": "five_hour", "status": "allowed"}},
    ),
    BackendEvent(
        kind="result",
        data={
            "text": "final",
            "cost_usd": 0.12,
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "cache_creation_tokens": 5,
            "num_turns": 2,
            "is_error": False,
            "stop_reason": "end_turn",
            "model_usage": {"fake-model": {"costUSD": 0.12}},
        },
    ),
]


def test_fold_event_accumulates_result():
    r = BackendResult()
    for ev in SCRIPT:
        fold_event(r, ev)
    assert r.session_id == "sess-123"
    assert r.text == "final"  # result event is authoritative, overrides partial
    assert r.cost_usd == pytest.approx(0.12)
    assert r.input_tokens == 100
    assert r.output_tokens == 50
    assert r.cache_read_tokens == 10
    assert r.cache_creation_tokens == 5
    assert r.num_turns == 2
    assert r.is_error is False
    assert r.stop_reason == "end_turn"
    assert r.model_usage == {"fake-model": {"costUSD": 0.12}}
    assert len(r.rate_limit_events) == 1
    assert r.rate_limit_events[0]["rateLimitType"] == "five_hour"


def test_fold_event_ignores_raw_and_tool_events():
    r = BackendResult()
    fold_event(r, BackendEvent(kind="raw", data={"payload": {"hi": "there"}}))
    fold_event(r, BackendEvent(kind="tool_use", data={"name": "x"}))
    fold_event(r, BackendEvent(kind="tool_result", data={"output_preview": "y"}))
    assert r.cost_usd == 0.0
    assert r.text == ""
    assert r.input_tokens == 0


def test_assistant_text_delta_wins_until_result_overrides():
    r = BackendResult()
    fold_event(r, BackendEvent(kind="assistant_text_delta", data={"text": "A"}))
    fold_event(r, BackendEvent(kind="assistant_text_delta", data={"text": "AB"}))
    assert r.text == "AB"
    fold_event(r, BackendEvent(kind="result", data={"text": "FINAL", "cost_usd": 0.01}))
    assert r.text == "FINAL"


def test_run_async_folds_script_into_result():
    cfg = Config()
    backend = FakeBackend(cfg, script=SCRIPT)

    import asyncio

    seen: list[str] = []
    result = asyncio.run(
        backend.run_async(
            RunOptions(prompt="hi"),
            on_event=lambda ev: seen.append(ev.kind),
        )
    )
    assert result.cost_usd == pytest.approx(0.12)
    assert result.text == "final"
    assert seen == [
        "session_start",
        "assistant_text_delta",
        "tool_use",
        "rate_limit",
        "result",
    ]


def test_run_sync_returns_same_result_as_run_async():
    cfg = Config()
    backend = FakeBackend(cfg, script=SCRIPT)
    result = backend.run_sync(RunOptions(prompt="hi"))
    assert result.cost_usd == pytest.approx(0.12)
    assert result.session_id == "sess-123"
    assert result.is_error is False


def test_on_event_callback_errors_do_not_crash_the_run():
    cfg = Config()
    backend = FakeBackend(cfg, script=SCRIPT)

    def boom(_ev):
        raise RuntimeError("oops")

    import asyncio

    result = asyncio.run(backend.run_async(RunOptions(prompt="hi"), on_event=boom))
    # Run completed despite the callback raising on every event.
    assert result.cost_usd == pytest.approx(0.12)


def test_unknown_backend_name_raises():
    from claude_p.backends import get_backend

    cfg = Config()
    cfg.backend = "does_not_exist"
    with pytest.raises(ValueError, match="unknown backend"):
        get_backend(cfg)


def test_default_backend_is_claude_cli():
    from claude_p.backends import ClaudeCLIBackend, get_backend

    cfg = Config()
    backend = get_backend(cfg)
    assert isinstance(backend, ClaudeCLIBackend)
    assert backend.name == "claude_cli"
