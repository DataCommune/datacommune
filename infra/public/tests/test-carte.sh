#!/usr/bin/env bash
# Proof of the route and of the privacy boundary of the address maps (04.7-17), with the real Caddy
# of the public stack and no API: what reaches `api:8000` and `frontend:3000` is echoed back by a
# second Caddy standing in for both.
#
#   bash infra/public/tests/test-carte.sh    # Git Bash on the workstation, or Linux bash
#
# 1. `caddy validate` on api/Caddyfile.public, with the image pinned in docker-compose.public.yml.
# 2. /carte/<ban_id>/plan.png on datacommune.fr reaches the API as /v1/carte/<ban_id>/plan.png,
#    with the real visitor address (CF-Connecting-IP) as X-Forwarded-For / X-Real-IP, and without
#    the Cookie and the Referer the visitor sent; a forged X-Forwarded-For does not get through.
# 3. The rest of datacommune.fr (pages, /carte without a key, a look-alike prefix) still goes to
#    the frontend upstream, never to the API.
# 4. The four routes of the interactive map (04.10) go through the same relay: parcels of a tile,
#    base map tile, sales of a parcel and context of an address reach the API under /v1/carte/...,
#    query string kept, without the Cookie and the Referer.
#
# Starts two containers on a private network created for the run; removes all three on exit.
# Nothing is published on the host, nothing reaches the internet but the pull of the pinned image.
set -euo pipefail

racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
caddyfile="$racine/api/Caddyfile.public"
image="$(sed -n 's/^ *image: \(caddy:2@sha256:[0-9a-f]*\)$/\1/p' "$racine/api/docker-compose.public.yml" | head -n1)"
[ -n "$image" ] || { echo "FAIL: no pinned caddy image in docker-compose.public.yml" >&2; exit 1; }

id="dc-carte-$$-$RANDOM"
reseau="$id-net"
travail="$(mktemp -d)"
nettoyer() {
	docker rm -f "$id-site" "$id-echo" >/dev/null 2>&1 || true
	docker network rm "$reseau" >/dev/null 2>&1 || true
	rm -rf "$travail"
	# A container or the network left behind fails the run, whatever the checks said; so does a
	# listing that cannot be made (Docker gone mid-run): absence is only what Docker answers.
	local conteneurs reseaux
	# $id is unique to this run: a plain substring filter is enough (no regex anchors in Docker filters).
	if ! conteneurs="$(docker ps -aq --filter "name=$id-")" || ! reseaux="$(docker network ls -q --filter "name=$reseau")"; then
		echo "FAIL - nettoyage : Docker ne répond pas, ressources $id non vérifiées" >&2
		exit 1
	fi
	if [ -n "$conteneurs" ] || [ -n "$reseaux" ]; then
		echo "FAIL - nettoyage : conteneur ou réseau $id restant" >&2
		exit 1
	fi
}
trap nettoyer EXIT

echecs=0
verifie() { # verifie <description> <condition-command...>
	local description="$1"; shift
	if "$@"; then echo "ok   - $description"; else echo "FAIL - $description"; echecs=$((echecs + 1)); fi
}

# MSYS (Git Bash) would rewrite /etc/... paths given to docker: keep them as written, and give
# Docker Desktop the Windows form of the host paths mounted (cygpath -m), unchanged on Linux.
export MSYS_NO_PATHCONV=1
hote() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi; }

cp "$caddyfile" "$travail/Caddyfile.public"
# Stand-in of the API (:8000) and of the frontend (:3000): each answers every request with its own
# name and the request as it received it, one per line.
cat > "$travail/Caddyfile.echo" <<'ECHO'
{
	auto_https off
	admin off
}
:8000 {
	respond <<TEXTE
	upstream=api
	host={http.request.host}
	uri={http.request.uri}
	cf-connecting-ip={http.request.header.CF-Connecting-IP}
	x-forwarded-for={http.request.header.X-Forwarded-For}
	x-real-ip={http.request.header.X-Real-IP}
	referer={http.request.header.Referer}
	cookie={http.request.header.Cookie}
	TEXTE 200
}
:3000 {
	respond <<TEXTE
	upstream=frontend
	uri={http.request.uri}
	TEXTE 200
}
ECHO

docker network create --internal "$reseau" >/dev/null

# 1. Syntax and adaptation of the public Caddyfile.
verifie "caddy validate" \
	docker run --rm -v "$(hote "$travail/Caddyfile.public"):/etc/caddy/Caddyfile:ro" \
	"$image" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

docker run -d --name "$id-echo" --network "$reseau" --network-alias api --network-alias frontend \
	-v "$(hote "$travail/Caddyfile.echo"):/etc/caddy/Caddyfile:ro" "$image" >/dev/null
