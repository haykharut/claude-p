<div align="center">

# claude-p

### Stop leaving Claude tokens on the table.

You built agentic workflows with Claude — Python scripts that call
`claude -p` to scan job boards, summarize Reddit threads, review PRs.
They work great, but only when your laptop is open. claude-p moves
them to a home server so they run on schedule, using the subscription
tokens you're already paying for, while you sleep.

[Why](#why-this-exists) · [The auto scheduler](#the-auto-scheduler) · [What people build](#what-people-build-with-it) · [Install](#install) · [Docs](./docs/)

</div>

---

![dashboard screenshot placeholder](https://via.placeholder.com/1200x600/0b0c0e/9aa0a6?text=claude-p+dashboard)

## Why this exists

If you're on Claude Max, you've probably built things with `claude -p`
by now. Python scripts that call Claude to do real work — scan job
boards, digest your RSS feeds, review PRs, triage photos. They work.
But they only run when you're at your laptop, when you remember to
trigger them.

Meanwhile, your 5-hour window refills while you sleep. Every hour
you're not at the keyboard is quota you paid for and didn't use — and
unused quota doesn't roll over.

**claude-p is a home-server job runner that puts those unused hours
to work.** Drop a folder with a `main.py`, tag it `schedule: auto`,
and the scheduler fires it when your quota has headroom — based on
your live 5-hour and 7-day utilization, time of day, and each job's
historical cost. Hot window? It defers. Quiet window at 02:00? It runs.

Four things make it tick:

1. **Auto scheduling that treats your subscription like off-peak
   electricity** — fire when quota is cheap, defer when it's tight,
   skip when the weekly cap is blown. Details below.
2. **Folder-as-job.** A job is a directory with a `main.py` and a
   `job.yaml`. `main.py` is arbitrary Python — call Claude, call
   OpenAI, hit an API, read a CSV, write a file, shell out to `ffmpeg`.
   No DAG engine, no SaaS-proprietary step format, no YAML-as-code.
   Your code, your rules. You drop the folder, it becomes a scheduled
   agent in two seconds.
3. **Lives on hardware you already own.** One Python binary, a SQLite
   file, 4 GB of RAM. Mac mini in a closet, retired Ubuntu laptop.
   No cloud, no per-agent licensing, no vendor to trust with your
   data, no webhook to churn off.
4. **No workflow change.** Syncthing keeps your laptop as the place you
   write and test code. Edits sync to the server in seconds; job outputs
   sync back. You never SSH in to deploy. The server is invisible —
   just a place where scheduled runs happen.

> Drop a folder → schedule: auto → claude-p burns your idle quota on
> your behalf. Develop on your laptop, outputs land back on your desk.
> Every token is cost-tracked.

## The auto scheduler

Cron is fine if you need a run at exactly 09:00. Most batch work
doesn't. "Run this roughly once a day, when my Claude session isn't
under load" is almost always what you actually want.

```yaml
schedule: auto
auto:
  every: 1d          # cadence target (1h / 6h / 1d / 1w)
  deadline: 2d       # optional; force-fire if deferred longer than this
  priority: low      # optional; 'low' waits harder for a cheap slot
```

On every 10-second tick the scheduler asks, for each due auto job:
*would firing this specific job right now push any quota past a
threshold?* If yes, defer. If no, fire.

It looks at:

- **Your current 5-hour window utilization** (day/night thresholds
  differ — night is permissive, day is strict).
- **Your 7-day utilization** — a hard-ish cap; firing won't happen
  if it would push the weekly window over.
- **Your weekly USD spend vs. `weekly_budget_usd`** — the job's own
  historical cost (avg of its last 10 runs) is added before comparing,
  so we don't fire runs we know would bust the budget.
- **Time of day** in your local timezone.
- **Soft deadline.** If we've been deferring a cadence period longer
  than `deadline` (default 2× cadence), fire anyway. Forward progress
  beats perfect timing.

**It learns each job's footprint.** Before/after snapshots of your
5-hour and 7-day utilization are taken at every run — the scheduler
uses the median delta of the last 10 runs of *this specific job* to
predict "if I fire it now, where does utilization end up?" Cold start
(fewer than 3 runs) falls back to conservative defaults.

**No claude.ai cookie? Still works.** Without live utilization data,
the scheduler falls back to ledger-only signals: weekly USD vs.
budget, time of day, cadence. Less precise, but opt-in precision, not
a hard requirement.

**Worked example.** A `priority: low, every: 1d` job on a weekday:

- 10:15 local, 5h utilization 58%. Daytime-low threshold is 30% →
  **defer**. Dashboard shows "deferred 14s ago."
- 23:40 local, 5h utilization 11%. Nighttime-low threshold is 70% →
  **fire.** The run starts; you wake up to the output.

Full details: [docs/jobs.md §Auto schedule](./docs/jobs.md#auto-schedule-fill-unused-quota-not-wall-clock-slots).

## What people build with it

The first two jobs I built were a **morning job scout** (hit 20 ATS
endpoints, score fits against my resume, drop a shortlist by 07:00) and
a **Reddit digest** (summarize the 50 posts I'd actually open from my
subreddit list). Each is ~50 lines of Python in a `main.py`. They ran
overnight while I slept, using quota I'd otherwise waste.

Here's what else people are building:

- **PR second opinion** — on every push to your open-source repo,
  claude-p fetches the diff, reviews for obvious bugs and typos, posts
  comments back.
- **Photo triage** — new uploads land in a shared folder; claude-p
  renames, tags by contents, sorts into `photos/YYYY/MM/`.
- **Invoice janitor** — weekly, parse incoming PDFs, extract line
  items, categorize, append to a Google Sheet.
- **Friday retro** — read the week's `git log` across N repos,
  summarize what you shipped and what stalled, email yourself.
- **Home-lab ops** — SMART stats on the NAS, `apt list --upgradable`
  on the servers, Cloudflare analytics — all distilled into one daily
  one-pager.

## Who it's for

- **Claude Max subscribers** who already pay $100–$200/mo and want to
  actually use the quota they're leaving on the table. claude-p costs
  nothing extra — it runs on hardware you own, using tokens you've
  already paid for. The only investment is an old laptop and 20 minutes
  of setup.
- **Indie hackers and homelab folk** who want the AI-agent future
  without renting a Kubernetes cluster to get there.
- **Engineers tired of paying Zapier / n8n** for what Claude can already
  do, if only something would babysit it.
- **Builders of one-person tools** who don't want Docker, Kubernetes,
  or Argo in the footnotes of their weekend project.

If you need RBAC, multi-tenant isolation, or a proper workflow DAG
engine, [Windmill](https://windmill.dev), [Kestra](https://kestra.io),
or [Prefect](https://prefect.io) will serve you better.

## What you get

- **Auto scheduler** (above). `schedule: auto` + `every: 1d` and the
  daemon decides *when* based on live quota signals.
- **Cron scheduler** for when you genuinely need 09:00 on Mondays —
  standard 5-field, polling every 10 seconds.
- **Folder-as-registry.** Drop a directory containing `job.yaml`
  under `~/claudectl/fs/jobs/` — it becomes a job within 2 seconds.
  Delete the folder, it's gone. There is no "register" button, no
  proprietary step format, no DAG to describe. `main.py` is arbitrary
  Python; do whatever you want — hit APIs, shell out to binaries,
  maintain SQLite databases in `workspace/`, scrape sites, write
  files, mix Claude with OpenAI with raw HTTP.
- **A token ledger.** Every `run_claude()` call is parsed for cost and
  tokens; totals roll up per-run, per-job, and across 5h / 24h / 7d
  windows. Per-model breakdown (Opus vs. Sonnet vs. Haiku) too. The
  auto scheduler learns from this same data.
- **A WebDAV mount.** Your jobs folder is a network share. Edit from
  Finder, Windows Explorer, the iOS Files app, or `davfs2`. One copy,
  server-side, no sync conflicts.
- **Syncthing-friendly dev loop.** Write and test jobs on your laptop;
  Syncthing mirrors them to the server in seconds. Run outputs sync
  back. Your editor, your Git workflow, your local tools — nothing
  changes. Setup: [docs/filesystem.md](./docs/filesystem.md#syncthing--bidirectional-sync-for-development).
- **A dashboard.** Dark, fast, zero JS frameworks — cost windows,
  per-job rollups, run history, auto-state ("deferred 3m ago,
  waiting for a good slot").
- **Backend-agnostic.** Wraps `claude -p` today. Swap to `codex exec`,
  `gemini-cli`, or a direct HTTP API by implementing one method in
  one file — everything else keeps working. See [Backends](#backends).

## How it works

0. **You develop on your laptop.** Syncthing (or a manual copy over
   WebDAV) puts your job folder on the server. From here, everything
   is automatic:
1. **The registry picks it up** in <2 seconds. A folder under
   `~/claudectl/fs/jobs/` with a `job.yaml` is a job.
2. **The scheduler fires it** — on its cron, or (for `schedule: auto`)
   the first tick where your live Claude utilization and weekly budget
   both have headroom. Or you click **Run now** on the dashboard.
3. **Your job calls `run_claude(...)`,** which routes through the
   configured backend (`claude -p` by default, anything else
   tomorrow). Output accumulates into a `BackendResult` with cost,
   tokens, and final text.
4. **Every token and dollar lands in the ledger.** 5h/7d utilization is
   snapshotted before and after each run, so the auto scheduler learns
   each job's real footprint over time. stdout, stderr, and matching
   `output_globs` are frozen under `runs/<run-id>/` for later
   inspection from the dashboard.

```
   your folder                     claude-p daemon
 ┌────────────────┐  watcher 2s  ┌────────────────────┐   ┌──────────┐
 │ job.yaml       │ ───────────▶ │ scheduler          │   │ Backend  │
 │ main.py        │              │ executor           │──▶│ claude / │
 │ workspace/     │ ◀──── runs/  │ ledger · dashboard │   │ codex /  │
 │ runs/<id>/     │              │ WebDAV             │   │ HTTP …   │
 └────────────────┘              └────────────────────┘   └──────────┘
```

## What a job looks like

A job is a folder. That's the whole abstraction.

```
~/claudectl/fs/jobs/my-job/
├── job.yaml          # the manifest
├── pyproject.toml    # uv-managed deps
├── main.py           # entrypoint
├── workspace/        # persistent state across runs
└── runs/<run-id>/    # stdout, stderr, output snapshot
```

```yaml
# job.yaml
name: my-job
description: one-line what it does
runtime: uv
entrypoint: main.py
schedule: auto              # let the scheduler pick a cheap slot
auto:
  every: 1d                 # run roughly once a day
  priority: low             # wait harder for quiet hours
timeout: 30m
llm:
  backend: claude_cli       # which LLM runs this job
  max_budget_usd: 1.50      # per-run circuit breaker
  options:                  # backend-specific flags, validated at load time
    allowed_tools: [Read, Write, Bash, WebFetch]
    permission_mode: dontAsk
notify:
  on_success: dashboard
  on_failure: dashboard
```

Prefer a fixed wall-clock time? Swap `schedule: auto` for a standard
5-field cron string (`"0 9 * * *"`) and drop the `auto:` block.

Inside the job, the SDK helper reads the `llm:` block for you. Nothing
backend-specific needs to be repeated in `main.py`:

```python
from claude_p import run_claude

result = run_claude(
    prompt="Summarise these job postings into a markdown digest: …",
)
print(result.text, result.cost_usd)
```

Explicit kwargs always win if you pass them. Resolution order is
**explicit kwargs → manifest `llm` block → code defaults** — the
manifest is defaults, not mandatory.

Need a flag `run_claude` doesn't expose (`--json-schema`, custom
system-prompt file)? Shell out to `claude` directly and write one JSON
line per call to `<CLAUDE_P_JOB_DIR>/runs/<CLAUDE_P_RUN_ID>/claude_calls.jsonl`.
See [CLAUDE.md](./CLAUDE.md) for the exact contract.

### One `main.py`, different LLMs per job

The `llm:` block is per-job. Same `main.py`, one yaml flip, different
engine:

```yaml
# my-job/job.yaml — running on Claude
llm:
  backend: claude_cli
  options:
    allowed_tools: [Read, Write, Bash, WebFetch]
```

```yaml
# my-job/job.yaml — same code, running on Codex / OpenAI
llm:
  backend: openai
  model: gpt-5
  options:
    tools: [shell, apply_patch]
```

`run_claude(prompt=...)` in `main.py` doesn't change. The manifest's
`llm.options` block is validated against the selected backend's schema
at load time (Pydantic `extra="forbid"`) — typos in the yaml fail
immediately with a clear error in the dashboard instead of blowing up
the first time the job runs.

## Install

### Mac (dev / testing)

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
uv venv --python 3.12 && uv pip install -e '.[dev]'
.venv/bin/claude-p set-password            # you pick one at the prompt
.venv/bin/claude-p dev                     # dashboard with auto-reload
```

Open <http://localhost:8080>, username `admin`, password = what you
just set.

### Ubuntu home server (production)

**Option A: full installer** (dedicated system user, recommended for shared machines):

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
sudo ./scripts/install.sh
```

The installer creates a dedicated `claudectl-runner` system user,
installs `uv` and the daemon's venv, prompts you to run `claude login`,
wires up a systemd unit, and prints the generated dashboard password.
See [`scripts/install.sh`](./scripts/install.sh) — it's idempotent and
safe to re-run.

**Option B: bootstrap** (run as your own user, one command, personal box):

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
./scripts/bootstrap.sh
```

That's it. The script sets up the venv, initializes the DB, prompts for
a dashboard password, installs a systemd user service, enables linger
(so it survives SSH logout), starts the daemon, and runs `doctor`.

Later, after pushing new code or migrations:

```bash
./scripts/update.sh
```

Pulls latest, re-syncs the venv, applies any new migrations, and
restarts the service.

Check status / logs:

```bash
systemctl --user status claude-p
journalctl --user -u claude-p -f
```

## First run (90 seconds)

1. Open the dashboard. **Settings → Access** shows the URLs to use
   from other devices and for the WebDAV mount.
2. Still on Settings, **Setup Claude** takes your `sessionKey` + org ID
   from [claude.ai/settings/usage](https://claude.ai/settings/usage) if
   you want live % utilization on the Ledger page (optional — the job
   runner works fine without it).
3. Copy an example job into place and run it:
   ```bash
   cp -r ~/claude-p/jobs-example/hello-world ~/claudectl/fs/jobs/
   ```
   The dashboard picks it up within 2 seconds.
4. Click **Run now**. Output appears under `/runs/…`.
5. The **Ledger** tab shows cost across rolling windows and per-job.

Full walkthrough: [docs/jobs.md](./docs/jobs.md).

## Access from other devices

### Dashboard

Any device on the same Wi-Fi, browser to `http://<server>:8080`.
Username `admin`, password = the one you set. From outside your LAN:
[Tailscale](https://tailscale.com) is the least-fuss path. See
[docs/network.md](./docs/network.md) for alternatives (mDNS, Cloudflare
Tunnel, SSH forward).

### Filesystem (the important bit)

Your files live in one place: `~/claudectl/fs/` on the host running
the daemon. Two ways to access them from other devices:

**WebDAV (live mount, no local copy):**

- Mac: `⌘K` in Finder → `http://<server>:8080/fs` → admin / password.
- Windows: Map Network Drive (needs a registry tweak for HTTP Basic).
- Linux: `davfs2`.
- iOS: Files → Connect to Server.

Good for browsing, one-off edits, and mobile access.

**Syncthing (bidirectional sync, recommended for development):**

Develop jobs locally on your laptop; changes appear on the server in
seconds. Job outputs sync back to your Mac automatically. Add `.venv`,
`__pycache__`, `*.pyc` to ignore patterns so platform-specific
artifacts don't cross.

Setup guide: [docs/filesystem.md — Syncthing](./docs/filesystem.md#syncthing--bidirectional-sync-for-development).

## Backends

`run_claude()` is a thin wrapper over a pluggable `Backend`. The
reference backend wraps `claude -p`; switching to `codex exec`,
`gemini-cli`, or a direct HTTP LLM call takes one new file plus a
`CLAUDE_P_BACKEND=…` flip. The ledger, scheduler, and dashboard are
unaware of which backend is behind the wheel.

See [`src/claude_p/backends/`](./src/claude_p/backends/) for the
`Backend` ABC and the reference implementation. Every backend
implements one method:

```python
async def stream(self, options: RunOptions) -> AsyncIterator[BackendEvent]:
    ...
```

Result folding, sync wrappers, and the ledger write are shared
automatically across all backends.

## Philosophy

Home-server first. LAN-only by default. Single-user. SQLite. One
Python binary. Runs on 4 GB of RAM and a slow disk. Boring,
debuggable, yours. No webhook to trust, no SaaS to churn off, no
config that expires when a startup pivots.

## Docs

- [docs/jobs.md](./docs/jobs.md) — add and run your first job,
  manifest reference, schedules, `run_claude` SDK, escape hatch for
  direct `claude -p` calls.
- [docs/filesystem.md](./docs/filesystem.md) — WebDAV mount recipes
  per OS.
- [docs/network.md](./docs/network.md) — LAN access, mDNS, Tailscale
  for remote.
- [CLAUDE.md](./CLAUDE.md) — conventions for writing jobs and
  contributing to the daemon.
- [CHANGELOG.md](./CHANGELOG.md) — what changed, when.

## Contributing

PRs welcome. See [CLAUDE.md](./CLAUDE.md) for the four hard rules
(never `--bare`, new migration file per schema change, Pydantic for
cross-module types, update `CHANGELOG.md` every change).

## License and trademark

MIT. See [LICENSE](./LICENSE).

claude-p is an independent open-source project. It is not affiliated
with, endorsed by, or sponsored by Anthropic. "Claude" is a trademark
of Anthropic, PBC.
