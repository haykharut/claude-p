from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from claude_p.auth import BasicAuthMiddleware
from claude_p.config import Config, get_config
from claude_p.registry import Registry
from claude_p.scheduler import Scheduler

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "web" / "templates"
STATIC_DIR = Path(__file__).parent.parent / "web" / "static"


@dataclass
class AppState:
    cfg: Config
    registry: Registry
    scheduler: Scheduler
    templates: Jinja2Templates


def build_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or get_config()
    cfg.ensure_dirs()

    registry = Registry(cfg)
    scheduler = Scheduler(cfg, registry)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from claude_p import claude_ai

        stop = asyncio.Event()
        registry.scan()
        reg_task = asyncio.create_task(registry.run(stop), name="registry-watcher")
        sched_task = asyncio.create_task(scheduler.run(stop), name="scheduler")
        claude_ai_task = asyncio.create_task(claude_ai.poller(cfg, stop), name="claude-ai-poller")
        log.info("claude-p daemon started (data_dir=%s)", cfg.data_dir)
        try:
            yield
        finally:
            stop.set()
            for t in (reg_task, sched_task, claude_ai_task):
                t.cancel()
            await asyncio.gather(reg_task, sched_task, claude_ai_task, return_exceptions=True)
            log.info("claude-p daemon stopped")

    app = FastAPI(title="claude-p", lifespan=lifespan)
    app.state.claude_p = AppState(cfg=cfg, registry=registry, scheduler=scheduler, templates=templates)

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Mount WebDAV before auth middleware so WebDAV handles its own auth headers.
    # BUT the BasicAuthMiddleware still wraps the whole app — see webdav.py for
    # why this interplay is safe (same credentials, same realm).
    from claude_p.webdav import mount_webdav

    mount_webdav(app, cfg)

    # Routes
    from claude_p.api import (  # noqa: E402
        jobs,
        runs,
    )
    from claude_p.api import (
        ledger as ledger_api,
    )
    from claude_p.api import (
        settings as settings_api,
    )

    app.include_router(jobs.router)
    app.include_router(runs.router)
    app.include_router(ledger_api.router)
    app.include_router(settings_api.router)

    app.add_middleware(BasicAuthMiddleware, db_path=cfg.db_path)
    return app
