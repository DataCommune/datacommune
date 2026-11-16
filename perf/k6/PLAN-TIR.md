# Plan de tir k6 — capacité de l'API publique (conteneur public)

> **PLAN-FIRST — rien n'est exécuté sur l'hyperviseur ni sur le conteneur public tant qu'Ianis n'a pas validé ce plan.**
> Rédigé le 2026-11-02. À la rédaction, seules trois lectures publiques ont touché le conteneur public, comme un
> visiteur, via Cloudflare : `GET /openapi.json` et deux `GET /v1/health` (SHA servi `c64ab822`).
>
> **Exécuté le 2026-11-02, 16:21–16:41**, après validation d'Ianis (option A) : voir
> [`RESULTATS-2026-11-02.md`](RESULTATS-2026-11-02.md). Deux écarts ont été validés en cours de route :
> relais `socat` temporaire sur l'hyperviseur au lieu du tunnel SSH vers le conteneur public (qui n'accepte aucune clé), et
> fiche adresse tirée en fond à 1/s (`FICHE=fond`) au lieu d'être dans chaque parcours.

## 1. Objet

Remplacer les estimations « 100 / 1 000 utilisateurs » par des mesures :

- le **débit soutenu**, en parcours/s et en requêtes/s, au p95 sous le seuil ;
- le **point de rupture** ;
- le **goulot** observé : CPU de l'API, connexions PostgreSQL, admission 503, température ;
- ce que cela implique pour **100 professionnels**, et donc pour le chiffrage d'un serveur loué.

## 2. Ce qui est mesuré

Un **parcours** est un agent qui cherche une adresse et ouvre sa fiche, comme le fait le frontend servi
(`c64ab822`) :

| # | Requête | Rôle |
|---|---|---|
| 1 | `GET /v1/adresses/recherche?q=<numéro voie commune>&limite=8` | recherche d'adresse |
| — | pause `THINK_S` = 1 s | l'agent choisit un résultat |
| 2 | `GET /v1/adresse/{ban_id}` | fiche adresse (loader du layout) |
| 3-6 | en parallèle, comme le `Promise.all` du loader de page : `GET /v1/commune?code_insee=`, `GET /v1/adresse/{id}/ventes`, `GET /v1/adresse/{id}/ventes-declarees`, `GET /v1/adresse/{id}/dpe` | fiche commune, DVF (ventes de la parcelle, ventes déclarées), DPE |

Donc **1 parcours/s = 6 requêtes/s**. Les requêtes sont générées à partir de noms de voie génériques et de
communes de Loire-Atlantique et de Vendée ; aucun cas « golden », aucune adresse privée. Aucune URL n'est
écrite dans les sorties (`systemTags` sans `url`).

Fichiers (dossier `perf/k6/`) :

| Fichier | Rôle |
|---|---|
| `parcours-agent.js` | scénario k6 : profils `smoke`, `controle`, `rampe` |
| `surveille.py` | chien de garde : relevés de l'hyperviseur et du conteneur public, arrêt de k6 par son API REST |
| `analyse.py` | lecture par palier de la sortie CSV : débit soutenu, rupture, p95 par route |

## 3. Cible et chemin

- **Générateur de charge : le PC** (k6 v2.3.0, binaire unique `k6.exe`, installé hors du dépôt, somme
  SHA-256 vérifiée contre `k6-v2.3.0-checksums.txt` de la release officielle). **Rien n'est installé sur
  l'hyperviseur ni dans un CT.**
- **Cible principale : l'origine sur le LAN**, sans Cloudflare. La pile publique ne publie rien sur le LAN ;
  le seul point d'entrée local est Caddy sur la boucle locale du CT (`127.0.0.1:8080`). Chemin :
  `PC:18080 → tunnel SSH → conteneur public 127.0.0.1:8080 (Caddy) → api:8000`, en-tête `Host: api.datacommune.fr`.
  C'est le chemin de `cloudflared` moins Cloudflare, et celui des contrôles de `public-deploy.sh`.
- **Tir léger de contrôle via Cloudflare** (`https://api.datacommune.fr`), profil `controle` seulement.

## 4. Quota : exemption de test

