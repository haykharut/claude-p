# Changelog

All notable changes to claude-p are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **job detail page: LLM config card now renders correctly.** Updated
  `job_detail.html` to reference `m.llm` instead of the removed `m.claude`
  attribute, aligning field paths with `LlmConfig` (`max_budget_usd`,
  `model`, `max_turns`, `backend`, `options`).
- **meta-scout: fix `--verbose` causing JSON array output.** Removed
  `--verbose` from `claude -p` invocation which caused `--output-format json`
  to return a message array instead of a result envelope, crashing on
  `'list' object has no attribute 'get'`. Added defensive list handling.

### Added
- **`jobs/meta-scout` — weekly codebase analysis job.** Runs two-phase
  Claude evaluation (brainstorm → filter) and creates GitHub issues
  labeled `scout-proposal` for the top-scoring feature ideas. Includes
  entrypoint-side dedup against existing issues.
- **`jobs/meta-builder` — daily builder job.** Picks the oldest
  `approved`-labeled GitHub issue, implements it on a feature branch
  using Claude, runs lint + tests (with one retry), and opens a PR.
  Draft PRs for failing checks, full PRs for clean implementations.

### Changed
- **README trimmed from ~206 to ~120 lines.** Moved "Why this exists",
  "What people build", "Who it's for" into `docs/overview.md`. Moved
  Ubuntu install options and first-run walkthrough into `docs/install.md`.
  README now focuses on: pitch → comparison table → code example →
  quickstart → docs index.

### Added
- **`scripts/setup-sync.sh` — one-command Syncthing pairing.** SSHs
  into the server, exchanges device IDs via the Syncthing REST API,
  creates the shared folder on both sides, and sets ignore patterns.
  No browser tabs needed.
- **Syncthing setup guide in `docs/filesystem.md`.** Full walkthrough
  for bidirectional sync between a dev laptop and the server, including
  ignore patterns for `.venv`/`__pycache__`/`*.pyc` (platform-specific
  artifacts that break cross-platform). README updated to recommend
  Syncthing for development alongside WebDAV for browsing.
- **`scripts/bootstrap.sh` — one-command personal-server setup.** Creates
  venv, initializes DB, prompts for dashboard password, installs a
  systemd user service, enables loginctl linger, starts the daemon, and
  runs doctor. Replaces the manual multi-step install for personal boxes.
- **`scripts/update.sh` — pull, migrate, restart.** Pulls latest code,
  re-syncs the venv, applies new migrations via `db-init`, and restarts
  the systemd user service. One command after pushing new code.
- **`systemd/claude-p.user.service` — user-level systemd unit template.**
  Reference file; `bootstrap.sh` generates the actual unit with correct
  absolute paths at install time.
- **`schedule: auto` mode for jobs.** Instead of a cron expression, a
  job can declare a cadence (`every: 1d`) and optional priority and let
  the scheduler decide *when* to fire based on current 5h / weekly
  Claude utilization, historical per-job cost and utilization impact,
  time of day, and remaining weekly USD budget. The goal is to fill
  quiet quota windows — nighttime, low utilization — without busting
  your session or weekly budget. Soft deadline (default 2× cadence)
  guarantees forward progress; firing is skipped when any threshold
  would be breached.
  - New `auto:` block in `job.yaml` with `every`, `deadline`, `priority`
    (`low` | `normal`).
  - Per-run claude.ai utilization snapshots (`five_hour_util_at_start/end`,
    `seven_day_util_at_start/end`) so each job's own footprint is
    learned from its last 10 runs (median delta). Cold-start defaults
    kick in when sample size < `auto_coldstart_min_samples` (default 3).
  - ~14 new `auto_*` global settings (daytime window, local TZ,
    day/night 5h thresholds × normal/low priority, weekly skip cap,
    weekly budget guard fraction, safety factor, cold-start defaults,
    fleet `min_seconds_between_fires` cooldown). All have sane defaults
    seeded by migration 005; tunable at `/settings` via the new
    `POST /settings/auto` form endpoint.
  - Dashboard: auto jobs render their cadence in the schedule column
    (`auto (every 1d)`), show a "deferred Xs" chip when the scheduler
    has been waiting for a good slot, and the detail page includes a
    **Cost estimate** card with avg/p90 cost and predicted 5h/7d
    utilization impact (flagged as cold-start until the job has run
    enough times to learn).
  - `docs/jobs.md` section "Auto schedule: fill unused quota, not
    wall-clock slots" with a worked example.
