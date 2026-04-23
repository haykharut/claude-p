"""
Tests for the Option-C manifest story: per-job `llm:` block validated
against the selected backend's `Options` schema, executor writes it to
`runs/<id>/llm_config.json`, `run_claude()` reads it as defaults.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from claude_p.backends import (
    ClaudeCLIBackend,
    effective_backend_name,
    resolve_backend_class,
    validate_llm_options,
)
from claude_p.config import Config
from claude_p.executor import _effective_llm_config
from claude_p.manifest import Manifest as M
from claude_p.manifest import ManifestError


def _mk(llm=None, backend_cfg: str = "claude_cli") -> tuple[M, Config]:
    cfg = Config()
    cfg.backend = backend_cfg
    data = {"name": "j", "entrypoint": "x.py"}
    if llm is not None:
        data["llm"] = llm
    return M.model_validate(data), cfg


# --- validator -----------------------------------------------------------


def test_validator_accepts_empty_options():
    m, cfg = _mk(llm={"options": {}})
    validate_llm_options(m, cfg)  # no raise


def test_validator_accepts_valid_claude_options():
    m, cfg = _mk(
        llm={
            "options": {
                "allowed_tools": ["Read", "Write"],
                "permission_mode": "dontAsk",
                "add_dir": ["/tmp/a"],
            }
        }
    )
    validate_llm_options(m, cfg)


def test_validator_rejects_typo_in_claude_options():
    m, cfg = _mk(llm={"options": {"alloed_tools": ["Read"]}})  # typo
    with pytest.raises(ManifestError, match="llm.options"):
        validate_llm_options(m, cfg)


def test_validator_rejects_bad_permission_mode():
    m, cfg = _mk(llm={"options": {"permission_mode": "nope"}})
    with pytest.raises(ManifestError):
        validate_llm_options(m, cfg)


def test_validator_rejects_unknown_backend():
    m, cfg = _mk(llm={"backend": "does_not_exist_backend", "options": {}})
    with pytest.raises(ManifestError, match="llm.backend"):
        validate_llm_options(m, cfg)


def test_validator_noop_when_llm_block_absent():
    m, cfg = _mk(llm=None)
    validate_llm_options(m, cfg)


def test_effective_backend_name_prefers_manifest_over_cfg():
    m, cfg = _mk(llm={"backend": "claude_cli"}, backend_cfg="something_else")
    assert effective_backend_name(m, cfg) == "claude_cli"


def test_effective_backend_name_falls_back_to_cfg():
    m, cfg = _mk(llm={"options": {}}, backend_cfg="claude_cli")
    assert effective_backend_name(m, cfg) == "claude_cli"


# --- backend Options schema --------------------------------------------


def test_claude_cli_backend_has_options_model():
    assert ClaudeCLIBackend.Options is not None
    # Defaults fill in without error. model_dump() avoids pyright
    # narrowing `Backend.Options` (a ClassVar[type[BaseModel]]) to its
    # base class and losing sight of the concrete fields.
    d = ClaudeCLIBackend.Options.model_validate({}).model_dump()
    assert d["permission_mode"] == "dontAsk"
    assert set(d["allowed_tools"]) == {"Read", "Write", "Bash", "WebFetch"}


def test_resolve_backend_class_roundtrip():
    assert resolve_backend_class("claude_cli") is ClaudeCLIBackend


# --- executor writes llm_config.json with filled defaults --------------


def test_effective_llm_config_applies_schema_defaults(tmp_path):
    m, cfg = _mk(llm={"options": {"allowed_tools": ["Read"]}})
    out = _effective_llm_config(m, cfg)
    assert out["backend"] == "claude_cli"
    # Schema default permission_mode survives even though manifest
    # didn't set it — explicit is better than sparse.
    assert out["options"]["permission_mode"] == "dontAsk"
    assert out["options"]["allowed_tools"] == ["Read"]


def test_effective_llm_config_without_manifest_llm_block():
    m, cfg = _mk(llm=None)
    out = _effective_llm_config(m, cfg)
    assert out["backend"] == "claude_cli"
    assert out["model"] is None
    # Empty manifest → schema defaults everywhere.
    assert set(out["options"].keys()) >= {"allowed_tools", "permission_mode"}


# --- run_claude picks up llm_config.json as defaults --------------------


def test_run_claude_loads_injected_config(tmp_path, monkeypatch):
    """Smoke: with CLAUDE_P_LLM_CONFIG set, run_claude() builds its
    RunOptions from the file, overriding code defaults but losing to
    explicit kwargs."""
    config_path = tmp_path / "llm_config.json"
    config_path.write_text(
        json.dumps(
            {
                "backend": "claude_cli",
                "model": "claude-opus-4-7",
                "max_budget_usd": 5.0,
                "max_turns": 7,
                "timeout_seconds": 120.0,
                "system_prompt": "be terse",
                "options": {
                    "allowed_tools": ["Read", "Bash"],
                    "permission_mode": "acceptEdits",
                    "add_dir": ["/tmp/a"],
                    "claude_cli": None,
                },
            }
        )
    )
    monkeypatch.setenv("CLAUDE_P_LLM_CONFIG", str(config_path))
    # Don't actually spawn claude; intercept at the backend layer.
    captured: dict = {}

    async def fake_run_async(self, options, on_event=None):
        captured["options"] = options
        from claude_p.models import BackendResult

        return BackendResult(text="ok", cost_usd=0.0)

    from claude_p.backends.base import Backend

    with patch.object(Backend, "run_async", fake_run_async):
        from claude_p import run_claude

        run_claude("hi")  # no explicit kwargs — should use injected config
    ro = captured["options"]
    assert ro.model == "claude-opus-4-7"
    assert ro.max_budget_usd == 5.0
    assert ro.max_turns == 7
    assert ro.timeout_seconds == 120.0
    assert ro.system_prompt == "be terse"
    assert ro.backend_options["allowed_tools"] == ["Read", "Bash"]
    assert ro.backend_options["permission_mode"] == "acceptEdits"
    assert ro.backend_options["add_dir"] == ["/tmp/a"]


def test_run_claude_explicit_kwargs_beat_injected_config(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_config.json"
    config_path.write_text(
        json.dumps(
            {
                "backend": "claude_cli",
                "model": "from-file",
                "max_budget_usd": 5.0,
                "max_turns": None,
                "timeout_seconds": None,
                "system_prompt": None,
                "options": {"allowed_tools": ["FromFile"], "permission_mode": "dontAsk"},
            }
        )
    )
    monkeypatch.setenv("CLAUDE_P_LLM_CONFIG", str(config_path))
    captured: dict = {}

    async def fake_run_async(self, options, on_event=None):
        captured["options"] = options
        from claude_p.models import BackendResult

        return BackendResult()

    from claude_p.backends.base import Backend

    with patch.object(Backend, "run_async", fake_run_async):
        from claude_p import run_claude

        run_claude("hi", model="explicit-model", allowed_tools=["Explicit"])
    ro = captured["options"]
    assert ro.model == "explicit-model"  # explicit wins
    assert ro.backend_options["allowed_tools"] == ["Explicit"]  # explicit wins
    assert ro.max_budget_usd == 5.0  # not passed explicitly → from file


def test_run_claude_without_injected_config_falls_back_to_code_defaults(monkeypatch):
    """Local invocation (no executor, no env) must still work."""
    monkeypatch.delenv("CLAUDE_P_LLM_CONFIG", raising=False)
    captured: dict = {}

    async def fake_run_async(self, options, on_event=None):
        captured["options"] = options
        from claude_p.models import BackendResult

        return BackendResult()

    from claude_p.backends.base import Backend

    with patch.object(Backend, "run_async", fake_run_async):
        from claude_p import run_claude

        run_claude("hi")
    ro = captured["options"]
    assert ro.max_budget_usd == 1.0  # code default
    assert ro.backend_options.get("permission_mode") == "dontAsk"


def test_run_claude_unreadable_config_falls_back_gracefully(tmp_path, monkeypatch):
    """A pointer to a missing file shouldn't crash user jobs — degrade
    to code defaults and move on."""
    monkeypatch.setenv("CLAUDE_P_LLM_CONFIG", str(tmp_path / "does-not-exist.json"))
    captured: dict = {}

    async def fake_run_async(self, options, on_event=None):
        captured["options"] = options
        from claude_p.models import BackendResult

        return BackendResult()

    from claude_p.backends.base import Backend

    with patch.object(Backend, "run_async", fake_run_async):
        from claude_p import run_claude

        run_claude("hi")
    assert captured["options"].max_budget_usd == 1.0
