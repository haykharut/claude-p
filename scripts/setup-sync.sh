#!/usr/bin/env bash
#
# claude-p sync setup — pairs local Syncthing with the server's,
# shares ~/claudectl/fs/jobs/ bidirectionally, sets ignore patterns.
# No browser tabs. One command.
#
# Usage:  ./scripts/setup-sync.sh user@server-ip
#
# Prerequisites: Syncthing running on both sides.
#   Mac:    brew install syncthing && brew services start syncthing
#   Server: sudo apt install syncthing -y && systemctl --user enable --now syncthing

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 user@server-ip"
  echo "  e.g. $0 hayk@192.168.10.19"
  exit 1
fi

SSH_TARGET="$1"
FOLDER_ID="claude-p-jobs"
LOCAL_FOLDER="${HOME}/claudectl/fs/jobs"
REMOTE_FOLDER="\${HOME}/claudectl/fs/jobs"
LOCAL_BASE="http://localhost:8384"
REMOTE_BASE="http://localhost:8384"
IGNORE_PATTERNS='{"ignore":[".venv","__pycache__","*.pyc",".ruff_cache",".DS_Store"]}'

err()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; }
info() { printf '\033[36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }

# ── helpers ────────────────────────────────────────────────────────
get_local_apikey() {
  local cfg
  if [[ "$(uname)" == "Darwin" ]]; then
    cfg="${HOME}/Library/Application Support/Syncthing/config.xml"
  else
    cfg="${HOME}/.local/state/syncthing/config.xml"
    [[ -f "$cfg" ]] || cfg="${HOME}/.config/syncthing/config.xml"
  fi
  if [[ ! -f "$cfg" ]]; then
    err "local Syncthing config not found at ${cfg}"
    err "is Syncthing installed and has it run at least once?"
    exit 1
  fi
  grep -o '<apikey>[^<]*' "$cfg" | cut -d'>' -f2
}

get_local_device_id() {
  curl -s -H "X-API-Key: $1" "${LOCAL_BASE}/rest/system/status" | python3 -c "import sys,json; print(json.load(sys.stdin)['myID'])"
}

api_local() {
  local method="$1" endpoint="$2" ; shift 2
  curl -s -X "$method" -H "X-API-Key: ${LOCAL_APIKEY}" -H "Content-Type: application/json" "${LOCAL_BASE}${endpoint}" "$@"
}

api_remote() {
  local method="$1" endpoint="$2" ; shift 2
  ssh "${SSH_TARGET}" "curl -s -X ${method} -H 'X-API-Key: ${REMOTE_APIKEY}' -H 'Content-Type: application/json' '${REMOTE_BASE}${endpoint}' $*"
}

# ── local info ─────────────────────────────────────────────────────
info "reading local Syncthing config…"
LOCAL_APIKEY="$(get_local_apikey)"
LOCAL_ID="$(get_local_device_id "$LOCAL_APIKEY")"
ok "local device ID: ${LOCAL_ID:0:7}…"

# ── remote info (over SSH) ────────────────────────────────────────
info "reading remote Syncthing config via SSH…"
REMOTE_APIKEY="$(ssh "${SSH_TARGET}" "
  cfg=\${HOME}/.local/state/syncthing/config.xml
  [ -f \"\$cfg\" ] || cfg=\${HOME}/.config/syncthing/config.xml
  grep -o '<apikey>[^<]*' \"\$cfg\" | cut -d'>' -f2
")"
REMOTE_ID="$(ssh "${SSH_TARGET}" "
  curl -s -H 'X-API-Key: ${REMOTE_APIKEY}' '${REMOTE_BASE}/rest/system/status' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"myID\"])'
")"
ok "remote device ID: ${REMOTE_ID:0:7}…"

# ── add devices to each other ─────────────────────────────────────
info "adding remote device to local Syncthing…"
api_local POST "/rest/config/devices" \
  -d "{\"deviceID\":\"${REMOTE_ID}\",\"name\":\"claude-p-server\",\"addresses\":[\"dynamic\"],\"autoAcceptFolders\":false}" >/dev/null
ok "remote device added locally"

info "adding local device to remote Syncthing…"
ssh "${SSH_TARGET}" "
  curl -s -X POST \
    -H 'X-API-Key: ${REMOTE_APIKEY}' \
    -H 'Content-Type: application/json' \
    '${REMOTE_BASE}/rest/config/devices' \
    -d '{\"deviceID\":\"${LOCAL_ID}\",\"name\":\"claude-p-dev\",\"addresses\":[\"dynamic\"],\"autoAcceptFolders\":false}' >/dev/null
"
ok "local device added on remote"

# ── create shared folder on both sides ────────────────────────────
mkdir -p "${LOCAL_FOLDER}"

info "creating shared folder on local…"
api_local POST "/rest/config/folders" \
  -d "{
    \"id\": \"${FOLDER_ID}\",
    \"label\": \"claude-p jobs\",
    \"path\": \"${LOCAL_FOLDER}\",
    \"type\": \"sendreceive\",
    \"devices\": [{\"deviceID\":\"${LOCAL_ID}\"},{\"deviceID\":\"${REMOTE_ID}\"}],
    \"fsWatcherEnabled\": true,
    \"rescanIntervalS\": 3600
  }" >/dev/null
ok "folder shared locally"

info "creating shared folder on remote…"
RESOLVED_REMOTE_FOLDER="$(ssh "${SSH_TARGET}" "echo ${REMOTE_FOLDER}")"
ssh "${SSH_TARGET}" "
  mkdir -p '${RESOLVED_REMOTE_FOLDER}'
  curl -s -X POST \
    -H 'X-API-Key: ${REMOTE_APIKEY}' \
    -H 'Content-Type: application/json' \
    '${REMOTE_BASE}/rest/config/folders' \
    -d '{
      \"id\": \"${FOLDER_ID}\",
      \"label\": \"claude-p jobs\",
      \"path\": \"${RESOLVED_REMOTE_FOLDER}\",
      \"type\": \"sendreceive\",
      \"devices\": [{\"deviceID\":\"${REMOTE_ID}\"},{\"deviceID\":\"${LOCAL_ID}\"}],
      \"fsWatcherEnabled\": true,
      \"rescanIntervalS\": 3600
    }' >/dev/null
"
ok "folder shared on remote"

# ── set ignore patterns ───────────────────────────────────────────
info "setting ignore patterns…"

# Wait a moment for Syncthing to register the folder before setting ignores
sleep 2

api_local POST "/rest/db/ignores?folder=${FOLDER_ID}" \
  -d "${IGNORE_PATTERNS}" >/dev/null

ssh "${SSH_TARGET}" "
  curl -s -X POST \
    -H 'X-API-Key: ${REMOTE_APIKEY}' \
    -H 'Content-Type: application/json' \
    '${REMOTE_BASE}/rest/db/ignores?folder=${FOLDER_ID}' \
    -d '${IGNORE_PATTERNS}' >/dev/null
"
ok "ignore patterns set (.venv, __pycache__, *.pyc, .ruff_cache)"

# ── done ──────────────────────────────────────────────────────────
cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Syncthing paired and syncing.

  Local:   ${LOCAL_FOLDER}
  Remote:  ${RESOLVED_REMOTE_FOLDER}

  Changes on either side appear on the other
  within seconds. Survives reboots on both sides.

  Status:  http://localhost:8384 (local Syncthing UI)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EOF