- **Migration `005_auto_schedule.sql`** — rebuilds `schedules` to make
  `cron` nullable (auto mode stores its config in the new
  `auto_config_json` column) and adds `mode`, `auto_config_json`,
  `deferred_since`. Adds the four snapshot columns on `runs`. Seeds
  default values for all new `auto_*` settings keys.
- **`claude_p.auto_schedule`** — pure-Python decision module. `decide_one`
  and `decide_batch` take dataclass inputs and return verdicts
  (`fire` | `defer` | `skip`) with human-readable reasons. The
  scheduler wires DB reads, settings, and state transitions; the
  decision function itself is trivially unit-testable without a DB.
- **Per-job cost estimates** via `queries.auto_job_cost_estimate()` —
  learned from the runs table, median-based for robustness to noisy
  concurrent-claude.ai-chat usage, falls back to cold-start values
  per-field when individual snapshots are missing.

### Changed
- **README trimmed from 466 → 204 lines.** Keeps the pitch (origin
  story, four pillars, examples, persona) and setup (install, first
  run). Moves auto scheduler deep dive, architecture, backends, job
  manifest details, and access recipes to docs/. Narrative unchanged.

### Known limitations
- **Which `window_key` tracks `claude -p` consumption is not
  empirically verified yet.** The algorithm reads `five_hour` and
  `seven_day` (constants `FIVE_HOUR_WINDOW_KEY` / `SEVEN_DAY_WINDOW_KEY`
  in `models.py`). If `claude -p` actually lands in
  `seven_day_oauth_apps` or a different key, decisions will be based
  on the wrong signal. To verify: enable the claude.ai poller, run a
  `claude -p` job, diff `list_claude_ai_windows(conn)` before/after,
  confirm which key moves. If it's different, change the two constants.
  Tests feed synthetic values and can't catch this.
- **Per-job LLM config in `job.yaml`** via a new top-level `llm:`
  block. Carries `backend`, `model`, `max_budget_usd`, `max_turns`,
  `timeout_seconds`, `system_prompt`, and a backend-native `options:`
  dict. The executor serializes the resolved block into
  `runs/<run-id>/llm_config.json` before spawning; `run_claude()`
  reads it and uses the values as defaults. Resolution order is
  **explicit kwargs → manifest `llm` → code defaults** so the same
  `main.py` works across backends when the only thing that changes is
  the yaml — the LinkedIn-moment for the backend-swap story.
- Per-backend **`Options` Pydantic schema** (`extra="forbid"`) declared
  on each `Backend` subclass. `ClaudeCLIBackend.Options` covers
  `allowed_tools`, `permission_mode`, `add_dir`, `claude_cli`. The
  registry validates `llm.options` against the selected backend's
  schema at load time (`backends.validate_llm_options`) — typos in
  the yaml fail immediately with a clear error in the dashboard.
- `backends.resolve_backend_class(name)` and `effective_backend_name(
  manifest, cfg)` helpers so the executor / `run_claude` can resolve
  per-job backend overrides without the manifest module importing
  backends.
- Tests: `tests/test_llm_injection.py` covers validator + executor's
  merged-config write + `run_claude()`'s three-source resolution.
