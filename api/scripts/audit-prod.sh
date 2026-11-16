#!/bin/bash
# Production audit script for DataCommune API.
#
# Tests all 22 API endpoints, collects row counts for 15 tables,
# runs EXPLAIN ANALYZE on 7 spatial queries, and tests 3 external proxies.
# Produces a timestamped report file + stdout log.
#
# Usage:
#   API_KEY=<your_key> bash /opt/datacommune/scripts/audit-prod.sh
#
# Environment:
#   API_KEY       (required) — valid API key for authenticated endpoints
#   COMPOSE_DIR   (optional) — default /opt/datacommune
#   REPORT_DIR    (optional) — default ${COMPOSE_DIR}/reports
set -uo pipefail

# ─── Config ─────────────────────────────────────────────────────────────────

COMPOSE_DIR="${COMPOSE_DIR:-/opt/datacommune}"
REPORT_DIR="${REPORT_DIR:-${COMPOSE_DIR}/reports}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
REPORT_FILE="${REPORT_DIR}/audit_${TIMESTAMP}.log"
RESP_FILE="/tmp/audit_resp.json"

if [[ -z "${API_KEY:-}" ]]; then
  echo "ERROR: API_KEY environment variable is required."
  echo "Usage: API_KEY=<your_key> bash $0"
  exit 1
fi

# 17-06: pass the API key to curl via a config file (chmod 600) instead of an
# inline header argument, so it never appears in the process table (pattern:
# privacy-canary.yml).
CURL_CFG="$(mktemp)"
trap 'rm -f "${CURL_CFG}"' EXIT
printf 'header = "X-API-Key: %s"\n' "${API_KEY}" > "${CURL_CFG}"
chmod 600 "${CURL_CFG}"

COMPOSE_CMD=(
  docker compose
  -f "${COMPOSE_DIR}/docker-compose.yml"
  -f "${COMPOSE_DIR}/docker-compose.prod.yml"
)

# Resolve API container IP (port 8000 not mapped to host)
# Container renamed from datacommune-api-1 to api-api-1 in a previous
# docker-compose restructure. Try the current name first, fall back to the
# legacy name for older environments.
API_CONTAINER_IP=$(docker inspect api-api-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || true)
if [[ -z "${API_CONTAINER_IP}" ]]; then
  API_CONTAINER_IP=$(docker inspect datacommune-api-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || true)
fi
if [[ -z "${API_CONTAINER_IP}" ]]; then
  echo "ERROR: Cannot resolve API container IP (tried api-api-1 and datacommune-api-1). Is the API container running?"
  exit 1
fi
API_BASE="http://${API_CONTAINER_IP}:8000"

mkdir -p "${REPORT_DIR}"

# ─── Counters ────────────────────────────────────────────────────────────────

PASS_COUNT=0
SLOW_COUNT=0
EMPTY_COUNT=0
FAIL_COUNT=0
TIMEOUT_COUNT=0
TOTAL_TESTS=0
ISSUES=()
AUDIT_START=$(date +%s)

# ─── Functions ───────────────────────────────────────────────────────────────

log() {
  echo "[$(date -Iseconds)] $*" | tee -a "${REPORT_FILE}"
}

section() {
  local title="$1"
  log ""
  log "================================================================"
  log "  ${title}"
  log "================================================================"
  log ""
}

