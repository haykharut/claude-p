# claude-p

> `claude -p` can do more than answer questions while you're at the keyboard. claude-p turns your Claude Code subscription into a fleet of small agents running on hardware you already own.

**claude-p** is a home-server job runner for Claude Code. Drop a folder
on a shared filesystem, and it becomes a scheduled agent job. Browse
the dashboard from any device on your LAN, describe new jobs in English
and have Claude scaffold them for you, track token usage across runs,
and let your old Ubuntu laptop do the boring work.

![dashboard screenshot placeholder](https://via.placeholder.com/900x500/0b0c0e/9aa0a6?text=claude-p+dashboard)

## Install

### On your Mac (development / testing)

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
uv venv --python 3.12 && uv pip install -e '.[dev]'
.venv/bin/claude-p set-password            # prompts you to pick one
.venv/bin/claude-p dev                     # starts the dashboard with --reload
```

Open <http://localhost:8080>, username `admin`, password = what you just set.

### On your Ubuntu home server (production)

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
sudo ./scripts/install.sh
```

The installer creates a dedicated `claudectl-runner` system user,
installs `uv` + the daemon's venv, prompts you to run
`claude login`, sets up a systemd unit, and prints the generated
dashboard password. See [`scripts/install.sh`](./scripts/install.sh)
for what it actually does — it's idempotent and safe to re-run.

## First run

1. Open the dashboard — go to **Settings** first. The **Access** card
   shows the exact URLs to use from other devices and for the WebDAV
   mount.
2. Still on Settings, find the **Setup Claude** section and paste your
   `sessionKey` + organization ID from
   [claude.ai/settings/usage](https://claude.ai/settings/usage) if you
   want real % utilization on the Ledger page (optional).
3. Go to **Scaffold**, describe a job in English, watch Claude build
   it live. Or copy `jobs-example/hello-world/` into
   `~/claudectl/fs/jobs/` from Finder. Full walkthrough in
   [docs/jobs.md](./docs/jobs.md).
4. Click **Run now** on the new job. See output under `/runs/…`.
5. The **Ledger** page shows cost over rolling windows + per-job.

## Access

### Dashboard
Any device on the same Wi-Fi, browser to `http://<server>:8080`.

### Filesystem (the important bit)

The server is the only place your files live — `~/claudectl/fs/` on
the host running the daemon. From other devices you get a **live
mount** of that folder over WebDAV: Finder (or Explorer, or the iOS
Files app) pretends it's a local drive, but every open/save is a
round-trip to the server. No sync, no local copy, no conflicts —
there's one copy and both sides edit it directly.

- Mac: `⌘K` in Finder → `http://<server>:8080/fs` → admin / your password.
- Windows: Map Network Drive (needs a registry tweak for HTTP Basic).
- Linux: `davfs2`.
- iOS: Files → Connect to Server.

Offline works if you're ON the server (`cd ~/claudectl/fs/`).
Off-LAN or server down → the mount disappears from Finder, as
expected for a network drive. If you want a local copy that syncs in
both directions (e.g. edit on a plane), layer Syncthing on top —
claude-p itself stays single-source-of-truth.

Full per-OS recipes: [docs/filesystem.md](./docs/filesystem.md).

### Remote access (outside your LAN)
Tailscale. [docs/network.md](./docs/network.md) explains why, plus
mDNS / Cloudflare Tunnel / SSH-forward alternatives.

## What a job looks like

A job is a folder. Drop it under `~/claudectl/fs/jobs/` and it's
registered within 2 seconds.

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

Inside the job, use the claude-p SDK helper for Claude calls that feed
the ledger automatically:

```python
from claude_p import run_claude

result = run_claude(
    prompt="Summarise these job postings into a markdown digest: …",
    allowed_tools=["Read", "Write"],
    max_budget_usd=0.30,
)
print(result.text, result.cost_usd)
```

If you need flags `run_claude` doesn't expose (e.g. `--json-schema`),
shell out to `claude` directly and write one JSON line per call to
`<CLAUDE_P_JOB_DIR>/runs/<CLAUDE_P_RUN_ID>/claude_calls.jsonl`. See
[CLAUDE.md](./CLAUDE.md) for the exact contract.

## Backends (swap `claude -p` for something else)

`run_claude()` is a thin wrapper over a pluggable `Backend`. The
reference backend wraps `claude -p`; a developer who wants to run
`codex exec`, `gemini-cli`, or a direct HTTP API call implements one
subclass and flips a config var.

The surface is in [`src/claude_p/backends/`](./src/claude_p/backends/):

- `base.py` — `Backend` ABC. One abstract method
  (`async stream(options) -> AsyncIterator[BackendEvent]`) plus a
  `name`. Result folding, sync wrappers, ledger writes — all shared.
- `claude_cli.py` — reference implementation. Read this file to see
  the shape.
- `__init__.py` — registry. Add a new `"codex_cli": CodexCLIBackend`
  entry here.

Then `CLAUDE_P_BACKEND=codex_cli` (or set it via `.env`) and restart
the daemon. User jobs calling `run_claude(...)` are unchanged — their
kwargs just get routed to whichever backend is selected.

## Philosophy

Home-server-first. LAN-only. Single-user. SQLite. One Python binary.
Runs on 4 GB of RAM and a slow disk. If you need Kubernetes or a
workflow DAG engine, this isn't it — look at
[Windmill](https://windmill.dev), [Kestra](https://kestra.io), or
[Prefect](https://prefect.io).

## Docs

- [docs/jobs.md](./docs/jobs.md) — add and run your first job, scaffolder vs manual, manifest, schedules, `run_claude` SDK
- [docs/filesystem.md](./docs/filesystem.md) — WebDAV mount recipes per OS
- [docs/network.md](./docs/network.md) — LAN access, mDNS, Tailscale for remote
- [CLAUDE.md](./CLAUDE.md) — conventions for writing jobs + contributing to the daemon
- [CHANGELOG.md](./CHANGELOG.md) — what changed, when

## Contributing

See [CLAUDE.md](./CLAUDE.md) for the four hard rules (never `--bare`,
new migration file per schema change, Pydantic for cross-module types,
update CHANGELOG every change).

## Trademark

claude-p is an independent open-source project. It is not affiliated
with, endorsed by, or sponsored by Anthropic. "Claude" is a trademark
of Anthropic, PBC.

## License

MIT. See [LICENSE](./LICENSE).
