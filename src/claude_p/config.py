from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLAUDE_P_", env_file=".env", extra="ignore")

    data_dir: Path = Path.home() / "claudectl"
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    poll_seconds: int = 10
    scaffolder_max_budget_usd: float = 0.50
    session_secret: str = "change-me-in-production"
    dashboard_password_hash: str = ""
    claude_cli: str = "claude"
    uv_cli: str = "uv"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "claude-p.db"

    @property
    def fs_root(self) -> Path:
        return self.data_dir / "fs"

    @property
    def jobs_dir(self) -> Path:
        return self.fs_root / "jobs"

    @property
    def shared_dir(self) -> Path:
        return self.fs_root / "shared"

    @property
    def inbox_dir(self) -> Path:
        return self.fs_root / "inbox"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.fs_root, self.jobs_dir, self.shared_dir, self.inbox_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_config() -> Config:
    cfg = Config()
    override = os.environ.get("CLAUDE_P_DATA_DIR")
    if override:
        cfg.data_dir = Path(override).expanduser()
    return cfg
