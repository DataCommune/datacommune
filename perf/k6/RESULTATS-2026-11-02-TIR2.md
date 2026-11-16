# Résultats du second tir k6 — API publique (conteneur public), 2026-11-02 au soir

Tir exécuté le lundi 2026-11-02 de 20:35 à 20:53 (heure de Paris ; 19:35:26–19:53:47 UTC), avec l'accord
d'Ianis (« Yes, fire »), même procédure que le premier tir ([RESULTATS-2026-11-02.md](RESULTATS-2026-11-02.md),
§ 9) : relais `socat` sur l'hyperviseur, `PROFILE=rampe FICHE=fond FICHE_PAR_S=1 SIM_IP=1`. Il mesure le conteneur public après
le déploiement des correctifs de la fiche adresse (quick `261102-p8x`, SHA servi `55751651` = `c64ab822` +
préfiltre des transports, élagage de `batiments`, budget de la stratégie D).

## 1. En bref

| Question | Premier tir (16:21, `c64ab822`) | Second tir (20:35, `55751651`) |
|---|---|---|
| **Débit soutenu** (p95 ≤ 1 s, échecs ≤ 1 %, aucune itération perdue, ≥ 95 % du débit visé) | 1 parcours/s | **6 parcours/s** (30 req/s), avec 1 fiche/s en fond |
| **Point de rupture** (`analyse.py`) | 2 parcours/s | 8 parcours/s (p95 920 ms, échecs 0,56 %, mais 4 itérations perdues) |
| Débit encore atteint | 3,67 parcours/s au palier 8, arrêt automatique | 11,95 et 15,88 parcours/s aux paliers 12 et 16 (p95 1,15 et 1,32 s) ; arrêt automatique au palier 24 |
| CPU du conteneur public à 1 parcours/s + 1 fiche/s | 3,74 cœurs sur 4 | **0,66 cœur** |
| Fiche adresse, p95 (fond, palier 1) | 3 418 ms | **603 ms** |
| Transports à 1 km (`pg_stat_statements`) | 1 820 ms par appel, 34,7 % du temps PostgreSQL, JIT 353 ms | **4,3 ms**, 0,27 %, sans JIT |
| Bâtiments de la parcelle | 2 090 ms, 28,4 %, JIT 2 224 ms | **1,2 ms**, 0,07 %, sans JIT |
| `out of shared memory` (500) | 17 | **0** |
| Température max de l'hyperviseur | 93,6 °C | 93,6 °C |
| **100 professionnels** (1,7 parcours/s en moyenne, 5 en pointe) | non tenu, marge 0,2 | **tenu en moyenne ; pointe dans le débit soutenu (marge 6 / 5 = 1,2)**, hors charge de fiches en pointe (§ 7) |

## 2. Conditions

| Élément | Valeur |
|---|---|
| Cible | conteneur public `datacommune-public`, 4 cœurs, 12 Gio ; API `55751651` (`/__version` vérifié avant et via le relais), déployée à 18:56:27 UTC ; uvicorn 1 worker ; pool 10 + 20 |
| Base | nationale, clone ZFS `dcpool/public/pg-20261102T131205Z` (inchangée depuis le premier tir) ; PostgreSQL 17.5, mêmes réglages (`jit` activé) |
| Cache | **tiède** : base basculée à 14:15, premier tir à 16:21. Biais en faveur du second tir, d'ampleur inconnue ; les gains des deux requêtes corrigées viennent du plan (parcours séquentiel → index, 103 partitions → 1), pas du cache |
| Générateur, chemin | identiques au premier tir : k6 v2.3.0 sur le PC, relais `socat` lié au loopback de l'hyperviseur + `nsenter` dans le réseau du conteneur public (PID d'init 1494664), `timeout 3000` ; arrêté à la fin, 0 processus et 0 port restants |
| Profil | `PROFILE=rampe FICHE=fond FICHE_PAR_S=1 SIM_IP=1`, paliers 1, 2, 4, 6, 8, 12, 16, 24… ; 30 s de montée, 120 s de palier |
| Préconditions | P1 lundi ; P2 `dc-national` inactif, 0 conteneur d'import ; P3 conteneurs `Up`, déploiement terminé ; P4 un relevé à 73,9 °C a suspendu le départ, puis 56,9 °C et charge 0,71 ; P6 `55751651`, base France |
| Chien de garde, échantillonneur | `surveille.py` (température, conteneurs, sessions PG) ; cgroup du conteneur public toutes les 15 s |

