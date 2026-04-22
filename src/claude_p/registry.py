from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter
from watchfiles import Change, awatch

from claude_p import queries
from claude_p.config import Config
from claude_p.db import connect
from claude_p.manifest import Manifest, ManifestError, load_manifest, manifest_hash
from claude_p.models import RegistryEntry

log = logging.getLogger(__name__)


class Registry:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.entries: dict[str, RegistryEntry] = {}
        self._task: asyncio.Task | None = None

    def scan(self) -> None:
        seen: set[str] = set()
        for job_dir in sorted(
            self.cfg.jobs_dir.iterdir() if self.cfg.jobs_dir.exists() else []
        ):
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
                slug=slug,
                path=yaml_path.parent,
                manifest=manifest,
                error=None,
                manifest_hash=mh,
            )
            self._persist(slug, manifest, mh, error=None)
            log.info(
                "loaded job %s (runtime=%s schedule=%s)",
                slug,
                manifest.runtime,
                manifest.schedule,
            )
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
            queries.set_job_disabled(conn, slug, "folder removed")
            queries.delete_schedule(conn, slug)
        log.info("job %s folder removed", slug)

    def _persist(
        self,
        slug: str,
        manifest: Manifest | None,
        mh: str | None,
        *,
        error: str | None,
    ) -> None:
        now = datetime.now(timezone.utc)
        with connect(self.cfg.db_path) as conn:
            queries.upsert_job_state(
                conn,
                slug=slug,
                last_seen_at=now,
                manifest_hash=mh,
                manifest_error=error,
            )
            if manifest is None or manifest.schedule is None:
                queries.delete_schedule(conn, slug)
            else:
                next_fire = croniter(manifest.schedule, now).get_next(datetime)
                queries.upsert_schedule(conn, slug, manifest.schedule, next_fire)

    async def run(self, stop_event: asyncio.Event) -> None:
        self.cfg.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.scan()
        async for changes in awatch(
            self.cfg.jobs_dir, stop_event=stop_event, recursive=True
        ):
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
            touched_slugs.add(parts[0])

        for slug in touched_slugs:
            job_dir = self.cfg.jobs_dir / slug
            yaml_path = job_dir / "job.yaml"
            if yaml_path.exists():
                self._load(slug, yaml_path)
            else:
                self._mark_missing(slug)
