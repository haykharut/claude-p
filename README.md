# claude-p

> `claude -p` can do more than answer questions while you're at the keyboard. claude-p turns your Claude Code subscription into a fleet of small agents running on hardware you already own.

**claude-p** is a home-server job runner for Claude Code. Drop a folder onto a shared filesystem, and it becomes a scheduled agent job. Browse the dashboard from any device on your LAN, describe new jobs in English and have Claude scaffold them for you, track token usage across runs, and let your old Ubuntu laptop do the boring work.

## What it does

- **Folders are jobs.** A job is a directory on the server with a `job.yaml` manifest and some code. Drop one in via a network mount, it's registered. Delete it, it's gone.
- **`uv`-managed runtimes.** Each job has its own `pyproject.toml` and `.venv`, managed by [uv](https://docs.astral.sh/uv/). No Docker required.
- **Scheduled or on-demand.** Cron expressions in the manifest, or "Run now" from the dashboard.
- **Describe, don't write.** The dashboard has a scaffolder: type what you want, Claude writes the `job.yaml`, `main.py`, and deps.
- **Token ledger.** Every `claude -p` invocation's cost and token usage is logged. See rolling 5h / 24h / 7d burn vs. your self-declared weekly budget.
- **WebDAV built in.** The server's filesystem is accessible from Finder (`cmd-K → http://home:8080/fs`) or Explorer. Edit job code, review outputs, drop files into workspaces — all from your laptop.

## Quickstart

On the Ubuntu host:

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
./scripts/install.sh
```

The installer creates a dedicated system user, prompts for `claude login`, initializes the DB, and starts the daemon. Then from any device on the LAN:

```
http://<your-server>:8080
```

Log in with the password the installer printed, and either:
- Copy `jobs-example/hello-world/` into `~claudectl-runner/claudectl/fs/jobs/` over WebDAV, or
- Click **Scaffold new job** and describe what you want.

## Job manifest (`job.yaml`)

```yaml
name: scan-job-boards
description: daily scan, markdown digest
runtime: uv
entrypoint: main.py
schedule: "0 9 * * *"
timeout: 10m
params:
  keywords: { type: list, default: [python, infra] }
env: [SMTP_PASSWORD]
workspace: true
output_globs: ["digest.md"]
claude:
  allowed_tools: [Read, Write, Bash, WebFetch]
  permission_mode: dontAsk
  max_budget_usd: 1.50
notify:
  on_success: dashboard
  on_failure: dashboard
```

## Philosophy

Home-server-first. LAN-only. Single-user. SQLite. One Python binary. Runs on 4 GB of RAM and a slow disk. If you need Kubernetes or a workflow DAG engine, this isn't that tool — look at [Windmill](https://windmill.dev), [Kestra](https://kestra.io), or [Prefect](https://prefect.io).

## Trademark

claude-p is an independent open-source project. It is not affiliated with, endorsed by, or sponsored by Anthropic. "Claude" is a trademark of Anthropic, PBC.

## Contributing

See [CLAUDE.md](./CLAUDE.md) for conventions (schema changes, Pydantic rules, the
"never `--bare`" rule) and [CHANGELOG.md](./CHANGELOG.md) for the history of
changes. Every code change should add a line under `[Unreleased]`.

## License

MIT. See [LICENSE](./LICENSE).