# test_endpoint <name> <url> <auth_required:0|1> [max_time_s:10]
test_endpoint() {
  local name="$1"
  local url="$2"
  local auth_required="${3:-1}"
  local max_time_s="${4:-10}"

  TOTAL_TESTS=$((TOTAL_TESTS + 1))

  local curl_args=(-s -o "${RESP_FILE}" -w "%{http_code}|%{time_total}|%{size_download}" --max-time "${max_time_s}")
  if [[ "${auth_required}" -eq 1 ]]; then
    curl_args+=(-K "${CURL_CFG}")
  fi

  local result
  result=$(curl "${curl_args[@]}" "${url}" 2>&1) || true

  # Check for curl timeout (exit code 28 — curl returns empty on timeout)
  if [[ -z "${result}" || "${result}" == *"timed out"* ]]; then
    TIMEOUT_COUNT=$((TIMEOUT_COUNT + 1))
    log "[TIMEOUT] ${name}"
    log "  URL: ${url}"
    log "  Max time: ${max_time_s}s — request timed out"
    ISSUES+=("CRITICAL|${name}|TIMEOUT after ${max_time_s}s")
    return
  fi

  local http_code time_total size_download
  http_code=$(echo "${result}" | cut -d'|' -f1)
  time_total=$(echo "${result}" | cut -d'|' -f2)
  size_download=$(echo "${result}" | cut -d'|' -f3)

  # Handle curl errors (http_code = 000)
  if [[ "${http_code}" == "000" ]]; then
    TIMEOUT_COUNT=$((TIMEOUT_COUNT + 1))
    log "[TIMEOUT] ${name}"
    log "  URL: ${url}"
    log "  curl error — connection failed or timed out"
    ISSUES+=("CRITICAL|${name}|Connection failed or timeout")
    return
  fi

  # Read first 200 chars of response body
  local body_preview=""
  if [[ -f "${RESP_FILE}" ]]; then
    body_preview=$(head -c 200 "${RESP_FILE}" 2>/dev/null || true)
  fi

  # Check for empty data (200 but donnees=[])
  local has_data=1
  if [[ -f "${RESP_FILE}" ]]; then
    # Check for empty donnees array or empty results
    if grep -qE '"donnees"\s*:\s*\[\s*\]' "${RESP_FILE}" 2>/dev/null; then
      has_data=0
    fi
  fi

  # Classify result
  local status_label
  local is_slow=0
  if [[ "${http_code}" =~ ^[45] ]]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
    status_label="FAIL"
    local severity="CRITICAL"
    [[ "${http_code}" =~ ^4 ]] && severity="HIGH"
    ISSUES+=("${severity}|${name}|HTTP ${http_code}")
  elif (( $(echo "${time_total} > 5.0" | bc -l 2>/dev/null || echo 0) )); then
    SLOW_COUNT=$((SLOW_COUNT + 1))
    status_label="SLOW"
    is_slow=1
    ISSUES+=("HIGH|${name}|Response time ${time_total}s (>5s)")
  elif [[ "${has_data}" -eq 0 ]]; then
    EMPTY_COUNT=$((EMPTY_COUNT + 1))
    status_label="EMPTY"
    ISSUES+=("MEDIUM|${name}|HTTP 200 but donnees is empty")
  else
    PASS_COUNT=$((PASS_COUNT + 1))
    status_label="PASS"
  fi

  log "[${status_label}] ${name}"
  log "  URL: ${url}"
  log "  HTTP: ${http_code} | Time: ${time_total}s | Size: ${size_download} bytes"
  if [[ -n "${body_preview}" ]]; then
    log "  Body: ${body_preview}"
  fi
}

# test_sql <name> <sql_query>
test_sql() {
  local name="$1"
  local sql="$2"
  log "--- ${name} ---"
  local output
  output=$(cd "${COMPOSE_DIR}" && "${COMPOSE_CMD[@]}" exec -T db \
    psql -U datacommune -d datacommune -c "${sql}" 2>&1) || true
  log "${output}"
}

# test_explain <name> <sql_query>
test_explain() {
  local name="$1"
  local sql="$2"
  log ""
  log "--- EXPLAIN: ${name} ---"
  local output
  output=$(cd "${COMPOSE_DIR}" && "${COMPOSE_CMD[@]}" exec -T db \
    psql -U datacommune -d datacommune -c "SET statement_timeout = '30s'; ${sql}" 2>&1) || true
  log "${output}"

  # Check for Seq Scan vs Index Scan
  if echo "${output}" | grep -qi "Seq Scan"; then
    log "  >> WARNING: Sequential Scan detected"
    ISSUES+=("HIGH|${name}|Seq Scan detected (no index used)")
  fi
  if echo "${output}" | grep -qi "Index Scan\|Bitmap Index Scan\|Bitmap Heap Scan"; then
    log "  >> OK: Index Scan detected"
  fi
  # Extract execution time if present
  local exec_time
  exec_time=$(echo "${output}" | grep -oP "Execution Time: \K[0-9.]+" | head -1 || true)
  if [[ -n "${exec_time}" ]]; then
    log "  >> Execution Time: ${exec_time} ms"
  fi
}

