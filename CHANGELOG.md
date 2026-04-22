# Changelog

All notable changes to claude-p are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `CHANGELOG.md` (this file) and `CLAUDE.md` with project conventions for
  contributors (human and AI).
- Rate-limit visibility on the ledger page. `claude -p` emits a
  `rate_limit_event` with the 5-hour (and weekly, when present) window's
  reset time and overage status; we now capture those, persist the
  latest per window in a new `rate_limit_snapshots` table, and render
  countdown + overage state as cards above the cost windows. Empty state
  gets a "Probe now (<$0.01)" button that fires a one-word `claude -p`
  call just to populate the snapshot.
- Per-model cost / token breakdown. The final `result` event's
  `modelUsage` field is now parsed per-run, persisted into a new
  `run_model_usage` table, and rolled up on the ledger page as a
  7-day "By model" table. Lets you see where the spend goes (Opus vs.
  Sonnet vs. Haiku).
- Migration `002_rate_limits_and_model_usage.sql` adds
  `rate_limit_snapshots` and `run_model_usage`.
- `RateLimitSnapshot` and `ModelUsage` models.
- Unit tests for the new stream-json parsing paths
  (`rate_limit_event`, `modelUsage`).

### Changed
- `ClaudeResult` now carries `model_usage` and `rate_limit_events`
  fields. `run_claude()` writes an adjacent `claude_rate_limits.jsonl`
  next to `claude_calls.jsonl` when invoked inside a job so the
  executor can aggregate both.

## [0.1.0] - 2026-04-22

First working cut. Dogfooded end-to-end on macOS: folder-as-registry, `uv`
runtime, real scaffolder runs, live SSE trace viewer, ledger tracking.

### Added
- FastAPI daemon (`claude-p serve`) with Jinja2 + pico-style dark dashboard.
- Filesystem-as-registry: drop a folder with `job.yaml` under
  `~/claudectl/fs/jobs/`, the watcher picks it up in <2s. Delete the
  folder, it's gone.
- `uv` and `shell` runtimes. `uv sync` runs per-job before execution;
  workspace is the per-job cwd, surviving across runs.
- `croniter`-backed scheduler polling SQLite every 10s.
- `claude_runner.run_claude()` helper that jobs import to call
  `claude -p` with consistent flags, stream-json parsing, and automatic
  ledger accounting. **Never** passes `--bare`.
- Scaffolder endpoint: describe a job in English, Claude writes
  `job.yaml` + code + deps. Live SSE trace streams to the browser.
- WebDAV served at `/fs` via wsgidav + a2wsgi, protected by the same
  Basic auth as the dashboard.
- Token ledger: per-window rollups (5h / 24h / 7d), per-job averages,
  self-declared weekly budget with progress bar. Scaffolder runs are
  tracked under `__scaffold__:<slug>`.
- Numbered SQL migrations (`migrations/NNN_*.sql`, tracked via
  `schema_migrations`).
- Pydantic models for all cross-module row types and rollups
  (`models.py`); raw SQL in queries.py returns typed models.
- Basic auth middleware (argon2-hashed single password) guarding both
  dashboard and WebDAV.
- CLI subcommands: `serve`, `db-init`, `doctor`, `set-password`,
  `set-budget`.
- Ubuntu install script + systemd unit (untested on Ubuntu as of this
  release).
- Example `hello-world` job, `scaffolder.md` system prompt.
- Tests: 19 unit tests covering manifest, claude runner, ledger,
  migrations.

### Known limitations
- Install script and systemd unit written but not validated on Ubuntu.
- `.venv/` folders visible in WebDAV listing (no `hide_file_in_dir`).
- HTTP Basic accepts any non-empty username; only the password is
  checked.
- No sandbox layer beyond Unix user isolation.
- No concurrency throttle on simultaneous `claude -p` invocations.
- Scaffolder default budget is `$0.50` — expect ~$0.10–0.20 per
  scaffold with Opus.

[Unreleased]: https://github.com/haykharut/claude-p/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/haykharut/claude-p/releases/tag/v0.1.0
