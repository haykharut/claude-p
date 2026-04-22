"""
SQLite connection + migrations loader.

Migrations live as numbered `.sql` files in `migrations/`. Each file is run
once, inside a transaction, and recorded in `schema_migrations`. To add a
new migration, drop in `src/claude_p/migrations/NNN_description.sql` where
NNN is the next integer. The filename's numeric prefix is the version.

Query helpers live in queries.py — this module is only about connection
lifecycle, migrations, and trivial key/value settings access.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_VERSION_RE = re.compile(r"^(\d+)_")


def _discover_migrations() -> list[tuple[int, Path]]:
    if not MIGRATIONS_DIR.is_dir():
        return []
    out: list[tuple[int, Path]] = []
    for p in MIGRATIONS_DIR.glob("*.sql"):
        m = _VERSION_RE.match(p.name)
        if not m:
            continue
        out.append((int(m.group(1)), p))
    out.sort(key=lambda t: t[0])
    seen: set[int] = set()
    for v, p in out:
        if v in seen:
            raise RuntimeError(f"duplicate migration version {v} ({p.name})")
        seen.add(v)
    return out


def _ensure_schema_migrations(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version      INTEGER PRIMARY KEY,
            applied_at   TEXT    NOT NULL,
            filename     TEXT    NOT NULL
        )
        """
    )
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def init_db(db_path: Path) -> list[int]:
    """Create the DB file if needed and apply any unapplied migrations.

    Returns the list of newly-applied migration versions (empty when
    already up to date). Safe and cheap to call on every startup.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    applied_now: list[int] = []
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        already = _ensure_schema_migrations(conn)
        for version, path in _discover_migrations():
            if version in already:
                continue
            sql = path.read_text()
            try:
                conn.execute("BEGIN")
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at, filename) VALUES (?,?,?)",
                    (version, datetime.now(timezone.utc).isoformat(), path.name),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            applied_now.append(version)
    return applied_now


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
