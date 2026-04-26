# Self-improving jobs

These jobs let claude-p maintain itself. A scout proposes ideas, you
approve the good ones, and a builder implements them as PRs.

## The loop

```
meta-scout (Sunday 06:00)
  │  analyzes codebase, creates GitHub issues labeled "scout-proposal"
  ▼
You review issues on GitHub
  │  add "approved" label to the ones you want built
  │  close the ones you don't
  ▼
meta-builder (daily 03:00)
  │  picks oldest "approved" issue
  │  implements on a branch, runs tests, opens a PR
  ▼
You review and merge the PR
```

## Labels

| Label | Who sets it | Meaning |
|-------|------------|---------|
| `scout-proposal` | scout | New idea, awaiting your review |
| `approved` | you | Green-lit for the builder |
| `in-progress` | builder | Currently being implemented |
| `builder-failed` | builder | Tests/lint failed after two attempts (PR opened as draft) |

Labels are created automatically on first run.

## How the scout thinks

The scout doesn't just brainstorm. It runs two separate Claude calls:

1. **Phase 1 — brainstorm.** Reads the full codebase, CHANGELOG, git log,
   and existing issues. Writes a strategic analysis of the project's current
   state before generating 8-15 candidate ideas. Each candidate must reference
   specific files and explain the gap it fills.

2. **Phase 2 — evaluate.** A separate Claude call scores each candidate on
   value, feasibility, and alignment (1-5 each). Rejects anything that's
   already done, out of scope, or scores below 3 on any axis. Only the top
   3-5 survivors become GitHub issues.

The two-call split prevents the evaluator from anchoring on its own enthusiasm.

## Setup

Symlink both jobs into the registry (already done if you followed install):

```bash
ln -s ~/claude-p/jobs/meta-scout ~/claudectl/fs/jobs/meta-scout
ln -s ~/claude-p/jobs/meta-builder ~/claudectl/fs/jobs/meta-builder
```

**Recommended:** enable branch protection on `main` (require PR review).
The builder has Bash access — the prompt says "don't push to main" but
branch protection is the real safety net.

## Cost

- Scout: ~$1-2 per run (two Claude calls, mostly reading)
- Builder: ~$3-6 per run (one implementation call + optional retry)