- **Pre-commit + type checking.** `.pre-commit-config.yaml` runs
  `ruff check --fix`, `ruff format`, `pyright` (basic mode), and a
  handful of `pre-commit-hooks` staples on every commit.
  `[tool.pyright]` and `[tool.ruff.format]` added to `pyproject.toml`;
  `pre-commit` added to dev deps. Install once with
  `.venv/bin/pre-commit install`. Existing code cleaned up in the
  same pass so `pre-commit run --all-files` starts green.

### Changed
- **Manifest: `claude:` → `llm:`** with a new nested shape. Breaking
  change; no job.yaml in the repo actually used the old block (it was
  documentation that the executor never consumed). See README / docs
  for the new shape. `run_claude()`'s Python-kwarg surface is
  preserved for callers that prefer hard-coding in `main.py`.

### Removed
- **Scaffolder.** The dashboard "Scaffold" tab, the English-prompt →
  Claude-writes-a-job flow, the live SSE trace viewer, the
  `api/scaffold.py` router, the `scaffolder.md` system prompt, the
  `scaffolder_max_budget_usd` config field, and the `"scaffold"`
  value from the `Trigger` literal. Jobs are now brought in the way
  the user sees fit — drop a folder containing a `job.yaml` into
  `~/claudectl/fs/jobs/` and the registry picks it up. Migration
  `004_remove_scaffold.sql` purges any historical runs with
  `trigger = 'scaffold'` and their `run_model_usage` rows. The
  `ScaffoldInfo` model and the `ul.trace-events` CSS block
  (only used by the scaffold view) are gone.

### Added
- `claude-p dev` subcommand — same as `serve` but with uvicorn
  `--reload` watching `src/claude_p/` for `.py`/`.html`/`.css`
  changes. Templates also hot-reload via Jinja's mtime check.
- `CHANGELOG.md` (this file) and `CLAUDE.md` with project conventions for
  contributors (human and AI).
- Pluggable LLM backend surface (`claude_p.backends`). Each backend
  implements one method — `async def stream(options) -> AsyncIterator
  [BackendEvent]` — and registers under a string key. Result folding,
  sync wrappers, and ledger writes are shared across backends, so
  swapping to `codex exec` or an HTTP API is one new file plus a config
  flip. New env var `CLAUDE_P_BACKEND` (default: `claude_cli`).
- `/settings` page now has **Setup Claude** and **Setup OpenAI**
  sections. Setup OpenAI is an inert placeholder for now — scaffolding
  for a future OpenAI/Codex backend.
- `tests/test_backend_protocol.py` — a FakeBackend proves the protocol
  (any backend yielding canonical events gets result folding for free).

### Changed
- Job detail page's manifest view is now a **Parsed / Raw** segmented
  toggle. Parsed (default) shows a human-readable card — schedule
  (cron + next/last fire), runtime + entrypoint, timeout, Claude
  config, storage flags, params, env, output globs. Raw shows the
  original `job.yaml`. Pure CSS toggle, no JS.

### Changed
- `claude_runner.py` removed; its logic split between
  `backends/claude_cli.py` (CLI + stream-json parsing) and
  `backends/base.py` (folding). `ClaudeResult` → `BackendResult` (now a
  Pydantic model per the cross-module-data rule). `run_claude()`'s
  public signature is unchanged; claude-specific kwargs (`allowed_tools`,
  `permission_mode`, `add_dir`) now land in
  `RunOptions.backend_options`.
- `run_claude(on_event=...)` callback now receives canonical
  `{kind, data}` dicts, not raw stream-json. If a job inspected
  `ev["type"]` or `ev["message"]["content"]`, switch to `ev["kind"]`
  and the `ev["data"]` fields listed in `backends/base.py` docstring.
  No job in this repo used `on_event` at the time of the refactor.

### Changed
- `/settings` save now **synchronously** runs one probe against the
  claude.ai endpoint so the user sees `connected` / `probe failed`
  immediately instead of having to wait for the next poll tick.
- `cf_clearance` field removed from the settings UI — the endpoint
  works with just `sessionKey`, and the CF cookie rotates too fast to
  be worth managing.