## 3. Écarts au plan et au premier tir

1. **Commune « ancenis » remplacée par « ancenis-saint-gereon »** (choix d'Ianis, « Remplacer par
   ancenis-saint-gereon »). Avec le budget de 2 s de la stratégie D, « 1 rue de la mairie ancenis » répond 503
   en 2,1 s : Ancenis a fusionné en 2019, la commune n'est plus détectée, la recherche part dans le repli
   national et le budget la coupe. Le setup de k6 s'arrête au premier refus : le smoke n'a fait que 12
   requêtes. Au premier tir, ces recherches aboutissaient en ~10 s (les 26 appels à 10,4 s du § 5.2).
2. Restent dans le mélange : **9 combinaisons sur 300** (« 12 <voie> les sables-d olonne », 9 voies sur 15)
   répondent 503 en 2,1–2,25 s pour la même raison (commune non détectée, plein texte vide, repli D coupé).
   Elles expliquent l'essentiel des 503 de la recherche (§ 5.3).
3. **`pg_stat_statements` lu avant et après** (différence par `queryid`, lecture seule) au lieu d'une remise à
   zéro. La fenêtre 19:05–19:54 UTC inclut le smoke, le contrôle et 320 recherches de diagnostic.
4. Le relais a été rouvert à 19:30 UTC : celui de 19:05 se serait coupé (50 min) pendant la rampe.

## 4. Mesures

### 4.1 Smoke et contrôle

| Tir | Requêtes | Échecs | Checks | p95 par route | Code |
|---|---|---|---|---|---|
| Smoke origine, 20:31 | 38 | 0 | 36/36 | recherche 110 ms, **fiche 486 ms**, commune 78 ms, ventes 43 ms, ventes déclarées 61 ms, DPE 72 ms | 0 |
| Contrôle via Cloudflare, vers 20:32–20:34, IP de la maison | 200 | 2 (1,00 %) | 178/180 | recherche 1,31 s (max 2,19 s : les 2 refus, budget D), fiche 602 ms, commune 100 ms, ventes 265 ms, ventes déclarées 277 ms, DPE 105 ms | 99 (échecs à 1,00 %, recherche) |
| Smoke de reprise, 20:55 | 38 | 0 | 36/36 | recherche 70 ms, fiche 359 ms | 0 |

### 4.2 Rampe, 19:35:26–19:53:47 UTC (code 108 : arrêt par le critère de rupture, au palier 24)

Totaux k6 : 40 822 requêtes, 1,22 % d'échecs, 8 746 itérations, 238 perdues, 472 refus.

| palier (parcours/s) | atteint | req/s | p50 ms | p95 ms | p99 ms | échecs | refus | itérations perdues | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 | 5.0 | 79 | 170 | 362 | 0.00% | - | 0 | soutenu |
| 2 | 2.00 | 10.0 | 82 | 180 | 427 | 0.75% | 503 × 9 | 0 | soutenu |
| 4 | 4.00 | 20.0 | 86 | 218 | 456 | 0.88% | 503 × 21 | 0 | soutenu |
| 6 | 6.00 | 30.0 | 92 | 344 | 713 | 0.72% | 503 × 26 | 0 | **soutenu** |
| 8 | 7.97 | 39.8 | 193 | 920 | 1279 | 0.56% | 503 × 27 | 4 | dégradé |
| 12 | 11.95 | 59.8 | 432 | 1152 | 1682 | 0.59% | 503 × 42 | 6 | dégradé |
| 16 | 15.88 | 79.4 | 488 | 1316 | 2544 | 0.66% | 503 × 63 | 15 | dégradé |
| 24 | 3.93 | 38.1 | 3973 | 8924 | 10763 | 15.47% | 503 × 99 | 116 | dégradé (tronqué) |

p95 par route (ms) :

