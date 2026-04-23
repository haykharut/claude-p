from pathlib import Path

import pytest
from pydantic import ValidationError

from claude_p.manifest import Manifest, ManifestError, load_manifest, parse_duration

EXAMPLE = Path(__file__).parent.parent / "jobs-example" / "hello-world" / "job.yaml"


def test_load_example_manifest():
    m = load_manifest(EXAMPLE, expected_slug="hello-world")
    assert m.name == "hello-world"
    assert m.runtime == "uv"
    assert m.entrypoint == "main.py"
    assert m.schedule is None
    assert m.workspace is True
    assert m.timeout == "30s"
    assert m.timeout_seconds == 30.0


def test_parse_duration():
    assert parse_duration("30s") == 30.0
    assert parse_duration("10m") == 600.0
    assert parse_duration("2h") == 7200.0
    assert parse_duration("500ms") == 0.5
    with pytest.raises(ValueError):
        parse_duration("forever")


def test_slug_validation():
    with pytest.raises(ValidationError):
        Manifest.model_validate({"name": "Bad Slug!", "entrypoint": "x.py"})
    Manifest.model_validate({"name": "ok-slug_1", "entrypoint": "x.py"})


def test_cron_validation_rejects_garbage():
    with pytest.raises(ValidationError):
        Manifest.model_validate({"name": "s", "entrypoint": "x.py", "schedule": "every monday"})


def test_expected_slug_mismatch(tmp_path):
    p = tmp_path / "job.yaml"
    p.write_text("name: real-slug\nentrypoint: main.py\n")
    with pytest.raises(ManifestError, match="does not match folder slug"):
        load_manifest(p, expected_slug="wrong-slug")


def test_malformed_yaml(tmp_path):
    p = tmp_path / "job.yaml"
    p.write_text("name: [unterminated")
    with pytest.raises(ManifestError):
        load_manifest(p)


# --- llm block -----------------------------------------------------------


def test_llm_block_accepts_full_shape():
    m = Manifest.model_validate(
        {
            "name": "j",
            "entrypoint": "x.py",
            "llm": {
                "backend": "claude_cli",
                "model": "claude-opus-4-7",
                "max_budget_usd": 2.5,
                "max_turns": 10,
                "timeout_seconds": 60.0,
                "system_prompt": "be terse",
                "options": {"allowed_tools": ["Read"], "permission_mode": "dontAsk"},
            },
        }
    )
    assert m.llm is not None
    assert m.llm.backend == "claude_cli"
    assert m.llm.model == "claude-opus-4-7"
    assert m.llm.max_budget_usd == 2.5
    assert m.llm.options == {"allowed_tools": ["Read"], "permission_mode": "dontAsk"}


def test_llm_block_is_optional():
    m = Manifest.model_validate({"name": "j", "entrypoint": "x.py"})
    assert m.llm is None


def test_llm_block_rejects_unknown_top_level_key():
    with pytest.raises(ValidationError, match="Extra"):
        Manifest.model_validate({"name": "j", "entrypoint": "x.py", "llm": {"badkey": 1}})


def test_llm_options_is_opaque_at_parse_time():
    """`llm.options` parses as a raw dict; the backend's Options
    schema validates it later (in `backends.validate_llm_options`).
    This lets `manifest.py` stay unaware of which backends exist."""
    m = Manifest.model_validate(
        {
            "name": "j",
            "entrypoint": "x.py",
            "llm": {"options": {"anything": "goes", "here": 42}},
        }
    )
    assert m.llm is not None
    assert m.llm.options == {"anything": "goes", "here": 42}