### Changed
- Full UI design pass. New visual language: cleaner typography scale,
  proper `status` chips with colored dots, metric cards with uppercase
  labels and tabular-nums, a consistent `.meta` strip for inline
  key/value data, breadcrumbs on detail pages, subtle sticky nav.
  Stripped explanatory paragraphs from every page — labels and chips
  carry the information now. CSS is still a single self-contained
  file; no external dependencies.

### Documented
- Ledger contract for jobs that shell out to `claude -p` directly
  (rather than using `run_claude()`) — append one JSON line per call to
  `<CLAUDE_P_JOB_DIR>/runs/<CLAUDE_P_RUN_ID>/claude_calls.jsonl`.
  Fields and schema now in `CLAUDE.md`.
- `docs/filesystem.md` — WebDAV mount recipes for macOS Finder,
  Windows Explorer, `davfs2` on Linux, iOS Files app, Android.
- `docs/network.md` — LAN access, mDNS, firewall notes, Tailscale
  recipe for remote access.
- `docs/jobs.md` — end-to-end "add and run your first job" walkthrough:
  scaffolder vs manual copy, manifest reference, first-run expectations,
  `run_claude` SDK usage, ledger escape-hatch for direct `claude -p`
  calls, schedule activation, edit/delete flows, troubleshooting.
- README rewritten around real first-run flow: Mac dev mode vs Ubuntu
  install script, first-session walkthrough, Settings → Access pointer.
  Filesystem section now spells out that WebDAV is a **live mount** (one
  copy on the server, remote devices read/write it directly) to dispel
  the Dropbox-style-sync assumption.

### Added (UX)
- `/settings` page now has an **Access** card showing detected
  dashboard + WebDAV URLs for `localhost`, LAN IP, and `.local` mDNS
  hostname, with copy buttons. No more guessing your own IP.

### Removed
- Weekly-budget form on `/ledger` and the progress bar it fed. It was
  self-declared, un-enforced, and added visual noise.
- "Rate limit events" cards + `Probe now` button from `/ledger`. The
  claude.ai integration shows real utilization %; the coarser
  `rate_limit_event` signal was redundant once that's connected.
- "By model (last 7 days)" table from `/ledger`. Noise for most users;
  the underlying `run_model_usage` table still gets populated on every
  run in case we want to bring it back in a different shape.
- `/ledger/probe` endpoint (orphaned after the rate-limit cards went).
- Extra-usage credits card, `seven_day_sonnet` / `seven_day_omelette`
  cards on `/ledger`. The subscription-usage section now shows just
  the two windows everyone cares about: `five_hour` and `seven_day`.
  Poller still stores all windows in `claude_ai_usage`.
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

### Added (claude.ai scrape, experimental)
- Opt-in `/settings` page to paste `sessionKey` + `org_id` (and
  optional `cf_clearance`) cookies from claude.ai. A background
  poller hits `claude.ai/api/organizations/<org>/usage` every 5
  minutes and stores each window's utilization %.
- `/ledger` shows a "Subscription usage" section with actual %
  utilization bars for `five_hour`, `seven_day`,
  `seven_day_sonnet`, `seven_day_omelette`, and extra-usage
  credits (Euro/USD amounts) when the integration is enabled.
- Migration `003_claude_ai_usage.sql` adds the `claude_ai_usage`
  table.
- `ClaudeAiUsageWindow` and `ClaudeAiExtraUsage` models; module
  `claude_ai.py` owns the scraper, cleanly isolated so we know
  where to look when Anthropic changes the endpoint.
- Tests for the persistence logic (no real network).

### Security notes
- `sessionKey` and `cf_clearance` are stored in the `settings`
  table unencrypted — same threat model as the rest of the
  SQLite DB. Set your `~/claudectl/` to 0700 if you're paranoid.
  The dashboard masks both when reflecting them in the UI.
- This endpoint is undocumented; Anthropic can disable or change
  it without notice. The integration is labelled **experimental**
  throughout.

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
