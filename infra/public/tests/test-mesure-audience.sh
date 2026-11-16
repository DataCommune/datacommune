#!/usr/bin/env bash
# Proof of the privacy boundary of the audience count (04.7-14b), with the real Caddy of the public
# stack and no GoatCounter: what reaches `goatcounter:8080` through /count is echoed back by a
# second Caddy standing in for it.
#
#   bash infra/public/tests/test-mesure-audience.sh    # Git Bash on the workstation, or Linux bash
#
# 1. `caddy validate` on api/Caddyfile.public, with the image pinned in docker-compose.public.yml.
# 2. Gate closed (MESURE_AUDIENCE_ACTIVE unset): /count answers 204 and the stand-in sees nothing.
# 3. Gate open: a request carrying every address header GoatCounter's RealIP reads, plus Forwarded,
#    True-Client-IP, Referer and Cookie, reaches the stand-in with none of them, Host = localhost.
# 4. The rest of datacommune.fr still goes to the frontend upstream, never to the stand-in.
#
# Starts two containers on a private network created for the run; removes all three on exit.
# Nothing is published on the host, nothing reaches the internet but the pull of the pinned image.
set -euo pipefail

racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
caddyfile="$racine/api/Caddyfile.public"
image="$(sed -n 's/^ *image: \(caddy:2@sha256:[0-9a-f]*\)$/\1/p' "$racine/api/docker-compose.public.yml" | head -n1)"
[ -n "$image" ] || { echo "FAIL: no pinned caddy image in docker-compose.public.yml" >&2; exit 1; }

id="dc-mesure-$$-$RANDOM"
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
# Stand-in of GoatCounter: answers every request with the headers it received, one per line.
cat > "$travail/Caddyfile.echo" <<'ECHO'
{
	auto_https off
	admin off
}
:8080 {
	respond <<TEXTE
	host={http.request.host}
	cf-connecting-ip={http.request.header.CF-Connecting-IP}
	fly-client-ip={http.request.header.Fly-Client-IP}
	x-azure-socketip={http.request.header.X-Azure-SocketIP}
	x-real-ip={http.request.header.X-Real-IP}
	x-forwarded-for={http.request.header.X-Forwarded-For}
	forwarded={http.request.header.Forwarded}
	true-client-ip={http.request.header.True-Client-IP}
	referer={http.request.header.Referer}
	cookie={http.request.header.Cookie}
	query={http.request.uri.query}
	TEXTE 200
}
ECHO

docker network create --internal "$reseau" >/dev/null

# 1. Syntax and adaptation of the public Caddyfile, gate open and closed.
for valeur in non oui; do
	verifie "caddy validate (MESURE_AUDIENCE_ACTIVE=$valeur)" \
		docker run --rm -e MESURE_AUDIENCE_ACTIVE="$valeur" -v "$(hote "$travail/Caddyfile.public"):/etc/caddy/Caddyfile:ro" \
		"$image" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
done

demarrer_site() { # demarrer_site <MESURE_AUDIENCE_ACTIVE value or empty>
	docker rm -f "$id-site" >/dev/null 2>&1 || true
	local env=()
	[ -n "$1" ] && env=(-e "MESURE_AUDIENCE_ACTIVE=$1")
	docker run -d --name "$id-site" --network "$reseau" --network-alias caddy "${env[@]}" \
		-v "$(hote "$travail/Caddyfile.public"):/etc/caddy/Caddyfile:ro" "$image" >/dev/null
}

docker run -d --name "$id-echo" --network "$reseau" --network-alias goatcounter \
	-v "$(hote "$travail/Caddyfile.echo"):/etc/caddy/Caddyfile:ro" "$image" >/dev/null

