"""meta-builder: picks an approved GitHub issue, implements it, opens a PR."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

from claude_p import run_claude

log = logging.getLogger(__name__)

REPO = "haykharut/claude-p"

LABELS = [
    ("scout-proposal", "0E8A16", "Proposed by meta-scout — awaiting review"),
    ("approved", "1D76DB", "Approved for meta-builder implementation"),
    ("in-progress", "FBCA04", "meta-builder is working on this"),
    ("builder-failed", "D93F0B", "meta-builder attempted but checks failed"),
]


def ensure_labels() -> None:
    for name, color, description in LABELS:
        subprocess.run(
            [
                "gh",
                "label",
                "create",
                name,
                "--color",
                color,
                "--description",
                description,
                "--force",
                "--repo",
                REPO,
            ],
            capture_output=True,
        )


def run_cmd(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd, check=check)


def find_approved_issue() -> dict | None:
    result = run_cmd(
        [
            "gh",
            "issue",
            "list",
            "--label",
            "approved",
            "--state",
            "open",
            "--limit",
            "50",
            "--sort",
            "created",
            "--json",
            "number,title,body,labels",
            "--repo",
            REPO,
        ]
    )
    issues = json.loads(result.stdout)
    for issue in issues:
        labels = {lab["name"] for lab in issue.get("labels", [])}
        if "in-progress" not in labels:
            return issue
    return None


def label_issue(number: int, *, add: list[str] | None = None, remove: list[str] | None = None) -> None:
    for label in add or []:
        run_cmd(
            ["gh", "issue", "edit", str(number), "--add-label", label, "--repo", REPO],
            check=False,
        )
    for label in remove or []:
        run_cmd(
            ["gh", "issue", "edit", str(number), "--remove-label", label, "--repo", REPO],
            check=False,
        )


def prepare_workspace(repo_dir: Path) -> None:
    if not (repo_dir / ".git").exists():
        print(f"Cloning {REPO} into {repo_dir}")
        run_cmd(["git", "clone", f"https://github.com/{REPO}.git", str(repo_dir)])

    run_cmd(["gh", "auth", "setup-git"], cwd=repo_dir, check=False)
    run_cmd(["git", "fetch", "origin"], cwd=repo_dir)
    run_cmd(["git", "checkout", "main"], cwd=repo_dir)
    run_cmd(["git", "reset", "--hard", "origin/main"], cwd=repo_dir)
    run_cmd(["git", "clean", "-fd"], cwd=repo_dir)

    print("Installing dev dependencies")
    run_cmd(["uv", "sync", "--extra", "dev"], cwd=repo_dir)


def make_branch_name(issue_number: int, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    return f"meta-builder/issue-{issue_number}-{slug}"


def build_implementation_prompt(issue_number: int, issue_title: str, issue_body: str) -> str:
    return f"""\
You are an expert Python developer implementing a feature for the claude-p
project. claude-p is a home-server job runner for Claude Code agent jobs.

## The issue to implement

**Title:** {issue_title}
**Issue #{issue_number}**

{issue_body}

## Project rules (read CLAUDE.md first — these are non-negotiable)

1. Read CLAUDE.md before writing any code. It has the project's hard rules.
2. Every commit that changes behavior must add an entry to CHANGELOG.md under
   ## [Unreleased]. Categories: Added | Changed | Deprecated | Removed |
   Fixed | Security.
3. Schema changes require a new migration file:
   src/claude_p/migrations/NNN_description.sql (next unused NNN). Update
   models.py in the same commit.
4. Cross-module data types go in models.py as Pydantic BaseModel.
5. Pydantic v2 only: ConfigDict, model_validate, model_dump.
6. Tests go in tests/test_<module>.py. Fast, no network.
7. Never pass --bare to claude -p.
8. Use logging.getLogger(__name__), not print() in library code.
9. No bare dicts in route responses.

## Implementation steps

1. Read CLAUDE.md and the relevant source files.
2. Plan your changes before writing code.
3. Implement incrementally — small, focused changes.
4. Write or update tests for every behavioral change.
5. Add a CHANGELOG.md entry under [Unreleased].
6. Run: ruff check --fix src/ tests/ && ruff format src/ tests/
7. Run: python -m pytest -q tests/
8. If lint or tests fail, fix and re-run.
9. When everything passes, commit with a clear message using conventional
   commit style (feat/fix/docs). Include "Closes #{issue_number}" in the
   commit body.

## Constraints

- Do NOT push to any branch. The entrypoint script handles pushing.
- Do NOT run destructive git operations (no force push, no rebase, no
  reset --hard).
