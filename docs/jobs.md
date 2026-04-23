# Adding and running your first job

This doc walks you end-to-end: from nothing → a job folder on the server
→ a successful run → a recurring schedule. If you've already gotten
`hello-world` running, skip to ["Writing your own from scratch"](#writing-your-own-from-scratch).

## Creating a job

Drop any folder containing a valid `job.yaml` under `~/claudectl/fs/jobs/`.
The registry watcher picks it up within 2 seconds — there is no "register"
button. Fastest way for your first attempt:

```bash
# on the server (or via WebDAV from your Mac — same effect)
cp -r ~/claude-p/jobs-example/hello-world ~/claudectl/fs/jobs/
```

The dashboard reflects the new job within ~2 seconds.

## Anatomy of a job folder

```
~/claudectl/fs/jobs/<slug>/
├── job.yaml           # the manifest — this file IS the registration
├── pyproject.toml     # uv-managed deps (only for runtime: uv)
├── main.py            # your entrypoint
├── workspace/         # persistent cwd across runs. Your job's home dir.
├── .venv/             # auto-created by `uv sync` on first run. Ignore.
└── runs/<run-id>/     # one folder per run, frozen after completion
    ├── stdout.log
    ├── stderr.log
    ├── trace.jsonl    # parsed claude -p stream-json events
    ├── result.json    # run summary (cost, exit code, duration)
    └── output/        # copies of files matching `output_globs`
```

Key distinction: `workspace/` persists across runs (use it for caches,
state, SQLite files your job maintains). `runs/<id>/` is frozen per
run — that's what the dashboard links to.

## The manifest (`job.yaml`)

Minimum viable:

```yaml
name: my-first-job
description: one-line summary (shown on jobs list)
runtime: uv
entrypoint: main.py
```

With the common knobs:

```yaml
name: my-first-job
description: one-line summary
runtime: uv                   # uv | shell
entrypoint: main.py
schedule: "0 9 * * *"         # cron; omit → on-demand only
timeout: 15m                  # wall-clock, SIGTERM on overrun
params:
  keywords: { type: list, default: [python, infra] }
env: [SMTP_PASSWORD]          # pulled from the server's secret store
output_globs: ["digest.md", "*.csv"]   # copied into runs/<id>/output/
llm:
  backend: claude_cli         # optional; defaults to CLAUDE_P_BACKEND
  max_budget_usd: 0.50        # per-run circuit breaker
  max_turns: null             # optional cap on agent turns
  timeout_seconds: null       # optional wall-clock cap per run_claude() call
  system_prompt: null         # optional; prepended as system prompt
  options:                    # backend-specific flags — validated against
    allowed_tools: [Read, Write, Bash, WebFetch]   # the selected backend's
    permission_mode: dontAsk                        # Options schema at load
    add_dir: []                                     # time (typos fail fast)
notify:
  on_success: dashboard
  on_failure: dashboard
```

**`name` must match the folder name.** If it doesn't, the dashboard
will show the job as "broken" and tell you why.

### The `llm:` block in detail

- `backend:` selects which LLM runs this job (default: the daemon's
  `CLAUDE_P_BACKEND`, which ships as `claude_cli`). Change this field
  to point at a different backend — same `main.py`, different engine.
- Top-level fields (`model`, `max_budget_usd`, `max_turns`,
  `timeout_seconds`, `system_prompt`) are shared across backends.
- `options:` is the escape hatch for backend-native flags. For the
  claude backend that's `allowed_tools`, `permission_mode`, `add_dir`.
  For a future Codex backend it would be its own set. Each backend
  declares a Pydantic schema with `extra="forbid"` — misspell a key
  and the job loads as "broken" in the dashboard before anyone runs it.

**Resolution order at call time:**

    explicit run_claude() kwargs  >  manifest llm block  >  code defaults

The `llm` block is *defaults*. Anything passed explicitly in `main.py`
wins. Anything absent falls through to the file, then to code
defaults. You can drop the block entirely and keep hard-coding in
`main.py` — nothing breaks.

Params become env vars inside the job: `CLAUDE_P_PARAM_KEYWORDS`
(JSON-encoded), `CLAUDE_P_PARAM_MAX_POSTINGS`, etc.

`CLAUDE_P_JOB_DIR` and `CLAUDE_P_RUN_ID` are always set.

## First run: what to expect

1. Dashboard → click your job → **Run now**.
2. Watch the run detail page. On first run, the uv runtime spends
   ~10–30s doing `uv sync` before your code runs. Subsequent runs skip
   this unless `pyproject.toml` changed.
3. When the run ends you'll see:
   - Exit code (0 = success)
   - Duration
   - `stdout.log` and `stderr.log` (collapsed by default, click to expand)
   - Trace viewer, if the job called `claude -p`
   - Output files (whatever matched `output_globs`)
   - Cost, in USD

If the run fails: check `stderr.log` first. The most common first-run
failures are:

- **Missing dep** — add it to `pyproject.toml`, hit Run now again (uv
  will pick up the change and re-sync).
- **`claude` command not found** — the daemon user hasn't been
  authenticated with `claude login`. Re-run the install step.
- **Bad cron** — `schedules` page on dashboard will flag invalid cron
  expressions. Fix `job.yaml`, save, watcher reloads.

## Calling Claude from your job

### The easy path: `run_claude` (gets ledger tracking for free)

```python
from claude_p import run_claude

# Tools, budget, permission_mode come from job.yaml's `llm:` block.
result = run_claude(prompt="Summarise these posts into one markdown digest: ...")
print(result.text)
print(f"cost: ${result.cost_usd:.4f}")
```

The helper reads `runs/<run-id>/llm_config.json` (written by the
executor from the manifest `llm:` block), fills in defaults, invokes
the selected backend, and increments the ledger. Prefer this over
shelling out to `claude` yourself unless you need a flag it doesn't
expose.

You can still pass kwargs explicitly if you want to override the
manifest on a per-call basis:

```python
result = run_claude(
    prompt="A quick thing",
    max_budget_usd=0.05,      # tighter than job.yaml says
    max_turns=1,
)
```

Explicit kwargs > manifest `llm` > code defaults.

Under the hood, `run_claude()` dispatches to whichever
[`Backend`](../src/claude_p/backends/) the job's `llm.backend:` picks —
`claude_cli`, or a future `codex_cli` / HTTP API. The signature and
the returned result object are the same across backends.

### The escape hatch: shell out to `claude` directly

For flags `run_claude` doesn't cover (`--json-schema`, custom
`--system-prompt-file`, etc.) invoke `claude` yourself but write one
JSON line per call to
`<CLAUDE_P_JOB_DIR>/runs/<CLAUDE_P_RUN_ID>/claude_calls.jsonl` so the
ledger can still see it:

