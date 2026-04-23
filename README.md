<div align="center">

# claude-p

### Your own fleet of AI agents, running on hardware you already own — on the Claude subscription you already pay for.

[Why](#why-this-exists) · [What you can build](#what-people-build-with-it) · [How it works](#how-it-works) · [Install](#install) · [Docs](./docs/)

</div>

---

![dashboard screenshot placeholder](https://via.placeholder.com/1200x600/0b0c0e/9aa0a6?text=claude-p+dashboard)

## Why this exists

You're already paying $20–$200/month for Claude Code. The same
`claude -p` that helps at the keyboard can run unattended on a cron —
summarizing, watching, filing, emailing, scraping, digesting — if you
give it a place to live.

**claude-p is that place.** One Python binary, a SQLite file, and a
folder you drop jobs into. Runs on 4 GB of RAM, on a Mac mini in a
closet or the Ubuntu laptop you retired last year. No cloud, no
per-agent licensing, no YAML DAGs to learn, no vendor to trust with
your data.

> Drop a folder → it becomes a scheduled agent. Describe a new one in
> English → Claude writes it for you, live. Every token is
> cost-tracked. Edit files over WebDAV from your phone.

## What people build with it

Each of these is ~50 lines of Python in a single `main.py`. The
scaffolder writes most of them for you.

- **Morning job scout** — hit 20 ATS endpoints, score fits against your
  resume, drop a shortlist in `digest.md` on your desk by 07:00.
- **Newsroom of one** — ten-minute read of the 50 posts you'd actually
  open, assembled from your RSS + subreddit list every morning.
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

- **Indie hackers and homelab folk** who want the AI-agent future
  without renting a Kubernetes cluster to get there.
- **Engineers tired of paying Zapier / n8n** for what Claude can already
  do, if only something would babysit it.
- **People on a $200 Claude Max plan** who want more return on that
  spend than "fancy autocomplete while I'm at the keyboard."
- **Builders of one-person tools** who don't want Docker, Kubernetes,
  or Argo in the footnotes of their weekend project.

If you need RBAC, multi-tenant isolation, or a proper workflow DAG
engine, [Windmill](https://windmill.dev), [Kestra](https://kestra.io),
or [Prefect](https://prefect.io) will serve you better.

## What you get

- **Folder-as-registry.** Drop a directory containing `job.yaml`
  under `~/claudectl/fs/jobs/` — it becomes a job within 2 seconds.
  Delete the folder, it's gone. There is no "register" button.
- **A scaffolder.** Describe a job in English on the dashboard.
  Claude reads, writes, runs — live, in your browser — producing the
  whole folder with `job.yaml`, deps, entrypoint, ready to run.
- **A cron scheduler** polling every 10 seconds. Standard 5-field
  cron. Next-fire time visible on the dashboard.
- **A token ledger.** Every `run_claude()` call is parsed for cost and
  tokens; totals roll up per-run, per-job, and across 5h / 24h / 7d
  windows. Per-model breakdown (Opus vs. Sonnet vs. Haiku) too.
- **A WebDAV mount.** Your jobs folder is a network share. Edit from
  Finder, Windows Explorer, the iOS Files app, or `davfs2`. One copy,
  server-side, no sync conflicts.
- **A dashboard.** Dark, fast, zero JS frameworks — cost windows,
  per-job rollups, run history, live SSE traces during scaffolding.
- **Backend-agnostic.** Wraps `claude -p` today. Swap to `codex exec`,
  `gemini-cli`, or a direct HTTP API by implementing one method in
  one file — everything else keeps working. See [Backends](#backends).

## How it works

1. **You drop a folder** under `~/claudectl/fs/jobs/` — via Finder
   (over WebDAV), `cp` on the server, or the scaffolder. The registry
   watcher picks it up in <2 seconds.
2. **The scheduler fires it** on its cron — or you click **Run now**
   on the dashboard.
3. **Your job calls `run_claude(...)`,** which routes through the
   configured backend (`claude -p` by default, anything else
   tomorrow). Output accumulates into a `BackendResult` with cost,
   tokens, and final text.
4. **Every token and dollar lands in the ledger.** Every run's stdout,
   stderr, and matching `output_globs` are frozen under
   `runs/<run-id>/` for later inspection from the dashboard.

```
   your folder                     claude-p daemon
 ┌────────────────┐  watcher 2s  ┌────────────────────┐   ┌──────────┐
 │ job.yaml       │ ───────────▶ │ scheduler          │   │ Backend  │
 │ main.py        │              │ scaffolder         │──▶│ claude / │
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
schedule: "0 9 * * *"       # cron; omit for on-demand only
timeout: 30m
claude:
  allowed_tools: [Read, Write, Bash, WebFetch]
  max_budget_usd: 1.50
notify:
  on_success: dashboard
  on_failure: dashboard
```

Inside the job, the SDK helper routes to whichever backend the daemon
is configured for, and token/cost flow to the ledger automatically:

```python
from claude_p import run_claude

result = run_claude(
    prompt="Summarise these job postings into a markdown digest: …",
    allowed_tools=["Read", "Write"],
    max_budget_usd=0.30,
)
print(result.text, result.cost_usd)
```

Need a flag `run_claude` doesn't expose (`--json-schema`, custom
system-prompt file)? Shell out to `claude` directly and write one JSON
line per call to `<CLAUDE_P_JOB_DIR>/runs/<CLAUDE_P_RUN_ID>/claude_calls.jsonl`.
See [CLAUDE.md](./CLAUDE.md) for the exact contract.

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

## First run (90 seconds)

1. Open the dashboard. **Settings → Access** shows the URLs to use
   from other devices and for the WebDAV mount.
2. Still on Settings, **Setup Claude** takes your `sessionKey` + org ID
   from [claude.ai/settings/usage](https://claude.ai/settings/usage) if
   you want live % utilization on the Ledger page (optional — the job
   runner works fine without it).
3. Go to **Scaffold**. Describe a job in English — e.g. *"Every
   morning at 9, fetch the top 10 Hacker News posts and summarize each
   in one sentence into `digest.md`."* Watch Claude build it live.
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
the daemon. From other devices you get a **live mount** of that folder
over WebDAV — Finder (or Explorer, or the iOS Files app) pretends it's
a local drive, but every open/save round-trips to the server. No sync,
no local copy, no conflicts.

- Mac: `⌘K` in Finder → `http://<server>:8080/fs` → admin / password.
- Windows: Map Network Drive (needs a registry tweak for HTTP Basic).
- Linux: `davfs2`.
- iOS: Files → Connect to Server.

Full per-OS recipes: [docs/filesystem.md](./docs/filesystem.md).

## Backends

`run_claude()` is a thin wrapper over a pluggable `Backend`. The
reference backend wraps `claude -p`; switching to `codex exec`,
`gemini-cli`, or a direct HTTP LLM call takes one new file plus a
`CLAUDE_P_BACKEND=…` flip. The ledger, scheduler, scaffolder, and
dashboard are unaware of which backend is behind the wheel.

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
  scaffolder vs manual, manifest reference, schedules, `run_claude`
  SDK, escape hatch for direct `claude -p` calls.
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
