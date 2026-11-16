# Résultats du tir k6 — API publique (conteneur public), 2026-11-02

Tir exécuté le lundi 2026-11-02 de 16:21 à 16:41 (heure de Paris), selon `PLAN-TIR.md` validé par Ianis
(option A), avec deux écarts qu'il a validés en cours de route (§ 3). Heures en heure de Paris, sauf
mention « UTC ».

## 1. En bref

| Question | Réponse mesurée |
|---|---|
| **Débit soutenu** (critère du plan : p95 ≤ 1 s sur les requêtes de la rampe, échecs ≤ 1 %, aucune itération perdue, ≥ 95 % du débit visé) | **1 parcours/s** (5 req/s sur les routes servies par la base), **avec 1 fiche adresse/s en fond**. Ce palier ne passe que sur le p95 global : la **recherche** y a déjà un **p95 de 7,0 s**. Route par route, **aucun palier n'est soutenu**. |
| **Point de rupture** | **2 parcours/s** (10 req/s + 1 fiche/s) : 1,17 % d'échecs, 2 itérations perdues, recherche p95 15,3 s. Arrêt automatique à 16:39:08, au palier 8 parcours/s : 31 % des 13 dernières requêtes d'un VU refusées ou plus lentes que 5 s. |
| **Goulot** | **CPU de PostgreSQL**. Le cgroup du conteneur public reste à **3,97–3,99 cœurs sur 4** de la montée à l'arrêt ; le conteneur `db` est à 340–370 %, l'API Python à 13–65 %. Le CPU part surtout dans **deux requêtes de la fiche adresse**, planifiées sur les 103 partitions départementales avec compilation JIT, et dans le **repli national de la recherche par trigrammes** (§ 5). |
| **Conséquences visibles** | 503 « Admission de publication indisponible » (budget de 250 ms dépassé quand la base est saturée), 503 sur la recherche, 500 `out of shared memory` (table de verrous de PostgreSQL). |
| **Température** | L'hyperviseur se cale à 93,1–93,6 °C dès la première minute et y reste. Le seuil d'arrêt de 95 °C n'a pas été atteint. |
| **100 professionnels** | **Non tenu** sur le conteneur public tel qu'il est configuré : la demande moyenne estimée (1,7 parcours/s, une fiche chacun) dépasse déjà ce qui sature les 4 cœurs (§ 7). |

## 2. Conditions

| Élément | Valeur |
|---|---|
| Cible | conteneur public `datacommune-public` sur l'hyperviseur : 4 cœurs, 12 Gio de RAM ; API `c64ab822` (`deployment_sha` de `/v1/health`) ; uvicorn 1 worker ; pool SQLAlchemy 10 + 20 |
| Base | nationale, clone ZFS `pg-20261102T131205Z`, bascule vérifiée à 14:15 ; 144 Go ; PostgreSQL 17.5 ; `shared_buffers` 2 Gio, `work_mem` 16 Mio, `max_connections` 100, `max_locks_per_transaction` 64, `jit` activé (`jit_above_cost` 100 000), 2 workers parallèles par nœud Gather ; 4 841 partitions (103 par table nationale) |
| Cache | froid : bascule 2 h avant le tir |
| Générateur | k6 v2.3.0 sur le PC (`k6.exe`, installé hors du dépôt, SHA-256 vérifié), jamais sur l'hyperviseur |
| Chemin | PC → `ssh -L` vers l'hyperviseur → `socat` sur `127.0.0.1:18081` de l'hyperviseur → `nsenter` dans le réseau du conteneur public → Caddy `127.0.0.1:8080` (Host `api.datacommune.fr`) → API |
| Quota | IP simulées `CF-Connecting-IP` en 198.18.0.0/16 (option A) : **0 réponse 429** sur 12 285 requêtes journalisées |
| Profil | `PROFILE=rampe FICHE=fond FICHE_PAR_S=1` ; paliers 1, 2, 4, 6, 8… parcours/s ; 30 s de montée puis 120 s de palier ; parcours = recherche, 1 s de réflexion, puis commune, ventes, ventes déclarées et DPE en parallèle ; fond = recherche puis fiche, 1 par seconde, depuis 198.18.254.0/24 |
| Préconditions | P1–P4 revérifiées à 16:27:37 : aucun import (unité `dc-national` inactive, 0 conteneur, 0 script), 52,5 °C, charge 1,10. Un relevé à 74,9 °C à 16:27:14 (pic passager après le smoke et le contrôle) avait suspendu le départ. |

