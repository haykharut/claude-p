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


class ClaudeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_tools: list[str] = Field(default_factory=lambda: ["Read", "Write", "Bash", "WebFetch"])
    permission_mode: Literal["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"] = "dontAsk"
    max_budget_usd: float = 0.50
    max_turns: int | None = None


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
    claude: ClaudeConfig | None = None
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
