#!/usr/bin/env bash
#
# claude-p bootstrap — personal-server setup.
# Run from the cloned repo. Sets up venv, DB, password, systemd user
# service, and loginctl linger. One command, good to go.
#
# Usage:  ./scripts/bootstrap.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${CLAUDE_P_DATA_DIR:-$HOME/claudectl}"
VENV_DIR="${REPO_DIR}/.venv"
CLAUDE_P="${VENV_DIR}/bin/claude-p"
SERVICE_NAME="claude-p"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/${SERVICE_NAME}.service"

err()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; }
info() { printf '\033[36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }

# ── prereqs ────────────────────────────────────────────────────────
check_prereqs() {
  local missing=0

  if ! command -v uv >/dev/null 2>&1; then
    err "uv not found. Install: https://docs.astral.sh/uv/"
    missing=1
  fi

  if ! command -v claude >/dev/null 2>&1; then
    err "claude CLI not found. Install: https://claude.com/claude-code"
    missing=1
  fi

  if [[ $missing -eq 1 ]]; then
    exit 1
  fi

  if [[ ! -d "${HOME}/.claude" ]]; then
    err "claude not logged in. Run 'claude login' first."
    exit 1
  fi

  ok "prerequisites found (uv, claude)"
}

# ── venv + install ─────────────────────────────────────────────────
setup_venv() {
  info "setting up venv at ${VENV_DIR}…"
  (cd "${REPO_DIR}" && uv venv --python 3.12 -q && uv pip install -e . -q)
  ok "venv ready"
}

# ── db-init ────────────────────────────────────────────────────────
init_database() {
  info "initializing database…"
  CLAUDE_P_DATA_DIR="${DATA_DIR}" "${CLAUDE_P}" db-init
  ok "database initialized at ${DATA_DIR}/claude-p.db"
}

# ── password ───────────────────────────────────────────────────────
set_password() {
  local db_path="${DATA_DIR}/claude-p.db"
  # Skip if password is already set.
  local has_pw
  has_pw=$(sqlite3 "${db_path}" "SELECT COUNT(*) FROM settings WHERE key='dashboard_password_hash' AND value != ''" 2>/dev/null || echo "0")
  if [[ "${has_pw}" -ge 1 ]]; then
    ok "dashboard password already set (skipping)"
    return
  fi
  info "set your dashboard password:"
  CLAUDE_P_DATA_DIR="${DATA_DIR}" "${CLAUDE_P}" set-password
}

# ── systemd user service ──────────────────────────────────────────
install_service() {
  info "installing systemd user service…"
  mkdir -p "${SERVICE_DIR}"
  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=claude-p — home server for Claude Code agent jobs
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=CLAUDE_P_DATA_DIR=${DATA_DIR}
Environment=PATH=${HOME}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=${CLAUDE_P} serve
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  ok "service file written to ${SERVICE_FILE}"
}

start_service() {
  systemctl --user enable --now "${SERVICE_NAME}" 2>/dev/null
  ok "service enabled and started"
}

enable_linger() {
  if loginctl show-user "${USER}" 2>/dev/null | grep -q "Linger=yes"; then
    ok "loginctl linger already enabled"
    return
  fi
  info "enabling loginctl linger (needs sudo)…"
  sudo loginctl enable-linger "${USER}"
  ok "linger enabled — service survives SSH logout"
}

# ── doctor ─────────────────────────────────────────────────────────
run_doctor() {
  info "running health checks…"
  echo
  CLAUDE_P_DATA_DIR="${DATA_DIR}" "${CLAUDE_P}" doctor || true
  echo
}

# ── summary ────────────────────────────────────────────────────────
summary() {
  local host
  host="$(hostname -I 2>/dev/null | awk '{print $1}')"
  host="${host:-$(hostname)}"
  cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  claude-p is running.

  Dashboard:  http://${host}:8080
  WebDAV:     http://${host}:8080/fs
  Username:   admin

  Logs:       journalctl --user -u ${SERVICE_NAME} -f
  Status:     systemctl --user status ${SERVICE_NAME}
  Restart:    systemctl --user restart ${SERVICE_NAME}
  Update:     ./scripts/update.sh

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EOF
}

# ── main ───────────────────────────────────────────────────────────
main() {
  echo
  info "claude-p bootstrap (repo=${REPO_DIR}, data=${DATA_DIR})"
  echo
  check_prereqs
  setup_venv
  init_database
  set_password
  install_service
  enable_linger
  start_service
  run_doctor
  summary
}

main "$@"