# ─── Section 1: Health & Public ──────────────────────────────────────────────

section "SECTION 1: Health & Public Endpoints"

test_endpoint "health/live" "${API_BASE}/v1/health/live" 0 5
test_endpoint "health" "${API_BASE}/v1/health" 0 10

# ─── Section 2: Authenticated Endpoints ─────────────────────────────────────

section "SECTION 2: Authenticated Endpoints (20 tests)"

test_endpoint "geocode" \
  "${API_BASE}/v1/geocode?q=1+place+de+la+concorde&code_postal=75008" 1 15

test_endpoint "reverse" \
  "${API_BASE}/v1/reverse?lat=48.8566&lon=2.3522" 1 15

test_endpoint "parcelle" \
  "${API_BASE}/v1/parcelle?lat=48.8566&lon=2.3522" 1 15

test_endpoint "parcelle/{ref}" \
  "${API_BASE}/v1/parcelle/75101000AB0001" 1 10

test_endpoint "resolve" \
  "${API_BASE}/v1/resolve?q=48.8566,2.3522" 1 20

test_endpoint "dvf" \
  "${API_BASE}/v1/dvf?lat=48.8566&lon=2.3522" 1 15

test_endpoint "dpe" \
  "${API_BASE}/v1/dpe?lat=48.8566&lon=2.3522&rayon_m=500" 1 30

test_endpoint "loyers" \
  "${API_BASE}/v1/loyers?code_insee=75056" 1 10

test_endpoint "risques" \
  "${API_BASE}/v1/risques?lat=48.8566&lon=2.3522" 1 15

test_endpoint "pollutions" \
  "${API_BASE}/v1/pollutions?lat=48.8566&lon=2.3522" 1 15

test_endpoint "urbanisme" \
  "${API_BASE}/v1/urbanisme?lat=48.8566&lon=2.3522" 1 15

test_endpoint "batiments" \
  "${API_BASE}/v1/batiments?lat=48.8566&lon=2.3522" 1 30

test_endpoint "monuments" \
  "${API_BASE}/v1/monuments?lat=48.8566&lon=2.3522&rayon_m=1000" 1 15

test_endpoint "connectivite" \
  "${API_BASE}/v1/connectivite?code_insee=75056" 1 10

test_endpoint "entreprises" \
  "${API_BASE}/v1/entreprises?lat=48.8566&lon=2.3522" 1 15

test_endpoint "scolaire" \
  "${API_BASE}/v1/scolaire?code_insee=75056" 1 10

test_endpoint "transports" \
  "${API_BASE}/v1/transports?lat=48.8566&lon=2.3522" 1 15

test_endpoint "scoring/renov" \
  "${API_BASE}/v1/scoring/renov?code_departement=75" 1 10

test_endpoint "scoring/renov-target" \
  "${API_BASE}/v1/scoring/renov-target?code_departement=75" 1 10

test_endpoint "datasets" \
  "${API_BASE}/v1/datasets" 1 10

# ─── Section 3: Row Counts ──────────────────────────────────────────────────

section "SECTION 3: Row Counts (15 tables)"

TABLES=(
  "adresses"
  "parcelles"
  "mutations"
  "diagnostics_dpe"
  "batiments"
  "eligibilite_fixe"
  "indicateurs_loyers"
  "monuments_historiques"
  "carte_scolaire"
  "arrets_transport"
  "gares"
  "api_keys"
  "quota_usage"
  "dataset_metadata"
  "etl_runs"
)

