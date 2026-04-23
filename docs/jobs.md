# Adding and running your first job

This doc walks you end-to-end: from nothing → a job folder on the server
→ a successful run → a recurring schedule. If you've already gotten
`hello-world` running, skip to ["Writing your own from scratch"](#writing-your-own-from-scratch).

## Two ways to create a job

### 1. Scaffolder (recommended for your first one)

Dashboard → **Scaffold**. Type what you want in English, e.g.

> *Every morning at 9, fetch the top 10 posts from Hacker News front page,
> summarise each in one sentence, and write the result to `digest.md`.*

Pick a slug (kebab-case, matches the folder name), hit Scaffold. You'll
see Claude's trace stream live — it creates `job.yaml`, `main.py`,
`pyproject.toml` under `~/claudectl/fs/jobs/<slug>/`. Within 2s of the
scaffolder finishing, the job appears in the jobs list.

The registry watcher picks up the folder automatically. There is no
"register" button.

### 2. Copy a folder manually

Any folder dropped under `~/claudectl/fs/jobs/` that contains a valid
`job.yaml` becomes a job. Fastest way for your first attempt:

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
claude:
  allowed_tools: [Read, Write, Bash, WebFetch]
  max_budget_usd: 0.50        # per-run circuit breaker
notify:
  on_success: dashboard
  on_failure: dashboard
```

**`name` must match the folder name.** If it doesn't, the dashboard
will show the job as "broken" and tell you why.

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

result = run_claude(
    prompt="Summarise these posts into one markdown digest: ...",
    allowed_tools=["Read", "Write"],
    max_budget_usd=0.30,
)
print(result.text)
print(f"cost: ${result.cost_usd:.4f}")
```

The helper parses `stream-json`, writes `trace.jsonl`, and increments
the ledger automatically. Prefer this unless you need a flag it
doesn't expose.

Under the hood, `run_claude()` dispatches to whichever
[`Backend`](../src/claude_p/backends/) the daemon is configured for —
`claude_cli` (default), or a future `codex_cli` / HTTP API. Your job
code doesn't care: the signature and the returned result object are
the same across backends. Backend-specific kwargs (`allowed_tools`,
`permission_mode`, `add_dir`) are forwarded to the claude backend and
ignored by others.

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
- **"Scaffolder wrote something broken"** — just edit the files by
  hand. The scaffolder is a convenience, not a source of truth.