- Do NOT modify files outside the repository working tree.
- Keep changes focused on the issue. Do not refactor unrelated code.
"""


def run_checks(repo_dir: Path) -> tuple[bool, str]:
    failures: list[str] = []

    lint = run_cmd(
        ["uv", "run", "ruff", "check", "--fix", "src/", "tests/"],
        cwd=repo_dir,
        check=False,
    )
    run_cmd(
        ["uv", "run", "ruff", "format", "src/", "tests/"],
        cwd=repo_dir,
        check=False,
    )
    if lint.returncode != 0:
        failures.append(f"ruff check failed:\n{lint.stdout}\n{lint.stderr}")

    tests = run_cmd(
        ["uv", "run", "pytest", "-q", "tests/"],
        cwd=repo_dir,
        check=False,
    )
    if tests.returncode != 0:
        failures.append(f"pytest failed:\n{tests.stdout}\n{tests.stderr}")

    if failures:
        return False, "\n\n".join(failures)
    return True, ""


def main() -> None:
    workspace = Path(os.environ.get("CLAUDE_P_WORKSPACE_DIR", "."))
    repo_dir = workspace / "repo"
    issue_number: int | None = None

    try:
        print("=== meta-builder: ensuring labels ===")
        ensure_labels()

        print("=== Finding approved issue ===")
        issue = find_approved_issue()
        if issue is None:
            print("No approved issues available. Nothing to do.")
            return

        issue_number = int(issue["number"])
        issue_title = str(issue["title"])
        issue_body = str(issue["body"])
        print(f"Picked issue #{issue_number}: {issue_title}")

        label_issue(issue_number, add=["in-progress"], remove=["approved"])

        print("=== Preparing workspace ===")
        prepare_workspace(repo_dir)

        branch = make_branch_name(issue_number, issue_title)
        run_cmd(["git", "checkout", "-b", branch], cwd=repo_dir)
        print(f"Created branch: {branch}")

        # Implementation
        print("=== Running Claude to implement ===")
        prompt = build_implementation_prompt(issue_number, issue_title, issue_body)
        result = run_claude(
            prompt=prompt,
            allowed_tools=["Read", "Write", "Edit", "Bash"],
            max_turns=60,
            max_budget_usd=5.00,
            cwd=repo_dir,
        )
        print(f"Implementation cost: ${result.cost_usd:.2f}, turns: {result.num_turns}")

        if result.is_error:
            print(f"Claude reported error: {result.text}", file=sys.stderr)

        # Verification
        print("=== Running checks ===")
        checks_passed, failure_output = run_checks(repo_dir)

        if not checks_passed:
            print("Checks failed, retrying with Claude")
            retry_prompt = (
                "The following checks failed after your implementation. "
                "Fix them and re-run the checks.\n\n" + failure_output
            )
            retry = run_claude(
                prompt=retry_prompt,
                allowed_tools=["Read", "Write", "Edit", "Bash"],
                max_turns=20,
                max_budget_usd=1.50,
                cwd=repo_dir,
            )
            print(f"Retry cost: ${retry.cost_usd:.2f}, turns: {retry.num_turns}")
            checks_passed, failure_output = run_checks(repo_dir)

        # Check if there are actual changes
        diff = run_cmd(["git", "diff", "main", "--stat"], cwd=repo_dir)
        if not diff.stdout.strip():
            print("No changes were made. Cleaning up.", file=sys.stderr)
            label_issue(issue_number, remove=["in-progress"])
            sys.exit(1)

        # Push and create PR
        print("=== Pushing and creating PR ===")
        run_cmd(["git", "push", "-u", "origin", branch], cwd=repo_dir)

        pr_body_parts = [
            f"## Summary\n\nImplementation of #{issue_number}: {issue_title}\n",
        ]
        if not checks_passed:
            pr_body_parts.append(
                "## :warning: Checks failed\n\n"
                "This PR is opened as a **draft** because lint or tests "
                "failed after two attempts.\n\n"
                f"```\n{failure_output[-3000:]}\n```\n"
            )
        pr_body_parts.append(f"\nCloses #{issue_number}\n")
        pr_body_parts.append("\n---\n_Implemented by meta-builder_")
        pr_body = "\n".join(pr_body_parts)

        pr_cmd = [
            "gh",
            "pr",
            "create",
            "--title",
            f"feat: {issue_title}",
            "--body",
            pr_body,
            "--head",
            branch,
            "--base",
            "main",
            "--repo",
            REPO,
        ]
        if not checks_passed:
            pr_cmd.append("--draft")

        pr_result = run_cmd(pr_cmd)
        print(f"PR created: {pr_result.stdout.strip()}")

        # Clean up labels
        label_issue(issue_number, remove=["in-progress"])
        if not checks_passed:
            label_issue(issue_number, add=["builder-failed"])

        # Write build log
        total_cost = result.cost_usd
        build_log = {
            "issue_number": issue_number,
            "issue_title": issue_title,
            "branch": branch,
            "checks_passed": checks_passed,
            "cost_usd": total_cost,
            "pr_url": pr_result.stdout.strip(),
        }
        (workspace / "build_log.json").write_text(json.dumps(build_log, indent=2))

        status = "ready for review" if checks_passed else "DRAFT (checks failed)"
        print(f"\n=== Done: PR {status} ===")
        print(f"Total cost: ${total_cost:.2f}")

    except Exception:
        if issue_number is not None:
            label_issue(issue_number, remove=["in-progress"])
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
