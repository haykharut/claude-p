from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from claude_p.config import Config
from claude_p.db import connect
from claude_p.executor import execute_run
from claude_p.registry import Registry

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, cfg: Config, registry: Registry):
        self.cfg = cfg
        self.registry = registry
        self._in_flight: set[str] = set()
        self._lock = asyncio.Lock()

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("scheduler tick error")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.cfg.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        due: list[str] = []
        with connect(self.cfg.db_path) as conn:
            rows = conn.execute(
                """
                SELECT s.slug FROM schedules s
                JOIN jobs_state j ON j.slug = s.slug
                WHERE s.next_fire_at IS NOT NULL
                  AND s.next_fire_at <= ?
                  AND (j.disabled_reason IS NULL)
                """,
                (now,),
            ).fetchall()
            due = [r["slug"] for r in rows]

        for slug in due:
            if slug in self._in_flight:
                continue
            entry = self.registry.entries.get(slug)
            if entry is None or entry.manifest is None:
                continue
            asyncio.create_task(self._spawn(slug, "schedule"))

    async def trigger(self, slug: str, trigger: str = "manual") -> str | None:
        async with self._lock:
            if slug in self._in_flight:
                return None
            entry = self.registry.entries.get(slug)
            if entry is None or entry.manifest is None:
                return None
            self._in_flight.add(slug)
        try:
            run_id = await execute_run(
                self.cfg, entry.manifest, entry.path, trigger=trigger
            )
            return run_id
        finally:
            self._in_flight.discard(slug)

    async def _spawn(self, slug: str, trigger: str) -> None:
        async with self._lock:
            if slug in self._in_flight:
                return
            entry = self.registry.entries.get(slug)
            if entry is None or entry.manifest is None:
                return
            self._in_flight.add(slug)
        try:
            await execute_run(self.cfg, entry.manifest, entry.path, trigger=trigger)
        except Exception:
            log.exception("run failed for %s", slug)
        finally:
            self._in_flight.discard(slug)

    def is_running(self, slug: str) -> bool:
        return slug in self._in_flight