for table in "${TABLES[@]}"; do
  test_sql "Row count: ${table}" "SELECT '${table}' AS table_name, count(*) FROM ${table};"
done

# Check scoring materialized view
test_sql "Row count: scoring_renov_target (matview)" \
  "SELECT 'scoring_renov_target' AS table_name, count(*) FROM scoring_renov_target;" 2>/dev/null || \
  log "  >> scoring_renov_target matview does not exist or is not populated"

# Cross-check with dataset_metadata
test_sql "dataset_metadata cross-check" \
  "SELECT dataset_name, row_count, status, last_updated_at FROM dataset_metadata ORDER BY dataset_name;"

# ─── Section 4: EXPLAIN ANALYZE spatial queries ─────────────────────────────

section "SECTION 4: EXPLAIN ANALYZE Spatial Queries (7 queries)"

test_explain "DPE (position::geography)" \
  "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) SELECT count(*) FROM diagnostics_dpe WHERE ST_DWithin(position::geography, ST_SetSRID(ST_MakePoint(2.3522, 48.8566), 4326)::geography, 500);"

test_explain "Reverse/Adresses (position::geography)" \
  "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) SELECT * FROM adresses WHERE ST_DWithin(position::geography, ST_SetSRID(ST_MakePoint(2.3522, 48.8566), 4326)::geography, 200) ORDER BY position <-> ST_SetSRID(ST_MakePoint(2.3522, 48.8566), 4326) LIMIT 1;"

test_explain "Batiments (geometrie::geography)" \
  "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) SELECT * FROM batiments WHERE ST_DWithin(geometrie::geography, ST_SetSRID(ST_MakePoint(2.3522, 48.8566), 4326)::geography, 500) LIMIT 5;"

test_explain "Monuments (position::geography)" \
  "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) SELECT * FROM monuments_historiques WHERE ST_DWithin(position::geography, ST_SetSRID(ST_MakePoint(2.3522, 48.8566), 4326)::geography, 1000) LIMIT 5;"

test_explain "Transports/Arrets (position::geography)" \
  "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) SELECT * FROM arrets_transport WHERE ST_DWithin(position::geography, ST_SetSRID(ST_MakePoint(2.3522, 48.8566), 4326)::geography, 500) LIMIT 5;"

test_explain "Gares (position::geography)" \
  "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) SELECT * FROM gares WHERE ST_DWithin(position::geography, ST_SetSRID(ST_MakePoint(2.3522, 48.8566), 4326)::geography, 5000) LIMIT 5;"

test_explain "Parcelles (ST_Intersects, pure geometry — control)" \
  "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) SELECT * FROM parcelles WHERE ST_Intersects(geometrie, ST_SetSRID(ST_MakePoint(2.3522, 48.8566), 4326)) LIMIT 1;"

# ─── Section 5: External Proxies ────────────────────────────────────────────

section "SECTION 5: External Proxy Tests (with retry)"