## 3. Écarts au plan validé

1. **Chemin.** Le tunnel `ssh -L … datacommune` (§ 8 étape 1) est impossible : le conteneur public n'a aucune clé
   autorisée (accès par `pct exec` seulement), et l'alias `datacommune` du `~/.ssh/config` du PC vise un
   ancien hôte du réseau local. Ianis a choisi un **relais temporaire sur l'hyperviseur** : `socat` lié au loopback
   de l'hyperviseur et `nsenter` dans le réseau du conteneur public, avec un arrêt automatique au bout de 50 min. Aucun fichier
   ni réglage n'a été modifié. Le relais a été arrêté à 16:41 ; il ne restait ensuite ni processus ni port
   ouvert. Coût mesuré : < 1 % de CPU sur l'hôte (§ 5.2).
2. **Fiche adresse.** Le smoke a montré qu'une fiche met ~2 s à vide et appelle Géorisques et l'IGN (GPU) en
   direct, sans cache (~5 appels sortants par fiche). Ianis a choisi de garder les **5 routes locales dans
   la rampe** et de tirer la **fiche en fond à débit fixe** (1/s), hors verdict, pour ne pas faire un test
   de charge de services publics tiers. Le script a reçu l'option `FICHE=fond`, testée sur bouchon avant le tir.
3. **Analyse.** Après le tir, j'ai corrigé un biais de mesure dans `analyse.py`. Le débit atteint était divisé
   par l'écart entre le premier et le dernier échantillon du palier. Or les itérations lentes (jusqu'à 30 s)
   débordent sur le palier suivant et allongent cette fenêtre. Le débit se calcule désormais sur la durée
   nominale du palier (120 s, coupée à la fin du test). Les itérations perdues sont comptées sur la même
   fenêtre. Sur la mini-rampe du bouchon (paliers de 15 s), les débits restent 2,00 et 4,00 parcours/s. Sur
   ce tir, le palier 1 passe de 0,88 à 1,00 parcours/s, et le verdict de `BREAK=1` à `SUSTAINED=1 BREAK=2`.
   Les latences et les échecs ne changent pas.

## 4. Mesures

### 4.1 Smoke et contrôle (avant la rampe)

| Tir | Requêtes | Échecs | Checks | p95 par route | Code |
|---|---|---|---|---|---|
| Smoke origine, 16:21 | 38 | 0 | 36/36 | recherche 168 ms, fiche 2,39 s, commune 26 ms, ventes 31 ms, ventes déclarées 208 ms, DPE 42 ms | 99 (seuil franchi par la fiche) |
| Contrôle via Cloudflare, 16:24–16:26, IP de la maison | 206 | 0 | 186/186 | recherche **3,52 s** (médiane 148 ms, max 8,66 s), fiche 2,89 s, commune 105 ms, ventes 114 ms, ventes déclarées 164 ms, DPE 155 ms | 99 (fiche et recherche) |

### 4.2 Rampe, 16:27:56–16:39:08 (`k6 run`, code 108 : arrêt par le critère de rupture)

Totaux k6 : 12 067 requêtes, 3,10 % d'échecs, 2 768 itérations, 84 itérations perdues, 368 refus.

Analyse par palier (`python perf/k6/analyse.py rampe.csv.gz --p95-ms 1000 --palier-s 120`) :

| palier (parcours/s) | atteint parcours/s | req/s | p50 ms | p95 ms | p99 ms | échecs | refus par statut | itérations perdues | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 | 5.0 | 127 | 340 | 7023 | 0.50% | {'503': 3} | 0 | soutenu |
| 2 | 1.99 | 10.0 | 137 | 548 | 15304 | 1.17% | {'503': 14} | 2 | dégradé |
| 4 | 3.98 | 19.9 | 218 | 1193 | 15623 | 1.64% | {'503': 39} | 4 | dégradé |
| 6 | 5.89 | 29.5 | 295 | 1610 | 15780 | 3.59% | {'503': 111, '500': 16} | 16 | dégradé |
| 8 | 3.67 | 21.9 | 3469 | 7279 | 10382 | 11.59% | {'503': 67} | 40 | dégradé |

Le palier 8 est tronqué : 28 s avant l'arrêt, et les itérations encore en cours ne sont pas comptées.

p95 par route (ms) :