```python
import json, os, subprocess
from pathlib import Path

proc = subprocess.run(
    ["claude", "-p", "--output-format", "json", "--json-schema", schema, prompt],
    capture_output=True, text=True, check=True,
)
envelope = json.loads(proc.stdout)

run_id = os.environ.get("CLAUDE_P_RUN_ID")
job_dir = os.environ.get("CLAUDE_P_JOB_DIR")
if run_id and job_dir:  # only report when running under claude-p
    ledger = Path(job_dir) / "runs" / run_id / "claude_calls.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    usage = envelope.get("usage", {})
    with ledger.open("a") as f:
        f.write(json.dumps({
            "cost_usd": envelope.get("total_cost_usd", 0.0),
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
            "model_usage": envelope.get("modelUsage", {}),
        }) + "\n")
```

Full field reference: [CLAUDE.md → Ledger contract](../CLAUDE.md).

**Never pass `--bare` to `claude -p`** — it strips OAuth auth and
silently breaks everything.

## Turning on the schedule

Add (or edit) `schedule:` in `job.yaml`:

```yaml
schedule: "0 9 * * *"    # 09:00 every day, server local time
```

Save the file. Within 2s the dashboard reflects the next-fire time.
The scheduler polls every 10s; a job fires when `now >= next_fire_at`
and no other run of the same job is currently in progress.

Cron syntax: standard 5-field (`min hour dom mon dow`). If you want
"every 5 minutes for debugging," use `*/5 * * * *`. The dashboard
shows the next-fire in human-readable form below the cron string.

To pause without deleting: **Disable** button on the job detail page.
The schedule stays in `job.yaml` but the scheduler skips the job until
you re-enable.

### Auto schedule: fill unused quota, not wall-clock slots

Cron is the right answer when you need a run at a specific time. For
batch-y jobs that just need to run "roughly once a day" and you'd
rather they use the cheap hours — quiet time when your 5-hour Claude
window isn't under load — use **`schedule: auto`**:

