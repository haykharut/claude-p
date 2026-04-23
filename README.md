<div align="center">

<img src="./assets/logo.svg" alt="claude-p" width="540">

### Stop leaving Claude tokens on the table.

claude-p is a seriously awesome mini-project that moves `claude -p (print mode)` + Python based agentic workflows to a remote server and runs them on a usage aware scheduler. It also promises to have zero impact on your local workflows. Read below to find out more.

[Overview](./docs/overview.md) · [Quick look](#quick-look) · [Install](#install) · [Docs](./docs/)

</div>

---

Claude-p is based on 3 core ideas:
  - `claude -p (print mode)` is a great way in to building agentic workflows. It allows us to use our claude code subscription without having to pay API costs.
  -  Opt-in `schedule: auto` mode, which fires workflows when your quota has headroom. It's based on your live 5-hour and 7-day utilization, time of day, and each job's historical cost. Hot window? It defers. Quiet window at 02:00? It runs.
  - Zero impact on local workflows via folder-as-workflow and [Syncthing](./docs/filesystem.md). Set up Syncthing for two-way folder sync and keep working on your laptop. The server picks up changes in seconds; job outputs land back on your desk.


## How is this different from…

|  | cron + scripts | Claude Code `/loop` | Prefect / Airflow / n8n | claude-p |
|---|---|---|---|---|
| Run arbitrary Python packages | yes | no — repeats a single prompt | yes | yes |
| Survives reboot / SSH logout | yes | no — dies with your terminal | yes | yes |
| Token-aware scheduling | no | no | no | **yes** — reads your 5h/7d utilization |
| Uses your Max subscription (no API cost) | DIY | yes | no — API keys + billing | **yes** |
| Dependency management (uv/pip) | DIY | no | yes | yes |
| Dashboard | no | no | yes (heavy) | yes (lightweight) |


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
# main.py — your code, your rules
import subprocess, json

result = subprocess.run(
    ["claude", "-p", "Summarise these job postings into a markdown digest: …",
     "--output-format", "json"],
    capture_output=True, text=True,
)
response = json.loads(result.stdout)

with open("digest.md", "w") as f:
    f.write(response["result"])
```

Drop the folder under `~/claudectl/fs/jobs/` — the daemon picks it up
in 2 seconds. Full manifest reference: [docs/jobs.md](./docs/jobs.md).

## Install

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
./scripts/bootstrap.sh
```

One command: venv, DB, password, systemd service. Then pair your laptop:

```bash
./scripts/setup-sync.sh user@your-server
```

Open <http://localhost:8080>. Full install options:
[docs/install.md](./docs/install.md).


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