L'API applique deux plafonds **par IP visiteur** (`rate_limit.py`, `auth/dependencies.py`) :
120 requêtes/min (intergiciel, toutes clés sauf la clé interne illimitée) et, pour la clé publique
`dc_anon_`, 120/min et 2 000/jour. Depuis une seule IP, le tir serait bloqué à 2 requêtes/s.

**Option A (recommandée) — visiteurs simulés, aucune modification du serveur.** Derrière Caddy, l'API lit
l'IP visiteur dans `CF-Connecting-IP` (`TRUST_PROXY_HEADERS=true`). Sur le chemin boucle locale, k6 envoie
`CF-Connecting-IP` pris dans un parc de `NB_PROS` = 2 000 adresses de `198.18.0.0/16` (plage réservée aux
bancs d'essai, RFC 2544). Chaque agent simulé garde ses propres plafonds, comme un vrai professionnel :
à 32 parcours/s, 5,8 req/min par IP (plafond 120) et environ 46 requêtes/IP sur tout le tir (plafond 2 000).
Le script refuse `SIM_IP=1` et le profil `rampe` hors boucle locale. Sur le chemin Cloudflare, l'en-tête
serait de toute façon écrasé par Cloudflare.

**Clé** : la clé publique `dc_anon_` (publique par conception, incrustée dans le JS du site). Elle est lue
au moment du tir depuis `/opt/datacommune/.env` du conteneur public, dans une variable d'environnement du shell,
jamais affichée, jamais écrite dans un fichier ni dans le script, effacée à la fin (`unset`).

Options écartées : **B** relever `RATE_LIMIT_PER_IP` / `ANON_RATE_LIMIT_PER_IP_*` dans le `.env` puis
redémarrer l'API (deux redémarrages, site public sans plafond pendant le tir) ; **C** clé interne
illimitée (compte admin vérifié et plan interne à créer sur la base publique, comptes coupés).

## 5. Profil de charge et débit maximal

- `rampe` : exécuteur `ramping-arrival-rate`, paliers **1, 2, 4, 6, 8, 12, 16, 24, 32 parcours/s**, chacun
  30 s de montée puis 120 s de maintien. **Débit maximal : 32 parcours/s = 192 requêtes/s** (plafond codé
  dans le script, refus au-delà). Durée maximale : **23 min** ; en pratique le tir s'arrête à la rupture.
  `maxVUs` = 300, délai par requête 30 s. Volume maximal : environ 15 300 parcours, 92 000 requêtes ; avec une
  rupture vers 8 parcours/s : environ 3 000 parcours, 18 000 requêtes.
- `smoke` : 1 VU, 3 parcours (+ 20 recherches de préparation), corps lus et enveloppe `donnees` vérifiée.
- `controle` : 0,25 parcours/s (90 req/min) pendant 2 min, sous le plafond de 120/min d'une IP réelle.

Seuils (verdict) : p95 de `http_req_duration` < `P95_MS` = **1 000 ms** (global et par route), échecs
< 1 %, checks > 99 %. Un palier est **soutenu** si p95 ≤ 1 000 ms, échecs ≤ 1 %, aucune itération perdue
(`dropped_iterations`) et débit atteint ≥ 95 % de la cible. Une `rampe` qui dépasse la capacité franchit
ces seuils par construction (code de sortie 99) ; le résultat se lit par palier (`analyse.py`).

## 6. Critères d'arrêt

| Déclencheur | Seuil | Mécanisme |
|---|---|---|
| Rupture vue par un VU | ≥ 30 % de ses 12 à 30 dernières requêtes refusées (5xx, 429, erreur réseau) ou > 5 s | `exec.test.abort` (code 108) |
| Filet global | `http_req_failed` cumulé > 20 % après 30 s | seuil `abortOnFail` |
| Échecs glissants | ≥ 30 % sur les ≥ 50 dernières requêtes (intervalle de relevé) | `surveille.py` → `PATCH /v1/status` (code 103) |
| Température de l'hyperviseur | CPU ≥ **95 °C** (seuil `PveCpuTemperatureHigh`) | `surveille.py` → arrêt |
| Fenêtre de sauvegarde | dimanche 03:30-06:00 | `surveille.py` → arrêt |
| Décision humaine | Ianis dit stop, ou anomalie hors critères | `Ctrl+C` dans le terminal k6 (arrêt propre) |

Les trois arrêts automatiques ont été démontrés sur un bouchon local (§ 11).

## 7. Préconditions (go / no-go)

Toutes en lecture seule. Un seul « non » = pas de tir, on replanifie.

| # | Contrôle | Commande | Attendu |
|---|---|---|---|
| P1 | Pas dimanche 03:30-06:00 | `date` | hors fenêtre |
| P2 | Aucun import en cours à la base de travail (même test que `public-db-restore.sh`) | `ssh <hyperviseur> 'pct exec <ct-travail> -- systemctl is-active dc-national; pct exec <ct-travail> -- docker ps -q --filter name=dcimport- --filter name=dc-national-'` | `inactive` (ou `unknown`) et liste vide |
| P3 | Pas de bascule nationale ni de déploiement en cours sur le conteneur public | demander aux sessions 04.2 / 04.7 ; `ssh <hyperviseur> 'pct exec <ct-public> -- docker ps --format "{{.Names}} {{.Status}}"'` | conteneurs `Up`, aucune opération annoncée |
| P4 | Hyperviseur au repos | `ssh <hyperviseur> 'for h in /sys/class/hwmon/hwmon*; do echo "$(cat $h/name) $(cat $h/temp1_input)"; done; uptime'` | k10temp < 70000 (70 °C), charge < 2 |
| P5 | SSH direct vers le conteneur public | `ssh -o BatchMode=yes datacommune true` | code 0 (sinon : pas de tunnel, replanifier) |
| P6 | Base servie connue | `curl -s https://api.datacommune.fr/v1/health` | `deployment_sha` noté dans les résultats ; couverture (44/85 ou France) notée |
| P7 | Ianis prévenu de l'alerte `DatacommunePublicDown` et d'un ralentissement visible du site au sommet de la rampe | — | accord explicite |

**Moment.** À la rédaction, l'import national (`batiments`, `foncier_pm`, `cadastre`, `connectivite`,
`rnb_liens`, `dvf`) a été relancé à 11:31Z sur la base de travail : P2 est très probablement « non » aujourd'hui.
Ensuite vient la bascule du conteneur public sur la base nationale (`public-db-restore.sh --from-zfs-clone`).
Mesurer **après la bascule** donne le chiffre utile au chiffrage d'un serveur France entière ;
mesurer **avant** (base 44/85) donne un plancher plus optimiste côté PostgreSQL. Choix d'Ianis (§ 12).

