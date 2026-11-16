#!/bin/bash
# Run local pre-deployment checks for DataCommune go-live readiness.
#
# Usage:
#   ./scripts/go-live-verify.sh
#
# This script validates:
#   1) quality gates (preflight)
#   2) ops scripts syntax (bash -n)
#   3) required go-live docs presence
#
# Optional flags (env):
#   SKIP_PREFLIGHT=1   -> skip quality gates step
#   ALLOW_MISSING_UV=1 -> skip quality gates if uv is unavailable in shell
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

echo "=== Go-live local verification ==="

echo ">> Quality gates (preflight)..."
if [[ "${SKIP_PREFLIGHT:-0}" == "1" ]]; then
  echo "[WARN] SKIP_PREFLIGHT=1 -> quality gates skipped"
elif command -v uv >/dev/null 2>&1; then
  bash "${REPO_ROOT}/scripts/preflight.sh"
elif [[ "${ALLOW_MISSING_UV:-0}" == "1" ]]; then
  echo "[WARN] uv not found in current shell -> quality gates skipped (ALLOW_MISSING_UV=1)"
else
  echo "[FAIL] uv is not available in this shell."
  echo "       Run from an environment with uv in PATH, or set ALLOW_MISSING_UV=1 to skip this step."
  exit 2
fi

echo ">> Ops scripts syntax..."
bash -n "${REPO_ROOT}/scripts/backup-pg.sh"
bash -n "${REPO_ROOT}/scripts/restore-pg.sh"
bash -n "${REPO_ROOT}/scripts/deploy.sh"
bash -n "${REPO_ROOT}/scripts/phase-10-02-check.sh"

echo ">> Required docs..."
required_docs=(
  "README.md"
  "docs/go-live-checklist.md"
  "docs/getting-started.md"
)

for file in "${required_docs[@]}"; do
  if [[ ! -f "${REPO_ROOT}/${file}" ]]; then
    echo "[FAIL] Missing required doc: ${file}" >&2
    exit 1
  fi
done

echo "=== Go-live local verification passed ==="