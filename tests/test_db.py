import sqlite3
from pathlib import Path

import pytest

from claude_p import db


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_init_db_applies_all_migrations(tmp_path: Path):
    p = tmp_path / "x.db"
    applied = db.init_db(p)
    # When new migrations land, extend this list.
    assert applied == [1, 2, 3, 4, 5]
    t = _tables(p)
    for name in (
        "runs",
        "jobs_state",
        "schedules",
        "settings",
        "secrets",
        "schema_migrations",
        "rate_limit_snapshots",
        "run_model_usage",
        "claude_ai_usage",
    ):
        assert name in t


def test_init_db_is_idempotent(tmp_path: Path):
    p = tmp_path / "x.db"
    first = db.init_db(p)
    second = db.init_db(p)
    assert first  # some migrations applied
    assert second == []


def test_extra_migration_applies_then_records(tmp_path: Path, monkeypatch):
    migs = tmp_path / "migrations"
    migs.mkdir()
    (migs / "001_initial.sql").write_text(
        "CREATE TABLE foo (id INTEGER PRIMARY KEY); "
        "CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    (migs / "002_add_bar.sql").write_text("CREATE TABLE bar (id INTEGER PRIMARY KEY, x TEXT);")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", migs)

    p = tmp_path / "y.db"
    applied = db.init_db(p)
    assert applied == [1, 2]
    t = _tables(p)
    assert "foo" in t and "bar" in t
    with sqlite3.connect(p) as conn:
        rows = conn.execute("SELECT version, filename FROM schema_migrations ORDER BY version").fetchall()
    assert rows == [(1, "001_initial.sql"), (2, "002_add_bar.sql")]


def test_duplicate_version_is_rejected(tmp_path: Path, monkeypatch):
    migs = tmp_path / "migrations"
    migs.mkdir()
    (migs / "001_a.sql").write_text("CREATE TABLE a(id INT);")
    (migs / "001_b.sql").write_text("CREATE TABLE b(id INT);")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", migs)
    with pytest.raises(RuntimeError, match="duplicate migration version"):
        db.init_db(tmp_path / "z.db")


def test_partial_failure_rolls_back(tmp_path: Path, monkeypatch):
    migs = tmp_path / "migrations"
    migs.mkdir()
    (migs / "001_bad.sql").write_text("CREATE TABLE good(id INT); NOT VALID SQL;")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", migs)
    p = tmp_path / "z.db"
    with pytest.raises(sqlite3.OperationalError):
        db.init_db(p)
    # schema_migrations is created (by _ensure_schema_migrations) but has no
    # row for version 1 because the migration failed and was rolled back.
    with sqlite3.connect(p) as conn:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    assert rows == []