## 8. Commandes, dans l'ordre

Terminal Git Bash sur le PC, à la racine du dépôt. Deux terminaux : **T1** (tunnel puis k6), **T2** (chien de garde).

```bash
# 0. Dossier de sortie hors dépôt (les CSV ne sont pas versionnés)
RUN=C:/tmp/dc-k6-$(date +%Y%m%d-%H%M); mkdir -p "$RUN"; K6=k6   # or the full path of the binary, installed outside the repository
```

```bash
# 1. T1 — tunnel vers la boucle locale de Caddy dans le conteneur public (en arrière-plan)
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -L 18080:127.0.0.1:8080 datacommune &
TUNNEL=$!
curl -s -o /dev/null -w "%{http_code}\n" -H "Host: api.datacommune.fr" http://127.0.0.1:18080/v1/health   # attendu 200
```

```bash
# 2. T1 — clé publique dans l'environnement, jamais affichée
export DC_API_KEY="$(ssh <hyperviseur> "pct exec <ct-public> -- sh -c 'grep -m1 ^PUBLIC_DC_API_KEY= /opt/datacommune/.env | cut -d= -f2-'")"
case "$DC_API_KEY" in dc_anon_*) echo "clé publique chargée";; *) echo "clé absente : stop"; unset DC_API_KEY;; esac
```

```bash
# 3. T1 — smoke sur l'origine (attendu : code 0)
PROFILE=smoke SIM_IP=1 BASE_URL=http://127.0.0.1:18080 HOST_HEADER=api.datacommune.fr \
  "$K6" run --no-usage-report --summary-export="$RUN/smoke.json" perf/k6/parcours-agent.js 2>&1 | tee "$RUN/smoke.log"
```

