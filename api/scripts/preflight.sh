#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}/backend"

echo "=== Preflight checks ==="

echo ">> Ruff lint..."
uv run ruff check app/

echo ">> Ruff format (auto-fix)..."
uv run ruff format app/

echo ">> Tests..."
uv run pytest tests/ -v --tb=short

echo "=== All checks passed ==="
