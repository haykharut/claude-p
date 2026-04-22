import json

import pytest

from claude_p.claude_runner import ClaudeResult, apply_event, build_claude_argv


def test_argv_never_includes_bare():
    argv = build_claude_argv("hi")
    assert "--bare" not in argv
    assert argv[0].endswith("claude")
    assert "-p" in argv
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv


def test_argv_includes_flags():
    argv = build_claude_argv(
        "prompt",
        allowed_tools=["Read", "Write"],
        permission_mode="dontAsk",
        max_budget_usd=0.25,
        max_turns=5,
        add_dir=["/tmp/a", "/tmp/b"],
        system_prompt="sys",
    )
    assert "--allowedTools" in argv
    assert "Read,Write" in argv
    assert "--max-budget-usd" in argv
    assert "0.25" in argv
    assert "--max-turns" in argv and "5" in argv
    assert "--add-dir" in argv
    assert "--append-system-prompt" in argv
    assert argv[-1] == "prompt"


def test_apply_event_init_and_result():
    r = ClaudeResult()
    apply_event(r, {"type": "system", "subtype": "init", "session_id": "abc"})
    assert r.session_id == "abc"

    apply_event(
        r,
        {
            "type": "result",
            "is_error": False,
            "stop_reason": "end_turn",
            "num_turns": 3,
            "total_cost_usd": 0.0123,
            "result": "done",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 2000,
                "cache_creation_input_tokens": 300,
            },
        },
    )
    assert r.cost_usd == pytest.approx(0.0123)
    assert r.input_tokens == 100
    assert r.output_tokens == 50
    assert r.cache_read_tokens == 2000
    assert r.cache_creation_tokens == 300
    assert r.text == "done"
    assert r.num_turns == 3


def test_apply_event_assistant_text():
    r = ClaudeResult()
    apply_event(
        r,
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        },
    )
    assert r.text == "hello"


def test_apply_event_handles_missing_usage_fields():
    r = ClaudeResult()
    apply_event(r, {"type": "result", "total_cost_usd": 0.01, "usage": {}})
    assert r.cost_usd == pytest.approx(0.01)
    assert r.input_tokens == 0
    assert r.output_tokens == 0