```bash
# 4. T1 — contrôle léger via Cloudflare, depuis l'IP de la maison (2 min, 90 req/min ; ne pas naviguer sur le site pendant ces 2 min)
PROFILE=controle BASE_URL=https://api.datacommune.fr \
  "$K6" run --no-usage-report --summary-export="$RUN/controle.json" perf/k6/parcours-agent.js 2>&1 | tee "$RUN/controle.log"
```

```bash
# 5. T2 — chien de garde (relevés toutes les ~5 s, arrêt automatique)
python perf/k6/surveille.py --out "$RUN/surveillance.tsv" --duree-max 1800 | tee "$RUN/surveillance.log"
```

```bash
# 6. T1 — rampe sur l'origine
date -u +%Y-%m-%dT%H:%M:%SZ > "$RUN/debut.txt"
K6_CSV_TIME_FORMAT=unix_milli PROFILE=rampe SIM_IP=1 BASE_URL=http://127.0.0.1:18080 HOST_HEADER=api.datacommune.fr \
  "$K6" run --no-usage-report -a 127.0.0.1:6565 --out csv="$RUN/rampe.csv.gz" --summary-export="$RUN/rampe.json" \
  perf/k6/parcours-agent.js 2>&1 | tee "$RUN/rampe.log"
```

```bash
# 7. T1 — retour à la normale (attendu : 200 puis smoke code 0, après 2 min de repos)
curl -s -o /dev/null -w "%{http_code}\n" -H "Host: api.datacommune.fr" http://127.0.0.1:18080/v1/health
PROFILE=smoke SIM_IP=1 BASE_URL=http://127.0.0.1:18080 HOST_HEADER=api.datacommune.fr \
  "$K6" run --no-usage-report perf/k6/parcours-agent.js 2>&1 | tee "$RUN/smoke-apres.log"
```

```bash
# 8. T1 — journaux de l'API pendant le tir (lecture) : refus d'admission, erreurs 500, pool
ssh <hyperviseur> "pct exec <ct-public> -- sh -c 'docker logs --since $(cat "$RUN/debut.txt") \$(docker ps -q --filter name=datacommune-public-api) 2>&1 | grep -ciE \"admission|unhandled|QueuePool|TimeoutError\"'"
```

```bash
# 9. T1 — fin : clé effacée, tunnel fermé, analyse
unset DC_API_KEY; kill "$TUNNEL"
python perf/k6/analyse.py "$RUN/rampe.csv.gz" --p95-ms 1000 | tee "$RUN/analyse.md"
```

## 9. Retour arrière

Le tir ne modifie **aucune configuration** : pas de `.env`, pas de redémarrage, pas de clé créée.

1. Arrêt : `Ctrl+C` dans T1 (ou arrêt automatique), puis `kill "$TUNNEL"`. La charge cesse aussitôt.
2. Contrôle : étape 7 (`/v1/health` 200, smoke code 0). Les plafonds en mémoire ne concernent que les IP
   simulées `198.18.*` et expirent seuls (60 s / 24 h).
3. Si l'API reste dégradée **plus de 5 min** après l'arrêt (health ≠ 200 ou smoke rouge) : diagnostic en
   lecture (journaux, `pg_stat_activity`), puis **sur accord explicite d'Ianis seulement**, redémarrage du
   seul conteneur API : `ssh <hyperviseur> "pct exec <ct-public> -- sh -c 'docker restart \$(docker ps -q --filter name=datacommune-public-api)'"`.

## 10. Effets de bord

- **`api_request_log`** : une ligne par requête (≤ 92 000), `client_ip` dans `198.18.0.0/16`, donc
  identifiables. La purge à 7 jours ne tourne pas encore sur le conteneur public (HANDOFF, C1). Comptage après tir
  (lecture) : `SELECT count(*) FROM api_request_log WHERE client_ip << '198.18.0.0/16'::inet;`.
  Suppression éventuelle : écriture sur la base publique, **sur décision d'Ianis** (§ 12).
