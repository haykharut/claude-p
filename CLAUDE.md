# CLAUDE.md — orientation + conventions for contributors

You are working in **claude-p**, a home-server job runner for Claude Code
(`claude -p`) agent jobs. The README covers the user-facing pitch; this
file covers the things anyone (human or AI) writing code in the repo must
know before touching anything.

## Project shape

Single Python package (`src/claude_p/`) deployed as a FastAPI daemon:

- `__main__.py` — CLI: `serve | db-init | doctor | set-password | set-budget`
- `config.py` — pydantic-settings; env prefix `CLAUDE_P_`
- `db.py` — connection + migrations loader (see rules below)
- `migrations/NNN_*.sql` — schema changes, one file per change, ever-forward
- `models.py` — every type that crosses a module boundary (DB rows, rollups,
  registry entries, view models). Pydantic `BaseModel`.
- `queries.py` — raw SQL in, typed models out. No ORM.
- `manifest.py` — `job.yaml` schema (Pydantic)
- `registry.py` — watchfiles → in-memory `dict[slug, RegistryEntry]` + DB
- `scheduler.py` — croniter poller, 10s tick, spawns runs via executor
- `executor.py` — asyncio subprocess, uv/shell runtimes, output copy, ledger roll-up
- `claude_runner.py` — `claude -p` wrapper + stream-json parser. **Always** parses
  `total_cost_usd` and `usage.{input,output,cache_read_input,cache_creation_input}_tokens`
  from the final `result` event.
- `scaffolder` lives in `api/scaffold.py` (historic reasons; it's really part of
  the core, just exposed via HTTP)
- `webdav.py` — wsgidav + a2wsgi mounted at `/fs`
- `auth.py` — argon2 single-password HTTP Basic middleware
- `api/` — FastAPI routers (`jobs`, `runs`, `ledger`, `scaffold`)
- `web/` — Jinja2 templates + one static CSS file (no JS framework, just
  vanilla + an `EventSource` for scaffold SSE)

Persistent state on disk under `~/claudectl/`:
- `claude-p.db` (SQLite, WAL)
- `fs/jobs/<slug>/{job.yaml, workspace/, runs/<run-id>/, .venv/, ...}`
- `fs/shared/`, `fs/inbox/`

## Hard rules

These are non-negotiable. If you need to break one, say why out loud first.

### 1. Never pass `--bare` to `claude -p`

Stream-json auth path depends on non-bare mode. `--bare` ignores both
`~/.claude/` OAuth creds and `CLAUDE_CODE_OAUTH_TOKEN`. The helper
`claude_runner.build_claude_argv()` is the single place that constructs
the argv — use it. If you invent a second place, make that place go
through it.

### 2. Schema changes go through a new migration file

To change the schema, add `src/claude_p/migrations/NNN_description.sql`
where `NNN` is the next unused version number. Never edit an existing
migration once it's been released. Migrations are applied in order,
each in its own transaction, tracked in `schema_migrations`. Update
`models.py` in the same commit so the types stay in sync.

### 3. Cross-module data is Pydantic

If a value crosses a module boundary — DB ↔ query ↔ API ↔ template,
or registry ↔ scheduler — it's a `BaseModel` in `models.py`. Internal
mutable state (stream-json accumulator, in-flight `Scaffold` struct)
can stay a `@dataclass` in its owning module. The test for "does this
deserve a model?" is "would a reader of the consumer expect attribute
access with autocomplete?" If yes, model.

### 4. Update `CHANGELOG.md` on every change

Every commit that changes behavior, CLI surface, schema, or dependencies
must add an entry to `CHANGELOG.md` under `## [Unreleased]` in the same
commit. Categories (Keep-a-Changelog): `Added | Changed | Deprecated |
Removed | Fixed | Security`. Small internal refactors with no user-visible
effect can be omitted — but when in doubt, log it.

When we cut a release, move the `[Unreleased]` section into a new
`[X.Y.Z] - YYYY-MM-DD` block and reset `[Unreleased]` to empty.

## Conventions

- **Pydantic v2 only.** `ConfigDict`, `model_validate`, `model_dump`. No
  v1 APIs.
- **asyncio subprocess** for anything the daemon spawns. Sync subprocess
  is fine inside `run_claude` since jobs are already subprocesses.
- **ISO datetimes** in the DB (TEXT column, `datetime.isoformat()`).
  Pydantic coerces strings → datetime automatically on `model_validate`.
- **No bare `dict`s in route responses.** Use `TemplateResponse(request,
  "x.html", {...})` (Starlette 1.0+ signature with `request` first).
- **Tests next to the feature.** `tests/test_<module>.py`. Fast, no
  network. For claude-related things, fake the stream-json payload —
  don't hit real `claude -p` in unit tests.
- **Logging via `logging.getLogger(__name__)`.** `log.info`/`log.warning`
  sparingly, `log.exception` in broad except blocks.

## Local development on macOS

```bash
uv venv --python 3.12 && uv pip install -e '.[dev]'
CLAUDE_P_DATA_DIR=~/claudectl .venv/bin/claude-p db-init
CLAUDE_P_DATA_DIR=~/claudectl .venv/bin/claude-p set-password
CLAUDE_P_DATA_DIR=~/claudectl .venv/bin/claude-p serve
```

Dashboard at `http://localhost:8080`. `cmd-K` in Finder → `http://localhost:8080/fs`.
`claude login` once if you haven't (Mac stores creds in Keychain; the daemon's
subprocesses inherit the session).

## Running tests

```bash
.venv/bin/pytest -q           # all tests
.venv/bin/pytest -x -q tests/test_manifest.py   # one file, stop on first fail
```

## Ledger contract for jobs that shell out to `claude -p` directly

Jobs that use `from claude_p import run_claude` get ledger entries for
free. Jobs that invoke the `claude` binary themselves (e.g. because they
need `--json-schema` or other flags not in `run_claude`) must write one
JSON line per call to:

```
<CLAUDE_P_JOB_DIR>/runs/<CLAUDE_P_RUN_ID>/claude_calls.jsonl
```

Fields (all optional, unknown keys ignored):
- `cost_usd: float`         — `total_cost_usd` from the envelope
- `input_tokens: int`       — `usage.input_tokens`
- `output_tokens: int`      — `usage.output_tokens`
- `cache_read_tokens: int`  — `usage.cache_read_input_tokens`
- `cache_creation_tokens: int` — `usage.cache_creation_input_tokens`
- `model_usage: dict`       — raw `modelUsage` from the envelope (keyed by model name)
- `is_error: bool`, `num_turns: int`, `session_id: str` — diagnostic

The env vars `CLAUDE_P_RUN_ID` and `CLAUDE_P_JOB_DIR` are always set by
the executor. If either is missing, the job is running locally and
should no-op the ledger write. Reference impl: see
`claude_p.claude_runner._append_ledger` and the inline helper
`_report_to_ledger` in jobs/job-search/scripts/scout/classify.py.

## Things that would be nice but aren't in v1

(Mirror of the "out of scope" in the plan file so nobody proposes them without
the context.)

Docker runtime, Node runtime, bubblewrap sandbox, Telegram notifier, email
notifier, `inbox/` file-triggered runs, shared-folder cross-job UI,
Syncthing setup docs, multi-user/RBAC, local image registry, proper agent
trace viewer (chat-style), per-job budget enforcement as a hard gate.
