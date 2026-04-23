<div align="center">

# claude-p

### Stop leaving Claude tokens on the table.

You built agentic workflows with Claude code + Python scripts that call
`claude -p` to scan job boards, summarize Reddit threads, review PRs.
They work great, but only when your laptop is open. claude-p moves
them to a home server so they run on schedule, using the subscription
tokens you're already paying for, while you sleep.

[Why](#why-this-exists) · [What people build](#what-people-build-with-it) · [Quick look](#quick-look) · [Install](#install) · [Docs](./docs/)

</div>

---
## Why this exists

Claude code already supports looping, meaning running a certain task on a schedule. Think of claude-p as looping on stereoids. It can execute any arbitrary python package and supports auto scheduling, where it will take a look at your token usage to understand if something should run. Drop a folder with a `main.py`, tag it `schedule: auto`, and the scheduler fires it when your quota has headroom — based on your live 5-hour and 7-day utilization, time of day, and each job's historical cost. Hot window? It defers. Quiet window at 02:00? It runs.

On top of that, we made a decent effort to have literally 0 impact on your workflow. Once you setup claude-p the server and enable 2-way folder syncing with syncthing, you can go back to your familiar environment and keep working. The server will pick up any changes you make automatically. Anything job runs produce will arrive in your computer.


3 things make it tick:

1. **Auto scheduling that treats your subscription like off-peak
   electricity** — fire when quota is cheap, defer when it's tight,
   skip when the weekly cap is blown.
   [How it works →](./docs/jobs.md#auto-schedule-fill-unused-quota-not-wall-clock-slots)
2. **Folder-as-job.** A job is a directory with a `main.py` and a
   `job.yaml`. No DAG engine, no proprietary step format. Your code,
   your rules.
3. **No workflow change.** Syncthing keeps your laptop as the place you
   write and test code. Edits sync to the server in seconds; job outputs
   sync back. The server is invisible.

> Drop a folder → schedule: auto → claude-p burns your idle quota on
> your behalf. Develop on your laptop, outputs land back on your desk.
> Every token is cost-tracked.

## What people build with it

The first two jobs I built were a **morning job scout** (hit 20 ATS
endpoints, score fits against my resume, drop a shortlist by 07:00) and
a **Reddit digest** (summarize the 50 posts I'd actually open from my
subreddit list). Each is ~50 lines of Python in a `main.py`. They ran
overnight while I slept, using quota I'd otherwise waste.

Here's what else people are building:

- **PR second opinion** — fetch the diff, review for bugs, post comments.
- **Friday retro** — `git log` across repos, summarize, email yourself.
- **Home-lab ops** — SMART stats, package updates, analytics in one daily page.

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

If you need RBAC, multi-tenant isolation, or a proper workflow DAG
engine, this is not it. Rather, this is a modelling of a very specific, personalized use-case.

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

### Mac (dev / testing)

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
uv venv --python 3.12 && uv pip install -e '.[dev]'
.venv/bin/claude-p set-password
.venv/bin/claude-p dev
```

Open <http://localhost:8080>, username `admin`, password = what you
just set.

### Ubuntu home server (production)

**Option A: full installer** (dedicated system user, shared machines):

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
sudo ./scripts/install.sh
```

**Option B: bootstrap** (your own user, one command, personal box):

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
./scripts/bootstrap.sh
```

Sets up the venv, DB, password, systemd service, and linger. One
command, good to go. Update after pushing new code:

```bash
./scripts/update.sh    # pull, migrate, restart
```

## First run

1. Open the dashboard. **Settings → Access** shows your URLs.
2. Copy an example job:
   ```bash
   cp -r ~/claude-p/jobs-example/hello-world ~/claudectl/fs/jobs/
   ```
3. Click **Run now**. Output appears under `/runs/…`.
4. The **Ledger** tab shows cost across rolling windows.

Full walkthrough: [docs/jobs.md](./docs/jobs.md).

## Philosophy

Home-server first. LAN-only by default. Single-user. SQLite. One
Python binary. 4 GB of RAM. Boring, debuggable, yours.

## Docs

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
