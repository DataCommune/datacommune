#!/usr/bin/env bash
#
# Vérification live d'une release dev -> master (étape 4 du RECADRAGE-2026-08-22).
#
# Les items S2 / S5 / A3 de la série sécurité sont dans le code depuis le 22/08 mais
# n'étaient PAS effectifs en production : seul le merge sur `master` les déploie. Un
# commit n'est pas un déploiement — c'est le piège que cette série a déjà payé une fois
# (HANDOFF.md §8). Ce script exige donc une preuve d'état, pas un fichier committé.
#
# Chaque témoin porte sa BASELINE, relevée en production le 2026-08-23 AVANT le merge de
# la PR #56. Sans elle, un vert post-déploiement ne prouve rien : il pourrait décrire un
# état qui était déjà là. Le script échoue si un témoin n'a pas basculé.
#
# Usage :
#   bash scripts/verify-release-live.sh            # vérifie l'état APRÈS merge
#   bash scripts/verify-release-live.sh --baseline # réaffiche l'état brut, sans verdict
#
set -uo pipefail

FRONT="https://datacommune.fr"
API="https://api.datacommune.fr"
# Origin *.workers.dev of the frontend Worker (personal account subdomain, kept out of the repository).
WORKERS_DEV="${WORKERS_DEV:?set WORKERS_DEV=https://<worker>.<account>.workers.dev}"

# Les cinq en-têtes d'A3, tels que livrés par les DEUX voies que le code sépare :
# `_headers` pour les pages prérendues, `hooks.server.ts` pour celles rendues par le
# Worker. S'ils ne sortent que d'un côté, c'est précisément la panne que la séparation
# visait à rendre visible.
HEADERS_A3="strict-transport-security x-frame-options x-content-type-options referrer-policy permissions-policy"

fail=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
ko()   { printf '  \033[31mKO\033[0m   %s\n' "$1"; fail=$((fail + 1)); }
head_of() { curl -sI --max-time 20 "$1" 2>/dev/null; }

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

if [ "${1:-}" = "--baseline" ]; then
  section "État brut (aucun verdict)"
  for url in "$FRONT/datasets" "$FRONT/communes"; do
    echo "--- $url"
    head_of "$url" | grep -iE "^HTTP|$(echo "$HEADERS_A3" | tr ' ' '|')" || echo "  (aucun en-tête de sécurité)"
  done
  echo "--- $WORKERS_DEV"
  head_of "$WORKERS_DEV?cb=$(date +%s)" | head -1
  echo "--- $API/v1/health"
  curl -s --max-time 20 "$API/v1/health" | grep -o '"version_postgis":"[^"]*"'
  echo "--- $API/ (témoin HSTS)"
  head_of "$API/" | grep -i strict-transport
  exit 0
fi

# --- A3 : cinq en-têtes, sur les deux voies -------------------------------------------
# BASELINE 2026-08-23 pré-merge : AUCUN des cinq en-têtes, sur ni l'une ni l'autre.
section "A3 — en-têtes de sécurité frontend (baseline pré-merge : aucun)"
for url in "$FRONT/datasets" "$FRONT/communes"; do
  got=$(head_of "$url" | tr '[:upper:]' '[:lower:]')
  for h in $HEADERS_A3; do
    if echo "$got" | grep -q "^$h:"; then
      ok "$url -> $h"
    else
      ko "$url -> $h ABSENT"
    fi
  done
done

# --- S2 : le subdomain workers.dev de la prod doit mourir -----------------------------
# BASELINE 2026-08-23 pré-merge : HTTP/1.1 200 OK (bien vivant).
# `wrangler.jsonc` porte workers_dev:false + preview_urls:false ; c'est `wrangler deploy`
# qui les applique, donc le merge.
section "S2 — subdomain workers.dev hors zone (baseline pré-merge : 200 OK)"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$WORKERS_DEV?cb=$(date +%s)")
if [ "$code" = "200" ]; then
  ko "$WORKERS_DEV répond encore 200 — S2 n'est PAS effectif en prod"
else
  ok "$WORKERS_DEV -> $code (plus servi)"
fi

# --- S5 : /v1/health ne divulgue plus que la version majeure --------------------------
# BASELINE 2026-08-23 pré-merge : "3.5 USE_GEOS=1 USE_PROJ=1 USE_STATS=1".
section "S5 — /v1/health, version PostGIS (baseline pré-merge : chaîne complète)"
pg=$(curl -s --max-time 20 "$API/v1/health" | grep -o '"version_postgis":"[^"]*"')
if echo "$pg" | grep -q "USE_"; then
  ko "$pg — la chaîne complète fuit encore, S5 n'est PAS effectif"
else
  ok "$pg"
fi

# --- Non-régression : HSTS de l'API ---------------------------------------------------
# BASELINE 2026-08-23 : max-age=31536000; includeSubDomains. Ce témoin ne doit PAS bouger
# (gotcha HSTS relevé pendant la série S).
section "Témoin de non-régression — HSTS de l'API (doit rester identique)"
hsts=$(head_of "$API/" | grep -i strict-transport | tr -d '\r')
if echo "$hsts" | grep -q "max-age=31536000"; then
  ok "$hsts"
else
  ko "HSTS API modifié : '$hsts' (attendu max-age=31536000)"
fi

# --- SEC-02 : aucune source map servie en production ----------------------------------
# Point laissé « à confirmer au 1er déploiement » par la review PR #56. Le bundle déployé
# est désormais celui qui est scanné (T3), mais le scan ne dit rien de ce que Cloudflare
# SERT — d'où ce contrôle côté réseau.
section "SEC-02 — aucune source map servie (à confirmer au 1er déploiement)"
chunk=$(curl -s --max-time 20 "$FRONT/" | grep -oE '/_app/immutable/[^"]+\.js' | head -1)
if [ -z "$chunk" ]; then
  ko "aucun chunk /_app/immutable trouvé sur la home — contrôle vacuous, à investiguer"
else
  map_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$FRONT$chunk.map")
  if [ "$map_code" = "200" ]; then
    ko "$chunk.map est servi (200) — source map exposée en production"
  else
    ok "$chunk.map -> $map_code"
  fi
fi

section "Verdict"
if [ "$fail" -eq 0 ]; then
  printf '  \033[32mTous les témoins ont basculé.\033[0m S2 / S5 / A3 sont effectifs en production.\n'
  exit 0
fi
printf '  \033[31m%d contrôle(s) en échec.\033[0m Ne PAS cocher les items au registre.\n' "$fail"
exit 1