```yaml
schedule: auto
auto:
  every: 1d              # cadence target. 1h / 6h / 1d / 1w are common.
  deadline: 2d           # optional. force-fire if we've deferred this long.
                         #           default: 2 × every.
  priority: low          # optional. 'low' uses stricter quota thresholds
                         #           → more likely to defer to nighttime.
                         #           default: 'normal'.
```

On each 10-second tick the scheduler asks: *is this job due? and if so,
would firing it right now push any quota over the line?* If yes, defer;
if no, fire. Inputs the algorithm looks at:

1. **Current 5-hour window utilization** (from the claude.ai poller —
   optional; see `/settings`). Day/night thresholds differ so nighttime
   is permissive and daytime is strict.
2. **Weekly window utilization** — a hard-ish cap; firing won't happen
   if it would push the 7-day window over `auto_weekly_skip_above`.
3. **Weekly USD spend vs. your `weekly_budget_usd`** — the job's own
   historical cost (avg of its last 10 runs) is added before comparing,
   so we don't fire runs we know will bust the budget.
4. **Time of day** in the configured local timezone.
5. **Deadline.** If we've been deferring a cadence period longer than
   `deadline`, we fire regardless. Forward progress beats perfect
   timing.

**Cold start.** New auto jobs (fewer than 3 runs) use conservative
global defaults for cost and utilization impact until they've run
enough times to learn their real footprint. Thereafter, the scheduler
uses the median observed 5h / 7d utilization delta of the last ten
runs of this specific job.

**No claude.ai cookie? Still works.** Without the poller, the
algorithm falls back to ledger-only signals: weekly USD vs. budget,
time-of-day, and cadence. Less precise, but opt-in precision, not a
hard requirement.

**Globals.** All threshold knobs live in `/settings` as `auto_*` keys
(`auto_5h_util_day_normal`, `auto_weekly_skip_above`,
`auto_safety_factor`, …). Defaults are sensible. Tune them once per
server, not once per job.

**Example flow.** A job with `every: 1d, priority: low` on a weekday:
- 10:00 local, 5h utilization 55%. Daytime threshold for low is 30% →
  defer. Dashboard shows "deferred Xs ago."
- 23:30 local, 5h utilization 12%. Nighttime low threshold is 70% →
  fire. Dashboard logs the reason and spawns the run.

## Editing and re-running

Just edit the files. The registry watcher picks up changes to
`job.yaml` within 2s. For code changes, the next run naturally uses
the latest version — nothing to reload.

If you edit `pyproject.toml`, the next run triggers a `uv sync` before
executing. Expect +10–20s on that run only.

## Deleting a job

`rm -rf ~/claudectl/fs/jobs/<slug>/` (or drag it to Trash over WebDAV).
The watcher removes the job from the dashboard within 2s. The SQLite
run history **is preserved** — deleted jobs just no longer appear in
the list. If you recreate a job with the same slug, old runs reattach.

## Writing your own from scratch

Skeleton:

```bash
mkdir -p ~/claudectl/fs/jobs/my-job/workspace
cd ~/claudectl/fs/jobs/my-job
cat > pyproject.toml <<'EOF'
[project]
name = "my-job"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["claude-p"]
EOF
cat > job.yaml <<'EOF'
name: my-job
description: what it does in one line
runtime: uv
entrypoint: main.py
claude:
  allowed_tools: [Read, Write]
  max_budget_usd: 0.25
EOF
cat > main.py <<'EOF'
from claude_p import run_claude

result = run_claude(
    prompt="Write a haiku about subprocesses.",
    allowed_tools=["Read", "Write"],
    max_budget_usd=0.10,
)
print(result.text)
EOF
```

Dashboard → Run now → see the haiku in `stdout.log`. Add a `schedule:`
when you're happy with it.

## Troubleshooting

- **"Job stuck in 'running'"** — happens if the daemon was killed
  mid-run. Dashboard shows a "force-fail stale run" button on the run
  detail page after 2× the job's timeout has elapsed.
- **"Run now button greyed out"** — another run of the same job is in
  progress. Jobs don't run concurrently with themselves.
- **"Cost is $0 but I called `claude -p`"** — you shelled out directly
  without writing to `claude_calls.jsonl`. See the escape-hatch section
  above.