| palier | recherche | fiche_adresse (fond) | commune | ventes | ventes déclarées | DPE | recherche du fond |
|---|---|---|---|---|---|---|---|
| 1 | 7023 | 3418 | 177 | 243 | 244 | 241 | 9820 |
| 2 | 15304 | 6685 | 235 | 313 | 320 | 778 | 15516 |
| 4 | 15623 | 9395 | 443 | 703 | 542 | 2822 | 12008 |
| 6 | 15780 | 12300 | 730 | 987 | 859 | 2448 | 15902 |
| 8 | 8624 | 17227 | 7224 | 6954 | 7014 | 8047 | 6716 |

Fiches en fond (hors verdict) : 1,00 puis 0,98 fiche/s ; p50 de 2,0 s au palier 1 à 7,4 s au palier 6 ;
p95 de 3,4 s à 12,3 s ; 1,7 % à 5,9 % d'échecs (503 de la recherche du fond).

Refus par route sur toute la rampe : recherche 503 × 180 (+ 53 dans le fond), DPE 503 × 97, ventes
déclarées 503 × 15, ventes 503 × 13 ; 500 × 17 (commune 12, ventes déclarées 2, ventes 1, recherche 1,
DPE 1).

### 4.3 Reprise

- `/v1/health` : 200 en 13 ms à 16:39:23 (origine) et 200 en 0,20 s via Cloudflare.
- Smoke après 2 min de repos (16:40:56) : 38 requêtes, 0 échec, 36/36. p95 : recherche 247 ms, routes
  locales 52–84 ms, fiche 1,27 s. Code 99, à cause de la fiche seule.
- Aucune relance de conteneur, aucun réglage modifié.

## 5. Goulot observé

### 5.1 Ressources (chien de garde `surveille.py`, échantillonneur cgroup)

| palier | cœurs du conteneur public (cgroup, moyenne) | `db` docker stats (moyenne) | `api` (moyenne) | température max de l'hyperviseur |
|---|---|---|---|---|
| montée (fond seul + début de rampe) | 3,97 | — | — | 93,1 °C |
| 1 | 3,74 (max 3,98) | 341 % | 13 % | 93,5 °C |
| 2 | 3,98 | 372 % | 19 % | 93,5 °C |
| 4 | 3,98 | 361 % | 33 % | 93,5 °C |
| 6 | 3,98 | 356 % | 42 % | 93,6 °C |
| 8 | 3,98 | 316 % | 21 % | 93,5 °C |
| après l'arrêt | 0,10 | 0 % | 0 % | 86,4 °C puis baisse |

Sessions PostgreSQL sur les 125 relevés de la rampe : en moyenne **14,7 actives sur le CPU** (max 33),
2,5 en attente d'E/S (max 8), 0,7 en attente `LWLock` (max 8). Au moment de l'arrêt : 18 sur le CPU,
4 `LWLock`, 2 E/S, 10 inactives, et une charge de 30 sur l'hyperviseur. **Le tir est limité par le CPU, pas par le
disque**, malgré le cache froid.

Coût du relais : `socat`, `sshd` et `nsenter` restent sous 0,6 % de CPU sur l'hôte, avec jusqu'à 637
processus `socat` au palier 8. Le relais ne fausse pas la mesure.

### 5.2 Où part le CPU (`pg_stat_statements`, remis à zéro à 13:14 UTC juste après la bascule)

| Requête | Bloc | Appels | Moyenne | JIT par appel | Part du temps PostgreSQL |
|---|---|---|---|---|---|
| Arrêts de transport à 1 km (`transport/queries.py`) | fiche | 708 | 1,82 s | 353 ms | **34,7 %** |
| Bâtiments de la parcelle (`adresse/relations.py`) | fiche | 505 | 2,09 s | 2 224 ms | **28,4 %** |
| Recherche dans la commune (`word_similarity`) | recherche | 2 856 | 137 ms | 0 | 10,5 % |
| Recherche nationale par trigrammes (`search_text % …`) | recherche, repli | 26 | **10,4 s** (max 14,8 s) | 0 | 7,3 % |
| Résolution du nom de commune (`communes_lookup`) | recherche | 3 915 | 45 ms | 0 | 4,7 % |
| Ventes isolées (`mutation_parcelles`) | ventes | 519 | 242 ms | 244 ms | 3,4 % |

Ce que montrent les plans, lus avec `EXPLAIN` sans exécution :