docker run -d --name "$id-site" --network "$reseau" --network-alias caddy \
	-v "$(hote "$travail/Caddyfile.public"):/etc/caddy/Caddyfile:ro" "$image" >/dev/null

# A client on the same network: the image ships wget (busybox).
requete() { # requete <path> [header...] -> prints "STATUS <code>" and the echoed lines
	local chemin="$1"; shift
	local entetes=(--header "Host: datacommune.fr")
	for h in "$@"; do entetes+=(--header "$h"); done
	docker run --rm --network "$reseau" "$image" sh -c \
		'wget -S -q -O - "$@" 2>&1 | sed -n "s/^  HTTP\/[0-9.]* \([0-9]*\).*/STATUS \1/p; /^[a-z-]*=/p"' \
		_ "${entetes[@]}" "http://caddy:80$chemin" || true
}

attendre() { for _ in $(seq 1 30); do requete /carte/attente | grep -q STATUS && return 0; sleep 0.5; done; return 1; }
attendre || { echo "FAIL - le Caddy du site ne répond pas" >&2; exit 1; }

cle="44109_zz04_00004"

# 2. A map: rewritten to the API, the visitor's address kept, cookie and referrer removed.
carte="$(requete "/carte/$cle/plan.png?couche=parcelles" \
	'CF-Connecting-IP: 203.0.113.1' 'X-Forwarded-For: 203.0.113.5' 'X-Real-IP: 203.0.113.4' \
	'Referer: https://datacommune.fr/adresse/44109_zz04_00004' \
	'Cookie: dc_session=0123456789abcdef0123456789abcdef')"
echo "$carte" | sed 's/^/     | /'
verifie "la carte atteint l'API" grep -q '^upstream=api$' <<<"$carte"
verifie "/carte/<clé>/plan.png arrive en /v1/carte/<clé>/plan.png" grep -q "^uri=/v1/carte/$cle/plan.png" <<<"$carte"
verifie "le Referer est retiré" grep -q '^referer=$' <<<"$carte"
verifie "le Cookie est retiré" grep -q '^cookie=$' <<<"$carte"
verifie "l'adresse réelle (CF-Connecting-IP) devient X-Forwarded-For" grep -q '^x-forwarded-for=203\.0\.113\.1$' <<<"$carte"
verifie "l'adresse réelle devient X-Real-IP" grep -q '^x-real-ip=203\.0\.113\.1$' <<<"$carte"
verifie "aucune adresse forgée ne passe (203.0.113.4, 203.0.113.5)" bash -c '! grep -Eq "203\.0\.113\.[45]" <<<"$1"' _ "$carte"

carte_parcelles="$(requete "/carte/$cle/parcelles.png")"
verifie "la couche parcelles est relayée de même" grep -q "^uri=/v1/carte/$cle/parcelles.png$" <<<"$carte_parcelles"

# 3. The rest of the site never reaches the API: the frontend upstream answers it.
for chemin in "/adresse/$cle" "/carte" "/cartes/$cle/plan.png" "/"; do
	autre="$(requete "$chemin")"
	verifie "$chemin va au frontend" grep -q '^upstream=frontend$' <<<"$autre"
	verifie "$chemin ne va pas à l'API" bash -c '! grep -q "^upstream=api$" <<<"$1"' _ "$autre"
done

# 4. The interactive map: its four paths reach the API as the images do. The identifiers are
# synthetic; the API validates them, the relay only carries them.
parcelle="00000000ZA0001"
for chemin in "/carte/parcelles/16/1/2" "/carte/fond/16/1/2.png" 	"/carte/$cle/parcelle/$parcelle/ventes?page=2&vg=0123456789abcdef" "/carte/$cle/contexte"; do
	relaye="$(requete "$chemin" 'CF-Connecting-IP: 203.0.113.1' 		"Referer: https://datacommune.fr/adresse/$cle" 		'Cookie: dc_session=0123456789abcdef0123456789abcdef')"
	verifie "$chemin atteint l'API" grep -q '^upstream=api$' <<<"$relaye"
	verifie "$chemin arrive en /v1$chemin" grep -qxF "uri=/v1$chemin" <<<"$relaye"
	verifie "$chemin : le Referer est retiré" grep -q '^referer=$' <<<"$relaye"
	verifie "$chemin : le Cookie est retiré" grep -q '^cookie=$' <<<"$relaye"
	verifie "$chemin : l'adresse réelle devient X-Forwarded-For" grep -q '^x-forwarded-for=203\.0\.113\.1$' <<<"$relaye"
done

if [ "$echecs" -gt 0 ]; then echo "CARTE FAILED ($echecs)"; exit 1; fi
echo "CARTE ok"
