#!/usr/bin/env bash
# Proof that the write route of the error reports cannot be reached from the internet, with the
# real Caddy of the public stack and no API: what reaches `api:8000` and `frontend:3000` is echoed
# back by a second Caddy standing in for both. Same harness as test-inscription-fermee.sh.
#
#   bash infra/public/tests/test-signalement-ferme.sh    # Git Bash on the workstation, or Linux bash
#
# Why: POST /v1/signalements writes a row for whoever calls it. On the public instance only the
# server of the site calls it, over the compose network, after the checks of its form (same
# origin, honeypot, visitor address passed on). Published on api.datacommune.fr, the route would
# let anyone skip the form.
#
# 1. `caddy validate` on api/Caddyfile.public, with the image pinned in docker-compose.public.yml.
# 2. /v1/signalements on api.datacommune.fr answers 404 from Caddy, whatever the method, with a
#    trailing slash, a sub-path or a percent-encoded letter; nothing reaches the API.
# 3. The neighbours of the route on api.datacommune.fr still reach the API, and the page of the
#    form on datacommune.fr reaches the frontend.
#
# Starts two containers on a private network created for the run; removes all three on exit.
# Nothing is published on the host, nothing reaches the internet but the pull of the pinned image.
set -euo pipefail

racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
caddyfile="$racine/api/Caddyfile.public"
image="$(sed -n 's/^ *image: \(caddy:2@sha256:[0-9a-f]*\)$/\1/p' "$racine/api/docker-compose.public.yml" | head -n1)"
[ -n "$image" ] || { echo "FAIL: no pinned caddy image in docker-compose.public.yml" >&2; exit 1; }

id="dc-signalement-$$-$RANDOM"
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
	method={http.request.method}
	uri={http.request.uri}
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
requete() { # requete <GET|POST> <path> [host] -> prints "STATUS <code>" and the echoed lines
	local methode="$1" chemin="$2" hote_vise="${3:-api.datacommune.fr}"
	local options=(--header "Host: $hote_vise")
	if [ "$methode" = POST ]; then options+=(--header "Content-Type: application/json" --post-data '{"commentaire":"un signalement de test"}'); fi
	docker run --rm --network "$reseau" "$image" sh -c \
		'wget -S -q -O - "$@" 2>&1 | sed -n "s/^  HTTP\/[0-9.]* \([0-9]*\).*/STATUS \1/p; /^[a-z-]*=/p"' \
		_ "${options[@]}" "http://caddy:80$chemin" || true
}

attendre() { for _ in $(seq 1 30); do requete GET /v1/health | grep -q STATUS && return 0; sleep 0.5; done; return 1; }
attendre || { echo "FAIL - le Caddy du site ne répond pas" >&2; exit 1; }

# 2. The write route is closed on the API host: Caddy answers 404 itself.
for chemin in /v1/signalements /v1/signalements/ /v1/signalements/42 /v1/%73ignalements; do
	for methode in POST GET; do
		reponse="$(requete "$methode" "$chemin")"
		verifie "$methode $chemin répond 404" grep -q '^STATUS 404$' <<<"$reponse"
		verifie "$methode $chemin n'atteint pas l'API" bash -c '! grep -q "^upstream=" <<<"$1"' _ "$reponse"
	done
done

# 3. The rest is untouched.
for chemin in /v1/health /v1/communes/44109 /v1/signalementsautre /v1/signale; do
	reponse="$(requete GET "$chemin")"
	verifie "GET $chemin atteint l'API" grep -q '^upstream=api$' <<<"$reponse"
done
reponse="$(requete GET /signaler datacommune.fr)"
verifie "GET datacommune.fr/signaler atteint le site" grep -q '^upstream=frontend$' <<<"$reponse"
reponse="$(requete POST /signaler datacommune.fr)"
verifie "POST datacommune.fr/signaler atteint le site" grep -q '^upstream=frontend$' <<<"$reponse"

if [ "$echecs" -gt 0 ]; then echo "SIGNALEMENT FAILED ($echecs)"; exit 1; fi
echo "SIGNALEMENT ok"
