import pytest

from claude_p.backends.claude_cli import build_claude_argv, _to_canonical


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


# --- stream-json → canonical event translation --------------------------


def test_translate_system_init_to_session_start():
    events = _to_canonical({"type": "system", "subtype": "init", "session_id": "abc"})
    assert len(events) == 1
    assert events[0].kind == "session_start"
    assert events[0].data == {"session_id": "abc"}


def test_translate_assistant_text_block():
    events = _to_canonical(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}
    )
    assert len(events) == 1
    assert events[0].kind == "assistant_text_delta"
    assert events[0].data == {"text": "hello"}


def test_translate_assistant_tool_use_block():
    events = _to_canonical(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"path": "/a"}},
                ]
            },
        }
    )
    assert len(events) == 1
    assert events[0].kind == "tool_use"
    assert events[0].data["name"] == "Read"
    assert events[0].data["input"] == {"path": "/a"}


def test_translate_result_event():
    events = _to_canonical(
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
            "modelUsage": {"claude-opus-4-7": {"costUSD": 0.0123}},
        }
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "result"
    assert ev.data["cost_usd"] == pytest.approx(0.0123)
    assert ev.data["input_tokens"] == 100
    assert ev.data["output_tokens"] == 50
    assert ev.data["cache_read_tokens"] == 2000
    assert ev.data["cache_creation_tokens"] == 300
    assert ev.data["text"] == "done"
    assert ev.data["num_turns"] == 3
    assert ev.data["stop_reason"] == "end_turn"
    assert ev.data["model_usage"] == {"claude-opus-4-7": {"costUSD": 0.0123}}


def test_translate_result_missing_usage_keys():
    events = _to_canonical({"type": "result", "total_cost_usd": 0.01, "usage": {}})
    ev = events[0]
    assert ev.kind == "result"
    assert ev.data["cost_usd"] == pytest.approx(0.01)
    assert ev.data["input_tokens"] == 0
    assert ev.data["output_tokens"] == 0


def test_translate_rate_limit_event():
    events = _to_canonical(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "resetsAt": 1776859200,
                "rateLimitType": "five_hour",
            },
        }
    )
    assert len(events) == 1
    assert events[0].kind == "rate_limit"
    assert events[0].data["info"]["rateLimitType"] == "five_hour"


def test_unknown_event_passes_through_as_raw():
    events = _to_canonical({"type": "something_unrecognized", "foo": "bar"})
    assert len(events) == 1
    assert events[0].kind == "raw"
    assert events[0].data["payload"] == {"type": "something_unrecognized", "foo": "bar"}