- **Transports** : parcours séquentiel parallèle des 365 544 `arrets_transport` et parcours séquentiel des
  `gares`, compilation JIT avec inlining et optimisation (coût estimé 1,97 M). Le filtre
  `ST_DWithin(position::geography, …)` ne peut pas utiliser l'index GiST `idx_arrets_position`, posé sur
  `position` en geometry.
- **Bâtiments de la parcelle** : le plan générique (celui que réutilise un statement préparé) couvre les
  **103 partitions** de `parcelles` et les **103** de `batiments`, sans élagage à la planification
  (`Subplans Removed` absent). La jointure prend `code_departement` dans la CTE et non dans le paramètre.
  Le JIT compile **1 241 fonctions** avec inlining et optimisation.

Les deux requêtes de la fiche représentent **63 % du temps PostgreSQL**. À 1 fiche/s pendant la montée, le
conteneur public est déjà à 3,97 cœurs : **une fiche par seconde suffit à saturer les 4 cœurs**.

### 5.3 Erreurs de l'API (`docker logs` du conteneur API depuis le début du tir)

| Événement | Nombre | Lecture |
|---|---|---|
| `geocode_query_failed` (recherche → 503) | 244 | requête de géocodage en échec sous saturation (avant le tir : 3 en 2 h) |
| `Admission de publication indisponible` (DPE → 503, cause `TimeoutError`) | 107 | chaque lecture publiée emprunte une seconde connexion et doit poser verrou + contrôle en 250 ms ; base saturée, budget dépassé |
| `unhandled_exception` `asyncpg OutOfMemoryError: out of shared memory` (→ 500) | 17 | table de verrous pleine : `max_locks_per_transaction` 64 × 100 connexions, face à des plans qui verrouillent 206 partitions et leurs index |
| `address_parcel_read_failed` | 27 | blocs de la fiche en panne (15:36–15:38, palier 6) |
| `georisques_response_unparseable` | 62 | 5 à 10 par minute pendant tout le tir (avant le tir : 3 en 2 h) |
| `apicarto_response_error` | 4 | IGN GPU |
| `request_log_failed` | 7 | écriture de `api_request_log` en échec (15:36:41) |
| `QueuePool` (pool épuisé) | 0 | le pool SQLAlchemy n'a pas été le goulot |

Après le tir, un seul appel `GET https://georisques.gouv.fr/api/v1/gaspar/risques?code_insee=44109`,
lancé depuis la même IP publique, renvoie 200 en JSON en 0,13 s : aucun blocage durable n'est visible.

## 6. Ce qui n'est pas mesuré

- Le **rendu serveur du frontend** (Node, mêmes 4 cœurs du conteneur public). Chaque page vue l'ajoute à la charge
  mesurée ici.
- **Cloudflare sous charge** : seul le contrôle léger (0,25 parcours/s) est passé par Cloudflare.
- La **fréquence réelle du CPU** de l'hyperviseur à 93,5 °C : un bridage thermique est possible, non mesuré.
- La **part des services tiers** dans la latence de la fiche.
- Un **cache chaud** : premier tir après la bascule. Les attentes d'E/S faibles laissent penser que le cache
  pèse peu, sans preuve.
- L'alerte **`DatacommunePublicDown`** : je n'ai consulté ni Prometheus ni Alertmanager. Je ne sais donc
  pas si elle s'est déclenchée pendant les 8 min de paliers dégradés (16:31–16:39).

## 7. Lecture pour 100 professionnels

