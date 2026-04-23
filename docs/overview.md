# Overview

## Why this exists

Claude Code already supports looping — running a single prompt on a
schedule. claude-p is looping on steroids. It executes arbitrary Python
packages on a schedule, with **auto scheduling** that reads your live
5-hour and 7-day token utilization to decide when to fire. Hot window?
It defers. Quiet window at 02:00? It runs.

Once you set up [Syncthing](./filesystem.md) for two-way folder sync,
claude-p has zero impact on your workflow. Edit on your laptop, the
server picks up changes in seconds, and job outputs land back on your
desk.

Three things make it tick:

1. **Auto scheduling that treats your subscription like off-peak
   electricity** — fire when quota is cheap, defer when it's tight,
   skip when the weekly cap is blown.
   [How it works →](./jobs.md#auto-schedule-fill-unused-quota-not-wall-clock-slots)
2. **Folder-as-job.** A job is a directory with a `main.py` and a
   `job.yaml`. No DAG engine, no proprietary step format.
3. **No workflow change.** Syncthing keeps your laptop as the place you
   write and test code. The server is invisible.

## What people build with it

The first two jobs I built were a **morning job scout** (hit 20 ATS
endpoints, score fits against my resume, drop a shortlist by 07:00) and
a **Reddit digest** (summarize the 50 posts I'd actually open from my
subreddit list). Each is ~50 lines of Python. They ran overnight while
I slept, using quota I'd otherwise waste.

More examples:

- **PR second opinion** — fetch the diff, review for bugs, post comments.
- **Friday retro** — `git log` across repos, summarize, email yourself.
- **Home-lab ops** — SMART stats, package updates, analytics in one daily page.

## Who it's for

- **Claude Max subscribers** ($100–$200/mo) who want to use the quota
  they're leaving on the table. claude-p costs nothing extra — it runs
  on hardware you own, using tokens you've already paid for.
- **Indie hackers and homelab folk** who want the AI-agent future
  without renting a Kubernetes cluster.
- **Engineers tired of paying Zapier / n8n** for what Claude can already
  do, if only something would babysit it.

If you need RBAC, multi-tenant isolation, or a proper workflow DAG
engine, this is not it. This models a very specific, personalized
use-case.
