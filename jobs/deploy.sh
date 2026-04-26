#!/usr/bin/env bash
#
# Deploy meta-scout and meta-builder jobs to the registry.
# Run from anywhere — finds paths from its own location.
#
# Usage:  ./jobs/deploy.sh

set -euo pipefail

JOBS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${CLAUDE_P_DATA_DIR:-$HOME/claudectl}"
REGISTRY="${DATA_DIR}/fs/jobs"

info() { printf '\033[36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }

for job_dir in "${JOBS_DIR}"/meta-*/; do
  [[ -f "${job_dir}/job.yaml" ]] || continue
  slug="$(basename "${job_dir}")"
  info "deploying ${slug}"
  mkdir -p "${REGISTRY}/${slug}"
  cp "${job_dir}"job.yaml "${REGISTRY}/${slug}/"
  cp "${job_dir}"*.py "${REGISTRY}/${slug}/" 2>/dev/null || true
  cp "${job_dir}"pyproject.toml "${REGISTRY}/${slug}/" 2>/dev/null || true
  ok "${slug} → ${REGISTRY}/${slug}"
done

echo
ok "done — jobs will be picked up by the registry on next scan"