Modèle de demande du plan (§ 13, hypothèses d'Ianis à confirmer) : un agent actif ouvre au plus 1 fiche par
minute. 100 agents en même temps, c'est **1,7 parcours/s ≈ 10 req/s, dont 1,7 fiche/s**. Avec un facteur
de pointe ×3 : **5 parcours/s ≈ 30 req/s, dont 5 fiches/s**.

- **Établi** : à 1 fiche/s plus 1 parcours/s, PostgreSQL occupe déjà les 4 cœurs du conteneur public et la recherche a
  un p95 de 7 s. À 2 parcours/s, le critère du plan casse.
- **Déduit** : la demande moyenne de 100 agents (1,7 fiche/s) dépasse ce que le conteneur public tient avec les
  requêtes actuelles, et la pointe (5 fiches/s) le dépasse d'environ 5 fois. La marge prévue par le plan
  (débit soutenu / 5) vaut **0,2**.
- **Déduit, FRAGILE** : chiffrer un serveur loué sur ces requêtes demanderait de l'ordre de 5 fois le CPU
  actuel rien que pour PostgreSQL. Ce serait chiffrer des requêtes à corriger, pas un besoin.
- **Hypothèses, à vérifier par un second tir** avec le même script (`PROFILE=rampe FICHE=fond`), dans l'ordre
  du gain attendu :
  1. requête des transports : un index sur `(position::geography)` ou un filtre sur la geometry indexée
     ferait sauter le parcours séquentiel (PROBABLE) ;
  2. requête des bâtiments : passer le département en paramètre de la jointure (`b.code_departement = :department`)
     permettrait l'élagage à une partition, et donc moins de verrous et un plan plus petit (PROBABLE) ;
  3. `jit = off`, ou des seuils JIT relevés, pour une API transactionnelle : la compilation coûte 0,35 à 2,2 s
     par appel (PROBABLE) ;
  4. le repli national de la recherche par trigrammes, à 10 s par appel, à borner ou à éviter (PROBABLE) ;
  5. `max_locks_per_transaction` plus haut contre les 500 `out of shared memory`, inutile si le point 2
     réduit les verrous (FRAGILE).

## 8. Effets de bord

- `api_request_log` : **12 285 lignes** du bloc 198.18.0.0/16 (15:21–15:40 UTC), dont 401 en 5xx et 0 en
  429. Elles sont **conservées** : Ianis a tranché la décision 5 du § 12 du plan après le tir (2026-11-02).
  Pour les compter ou les exclure d'une statistique : `client_ip << '198.18.0.0/16'::inet`.
- `quota_usage` : le compteur de la clé publique `dc_anon_` du mois a été incrémenté d'autant.
- Services tiers : environ 650 fiches pendant le tir, soit de l'ordre de 3 000 appels vers Géorisques et
  l'IGN en 11 min, dont 62 réponses illisibles de Géorisques et 4 erreurs de l'IGN.
- Visiteurs réels : le site public a été lent pendant le tir (16:28–16:39). La reprise a été immédiate.
- Clé publique : lue dans le `.env` du conteneur public pour chaque commande, puis effacée. Elle ne figure dans aucun
  fichier ni journal (`grep -c dc_anon_` vaut 0 sur tous les journaux et résumés k6).

## 9. Preuves et reproduction

Sorties brutes, non versionnées : `C:/tmp/dc-k6-20261102-1616/`.

| Fichier | Contenu |
|---|---|
| `smoke.log`, `smoke.json` | smoke sur l'origine |
| `controle.log`, `controle.json` | contrôle via Cloudflare |
| `debut.txt`, `fin.txt` | `2026-11-02T15:27:56Z` – `2026-11-02T15:39:08Z` |
| `rampe.log`, `rampe.json`, `rampe.csv.gz` | rampe (CSV `unix_milli`, sans URL) |
| `analyse.md` | sortie d'`analyse.py` (§ 4.2) |
| `surveillance.tsv`, `surveillance.log` | chien de garde (température, CPU des conteneurs, sessions PostgreSQL) |
| `relais-cgroup.tsv` | cœurs du cgroup du conteneur public et CPU du relais, toutes les 15 s |
| `api-logs.txt` | journaux de l'API depuis le début du tir |
| `smoke-apres.log` | smoke de reprise |

Commande de la rampe (Git Bash, racine du dépôt, relais ouvert sur `127.0.0.1:18080`) :

```bash
K6_CSV_TIME_FORMAT=unix_milli PROFILE=rampe FICHE=fond FICHE_PAR_S=1 SIM_IP=1 \
  BASE_URL=http://127.0.0.1:18080 HOST_HEADER=api.datacommune.fr \
  k6 run --no-usage-report -a 127.0.0.1:6565 --out csv="$RUN/rampe.csv.gz" \
  --summary-export="$RUN/rampe.json" perf/k6/parcours-agent.js
```

Relais utilisé (§ 3, point 1) :

```bash
ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -L 18080:127.0.0.1:18081 <hyperviseur> \
  'exec timeout 3000 socat TCP-LISTEN:18081,bind=127.0.0.1,reuseaddr,fork "EXEC:nsenter -t <pid init du conteneur public> -n socat STDIO TCP\:127.0.0.1\:8080"'
```

Le PID d'init se lit avec `lxc-info -n 103 -p -H`. Pour arrêter le relais :
`ssh <hyperviseur> "pkill -f '[T]CP-LISTEN:18081'"`.
