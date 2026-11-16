#!/bin/bash
# Negative controls for security remediation lot L8 (findings 17-01 / 17-06).
#
# Proves that:
#   1. (17-01) phase-10-02-check.sh never persists a provided API key in its
#      report file — the key is masked before any log write;
#   2. (17-06) no script hands an API key to a command line: audit-prod.sh
#      uses a curl config file, the Makefile live targets rely on the
#      environment, and test_all_endpoints.py reads DC_API_KEY/API_KEY.
#
# Uses a FAKE key only (dc_live_TESTFAKE...) — never run with a real key.
# Exit code: number of failed controls.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAKE_KEY="dc_live_TESTFAKE_0123456789abcdef"
TMP_RUN="$(mktemp -d)"
trap 'rm -rf "${TMP_RUN}"' EXIT
FAILURES=0

fail() { echo "FAIL: $*"; FAILURES=$((FAILURES + 1)); }
pass() { echo "PASS: $*"; }

# ── Control 1 (17-01): report must not contain the provided key ─────────────

REPORT_DIR="${TMP_RUN}/reports" \
COMPOSE_DIR="${TMP_RUN}/compose" \
API_KEY="${FAKE_KEY}" \
  bash "${SCRIPT_DIR}/phase-10-02-check.sh" >/dev/null 2>&1 || true

REPORT_FILE="$(ls "${TMP_RUN}/reports"/phase-10-02_*.log 2>/dev/null | head -n 1)"
if [[ -z "${REPORT_FILE}" ]]; then
  fail "17-01: no report produced by phase-10-02-check.sh"
elif grep -q "${FAKE_KEY}" "${REPORT_FILE}"; then
  fail "17-01: fake API key found in report ${REPORT_FILE}"
else
  pass "17-01: fake API key absent from report"
fi

if [[ -n "${REPORT_FILE}" ]] \
  && grep -q 'X-API-Key: \*\*\*REDACTED\*\*\*' "${REPORT_FILE}"; then
  pass "17-01: X-API-Key value masked in report"
else
  fail "17-01: X-API-Key value not masked in report"
fi

# ── Control 2 (17-06): no API key on any command line ───────────────────────

# audit-prod.sh must not build an inline X-API-Key header argument
if grep -qE 'X-API-Key: \$\{API_KEY\}' "${SCRIPT_DIR}/audit-prod.sh"; then
  fail "17-06: audit-prod.sh still passes X-API-Key inline (argv exposure)"
else
  pass "17-06: audit-prod.sh passes the key via curl config file"
fi

# Makefile live targets must rely on the environment, not --api-key argv
LIVE_RECIPES="$(sed -n '/^test-live:/,/^[a-z]/p;/^test-local:/,/^[a-z]/p;/^test-phase67:/,/^[a-z]/p' "${SCRIPT_DIR}/../Makefile")"
if echo "${LIVE_RECIPES}" | grep -q -- '--api-key'; then
  fail "17-06: Makefile test-live/test-local/test-phase67 still pass --api-key on argv"
else
  pass "17-06: Makefile test-live/test-local/test-phase67 use environment-provided key"
fi

# test_all_endpoints.py must support the env-var fallback
if grep -q 'DC_API_KEY' "${SCRIPT_DIR}/test_all_endpoints.py"; then
  pass "17-06: test_all_endpoints.py reads DC_API_KEY from environment"
else
  fail "17-06: test_all_endpoints.py has no environment fallback for the key"
fi

# test_phase67.py must support the env-var fallback too
if grep -q 'DC_API_KEY' "${SCRIPT_DIR}/test_phase67.py"; then
  pass "17-06: test_phase67.py reads DC_API_KEY from environment"
else
  fail "17-06: test_phase67.py has no environment fallback for the key"
fi

echo ""
if [[ "${FAILURES}" -gt 0 ]]; then
  echo "secrets-hygiene: ${FAILURES} control(s) FAILED"
else
  echo "secrets-hygiene: all controls passed"
fi
exit "${FAILURES}"