# A client on the same network: the image ships wget (busybox).
requete() { # requete <path> [header...] -> prints "<status>\n<body>"
	local chemin="$1"; shift
	local entetes=(--header "Host: datacommune.fr")
	for h in "$@"; do entetes+=(--header "$h"); done
	docker run --rm --network "$reseau" "$image" sh -c \
		'wget -S -q -O - "$@" 2>&1 | sed -n "s/^  HTTP\/[0-9.]* \([0-9]*\).*/STATUS \1/p; /^[a-z-]*=/p"' \
		_ "${entetes[@]}" "http://caddy:80$chemin" || true
}

attendre() { for _ in $(seq 1 30); do requete /count | grep -q STATUS && return 0; sleep 0.5; done; return 1; }

# 2. Gate closed.
demarrer_site ""
attendre
ferme="$(requete '/count?p=%2F')"
verifie "porte fermée : 204 de Caddy" grep -q 'STATUS 204' <<<"$ferme"
verifie "porte fermée : le faux GoatCounter ne reçoit rien" bash -c '! grep -q "^host=" <<<"$1"' _ "$ferme"

# 3. Gate open, every address carrier forged.
demarrer_site oui
attendre
ouvert="$(requete '/count?p=%2Fadresse' \
	'CF-Connecting-IP: 203.0.113.1' 'Fly-Client-IP: 203.0.113.2' 'X-Azure-SocketIP: 203.0.113.3' \
	'X-Real-IP: 203.0.113.4' 'X-Forwarded-For: 203.0.113.5' 'Forwarded: for=203.0.113.6' \
	'True-Client-IP: 203.0.113.7' 'Referer: https://datacommune.fr/adresse/44109_zz04_00004' \
	'Cookie: dc_session=0123456789abcdef0123456789abcdef')"
echo "$ouvert" | sed 's/^/     | /'
verifie "porte ouverte : la requête atteint le faux GoatCounter" grep -q '^host=localhost$' <<<"$ouvert"
verifie "porte ouverte : le chemin agrégé passe tel quel" grep -q '^query=p=%2Fadresse$' <<<"$ouvert"
verifie "aucune adresse forgée ne passe (203.0.113.x)" bash -c '! grep -q "203\.0\.113" <<<"$1"' _ "$ouvert"
for entete in cf-connecting-ip fly-client-ip x-azure-socketip x-real-ip forwarded true-client-ip referer cookie; do
	verifie "$entete vide" grep -q "^$entete=\$" <<<"$ouvert"
done
# Caddy sets its own X-Forwarded-For before header_up runs: the removal must cover it too.
verifie "x-forwarded-for vide (pas même l'adresse du pair)" grep -q '^x-forwarded-for=$' <<<"$ouvert"

# Provenance by channel (2026-11-11): the virtual page of a channel is one more value of `p`; Caddy
# forwards it as written, with the same stripped headers.
provenance="$(requete '/count?p=%2Fvenu-de%2Freddit' 'Referer: https://www.reddit.com/r/exemple/' 'X-Forwarded-For: 203.0.113.8')"
verifie "porte ouverte : la page virtuelle d'un canal passe telle quelle" grep -q '^query=p=%2Fvenu-de%2Freddit$' <<<"$provenance"
verifie "page virtuelle d'un canal : ni référent ni adresse en amont" bash -c 'grep -q "^referer=\$" <<<"$1" && grep -q "^x-forwarded-for=\$" <<<"$1"' _ "$provenance"

# 4. The rest of the site never reaches the stand-in: Caddy answers it (502, no frontend upstream
# here), so an empty answer (a transport failure) cannot pass for « not sent to GoatCounter ».
autre="$(requete '/adresse/44109_zz04_00004')"
verifie "une autre page a une réponse HTTP de Caddy (502 sans frontend)" grep -q 'STATUS 502' <<<"$autre"
verifie "une autre page ne va pas au faux GoatCounter" bash -c '! grep -q "^host=" <<<"$1"' _ "$autre"

if [ "$echecs" -gt 0 ]; then echo "MESURE-AUDIENCE FAILED ($echecs)"; exit 1; fi
echo "MESURE-AUDIENCE ok"
