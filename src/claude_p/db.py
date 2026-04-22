from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS runs (
        id                      TEXT PRIMARY KEY,
        job_slug                TEXT NOT NULL,
        started_at              TEXT NOT NULL,
        ended_at                TEXT,
        exit_code               INTEGER,
        trigger                 TEXT NOT NULL,       -- 'schedule' | 'manual' | 'scaffold'
        cost_usd                REAL DEFAULT 0,
        input_tokens            INTEGER DEFAULT 0,
        output_tokens           INTEGER DEFAULT 0,
        cache_read_tokens       INTEGER DEFAULT 0,
        cache_creation_tokens   INTEGER DEFAULT 0,
        error                   TEXT,
        run_dir                 TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_job_slug ON runs(job_slug, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS jobs_state (
        slug                    TEXT PRIMARY KEY,
        last_seen_at            TEXT NOT NULL,
        last_manifest_hash      TEXT,
        disabled_reason         TEXT,
        manifest_error          TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schedules (
        slug                    TEXT PRIMARY KEY,
        cron                    TEXT,
        next_fire_at            TEXT,
        last_fire_at            TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key                     TEXT PRIMARY KEY,
        value                   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS secrets (
        name                    TEXT PRIMARY KEY,
        value_encrypted         TEXT NOT NULL,
        created_at              TEXT NOT NULL
    )
    """,
]


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        for stmt in SCHEMA:
            conn.execute(stmt)
        conn.commit()


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