| palier | recherche | fiche (fond) | commune | ventes | ventes déclarées | DPE | recherche du fond |
|---|---|---|---|---|---|---|---|
| 1 | 184 | 603 | 92 | 139 | 139 | 131 | 232 |
| 2 | 267 | 924 | 96 | 139 | 139 | 140 | 198 |
| 4 | 340 | 698 | 120 | 161 | 152 | 162 | 302 |
| 6 | 565 | 972 | 205 | 302 | 291 | 358 | 642 |
| 8 | 1094 | 1466 | 677 | 896 | 839 | 1052 | 2231 |
| 12 | 1392 | 2083 | 736 | 964 | 882 | 1272 | 1514 |
| 16 | 1777 | 2404 | 906 | 1123 | 974 | 1483 | 1612 |
| 24 | 8701 | 8250 | 7432 | 8149 | 9607 | 10024 | 6890 |

Premier tir, pour mémoire (p95 fiche en fond) : 3 418, 6 685, 9 395, 12 300 ms aux paliers 1, 2, 4, 6.
Fiches en fond : 1,00 fiche/s jusqu'au palier 16, p50 387 → 1 310 ms, p95 603 → 2 404 ms, aucun refus de
la fiche elle-même (les échecs du fond sont des 503 de sa recherche).

## 5. Ressources et goulot

### 5.1 Par palier (cgroup du conteneur public, `docker stats`, sessions PostgreSQL)

| palier | cœurs du conteneur public (moy / max) | db (moy) | api (moy) | sessions PG sur CPU (moy / max) | temp max |
|---|---|---|---|---|---|
| 1 | 0,66 / 1,26 | 36 % | 13 % | 1,3 / 4 | 84,9 °C |
| 2 | 1,12 / 1,61 | 77 % | 17 % | 1,8 / 4 | 90,9 °C |
| 4 | 2,02 / 3,05 | 152 % | 28 % | 3,0 / 8 | 93,1 °C |
| 6 | 2,40 / 2,88 | 204 % | 35 % | 3,1 / 13 | 92,9 °C |
| 8 | 3,18 / 3,66 | 270 % | 47 % | 7,5 / 17 | 93,5 °C |
| 12 | 3,71 / 3,89 | 301 % | 71 % | 9,0 / 17 | 93,6 °C |
| 16 | 3,88 / 3,97 | 297 % | 81 % | 14,2 / 26 | 93,6 °C |

Premier tir : 3,74 cœurs dès le palier 1 (db 341 %). **Le conteneur public sature désormais vers 12–16 parcours/s**,
toujours par PostgreSQL d'abord, l'API Python montant à 81 %. Le relais reste sous 1 % de CPU.

### 5.2 Où part le temps PostgreSQL (différence `pg_stat_statements`, 1 806 s sur la fenêtre)

| Requête | Appels | Moyenne | JIT par appel | Part |
|---|---|---|---|---|
| Recherche dans la commune (stratégie A/C, `word_similarity`) | 9 548 | 68 ms | 0 | **36,2 %** |
| `INSERT INTO quota_usage` (compteur mensuel de la clé `dc_anon_`, une ligne par requête) | 35 942 | 7,9 ms | 0 | **15,8 %** |
| Résolution du nom de commune (`communes_lookup`) | 11 316 | 23 ms | 0 | **14,6 %** |
| Ventes isolées (`_ISOLEMENT_SQL`) | 1 773 | 117 ms | **124 ms** (76 fonctions) | **11,5 %** |
| Parcelles par point (cadastre) | 17 193 | 4,2 ms | 0 | 4,0 % |
| Ventes de la parcelle (`mutations_grain`, deux requêtes) | 7 312 × 2 | 9,4 et 7,2 ms | 0 | 6,7 % |
| Bâtiments au point (`batiments_par_point`) | 657 | 71 ms | 0 | 2,6 % |
| Transports à 1 km | 1 123 | **4,3 ms** | 0 | 0,27 % |
| Bâtiments de la parcelle | 1 050 | **1,2 ms** | 0 | 0,07 % |

La stratégie D n'a aucun appel terminé dans la fenêtre : les appels annulés par le budget ne sont pas
comptés par `pg_stat_statements`.

### 5.3 Erreurs (journaux de l'API depuis le début de la rampe, refus k6 par route)

