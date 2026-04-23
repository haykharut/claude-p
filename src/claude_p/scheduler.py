from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from claude_p import queries
from claude_p.auto_schedule import AutoInputs, decide_batch
from claude_p.config import Config
from claude_p.db import connect
from claude_p.executor import execute_run
from claude_p.models import FIVE_HOUR_WINDOW_KEY, SEVEN_DAY_WINDOW_KEY
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
            except TimeoutError:
                pass

    async def _tick(self) -> None:
        now = datetime.now(UTC)

        due: list[str] = []
        auto_fires: list[str] = []

        with connect(self.cfg.db_path) as conn:
            # Cron-mode jobs: same as before.
            due = queries.due_job_slugs(conn, now)

            # Auto-mode jobs: load everything the decision function needs.
            auto_schedules = queries.list_auto_schedules(conn)
            if auto_schedules:
                settings = queries.load_auto_settings(conn)
                five_hour_util = queries.fetch_window_util(conn, FIVE_HOUR_WINDOW_KEY)
                seven_day_util = queries.fetch_window_util(conn, SEVEN_DAY_WINDOW_KEY)
                spend_7d = queries.window_totals(conn, 24 * 7).cost_usd
                weekly_budget = queries.get_weekly_budget(conn)
                last_auto_fire_at = queries.latest_auto_fire_at(conn)

                auto_configs = {}
                for s in auto_schedules:
                    entry = self.registry.entries.get(s.slug)
                    if entry and entry.manifest and entry.manifest.auto:
                        auto_configs[s.slug] = entry.manifest.auto

                estimates = {
                    s.slug: queries.auto_job_cost_estimate(conn, s.slug, settings) for s in auto_schedules
                }

                decisions = decide_batch(
                    AutoInputs(
                        now_utc=now,
                        schedules=auto_schedules,
                        auto_configs=auto_configs,
                        estimates=estimates,
                        five_hour_util=five_hour_util,
                        seven_day_util=seven_day_util,
                        spend_7d=spend_7d,
                        weekly_budget=weekly_budget,
                        settings=settings,
                        in_flight=set(self._in_flight),
                        last_auto_fire_at=last_auto_fire_at,
                    )
                )

                for slug, decision in decisions.items():
                    sched = next((s for s in auto_schedules if s.slug == slug), None)
                    if decision.verdict == "fire":
                        # last_fire_at is only bumped once we've confirmed
                        # the registry entry is still viable (see below).
                        auto_fires.append(slug)
                        log.info("auto fire %s: %s", slug, decision.reason)
                    elif decision.verdict == "defer":
                        if sched and sched.deferred_since is None:
                            queries.set_schedule_deferred(conn, slug, now)
                        log.debug("auto defer %s: %s", slug, decision.reason)

        for slug in due:
            if slug in self._in_flight:
                continue
            entry = self.registry.entries.get(slug)
            if entry is None or entry.manifest is None:
                continue
            asyncio.create_task(self._spawn(slug, "schedule"))

        for slug in auto_fires:
            if slug in self._in_flight:
                continue
            entry = self.registry.entries.get(slug)
            if entry is None or entry.manifest is None:
                # Registry vanished between decision and dispatch. Do NOT
                # bump last_fire_at — we didn't actually fire. Next tick
                # will re-evaluate.
                continue
            with connect(self.cfg.db_path) as conn:
                queries.set_schedule_fired(conn, slug, now)
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
            return await execute_run(self.cfg, entry.manifest, entry.path, trigger=trigger)
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
