<div align="center">

# claude-p

### Stop leaving Claude tokens on the table.

You built agentic workflows with Claude Code + Python scripts that call
`claude -p` to scan job boards, summarize Reddit threads, review PRs.
They work great, but only when your laptop is open. claude-p moves
them to a home server so they run on schedule, using the subscription
tokens you're already paying for, while you sleep.

[Overview](./docs/overview.md) · [Quick look](#quick-look) · [Install](#install) · [Docs](./docs/)

</div>

---

## How is this different from…

|  | cron + scripts | Claude Code `/loop` | Prefect / Airflow / n8n | claude-p |
|---|---|---|---|---|
| Run arbitrary Python packages | yes | no — repeats a single prompt | yes | yes |
| Survives reboot / SSH logout | yes | no — dies with your terminal | yes | yes |
| Token-aware scheduling | no | no | no | **yes** — reads your 5h/7d utilization |
| Uses your Max subscription (no API cost) | DIY | yes | no — API keys + billing | **yes** |
| Cost ledger per job/run | no | no | partial | **yes** — tokens, USD, rolling windows |
| Dependency management (uv/pip) | DIY | no | yes | yes |
| Dashboard | no | no | yes (heavy) | yes (lightweight) |
| Setup complexity | zero | zero | high (Docker, DB, workers) | low (one script) |
| Designed for | anything | prompt repetition | team data pipelines | **Max subscribers running agentic batch work on idle hardware** |

**The short version:** if your workload is "repeat a prompt every N
minutes," `/loop` is fine. If your workload is "run a Python project
that calls Claude, hits APIs, writes files, and does it on a schedule
that won't eat my active session" — that's what claude-p is for.

## Quick look

A job is a folder with a `job.yaml` and a `main.py`:

```yaml
# job.yaml
name: my-job
description: one-line what it does
runtime: uv
entrypoint: main.py
schedule: auto
auto:
  every: 1d
  priority: low
timeout: 30m
llm:
  max_budget_usd: 1.50
  options:
    allowed_tools: [Read, Write, Bash, WebFetch]
    permission_mode: dontAsk
```

```python
# main.py
from claude_p import run_claude

result = run_claude(
    prompt="Summarise these job postings into a markdown digest: …",
)
print(result.text, result.cost_usd)
```

Drop the folder under `~/claudectl/fs/jobs/` — the daemon picks it up
in 2 seconds. Full manifest reference: [docs/jobs.md](./docs/jobs.md).

## Install

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
uv venv --python 3.12 && uv pip install -e '.[dev]'
.venv/bin/claude-p set-password
.venv/bin/claude-p dev
```

Open <http://localhost:8080>. For Ubuntu server setup, see
[docs/install.md](./docs/install.md).

## Philosophy

Home-server first. LAN-only by default. Single-user. SQLite. One
Python binary. 4 GB of RAM. Boring, debuggable, yours.

## Docs

- [docs/overview.md](./docs/overview.md) — why this exists, what
  people build, who it's for.
- [docs/install.md](./docs/install.md) — Mac, Ubuntu, first run.
- [docs/jobs.md](./docs/jobs.md) — manifest reference, schedules,
  auto scheduler deep dive, `run_claude` SDK.
- [docs/filesystem.md](./docs/filesystem.md) — WebDAV mounts,
  Syncthing setup for bidirectional dev sync.
- [docs/network.md](./docs/network.md) — LAN access, Tailscale,
  mDNS.
- [CLAUDE.md](./CLAUDE.md) — contributor conventions, backend
  architecture.
- [CHANGELOG.md](./CHANGELOG.md)

## Contributing

PRs welcome. See [CLAUDE.md](./CLAUDE.md) for the hard rules.

## License

MIT. See [LICENSE](./LICENSE).

claude-p is not affiliated with or endorsed by Anthropic. "Claude" is
a trademark of Anthropic, PBC.