| Événement | Premier tir | Second tir | Lecture |
|---|---|---|---|
| `unhandled_exception` `out of shared memory` (500) | 17 | **0** | la table de verrous ne sature plus |
| `address_parcel_read_failed` | 27 | 0 | |
| `geocode_query_failed` (recherche → 503) | 244 | 299 | surtout le budget D (« les sables-d olonne ») ; rampe trois fois plus haute |
| `Admission de publication indisponible` (DPE → 503) | 107 | 270 | budget d'admission de 250 ms dépassé quand la base sature (paliers ≥ 12) |
| `georisques_response_unparseable` | 62 | 124 | ~1 090 fiches, tiers |
| `QueuePool` | 0 | 0 | |

Refus k6 par route sur la rampe : recherche 503 × 252 (3,2 % des recherches) et 41 dans le fond, DPE
503 × 207 (2,7 %) ; fiche, commune, ventes et ventes déclarées : 0.

## 6. Ce qui n'est pas mesuré

- 5 fiches/s : le fond reste à 1 fiche/s ; la pointe de 100 professionnels en demande 5 (§ 7).
- Le rendu serveur du frontend, Cloudflare sous charge, la fréquence réelle du CPU à 93 °C (comme au premier tir).
- L'effet du cache seul (le premier tir était à froid).
- La stratégie D au national dans le budget : aucun appel n'y aboutit dans la fenêtre.

## 7. Lecture pour 100 professionnels

Modèle du plan (§ 13) : 1,7 parcours/s en moyenne (une fiche chacun), 5 en pointe.

- **Établi** : 6 parcours/s + 1 fiche/s soutenus (p95 344 ms, échecs 0,72 %) ; à 1 parcours/s + 1 fiche/s,
  0,66 cœur. Les deux requêtes corrigées pèsent 0,34 % du temps PostgreSQL.
- **Déduit** : la demande moyenne (1,7 parcours/s + 1,7 fiche/s) tient avec de la marge ; la pointe de
  5 parcours/s est dans le débit soutenu (marge du plan : 6 / 5 = **1,2**, contre 0,2). La pointe complète
  (5 fiches/s) n'est pas mesurée : chaque fiche coûte désormais peu à PostgreSQL, mais appelle Géorisques et
  l'IGN (~5 appels) — PROBABLE côté CPU, FRAGILE côté services tiers.
- **Déduit, FRAGILE** : le conteneur public actuel suffit au besoin estimé de 100 professionnels ; un serveur loué se
  chiffre désormais sur la redondance et la marge, plus sur le CPU de requêtes à corriger.
- **Prochains gains, par ordre de part** (hypothèses) : recherche dans la commune (36 %), `quota_usage`
  (16 % : une ligne unique mise à jour à chaque requête de la clé publique, partagée par tous les
  visiteurs), `communes_lookup` (15 %, `LIKE` sur une expression), ventes isolées (11,5 %, dont 124 ms de
  JIT par appel : `jit = off` les supprimerait).

## 8. Effets de bord

- `api_request_log` : ~41 200 lignes de plus en `198.18.0.0/16` (rampe, smokes et 332 recherches de diagnostic), **conservées**
  selon la décision d'Ianis (`82a4bb6f`). Le contrôle via Cloudflare (200 requêtes) vient de l'IP de la maison.
- `quota_usage` de la clé `dc_anon_` : incrémenté d'autant.
- Services tiers : ~1 090 fiches, de l'ordre de 5 000 appels vers Géorisques et l'IGN en 18 min.
- Visiteurs réels : site lent au sommet (20:48–20:53) ; reprise immédiate, smoke vert à 20:55.
- Clé publique : lue dans le `.env` du conteneur public par chaque commande, jamais affichée ni écrite ; 0 occurrence
  de `dc_anon_` dans les sorties.

## 9. Preuves

Sorties brutes, non versionnées : `C:/tmp/dc-k6-20261102-2005/` — `smoke.*`, `controle.*`, `rampe.log`,
`rampe.json`, `rampe.csv.gz`, `analyse.md`, `surveillance.*`, `relais-cgroup.tsv`, `ressources-paliers.md`,
`pgss-avant.txt`, `pgss-apres.txt`, `pgss-diff.md`, `refus-par-route.md`, `api-logs.txt`, `smoke-apres.log`,
`communes.txt`, `debut.txt`, `fin.txt`.