- **`quota_usage`** de la clé `dc_anon_` : compteur de supervision du mois augmenté du même nombre.
- **Alerte `DatacommunePublicDown`** : la sonde `obs` (via Cloudflare, délai 8 s) échoue si l'API ne
  répond plus ; elle n'alerte qu'après **5 min** d'échec continu. L'arrêt à la rupture rend ce cas peu
  probable, mais possible si l'API ne se rétablit pas (message Telegram). **Ianis prévenu (P7).**
- **Visiteurs réels** : ralentissement possible au sommet de la rampe (quelques minutes). Créneau creux
  conseillé, en semaine, en soirée.
- **`PveCpuTemperatureHigh`** : 95 °C pendant 15 min ; le chien de garde arrête à 95 °C bien avant.
- GoatCounter et le frontend ne sont pas sollicités (API seule).

## 11. Ce qui est déjà vérifié (local, sans le conteneur public)

Bouchon HTTP local (`127.0.0.1`, script Python jetable du scratchpad, clé factice), k6 v2.3.0 :

| Contrôle | Résultat |
|---|---|
| `k6 inspect` des trois profils | `smoke` 1 VU / 2 min 30 ; `rampe` 300 VU max / 23 min ; `controle` 10 VU / 2 min 30 ; 9 seuils |
| Garde-fous (avant toute requête) | refus : `rampe` hors boucle locale, `SIM_IP=1` hors boucle locale, palier 64 > 32, clé absente |
| `smoke` | code 0, 38 requêtes (20 de préparation + 3 × 6), checks 36/36 ; bouchon : `Host: api.datacommune.fr`, 4 IP simulées, clé reçue |
| `rampe` courte (2, 4, 8 parcours/s) + `analyse.py` | code 0 ; 2,08 / 4,03 parcours/s atteints aux paliers 2 et 4 ; `SUSTAINED=8 BREAK=none` |
| Arrêt « rupture » (bouchon en 503 après 160 requêtes) | **avant correction** : aucun arrêt sur 538 refus (fenêtre par VU jamais pleine, VU dispersés) ; **après** : arrêt code 108 au 4e refus |
| Analyse d'une rupture | palier 2 `soutenu`, palier 4 `dégradé` (503 : 190), `SUSTAINED=2 BREAK=4` |
| Chien de garde, température simulée 96 °C | k6 arrêté par l'API REST (code 103), résumé écrit |
| Chien de garde, échecs glissants | arrêt à 79 % sur 72 requêtes (code 103) |
| Boucle distante de `surveille.py` | `bash -n` et `shellcheck` 0.10.0 propres ; envoi en binaire (un tube texte Windows l'aurait envoyée en CRLF) |

Non vérifié avant validation : SSH direct vers le conteneur public, le chemin tunnel/Caddy, la clé réelle, la
lecture `psql` dans le conteneur `db`, le capteur k10temp de l'hyperviseur, et tout chiffre de capacité.

## 12. Décisions demandées à Ianis

1. **Valider ce plan** (ou le corriger) : cible, débit maximal 32 parcours/s, durée ≤ 23 min, critères d'arrêt.
2. **Exemption de quota** : option A (IP simulées, aucune modification) — ou B.
3. **Moment** : après la fin de l'import et la bascule nationale du conteneur public (recommandé), ou dès la fin de
   l'import sur la base 44/85.
4. **Seuil p95** : 1 000 ms par requête (une fiche fait 2 allers-retours, soit ~2 s de chargement au p95).
5. **Lignes de test dans `api_request_log`** : les garder (identifiables) ou les supprimer après le tir.

## 13. Lecture pour 100 professionnels (remplie dans `RESULTATS-2026-11-02.md`, § 7)

Modèle de demande, hypothèses à confirmer par Ianis : un agent actif ouvre au plus **1 fiche/min** en
prospection ; 100 agents actifs en même temps = **1,7 parcours/s ≈ 10 req/s** ; avec un facteur de pointe
×3, **5 parcours/s ≈ 30 req/s**. Le résultat se compare à ce besoin : débit soutenu mesuré / 5 = marge.
Hors mesure : le rendu serveur du frontend (Node, mêmes 4 cœurs du conteneur public), qui ajoute sa propre charge
à chaque page ; Cloudflare ; la base nationale si le tir a lieu avant la bascule.
