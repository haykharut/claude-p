#!/usr/bin/env bash
#
# claude-p update — pull latest code, apply migrations, restart.
# Run from the cloned repo (or anywhere — it finds the repo from its
# own location).
#
# Usage:  ./scripts/update.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${CLAUDE_P_DATA_DIR:-$HOME/claudectl}"
VENV_DIR="${REPO_DIR}/.venv"
CLAUDE_P="${VENV_DIR}/bin/claude-p"
SERVICE_NAME="claude-p"

err()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; }
info() { printf '\033[36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }

# ── pull ───────────────────────────────────────────────────────────
pull_latest() {
  info "pulling latest code…"
  if ! git -C "${REPO_DIR}" diff --quiet 2>/dev/null; then
    err "working tree has uncommitted changes — commit or stash first"
    exit 1
  fi
  local branch
  branch="$(git -C "${REPO_DIR}" symbolic-ref --short HEAD 2>/dev/null || echo "")"
  if [[ -z "${branch}" ]]; then
    err "detached HEAD — check out a branch first"
    exit 1
  fi
  git -C "${REPO_DIR}" pull --ff-only
  ok "code updated (${branch})"
}

# ── sync venv ──────────────────────────────────────────────────────
sync_venv() {
  info "syncing venv…"
  (cd "${REPO_DIR}" && uv pip install -e . -q)
  ok "venv synced"
}

# ── migrations ─────────────────────────────────────────────────────
run_migrations() {
  info "applying migrations…"
  CLAUDE_P_DATA_DIR="${DATA_DIR}" "${CLAUDE_P}" db-init
  ok "database up to date"
}

# ── sync repo-tracked jobs ─────────────────────────────────────────
sync_jobs() {
  local jobs_src="${REPO_DIR}/jobs"
  local jobs_dst="${DATA_DIR}/fs/jobs"
  if [[ ! -d "${jobs_src}" ]]; then
    return
  fi
  info "syncing repo-tracked jobs…"
  for job_dir in "${jobs_src}"/*/; do
    [[ -f "${job_dir}/job.yaml" ]] || continue
    local slug
    slug="$(basename "${job_dir}")"
    # Copy job files, preserving workspace/ and runs/ if they exist at destination
    mkdir -p "${jobs_dst}/${slug}"
    cp "${job_dir}"job.yaml "${jobs_dst}/${slug}/"
    cp "${job_dir}"*.py "${jobs_dst}/${slug}/" 2>/dev/null || true
    cp "${job_dir}"pyproject.toml "${jobs_dst}/${slug}/" 2>/dev/null || true
  done
  ok "jobs synced"
}

# ── restart ────────────────────────────────────────────────────────
restart_service() {
  if systemctl --user is-active "${SERVICE_NAME}" >/dev/null 2>&1; then
    info "restarting service…"
    systemctl --user restart "${SERVICE_NAME}"
    ok "service restarted"
  else
    info "service not running — starting…"
    systemctl --user start "${SERVICE_NAME}"
    ok "service started"
  fi
  echo
  systemctl --user status "${SERVICE_NAME}" --no-pager || true
}

# ── main ───────────────────────────────────────────────────────────
main() {
  echo
  info "claude-p update (repo=${REPO_DIR}, data=${DATA_DIR})"
  echo
  pull_latest
  sync_venv
  run_migrations
  sync_jobs
  restart_service
  echo
  ok "done"
}

main "$@"
