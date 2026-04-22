#!/usr/bin/env bash
#
# claude-p installer.
# Targets Ubuntu 22.04+; friendly error otherwise. Idempotent — safe to re-run.
#
# What it does:
#   1. Installs curl/git/python3 via apt if missing
#   2. Creates system user `claudectl-runner`
#   3. Clones/updates the claude-p repo to /home/claudectl-runner/claude-p
#   4. Installs uv for that user
#   5. `uv sync` inside the repo to create the daemon's venv
#   6. Creates ~claudectl-runner/claudectl/{,fs/,fs/jobs,fs/shared,fs/inbox}
#   7. Prompts admin to run `sudo -u claudectl-runner claude login`
#   8. Generates a random dashboard password
#   9. Installs + enables the systemd service
#
# Run as: sudo ./scripts/install.sh  (or curl | sudo bash)

set -euo pipefail

RUNNER_USER="claudectl-runner"
RUNNER_HOME="/home/${RUNNER_USER}"
REPO_URL="${CLAUDE_P_REPO_URL:-https://github.com/haykharut/claude-p.git}"
INSTALL_DIR="${RUNNER_HOME}/claude-p"
DATA_DIR="${RUNNER_HOME}/claudectl"
SERVICE_NAME="claude-p"

err() { printf '\033[31m%s\033[0m\n' "$*" >&2; }
info() { printf '\033[36m▸ %s\033[0m\n' "$*"; }
ok() { printf '\033[32m✓ %s\033[0m\n' "$*"; }

require_root() {
  if [[ ${EUID} -ne 0 ]]; then
    err "run as root: sudo $0"
    exit 1
  fi
}

check_os() {
  if [[ ! -f /etc/os-release ]]; then
    err "cannot detect OS (no /etc/os-release)"
    exit 1
  fi
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) : ;;
    *) err "only Ubuntu/Debian supported in v1 (detected: ${ID:-unknown})"; exit 1 ;;
  esac
}

ensure_packages() {
  info "installing system packages (apt)…"
  apt-get update -qq
  apt-get install -y -qq curl git ca-certificates python3 python3-venv >/dev/null
  ok "packages installed"
}

ensure_user() {
  if id -u "${RUNNER_USER}" >/dev/null 2>&1; then
    ok "user ${RUNNER_USER} exists"
  else
    info "creating user ${RUNNER_USER}…"
    useradd --create-home --shell /bin/bash "${RUNNER_USER}"
    ok "user created"
  fi
  mkdir -p "${DATA_DIR}/fs/jobs" "${DATA_DIR}/fs/shared" "${DATA_DIR}/fs/inbox"
  chown -R "${RUNNER_USER}:${RUNNER_USER}" "${DATA_DIR}"
}

clone_or_update() {
  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    info "updating existing clone at ${INSTALL_DIR}…"
    sudo -u "${RUNNER_USER}" git -C "${INSTALL_DIR}" fetch --quiet
    sudo -u "${RUNNER_USER}" git -C "${INSTALL_DIR}" reset --hard origin/HEAD --quiet
  else
    info "cloning ${REPO_URL} → ${INSTALL_DIR}…"
    sudo -u "${RUNNER_USER}" git clone --quiet "${REPO_URL}" "${INSTALL_DIR}"
  fi
  chown -R "${RUNNER_USER}:${RUNNER_USER}" "${INSTALL_DIR}"
  ok "repo ready"
}

install_uv() {
  if sudo -u "${RUNNER_USER}" bash -c 'command -v uv' >/dev/null 2>&1; then
    ok "uv already installed for ${RUNNER_USER}"
  else
    info "installing uv for ${RUNNER_USER}…"
    sudo -u "${RUNNER_USER}" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
    ok "uv installed"
  fi
}

sync_venv() {
  info "syncing daemon venv…"
  sudo -u "${RUNNER_USER}" bash -lc "cd '${INSTALL_DIR}' && uv venv --python 3.12 && uv pip install -e ."
  ok "venv synced"
}

ensure_claude_login() {
  if sudo -u "${RUNNER_USER}" test -f "${RUNNER_HOME}/.claude/.credentials.json"; then
    ok "claude already logged in for ${RUNNER_USER}"
    return
  fi
  if ! command -v claude >/dev/null 2>&1; then
    err "claude CLI not found on PATH. Install Claude Code first: https://claude.com/claude-code"
    exit 1
  fi
  cat <<EOF

${YELLOW:-}Claude Code authentication required.${RESET:-}

Run this, sign in to your Claude account, then re-run install:

    sudo -u ${RUNNER_USER} claude login

EOF
  exit 1
}

generate_password() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(16))'
}

set_password_and_init_db() {
  local pw
  pw="$(generate_password)"
  info "initializing DB…"
  sudo -u "${RUNNER_USER}" "${INSTALL_DIR}/.venv/bin/claude-p" db-init
  sudo -u "${RUNNER_USER}" env CLAUDE_P_DATA_DIR="${DATA_DIR}" "${INSTALL_DIR}/.venv/bin/claude-p" \
    set-password --password "${pw}"
  DASHBOARD_PASSWORD="${pw}"
  ok "dashboard password generated"
}

install_service() {
  info "installing systemd unit…"
  install -m 0644 "${INSTALL_DIR}/systemd/${SERVICE_NAME}.service" \
    "/etc/systemd/system/${SERVICE_NAME}.service"
  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}" >/dev/null
  ok "service ${SERVICE_NAME} enabled and started"
}

summary() {
  local host
  host="$(hostname -I 2>/dev/null | awk '{print $1}')"
  host="${host:-$(hostname)}"
  cat <<EOF

${GREEN:-}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET:-}

  claude-p is running.

  Dashboard: http://${host}:8080
  WebDAV:    http://${host}:8080/fs

  Username:  admin   (any non-empty username works)
  Password:  ${DASHBOARD_PASSWORD:-(check ~/claudectl/claude-p.db via set-password)}

  Save this password somewhere safe. You can change it with:
    sudo -u ${RUNNER_USER} ${INSTALL_DIR}/.venv/bin/claude-p set-password

  Logs:
    journalctl -u ${SERVICE_NAME} -f

  Health check:
    sudo -u ${RUNNER_USER} ${INSTALL_DIR}/.venv/bin/claude-p doctor

${GREEN:-}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET:-}
EOF
}

main() {
  require_root
  check_os
  ensure_packages
  ensure_user
  clone_or_update
  install_uv
  sync_venv
  ensure_claude_login
  set_password_and_init_db
  install_service
  summary
}

main "$@"
