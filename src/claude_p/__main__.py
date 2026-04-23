from __future__ import annotations

import argparse
import getpass
import logging
import shutil
import subprocess
import sys

from claude_p.auth import set_dashboard_password
from claude_p.config import get_config
from claude_p.db import init_db
from claude_p.ledger import set_weekly_budget


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    cfg = get_config()
    cfg.ensure_dirs()
    init_db(cfg.db_path)
    from claude_p.api import build_app

    app = build_app(cfg)
    uvicorn.run(app, host=cfg.bind_host, port=cfg.bind_port, log_level="info")
    return 0


def cmd_dev(args: argparse.Namespace) -> int:
    """Dev mode: uvicorn with --reload watching src/. Same port as `serve`."""
    from pathlib import Path

    import uvicorn

    cfg = get_config()
    cfg.ensure_dirs()
    init_db(cfg.db_path)
    src_dir = Path(__file__).parent
    uvicorn.run(
        "claude_p.api:build_app",
        factory=True,
        host=cfg.bind_host,
        port=cfg.bind_port,
        reload=True,
        reload_dirs=[str(src_dir)],
        reload_includes=["*.py", "*.html", "*.css"],
        log_level="info",
    )
    return 0


def cmd_db_init(args: argparse.Namespace) -> int:
    cfg = get_config()
    cfg.ensure_dirs()
    init_db(cfg.db_path)
    print(f"initialized db at {cfg.db_path}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = get_config()
    ok = True

    def check(label: str, cond: bool, hint: str = "") -> None:
        nonlocal ok
        marker = "✓" if cond else "✗"
        print(f"  {marker} {label}")
        if not cond:
            ok = False
            if hint:
                print(f"      → {hint}")

    print(f"data_dir: {cfg.data_dir}")
    print(f"backend:  {cfg.backend}")
    check("data_dir exists", cfg.data_dir.exists(), "run `claude-p db-init`")
    check("db file exists", cfg.db_path.exists(), "run `claude-p db-init`")
    check("jobs_dir exists", cfg.jobs_dir.exists(), "run `claude-p db-init`")

    uv_path = shutil.which(cfg.uv_cli)
    check(f"{cfg.uv_cli} CLI on PATH", uv_path is not None, "install uv: https://docs.astral.sh/uv/")

    if cfg.backend == "claude_cli":
        claude_path = shutil.which(cfg.claude_cli)
        check(f"{cfg.claude_cli} CLI on PATH", claude_path is not None, "install Claude Code CLI")
        if claude_path:
            try:
                out = subprocess.run([cfg.claude_cli, "--version"], capture_output=True, text=True, timeout=5)
                check(f"{cfg.claude_cli} --version", out.returncode == 0, out.stderr.strip())
            except Exception as e:
                check(f"{cfg.claude_cli} --version", False, str(e))
            # Trivial `claude -p` to verify auth (no cost, just a ping).
            try:
                r = subprocess.run(
                    [cfg.claude_cli, "-p", "--output-format", "json", "--max-turns", "1", "say 'ok'"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                check(
                    "claude -p auth smoke",
                    r.returncode == 0,
                    r.stderr[:400] if r.stderr else f"exit={r.returncode}",
                )
            except subprocess.TimeoutExpired:
                check("claude -p auth smoke", False, "timed out after 60s")
            except Exception as e:
                check("claude -p auth smoke", False, str(e))
    else:
        print(f"  · backend-specific health checks for {cfg.backend!r} not implemented; skipping")

    from claude_p.auth import get_dashboard_password_hash

    pwd_set = bool(get_dashboard_password_hash(cfg.db_path)) if cfg.db_path.exists() else False
    check("dashboard password set", pwd_set, "run `claude-p set-password`")

    return 0 if ok else 1


def cmd_set_password(args: argparse.Namespace) -> int:
    cfg = get_config()
    cfg.ensure_dirs()
    init_db(cfg.db_path)
    pw = args.password or getpass.getpass("new dashboard password: ")
    if not pw:
        print("empty password; aborting", file=sys.stderr)
        return 1
    if not args.password:
        confirm = getpass.getpass("confirm: ")
        if confirm != pw:
            print("mismatch; aborting", file=sys.stderr)
            return 1
    set_dashboard_password(cfg.db_path, pw)
    print("dashboard password updated")
    return 0


def cmd_set_budget(args: argparse.Namespace) -> int:
    cfg = get_config()
    set_weekly_budget(cfg.db_path, args.amount)
    print(f"weekly budget set to ${args.amount:.2f}")
    return 0


def cmd_verify_windows(args: argparse.Namespace) -> int:
    """Empirically identify which claude.ai `/usage` window_key moves
    when `claude -p` runs. This is the signal the auto-schedule algorithm
    reads; the constants in `models.py` assume `five_hour` + `seven_day`.

    Flow: poll → snapshot → run one tiny `claude -p` call → poll → diff.
    Prints windows sorted by utilization delta, largest first. Whichever
    key moves is the one the algorithm should read.
    """
    import asyncio

    from claude_p import claude_ai, queries
    from claude_p.db import connect, get_setting
    from claude_p.models import CLAUDE_AI_ENABLED_SETTING

    cfg = get_config()

    with connect(cfg.db_path) as conn:
        if get_setting(conn, CLAUDE_AI_ENABLED_SETTING) != "1":
            print(
                "claude.ai poller is not configured. Open /settings in the\n"
                "dashboard, paste sessionKey + org_id from claude.ai, and enable\n"
                "the integration before running this command.",
                file=sys.stderr,
            )
            return 1

    def _snapshot() -> dict[str, float | None]:
        with connect(cfg.db_path) as conn:
            return {w.window_key: w.utilization for w in queries.list_claude_ai_windows(conn)}

    print("polling claude.ai usage (before)…")
    asyncio.run(claude_ai.poll_once(cfg))
    before = _snapshot()
    if not before:
        print("no usage data returned. Check /settings for a last_error.", file=sys.stderr)
        return 1
    print(f"  {len(before)} windows captured")

    claude_bin = shutil.which(cfg.claude_cli) or cfg.claude_cli
    prompt = "say 'ok' and nothing else"
    print(f"running `{cfg.claude_cli} -p` to move the needle (costs <$0.01)…")
    try:
        r = subprocess.run(
            [claude_bin, "-p", "--output-format", "json", "--max-turns", "1", prompt],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("claude -p timed out after 120s", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print(f"claude -p failed (exit {r.returncode}): {r.stderr[:300]}", file=sys.stderr)
        return 1
    print("  done")

    # Claude.ai's /usage endpoint is eventually-consistent; give it a moment.
    print("waiting 8s for claude.ai /usage to catch up…")
    import time

    time.sleep(8)
    print("polling claude.ai usage (after)…")
    asyncio.run(claude_ai.poll_once(cfg))
    after = _snapshot()

    # Diff every window we saw in either snapshot.
    all_keys = sorted(set(before) | set(after))
    deltas: list[tuple[str, float | None, float | None, float | None]] = []
    for k in all_keys:
        b = before.get(k)
        a = after.get(k)
        if a is None or b is None:
            delta = None
        else:
            delta = a - b
        deltas.append((k, b, a, delta))

    # Sort: real moves (positive delta) first, largest first; then no-change; then missing.
    def _sort_key(row):
        _k, _b, _a, d = row
        if d is None:
            return (2, 0.0)
        if d > 0:
            return (0, -d)  # largest positive first
        return (1, -d)

    deltas.sort(key=_sort_key)

    print("\nUtilization delta (after − before):")
    print(f"  {'window_key':30s}  {'before':>8s}  {'after':>8s}  {'delta':>8s}")
    for k, b, a, d in deltas:
        b_s = f"{b:.2f}%" if b is not None else "—"
        a_s = f"{a:.2f}%" if a is not None else "—"
        d_s = f"{d:+.2f}%" if d is not None else "—"
        marker = " ←" if d is not None and d > 0.01 else ""
        print(f"  {k:30s}  {b_s:>8s}  {a_s:>8s}  {d_s:>8s}{marker}")

    movers = [k for k, _b, _a, d in deltas if d is not None and d > 0.01]
    print()
    if not movers:
        print(
            "No window moved measurably. This can happen if: (a) your 5h window\n"
            "was already full, (b) the /usage endpoint hadn't yet ingested this\n"
            "run (retry in a minute), or (c) `claude -p` is tracked by a window\n"
            "not in the response payload. Inspect the raw JSON by running:\n"
            "  sqlite3 ~/claudectl/claude-p.db 'SELECT window_key, raw_json FROM claude_ai_usage'"
        )
        return 2

    print(f"Windows that moved: {', '.join(movers)}")
    print()
    print(
        "The auto-schedule algorithm reads `FIVE_HOUR_WINDOW_KEY` and\n"
        "`SEVEN_DAY_WINDOW_KEY` in src/claude_p/models.py. If the keys above\n"
        "don't match those constants (currently 'five_hour' and 'seven_day'),\n"
        "update them so the scheduler uses the correct signal."
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="claude-p", description="Home server for Claude Code agent jobs.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="Start the dashboard + scheduler daemon")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("dev", help="Like `serve` but with uvicorn --reload")
    sp.set_defaults(func=cmd_dev)

    sp = sub.add_parser("db-init", help="Initialize the SQLite DB and filesystem layout")
    sp.set_defaults(func=cmd_db_init)

    sp = sub.add_parser("doctor", help="Run health checks")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("set-password", help="Set the dashboard password")
    sp.add_argument("--password", help="password on CLI (omit to prompt)")
    sp.set_defaults(func=cmd_set_password)

    sp = sub.add_parser("set-budget", help="Set the weekly USD budget")
    sp.add_argument("amount", type=float)
    sp.set_defaults(func=cmd_set_budget)

    sp = sub.add_parser(
        "verify-windows",
        help="Run one `claude -p` and print which claude.ai window_keys moved.",
    )
    sp.set_defaults(func=cmd_verify_windows)

    args = p.parse_args()
    _setup_logging(args.verbose)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
