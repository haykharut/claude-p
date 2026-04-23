from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{0,62}$")
DURATION_RE = re.compile(r"^(\d+)(ms|s|m|h)$")


class ManifestError(Exception):
    pass


def parse_duration(s: str) -> float:
    m = DURATION_RE.match(s.strip())
    if not m:
        raise ValueError(f"invalid duration: {s!r} (use e.g. '30s', '10m', '1h', '500ms')")
    n = int(m.group(1))
    unit = m.group(2)
    return {"ms": n / 1000.0, "s": float(n), "m": float(n * 60), "h": float(n * 3600)}[unit]


class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["str", "int", "float", "bool", "list", "dict"] = "str"
    default: Any = None


class LlmConfig(BaseModel):
    """How this job should invoke the LLM.

    This block is what turns a `main.py` that calls `run_claude(...)`
    into a *portable* job — the same `main.py` can drive different
    backends by changing `backend:` here. The executor writes these
    values into `runs/<run-id>/llm_config.json` before spawning the
    job process; `run_claude()` reads that file and uses the values as
    defaults. Explicit kwargs passed to `run_claude()` still win.

    Shape:

        llm:
          backend: claude_cli         # optional; falls back to CLAUDE_P_BACKEND
          model: null                 # optional; backend decides the default
          max_budget_usd: 1.00        # circuit-breaker; forwarded to the backend
          max_turns: null             # optional cap on agent turns
          timeout_seconds: null       # optional wall-clock cap for one run_claude() call
          system_prompt: null         # optional: prepended as system prompt
          options:                    # **backend-specific** flags, validated
            allowed_tools: [...]      # against the selected backend's Options
            permission_mode: dontAsk  # model at registry-load time (extra="forbid"
            add_dir: [...]            # catches typos).

    Explicit resolution order at call time:

        explicit run_claude() kwargs  >  manifest llm block  >  code defaults

    The `options` dict is passed through verbatim to
    `RunOptions.backend_options`. It means different things depending
    on `backend:` — `allowed_tools` / `permission_mode` / `add_dir` for
    the claude_cli backend; whatever future backends declare.
    """

    model_config = ConfigDict(extra="forbid")

    backend: str | None = None
    model: str | None = None
    max_budget_usd: float = 0.50
    max_turns: int | None = None
    timeout_seconds: float | None = None
    system_prompt: str | None = None
    # Raw backend-native options. Validated against the selected
    # backend's Options schema by `validate_llm_options` in the
    # registry; a manifest using unknown keys for its backend will be
    # rejected and the job marked broken in the dashboard.
    options: dict[str, Any] = Field(default_factory=dict)


class NotifyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on_success: str = "dashboard"
    on_failure: str = "dashboard"


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    runtime: Literal["uv", "shell"] = "uv"
    entrypoint: str
    schedule: str | None = None
    timeout: str = "10m"
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    env: list[str] = Field(default_factory=list)
    workspace: bool = True
    shared: bool = False
    output_globs: list[str] = Field(default_factory=list)
    # How this job invokes the LLM. See `LlmConfig` for the full shape
    # and resolution order. Omit to let the job hard-code everything in
    # `run_claude()` kwargs.
    llm: LlmConfig | None = None
    notify: NotifyConfig = Field(default_factory=NotifyConfig)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(
                "name must be a slug: lowercase letters, digits, '-' or '_', starting with letter/digit, max 63 chars"
            )
        return v

    @field_validator("schedule")
    @classmethod
    def _validate_schedule(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not croniter.is_valid(v):
            raise ValueError(f"invalid cron expression: {v!r}")
        return v

    @field_validator("timeout")
    @classmethod
    def _validate_timeout(cls, v: str) -> str:
        parse_duration(v)
        return v

    @model_validator(mode="after")
    def _check_entrypoint(self) -> Manifest:
        if "/" in self.entrypoint and self.entrypoint.startswith("/"):
            raise ValueError("entrypoint must be relative to the job folder")
        if ".." in Path(self.entrypoint).parts:
            raise ValueError("entrypoint may not contain '..'")
        return self

    @property
    def timeout_seconds(self) -> float:
        return parse_duration(self.timeout)


def load_manifest(yaml_path: Path, expected_slug: str | None = None) -> Manifest:
    if not yaml_path.is_file():
        raise ManifestError(f"{yaml_path} not found")
    try:
        raw = yaml.safe_load(yaml_path.read_text())
    except yaml.YAMLError as e:
        raise ManifestError(f"{yaml_path}: YAML parse error: {e}") from e
    if not isinstance(raw, dict):
        raise ManifestError(f"{yaml_path}: top-level must be a mapping, got {type(raw).__name__}")
    try:
        m = Manifest.model_validate(raw)
    except Exception as e:
        raise ManifestError(f"{yaml_path}: {e}") from e
    if expected_slug is not None and m.name != expected_slug:
        raise ManifestError(f"{yaml_path}: name {m.name!r} does not match folder slug {expected_slug!r}")
    return m


def manifest_hash(yaml_path: Path) -> str:
    return hashlib.sha256(yaml_path.read_bytes()).hexdigest()
