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
    import uvicorn
    from pathlib import Path

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
    check("data_dir exists", cfg.data_dir.exists(), "run `claude-p db-init`")
    check("db file exists", cfg.db_path.exists(), "run `claude-p db-init`")
    check("jobs_dir exists", cfg.jobs_dir.exists(), "run `claude-p db-init`")

    claude_path = shutil.which(cfg.claude_cli)
    check(f"{cfg.claude_cli} CLI on PATH", claude_path is not None, "install Claude Code CLI")
    if claude_path:
        try:
            out = subprocess.run(
                [cfg.claude_cli, "--version"], capture_output=True, text=True, timeout=5
            )
            check(f"{cfg.claude_cli} --version", out.returncode == 0, out.stderr.strip())
        except Exception as e:
            check(f"{cfg.claude_cli} --version", False, str(e))

    uv_path = shutil.which(cfg.uv_cli)
    check(f"{cfg.uv_cli} CLI on PATH", uv_path is not None, "install uv: https://docs.astral.sh/uv/")

    # Try a trivial `claude -p` to verify auth works (no cost, just a ping).
    if claude_path:
        try:
            r = subprocess.run(
                [cfg.claude_cli, "-p", "--output-format", "json", "--max-turns", "1", "say 'ok'"],
                capture_output=True, text=True, timeout=60,
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

    args = p.parse_args()
    _setup_logging(args.verbose)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