test_proxy() {
  local name="$1"
  local url="$2"
  local attempt

  for attempt in 1 2; do
    log "--- Proxy: ${name} (attempt ${attempt}/2) ---"
    TOTAL_TESTS=$((TOTAL_TESTS + 1))

    local curl_args=(-s -o "${RESP_FILE}" -w "%{http_code}|%{time_total}|%{size_download}" --max-time 15)
    curl_args+=(-K "${CURL_CFG}")

    local result
    result=$(curl "${curl_args[@]}" "${url}" 2>&1) || true

    if [[ -z "${result}" || "${result}" == "000"* ]]; then
      log "  [TIMEOUT] ${name} attempt ${attempt}"
      if [[ "${attempt}" -eq 2 ]]; then
        TIMEOUT_COUNT=$((TIMEOUT_COUNT + 1))
        ISSUES+=("HIGH|${name}|Proxy timeout on both attempts")
      fi
      sleep 5
      continue
    fi

    local http_code time_total size_download
    http_code=$(echo "${result}" | cut -d'|' -f1)
    time_total=$(echo "${result}" | cut -d'|' -f2)
    size_download=$(echo "${result}" | cut -d'|' -f3)

    local body_preview=""
    if [[ -f "${RESP_FILE}" ]]; then
      body_preview=$(head -c 200 "${RESP_FILE}" 2>/dev/null || true)
    fi

    log "  HTTP: ${http_code} | Time: ${time_total}s | Size: ${size_download} bytes"
    log "  Body: ${body_preview}"

    if [[ "${http_code}" =~ ^[45] ]]; then
      # Distinguish upstream vs our code
      if [[ "${http_code}" =~ ^(502|503|504)$ ]]; then
        log "  >> Upstream error (${http_code})"
        ISSUES+=("HIGH|${name}|Upstream error HTTP ${http_code}")
      elif [[ "${http_code}" == "500" ]]; then
        log "  >> Internal error (500) — checking for JSONDecodeError..."
        local api_logs
        api_logs=$(cd "${COMPOSE_DIR}" && "${COMPOSE_CMD[@]}" logs --tail=20 api 2>&1 | \
          grep -iE "json|decode|georisques|ign|sirene|error" || true)
        if [[ -n "${api_logs}" ]]; then
          log "  >> API logs: ${api_logs}"
        fi
        ISSUES+=("CRITICAL|${name}|Internal error 500 (possible JSONDecodeError)")
      fi

      if [[ "${attempt}" -eq 2 ]]; then
        FAIL_COUNT=$((FAIL_COUNT + 1))
      fi
      sleep 5
      continue
    fi

    # Success — count as pass and break
    PASS_COUNT=$((PASS_COUNT + 1))
    log "  [PASS] ${name} succeeded on attempt ${attempt}"
    break
  done
}

test_proxy "risques (Georisques)" \
  "${API_BASE}/v1/risques?lat=48.8566&lon=2.3522"

test_proxy "pollutions (Georisques)" \
  "${API_BASE}/v1/pollutions?lat=48.8566&lon=2.3522"

test_proxy "urbanisme (API Carto IGN)" \
  "${API_BASE}/v1/urbanisme?lat=48.8566&lon=2.3522"

test_proxy "entreprises (Recherche Entreprises)" \
  "${API_BASE}/v1/entreprises?lat=48.8566&lon=2.3522"

# ─── Section 6: Summary ─────────────────────────────────────────────────────

section "SECTION 6: Audit Summary"

AUDIT_END=$(date +%s)
AUDIT_DURATION=$((AUDIT_END - AUDIT_START))

log "================================================================"
log "  RESULTS"
log "================================================================"
log ""
log "  PASS:    ${PASS_COUNT}"
log "  SLOW:    ${SLOW_COUNT}"
log "  EMPTY:   ${EMPTY_COUNT}"
log "  FAIL:    ${FAIL_COUNT}"
log "  TIMEOUT: ${TIMEOUT_COUNT}"
log "  TOTAL:   ${TOTAL_TESTS}"
log ""

if [[ ${#ISSUES[@]} -gt 0 ]]; then
  log "================================================================"
  log "  ISSUES FOUND (${#ISSUES[@]})"
  log "================================================================"
  log ""
  issue_num=0
  for issue in "${ISSUES[@]}"; do
    issue_num=$((issue_num + 1))
    severity=$(echo "${issue}" | cut -d'|' -f1)
    name=$(echo "${issue}" | cut -d'|' -f2)
    detail=$(echo "${issue}" | cut -d'|' -f3)
    log "  #${issue_num} [${severity}] ${name}: ${detail}"
  done
  log ""
fi

log "================================================================"
log "  Audit completed in ${AUDIT_DURATION}s"
log "  Report saved to: ${REPORT_FILE}"
log "================================================================"

# Exit code: number of CRITICAL + FAIL issues
CRITICAL_COUNT=0
for issue in "${ISSUES[@]}"; do
  if echo "${issue}" | grep -q "^CRITICAL"; then
    CRITICAL_COUNT=$((CRITICAL_COUNT + 1))
  fi
done

exit "${CRITICAL_COUNT}"
