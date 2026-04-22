from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter
from watchfiles import Change, awatch

from claude_p.config import Config
from claude_p.db import connect
from claude_p.manifest import Manifest, ManifestError, load_manifest, manifest_hash

log = logging.getLogger(__name__)


@dataclass
class RegistryEntry:
    slug: str
    path: Path
    manifest: Manifest | None
    error: str | None
    manifest_hash: str | None


class Registry:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.entries: dict[str, RegistryEntry] = {}
        self._task: asyncio.Task | None = None

    def scan(self) -> None:
        seen: set[str] = set()
        for job_dir in sorted(self.cfg.jobs_dir.iterdir() if self.cfg.jobs_dir.exists() else []):
            if not job_dir.is_dir():
                continue
            yaml_path = job_dir / "job.yaml"
            if not yaml_path.exists():
                continue
            slug = job_dir.name
            seen.add(slug)
            self._load(slug, yaml_path)
        for slug in list(self.entries):
            if slug not in seen:
                self._mark_missing(slug)

    def _load(self, slug: str, yaml_path: Path) -> None:
        try:
            mh = manifest_hash(yaml_path)
            existing = self.entries.get(slug)
            if existing and existing.manifest_hash == mh and existing.manifest is not None:
                return
            manifest = load_manifest(yaml_path, expected_slug=slug)
            self.entries[slug] = RegistryEntry(
                slug=slug, path=yaml_path.parent, manifest=manifest, error=None, manifest_hash=mh
            )
            self._persist(slug, manifest, mh, error=None)
            log.info("loaded job %s (runtime=%s schedule=%s)", slug, manifest.runtime, manifest.schedule)
        except ManifestError as e:
            msg = str(e)
            self.entries[slug] = RegistryEntry(
                slug=slug, path=yaml_path.parent, manifest=None, error=msg, manifest_hash=None
            )
            self._persist(slug, None, None, error=msg)
            log.warning("job %s has invalid manifest: %s", slug, msg)

    def _mark_missing(self, slug: str) -> None:
        entry = self.entries.pop(slug, None)
        if entry is None:
            return
        with connect(self.cfg.db_path) as conn:
            conn.execute(
                "UPDATE jobs_state SET disabled_reason=? WHERE slug=?",
                ("folder removed", slug),
            )
            conn.execute("DELETE FROM schedules WHERE slug=?", (slug,))
        log.info("job %s folder removed", slug)

    def _persist(
        self,
        slug: str,
        manifest: Manifest | None,
        mh: str | None,
        *,
        error: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.cfg.db_path) as conn:
            conn.execute(
                """
                INSERT INTO jobs_state(slug, last_seen_at, last_manifest_hash, manifest_error, disabled_reason)
                VALUES(?,?,?,?,NULL)
                ON CONFLICT(slug) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    last_manifest_hash=excluded.last_manifest_hash,
                    manifest_error=excluded.manifest_error,
                    disabled_reason=NULL
                """,
                (slug, now, mh, error),
            )
            if manifest is None or manifest.schedule is None:
                conn.execute("DELETE FROM schedules WHERE slug=?", (slug,))
            else:
                base = datetime.now(timezone.utc)
                next_fire = croniter(manifest.schedule, base).get_next(datetime).isoformat()
                conn.execute(
                    """
                    INSERT INTO schedules(slug, cron, next_fire_at, last_fire_at)
                    VALUES(?,?,?,NULL)
                    ON CONFLICT(slug) DO UPDATE SET
                        cron=excluded.cron,
                        next_fire_at=CASE
                            WHEN schedules.cron=excluded.cron THEN schedules.next_fire_at
                            ELSE excluded.next_fire_at
                        END
                    """,
                    (slug, manifest.schedule, next_fire),
                )

    async def run(self, stop_event: asyncio.Event) -> None:
        self.cfg.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.scan()
        async for changes in awatch(self.cfg.jobs_dir, stop_event=stop_event, recursive=True):
            self._apply_changes(changes)

    def _apply_changes(self, changes: set[tuple[Change, str]]) -> None:
        root = self.cfg.jobs_dir.resolve()
        touched_slugs: set[str] = set()
        for _change, raw in changes:
            p = Path(raw).resolve()
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            parts = rel.parts
            if not parts:
                continue
            slug = parts[0]
            touched_slugs.add(slug)

        for slug in touched_slugs:
            job_dir = self.cfg.jobs_dir / slug
            yaml_path = job_dir / "job.yaml"
            if yaml_path.exists():
                self._load(slug, yaml_path)
            else:
                self._mark_missing(slug)
