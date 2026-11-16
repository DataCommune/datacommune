# ETL Production Runbook

Guide operationnel pour la verification et la relance des ETL en production.

## 1. Verification rapide (script automatise)

### Prerequis

- SSH alias `datacommune` configure (acces au LXC prod)
- Le conteneur DB `datacommune-api-db-1` doit etre UP

### Commande

```bash
./scripts/etl-runbook.sh
```

### Options

| Option | Defaut | Description |
|--------|--------|-------------|
| `--host HOST` | `datacommune` | Alias SSH vers le serveur prod |
| `--max-age DAYS` | `30` | Seuil de fraicheur en jours |

Variables d'environnement alternatives : `ETL_SSH_HOST`, `ETL_MAX_AGE_DAYS`, `ETL_DB_CONTAINER`, `ETL_DB_USER`, `ETL_DB_NAME`.

### Exemple de sortie OK

```
=== ETL Production Runbook — 2026-02-19 10:00:00 ===
Host: datacommune | Max age: 30j | Container: datacommune-api-db-1

[OK]   ban             — 25432100 rows — 5j
[OK]   cadastre        — 98765432 rows — 12j
[OK]   dvf             — 10234567 rows — 20j
[OK]   dpe             — 9876543 rows — 8j
[OK]   loyers          — 487200 rows — 25j
[OK]   batiments       — 52341000 rows — 15j
[OK]   connectivite    — 48765000 rows — 18j
[OK]   monuments       — 45321 rows — 10j
[OK]   scolaire        — 98765 rows — 22j
[OK]   transports      — 112345 rows — 14j

=== Bilan : 10 OK | 0 WARN | 0 FAIL ===
```

### Exemple de sortie avec problemes

```
=== ETL Production Runbook — 2026-02-19 10:00:00 ===
Host: datacommune | Max age: 30j | Container: datacommune-api-db-1

[OK]   ban             — 25432100 rows — 5j
[FAIL] cadastre        — ABSENT ou vide
[OK]   dvf             — 10234567 rows — 20j
[WARN] dpe             — 9876543 rows — 45j (perime > 30j)
[OK]   loyers          — 487200 rows — 25j
[OK]   batiments       — 52341000 rows — 15j
[OK]   connectivite    — 48765000 rows — 18j
[OK]   monuments       — 45321 rows — 10j
[OK]   scolaire        — 98765 rows — 22j
[OK]   transports      — 112345 rows — 14j

=== Bilan : 8 OK | 1 WARN | 1 FAIL ===
Action requise — voir docs/etl-runbook.md pour les commandes de relance
```

### Interpretation des codes

| Code | Signification | Action |
|------|---------------|--------|
| `[OK]` | Dataset present, rows > 0, age < seuil | Rien a faire |
| `[WARN]` | Dataset present mais perime (age >= seuil) | Relancer l'ETL du dataset |
| `[FAIL]` | Dataset absent ou vide (rows = 0) | Relancer l'ETL du dataset |

**Exit code** : `0` si tous OK, `1` si au moins un WARN ou FAIL.

---

## 2. Verification manuelle par dataset

### Tableau de reference

| Dataset | Nom CLI | Rows attendus (~) | Frequence MAJ | Source |
|---------|---------|-------------------|---------------|--------|
| BAN | `ban` | ~25M | Mensuelle | adresse.data.gouv.fr |
| Cadastre | `cadastre` | ~100M | Trimestrielle | cadastre.data.gouv.fr |
| DVF | `dvf` | ~10M | Semestrielle | data.gouv.fr |
| DPE | `dpe` | ~10M | Mensuelle | data.ademe.fr |
| Loyers | `loyers` | ~500K | Annuelle | data.gouv.fr |
| BD TOPO | `batiments` | ~50M | Semestrielle | geoservices.ign.fr |
| Arcep | `connectivite` | ~50M | Semestrielle | data.arcep.fr |
| Monuments | `monuments` | ~50K | Semestrielle | data.gouv.fr |
| Scolaire | `scolaire` | ~100K | Annuelle | data.gouv.fr |
| Transports | `transports` | ~100K | Semestrielle | transport.data.gouv.fr |
| Liens BAN↔RNB | `rnb_liens` | ~1,8M (44+85) | Semestrielle | rnb-opendata.s3.fr-par.scw.cloud |
| Bâtiments BDNB par parcelle | `bdnb_batiment_parcelle` | ~1,4M (44+85) | Irrégulière (2 à 3 millésimes par an) | open-data.s3.fr-par.scw.cloud (via data.gouv.fr) |

### Attribution des sources

Les licences et producteurs font autorite dans
`backend/app/common/source_catalog.py` (`DATASET_CATALOG`), pas ici.

Cas particulier `rnb_liens` (Lot B, `PLAN-RATTACHEMENT-DPE-2026-08-05.md`) :
la table `rnb_liens` est une **table interne de jointure**, pas un dataset
expose par l'API. Elle materialise les exports departementaux du **Referentiel
National des Batiments (RNB)** — producteur **beta.gouv.fr / CSTB**, **Licence
Ouverte 2.0**, obligation d'attribution. L'attribution publique est deja portee
par l'entree `rnb` du catalogue (le proxy `/v1/rnb`), qui designe la **meme
source sous la meme licence** : aucune entree de catalogue supplementaire n'est
creee tant que `rnb_liens` n'alimente pas de reponse publique.
URL des exports : `https://rnb-opendata.s3.fr-par.scw.cloud/files/RNB_<dept>.csv.zip`.
Millesime : lu dans l'en-tete HTTP `Last-Modified` de l'export (colonne
`millesime`), jamais fabrique depuis l'heure d'import.

Cas particulier `bdnb_batiment_parcelle` (plan 04.7-19) : la table alimente le
thème `construction` du rapport d'adresse, à la place de l'API BDNB ; les
routes `/v1/bdnb*` restent un proxy. Source : **Base de Données Nationale des
Bâtiments (BDNB)**, producteur **CSTB**, **Licence Ouverte 2.0**, entrée `bdnb`
du catalogue (même clé de périmètre que le proxy). Citation demandée par le
CSTB : « Base de Données Nationale des Bâtiments (BDNB) - Millésime <code> -
CSTB ». Exports départementaux de l'open data :
`https://open-data.s3.fr-par.scw.cloud/bdnb_millesime_<m>/millesime_<m>_dep<xx>/open_data_millesime_<m>_dep<xx>_csv.zip`
(`<xx>` sur deux caractères, Corse `2a`/`2b` ; DROM absents, hors couverture).
Seuls trois membres du zip sont lus : `csv/rel_batiment_groupe_parcelle.csv`,
`csv/batiment_groupe_ffo_bat.csv`, `csv/metadonnees_bdnb_version.csv`.
Millésime : étiquette du membre de version (`2026-02.b`), jamais l'heure
d'import ; le millésime courant est découvert dans les ressources data.gouv.fr,
ou ciblé. Lancement **manuel uniquement** (ni cron ni ordre national) :

```bash
python run_etl.py bdnb_batiment_parcelle --millesime 2026-02-b
```

Contrôle bloquant avant tout import : `python -m app.scope show --json` doit
donner exactement le périmètre voulu (`44,85` en préproduction) ; le périmètre
global n'est jamais réduit pour rendre l'import petit. Migration 113 avant le
premier run (ADR-0003).

> **Note** : les "rows attendus" sont des ordres de grandeur. L'important est que `row_count > 0` et `last_updated_at` soit recent (< 30 jours par defaut).

### Requete directe dataset_metadata

```bash
ssh datacommune "docker exec datacommune-api-db-1 psql -U datacommune -d datacommune -c \"
SELECT dataset_name, row_count, last_updated_at,
       EXTRACT(DAY FROM NOW() - last_updated_at)::int AS age_days
FROM dataset_metadata ORDER BY last_updated_at ASC;\""
```

---

## 3. Relance ETL si dataset perime ou absent

> **Important** : la relance est une **decision humaine**. Le script runbook signale mais ne relance jamais automatiquement.

### Relancer UN dataset

```bash
# Exemple : relancer l'ETL BAN
ssh datacommune "docker exec -d datacommune-api-api-1 bash -c 'python /app/run_etl.py ban > /tmp/etl_ban.log 2>&1'"
```

### Suivre la progression

```bash
ssh datacommune "docker exec datacommune-api-api-1 tail -f /tmp/etl_ban.log"
```

### Relancer TOUS les datasets

```bash
# Attention : operation longue (plusieurs heures selon les datasets)
ssh datacommune "docker exec -d datacommune-api-api-1 bash -c 'python /app/run_etl.py ban cadastre dvf dpe loyers batiments connectivite monuments scolaire transports > /tmp/etl_full.log 2>&1'"
```

### Verifier apres relance

```bash
# Re-executer le runbook
./scripts/etl-runbook.sh

# Ou verifier dataset_metadata directement
ssh datacommune "docker exec datacommune-api-db-1 psql -U datacommune -d datacommune -c \"
SELECT dataset_name, row_count, last_updated_at
FROM dataset_metadata WHERE dataset_name = 'ban';\""
```

---

## 4. Signes d'alerte et actions

| Symptome | Cause probable | Action |
|----------|----------------|--------|
| `[FAIL]` sur un dataset | ETL n'a jamais tourne ou a echoue | Relancer l'ETL du dataset (section 3) |
| `[WARN]` fraicheur | Scheduler inactif ou ETL echoue silencieusement | Verifier `etl_runs`, relancer si necessaire |
| `row_count=0` apres relance | Probleme source de donnees ou parser ETL | Verifier les logs ETL (section 5) |
| Tous les datasets WARN en meme temps | Scheduler arrete depuis longtemps | Verifier et redemarrer le scheduler |
| Erreur SSH / connexion | LXC prod inaccessible | Verifier l'alias SSH et l'etat du LXC |

### Redemarrer le scheduler

```bash
ssh datacommune "docker compose -f /opt/datacommune/docker-compose.yml -f /opt/datacommune/docker-compose.prod.yml restart scheduler"
```

---

## 5. Verification de l'historique ETL

La table `etl_runs` conserve l'historique de chaque execution ETL.

```bash
ssh datacommune "docker exec datacommune-api-db-1 psql -U datacommune -d datacommune -c \"
SELECT dataset_name, status, started_at, total_rows_loaded, total_duration_seconds
FROM etl_runs ORDER BY started_at DESC LIMIT 20;\""
```

### Verifier un dataset specifique

```bash
ssh datacommune "docker exec datacommune-api-db-1 psql -U datacommune -d datacommune -c \"
SELECT dataset_name, status, started_at, total_rows_loaded, total_duration_seconds
FROM etl_runs WHERE dataset_name = 'ban' ORDER BY started_at DESC LIMIT 5;\""
```

### Colonnes cles de etl_runs

| Colonne | Description |
|---------|-------------|
| `dataset_name` | Nom du dataset (ex: `ban`, `dvf`) |
| `status` | `success`, `failed`, `running` |
| `started_at` | Date/heure de debut |
| `total_rows_loaded` | Nombre de lignes chargees |
| `total_duration_seconds` | Duree totale en secondes |

---

## 6. Contrôle post-refresh DPE — clés BAN et populations distinctes

La clé `diagnostics_dpe.identifiant_ban` permet une jointure descriptive vers
le référentiel BAN. Son remplissage ne prouve ni l'exactitude de l'adresse,
ni l'identité du logement ou de la parcelle. L'inventaire à l'adresse et
l'attribution au logement restent deux contrats distincts.

Conserver le filtre départemental44/85 dans les sondes. Vérifier le type réel
de relation dans `pg_class` et ses partitions dans `pg_inherits` : le nom de
table ne garantit aucun partitionnement ni élagage du plan. Ne pas ajouter un
filtre `statut_geocodage` sans réconciliation des rejets avec la source gelée.

Le conteneur DB reel en prod est `datacommune-api-db-1` (projet compose
`datacommune-api`).

### Requete 1 — taux de remplissage de la cle

```bash
ssh datacommune "docker exec datacommune-api-db-1 psql -U datacommune -d datacommune -c \"
SELECT count(*) FILTER (WHERE NULLIF(btrim(identifiant_ban), '') IS NOT NULL) AS avec_cle,
       count(*) AS total
FROM diagnostics_dpe WHERE code_departement IN ('44','85');\""
```

### Requête 2 — documents dont la clé existe dans BAN

```bash
ssh datacommune "docker exec datacommune-api-db-1 psql -U datacommune -d datacommune -c \"
SELECT count(*) AS documents,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM adresses a WHERE a.ban_id = d.identifiant_ban
       )) AS documents_avec_cle_presente
FROM diagnostics_dpe d WHERE d.code_departement IN ('44','85');\""
```

### Interprétation et décision d'import

`EXISTS` compte chaque document une fois malgré les doublons BAN. Le résultat
reste un nombre de documents, pas d'adresses distinctes ni de logements. La
présence d'une clé dans BAN ne prouve pas que la recherche applicative la résout.

Les anciens repères95%,99,96% et5% ne sont pas des gates de la génération
courante. Un écart au relevé de juillet peut refléter une population, une
source ou un référentiel différents ; il ne démontre pas automatiquement une
perte ETL. Un résultat nul demande un diagnostic, jamais une relance aveugle.

Pour décider, utiliser le manifeste complet existant/neuf44/85, le validateur
source et `scripts/mesure_dpe_couverture.py` selon la procédure58.2 ci-dessous.
Comparer sur les mêmes sources, dates et référentiels : documents, rejets,
traces de retrait, clés BAN distinctes, adresses résolubles et résultats HTTP.
Le dénominateur nul reste inconnu ; aucun gain SQL n'est un gain utilisateur
sans comparaison avant/après de la résolution réelle.

### Historique des mesures

Relevés et interprétations historiques conservés. Les expressions « taux de
rattachement réel » et les causes affirmées ci-dessous ne sont pas des preuves
d'identité ni des diagnostics actuels ; utiliser l'interprétation corrigée ci-dessus.

| Date | Taux 44 | Taux 85 | Jointure 44 | Jointure 85 | Verdict |
|------|---------|---------|-------------|-------------|---------|
| 2026-08-02 | **100,00 %** (344 930 / 344 930) | **100,00 %** (126 143 / 126 143) | 314 900 lignes | 100 959 lignes | Remplissage conforme (0 NULL, 0 chaine vide) — le backfill a bien tourne. **Mais** taux de rattachement reel (cle presente dans `adresses`) = **91,27 % (44)** et **80,02 % (85)**, soit 30 108 + 25 203 cles orphelines : bien au-dessus du repere 5 %. A investiguer cote millesime BAN. |
| 2026-08-05 | **100,00 %** (345 139 / 345 139) | **100,00 %** (126 227 / 126 227) | 315 096 lignes | 101 040 lignes | Refresh Lot A (backfill migration 078). Remplissage conforme. Taux de rattachement reel **91,27 % (44)** / **80,03 % (85)** — **inchange** malgre le refresh : les 30 121 + 25 206 cles orphelines ne viennent pas du millesime ADEME mais du millesime **BAN** (dernier run `ban` = 2026-06-30). Nouveau : `id_rnb` peuple a **58,61 % (44)** / **46,62 % (85)**, `score_ban` a 100 %. |

---

## 7. Gates de protection du swap — GATE-B′ (plancher absolu de lignes)

### Ce que protege GATE-B′

Le swap atomique remplace la table de production par le staging. Deux filets
distincts empechent de publier un staging degrade :

1. **Safety 50 %** (DPE/BAN/Monuments, dans `swap()`) et **regression 20 %**
   (`etl_max_row_drop_pct`, runner) — filets **relatifs** : ils ne se
   declenchent **que si la production a deja des lignes**. Premiere ingestion
   ou production deja videe → ils sont muets.
2. **GATE-B′** — plancher **absolu** de lignes : `min_staging_rows`
   (runner, GATE-01) ou `min_staging_rows_final` (dans `swap()`, pour les
   pipelines flat-staging DPE/BAN/Monuments dont le compte runner reflete le
   staging plat, pas la table finale). C'est le seul filet qui ferme le trou
   « staging quasi vide » dans tous les cas.

Depuis 2026-08-04, un pipeline **sans plancher propre** herite d'un plancher
fallback global : `ETL_MIN_STAGING_ROWS` (defaut **1000**, mis a l'echelle des
departements du perimetre heberge via `scoped_min_rows`). Valeur volontairement basse :
elle ne bloque aucun dataset legitime (les plus petits — commune-level — font
~35 000 lignes en national) mais interdit de swapper un staging quasi vide.
Seul opt-out explicite : `permis` (`min_staging_rows=0`, source SITADEL en
quarantaine).

### Valeurs prod attendues (ancien conteneur de production, perimetre 44,85 : `python -m app.scope set --datasets … --departements 44,85`)

| Pipeline | Plancher | Ou il s'applique |
|----------|----------|------------------|
| dpe | `scoped_min_rows(10 000 000)` ≈ **198 019** | dans `swap()` (`min_staging_rows_final`) |
| ban | `scoped_min_rows(20 000 000)` ≈ **396 039** | dans `swap()` (`min_staging_rows_final`) |
| monuments | **30 000** (fixe, pipeline national non scope) | dans `swap()` (`min_staging_rows_final`) |
| rnb_liens | **250 000 x nb de departements** = **500 000** en 44+85 | dans `swap()` (`min_staging_rows_final`) |
| autres pipelines | plancher propre (~80 % de la prod connue, cf. `min_staging_rows` de chaque pipeline) | runner (GATE-01) |
| pipeline sans plancher | fallback `ETL_MIN_STAGING_ROWS` (1000, scale) | runner (GATE-01) |

### Piege de lecture des logs (incident 48-10)

L'ancien log runner `GATE-01 row count check disabled for dpe
(min_staging_rows=0)` a fait conclure a tort que **aucun** plancher absolu
n'existait en prod (dette 8.5). En realite le plancher DPE etait bien actif
**dans `swap()`**, mais ne loggait qu'en echec. Les logs disent desormais :

- runner : `GATE-01 row count check deferred for dpe: floor of N rows enforced
  inside swap() via min_staging_rows_final`
- `swap()` : `GATE-01 final floor passed: X rows >= N minimum`

Si ni « deferred » ni « final floor passed » n'apparaissent sur un run
DPE/BAN/Monuments, l'image deployee est anterieure a ce fix.

### Pose effective en production

La valeur vit **dans le code** (defaut Pydantic `etl_min_staging_rows`,
surchargeable par variable d'environnement `ETL_MIN_STAGING_ROWS` si un jour
necessaire). Elle arrive en production **par le deploiement standard**
(release dev → master → CD, procedure migrations ADR-0003) — **jamais** par
modification SSH directe sur le conteneur de production.

---

## 8. Fenetre d'ingestion et exposition publique — etat factuel

### Etat actuel

Pendant un re-run ETL sous l'overlay `docker-compose.vps-etl.yml`, `caddy` et
`cloudflared` **restent up** (constat 48-10 : `docker compose up -d` avec le
nouvel overlay les signale comme orphelins mais ne les arrete pas).
`api.datacommune.fr` reste donc publiquement servi pendant toute la fenetre
d'ingestion. Aucun mecanisme de coupure n'existe, et **aucun n'est a coder** —
la decision ops n'a pas ete prise.

### Frontières de publication et indisponibilité

Le swap transactionnel protège la bascule des tables ; il ne rend pas atomiques
les hooks et reconstructions exécutés ensuite. Des vues peuvent encore dépendre
de l'ancien OID, une publication peut échouer après le commit et une requête
peut atteindre son timeout pendant les verrous. Aucune durée instantanée ni
absence d'erreur n'est garantie par les deux renommages.

Le candidat de stabilisation094 ajoute le journal `etl_publications`, les
verrous de publication et les contrôles de générations/OID. Le lecteur Commune
refuse une génération non prête par503 avec reprise explicite ; il ne transforme
pas une reconstruction incomplète en jeu vide. Vérifier le SHA et le schéma
réellement servis avant de compter sur ces protections : leur présence dans
le dépôt n'atteste pas leur déploiement.

Après un échec, distinguer pré-swap, données basculées, vues reconstruites et
publication finalisée. Conserver l'erreur du job et diagnostiquer le journal
avant toute reprise ; ne pas modifier ses états pour forcer le service.
Contrôler les lecteurs concernés après la reprise. L'ancien binaire de secours
peut ignorer le protocole094 : appliquer le garde du rollback et la procédure
de récupération qualifiée dans [le runbook de déploiement](deploy-runbook.md).

`refresh-commune-synthese` reconstruit les vues mais ne clôt pas une publication
`failed` ou `pending` : cette seule commande ne rétablit donc pas les lecteurs.
Après diagnostic de la cause et de la capacité, reprendre par le runner avec
le même manifeste qualifié. Son garde `requires_fresh_start` impose un staging
neuf après un échec de publication ; le nouveau cycle doit valider le stock,
les vues, les OID et les métadonnées avant `published`. Une récupération après
restauration doit suivre la procédure éprouvée avec son binaire compatible,
sans modification manuelle du journal.

### Si un jour une coupure est voulue (options, non tranchees)

- `docker compose up -d --remove-orphans` avec l'overlay ETL (retire
  caddy/cloudflared pour la duree de la fenetre) ;
- stop explicite `docker stop caddy cloudflared` encadre par la procedure ;
- page de maintenance cote Cloudflare (zero intervention sur le CT).

Tout mecanisme retenu passera par le depot (repo → PR → CD), pas par SSH.


## DPE 58.2 — import complet contrôlé 44/85

Cette procédure remplace pour DPE les ordres de grandeur nationaux et la relance
non qualifiée ci-dessus. Le contrat de source 58.2 exige au minimum le schéma
091. Toute exécution exige les correctifs de publication092–094 et une image
qualifiée. La version a5ea02f mentionnée dans la
[passation préimport historique](../../docs/audit-stabilisation-2026-09-08/lot-11/dpe-preimport/README.md)
ne les possédait pas. Le8 septembre, la release46c73f2 a effectivement publié
540717 lignes sous schéma094 ; les mesures et limites sont dans
[L16](../../docs/audit-stabilisation-2026-09-08/lot-16-import-dpe/README.md).
Cette preuve datée ne dispense pas de requalifier l'état courant avant une autre exécution.

### Entrées et préparation

Les quatre couples sont obligatoires : existant44, existant85, neuf44, neuf85.
Le pipeline refuse tout département hors de ce verrou (`SCOPE_LOCKS`, migration 091)
avant téléchargement ; le périmètre se configure en base par
`python -m app.scope set --datasets dpe,… --departements 44,85` et aucun import
national n'est lancé.

Le lot gelé du 05/09/2026 comporte 540798 numéros distincts sans collision :
349361, 128086, 41292 et 22059 respectivement. Ses 144 rejets géométriques sont
nouveaux, sans BAN exploitable ; aucun refresh BAN/cadastre ou point de
substitution n'est justifié. Les dates d'observation du 05/09 ne deviennent pas
la date d'import. Les métadonnées de publication ADEME datent du 02/09.

Le manifeste d'import est celui de `DpeImportManifest`, distinct du manifeste
historique du harness de mesure. Un cache déplacé Windows→Linux doit avoir ses
chemins relocalisés dans une nouvelle copie du manifeste ; conserver données,
hashes, pages, observations, versions et import_id à l'identique. Valider le
manifeste **dans le conteneur cible** avant toute écriture DB. Aucun faux
checkpoint ne doit servir à sélectionner le cache.

La stack réellement utilisée par CD est `docker-compose.yml` avec
`docker-compose.vps.yml`. Ce dernier déclare le montage DPE privé
`/opt/datacommune/data/dpe` en lecture seule dans l’API. Les commandes
opérationnelles scheduler lancées dans ce conteneur le partagent. Un processus
scheduler séparé doit recevoir explicitement ce montage et la variable. Placer chaque génération sous `dpe/generation-<date-ou-id>` et
son manifeste sous `import-manifest.json`. Ne pas placer les seules preuves
sous `/etl_tmp/datacommune_dpe` : ce cache est supprimé après un import réussi.
Ne pas copier de .env dans les preuves ou le checkout. Préparer le répertoire
hôte privé avant CD (0700, propriétaire du processus applicatif, fichiers0600),
puis vérifier la lecture depuis le conteneur ; ne pas supposer qu’un bind mount
créé par Docker possède les bons droits.

Avant chaque import : vérifier le SHA de code, les quatre couples et leurs
artefacts, les versions début/fin, l'espace hôte **et** celui de PostgreSQL.
Réserver la nouvelle table, le staging intermédiaire, tous les index, la table
ancienne conservée et le WAL. Les capacités du 05/09 sont historiques ; mesurer
à nouveau. Prélever les tailles et le LSN avant/après et surveiller le minimum
d'espace libre durant l'exécution. La somme de lignes source n'est pas un gain
d'adresses ou de logements.

Pendant le swap, ne pas combiner un `count(*)` de production avec
`pg_total_relation_size()` du staging dans la même transaction de surveillance.
Cette lecture prend les verrous dans l'ordre inverse du swap et peut provoquer
un deadlock, même en lecture seule. Utiliser pendant l'import les tailles
globales DB/WAL, le LSN, l'espace disque et les estimations `pg_class.relpages`.
Réserver les tailles exactes par relation et les comptes détaillés aux mesures
avant/après, observateur arrêté. Les estimations de catalogue ne sont pas des
pics exacts par table/index.

### Sauvegarde et restauration

Conserver une sauvegarde actuelle et en éprouver la restauration sur une base
jetable avant de publier. Le drill local du 08/09 a restauré le DPE090 réel puis
une copie091 avec ancienne relation simulée. Il ne prouve ni PBS ni une
restauration de production. L'image précédente seule ne restaure aucun contenu
purgé lors de la réconciliation.

Une sauvegarde DPE ciblée comprend les tables courante et anciennes retenues,
leurs séquences, schéma/contraintes/index, les seules lignes DPE pertinentes de
`dataset_metadata`, `etl_runs`, `etl_old_tables`, et l'état Alembic. Inclure
explicitement les six fonctions `dc_dpe_*` nécessaires aux contraintes091 : un
pg_dump limité aux tables ne les emporte pas. Restaurer fonctions, tables puis
métadonnées dans cet ordre, contrôler tous les hashes et états de séquences.
Exporter les définitions SQL avec séparateurs et fins de ligne LF ; ne pas
faire transiter le dump binaire par une redirection texte PowerShell.

Conserver le schéma Alembic réellement compatible avec toute la base : restaurer
une sauvegarde ciblée DPE ne permet pas de réécrire arbitrairement la version
globale. Les vues dérivées sont à reconstruire. Une restauration change les OID
et invalide les références d'inventaire ; le client doit actualiser la page.

### Migration, import et contrôles

La CD exécute les migrations. En laboratoire seulement, appliquer l'upgrade
avec le CLI Alembic du checkout avant de démarrer l'API candidate. Conserver
un avant local SQL/HTTP avant l'import, puis un après dans le **même**
environnement. La base locale possède des référentiels différents de la
production : aucune comparaison croisée ne constitue un gain.

Dans le conteneur API du SHA vérifié, depuis `/app` :

```bash
python -m run_etl dpe --dpe-manifest /opt/datacommune/data/dpe/generation-20260905/import-manifest.json
```

Un échec avant swap conserve l'ancienne génération. Un échec de vues requis
après swap rend un résultat ETL négatif et demande une reconstruction : les
nouvelles données peuvent déjà être publiées. Ne pas annoncer un rollback
implicite ni relancer aveuglément l'import complet. Le contrôle communal
refuse une chaîne de vues qui ne dépend plus des OID actuels, ou une
reconstruction marquée en échec.

Reconstruction explicite avec l'outil existant du même checkout :

```bash
python -m app.scheduler.run_job refresh-commune-synthese
```

Un simple REFRESH ne remplace pas le SQL des vues. Contrôler leurs définitions,
indices, dépendances sur la nouvelle relation et `matview_metadata` avant de
considérer les agrégats disponibles.

Le validateur exige un manifeste **durable et de la génération importée** :
`DpeValidator(manifest_path=Path(...)).run(dsn)` ou variable
`DPE_VALIDATION_MANIFEST` pour le processus scheduler. L’overlay VPS utilisé par CD fixe par défaut
`/opt/datacommune/data/dpe/current/import-manifest.json`. Après publication et contrôle,
l'opérateur de données doit faire pointer atomiquement `current` vers la bonne
génération en conservant tous ses artefacts. Un chemin manquant ou une génération
précédente produit un échec structuré ; aucun fallback au cache effacé.

**Chaque import planifié ultérieur a la même obligation de conservation et de
sélection de génération.** Le téléchargement ordinaire écrit un cache temporaire,
pas une archive durable. Ne pas présenter une validation planifiée comme active
sans avoir configuré cette conservation ; ne pas conserver indéfiniment le
manifeste du premier lot comme référence des imports suivants.

Comparer séparément les quatre provenances présentes, tombstones, validités,
classes connues/inconnues, communes et proximité. L'inventaire compte une trace
de retrait minimale comme document connu ; cela ne signifie pas qu'un détail
descriptif a été conservé. Rejouer le corpus général gelé, les 21 cibles et le
témoin de perte sans changer les pins. Le harness fournit `after`, `compare` et
`--check-report` ; conserver les trois retours séparés et les preuves privées.
Les nouveaux entrants BAN sont une union distincte ; sans refresh des
référentiels, aucune nouvelle clé BAN ne doit être inventée.

La release reste soumise aux CI du SHA, replay privé, PR dev→master, CD
coordonnée frontend puis API, import effectif et UAT applicative. Ni cette
procédure, ni la sauvegarde locale, ni un dry-run ne clôturent DPE-COV.

## Sources locales figées LOVAC et loyers

`--source-manifest` sélectionne les fichiers locaux approuvés pour exactement
un dataset : `vacance` ou `loyers`. L'option est incompatible avec
`--dpe-manifest`. Les commandes sans manifeste conservent leur téléchargement
habituel ; prépositionner un CSV dans le cache ne les rend pas hors ligne.

Après qualification de la release, transfert et vérification des sources dans
le conteneur cible, les commandes sont :

```sh
python -m run_etl vacance --source-manifest /opt/datacommune/data/sources/vacance/generation-qualifiee/source-manifest.json
python -m run_etl loyers --source-manifest /opt/datacommune/data/sources/loyers/generation-qualifiee/source-manifest.json
```

Les chemins sont des exemples de générations à préparer, pas des répertoires
présumés déjà installés. Exécuter et qualifier chaque dataset séparément.
`--dry-run` utilise le même contrat local, sans écriture DB ; il ne remplace pas
les contrôles de publication après l'import réel.

L'overlay VPS monte uniquement `/opt/datacommune/data/sources` à ce même chemin
dans le service API, en lecture seule. Le répertoire hôte doit exister avant CD :
`create_host_path: false` refuse sa création implicite. Ne pas monter tout le
répertoire `data`, ni placer ces sources dans le montage DPE. Préparer un dossier
de génération inédit par dataset ; ne pas écraser un lot existant ni réutiliser
un pointeur `current` mutable pour la décision d'import.

Avant transfert, vérifier l'espace libre en conservant la réserve ETL existante,
le confinement des chemins résolus sous ce seul montage et l'absence de liens
symboliques. L'administrateur hôte possède les dossiers et fichiers (root) ;
pour ces seuls CSV publics, les dossiers `0755` et fichiers `0444` permettent
la lecture par l'utilisateur non-root de l'image sans écriture. Ne pas modifier
les permissions des preuves privées DPE ou golden. Recalculer tailles et SHA256
sur les octets transférés, puis vérifier les deux manifestes depuis l'image
immuable déployée et l'utilisateur API effectif. La préparation du montage et
la validation des fichiers ne déclenchent ni import ni reconstruction de vue.

Le manifeste JSON possède exactement `version`, `dataset`, `vintage`,
`source_contract` et `files`. Chaque fichier possède exactement `kind`, `path`,
`bytes` et `sha256`. Les chemins doivent être absolus et désigner des fichiers
réguliers durables **hors de `ETL_TEMP_DIR`**. Les fichiers ne doivent pas partager
le même chemin ou le même contenu sous plusieurs types. Les clés JSON répétées,
champs inconnus, listes incomplètes et types supplémentaires sont refusés.

| Dataset | vintage | source_contract | Types obligatoires |
|---|---:|---|---|
| vacance | 2026 | `lovac-communes-v1:2026:2025` | `communes` |
| loyers | 2025 | `loyers-prediction-metadata-v1:2025` | `appartement`, `maison`, `1-2_pieces`, `3+_pieces` |

Exemple complet pour la source LOVAC déjà qualifiée en laboratoire, à adapter
uniquement au chemin de son transfert durable vérifié :

```json
{
  "version": 1,
  "dataset": "vacance",
  "vintage": 2026,
  "source_contract": "lovac-communes-v1:2026:2025",
  "files": [
    {
      "kind": "communes",
      "path": "/opt/datacommune/data/sources/vacance/generation-qualifiee/lovac_communes.csv",
      "bytes": 7628201,
      "sha256": "b856568d4b69784d32f9fafaf7e1376047e45eed245fb114dd737d2d43826245"
    }
  ]
}
```

Pour les loyers, déclarer les quatre entrées avec leurs tailles et SHA256
réellement approuvés. Le type est une désignation explicite de source par le
manifeste ; le hash ne prouve pas, à lui seul, la provenance officielle ou
l'appartenance à ce millésime. Conserver le reçu d'acquisition et sa liaison
avec les fichiers, puis vérifier le hash du manifeste dans la décision
opérationnelle. Aucun champ de date source n'est déduit ou accepté implicitement.

Avant toute entrée dans le runner, les octets du manifeste et de tous les
fichiers sont contrôlés : taille, SHA256, stabilité de lecture et schéma.
LOVAC exige les colonnes 2026 et le dénominateur privé 2025. Les snapshots de
travail sont créés dans un sous-répertoire unique de `ETL_TEMP_DIR/frozen_sources`,
hors du cache partagé du dataset, avec les noms de types attendus par la
transformation. Une invocation ne peut donc pas effacer les copies d'une autre
invocation déjà préparée en nettoyant son cache après succès. Le plafond de téléchargement et la
réserve disque ETL existants sont conservés ; les tailles attendues des copies
sont prises en compte avant de copier. Le manifeste lui-même est borné à 128 Kio.

La phase download recontrôle uniquement ces copies : aucun fallback réseau,
aucune relecture d'un amont mutable. Une modification ultérieure du fichier
durable ne change pas le snapshot sélectionné ; une modification du snapshot
fait échouer le traitement. Le `finally` du runner ferme les copies de son seul
propriétaire après succès, échec, dry-run ou refus du verrou ; jamais les originaux
durables. Une interruption brutale du processus peut laisser ce répertoire unique
orphelin ; aucun nettoyage global des snapshots n'est effectué automatiquement.
Le checkpoint contient un scope lié au SHA256
exact du manifeste ; un checkpoint antérieur incompatible déclenche la reprise
fraîche existante du runner. À manifeste identique, les nouvelles copies gardent
les mêmes basenames utilisés par le checkpoint, même si leur répertoire change.
Les transactions, seuils de lignes et gardes de
publication ne changent pas.

Attendus des sources figées qualifiées : 34 912 codes LOVAC, dont 18 321
dénominateurs secrets, 19 558 taux globaux NULL et 19 taux globaux zéro ;
139 600 lignes loyers, dont 671 EPCI. Ces chiffres sont attachés aux hashes
retenus, pas des nouveaux seuils universels. Le plancher LOVAC reste 28 000 et
celui des loyers 100 000. Les taux LOVAC longue, les loyers prédits et leurs
échelles/qualités gardent leur sens ; un zéro ne remplace pas une absence.

Après chaque import : code CLI, journal `published`, génération/OID live et
dépendances des vues, comparaisons indépendantes source→SQL, NULL/zéros, scopes,
millésimes, puis contrôles HTTP/documentaires. Une panne requise conserve le
refus de lecture prévu par 094. Une nouvelle reprise doit repasser la validation
des fichiers ; ne pas fabriquer un checkpoint pour forcer une admission.

Les pipelines ne fournissent toujours pas de `source_period_label`,
`source_updated_at` ou `effective_departments` au mécanisme de metadata.
En base vide ces champs restent NULL ; le COALESCE existant peut conserver
une valeur antérieure en base réelle. Relever et comparer l'avant/après : ni
l'heure d'import, ni la date d'acquisition locale ne prouvent une publication
source. Toute contradiction effective doit être résolue avant d'annoncer une
qualification de provenance. Ce contrat ne modifie pas les imports planifiés.

Qualification reproductible en PostgreSQL jetable :

```powershell
pwsh -NoProfile -File scripts/run_contract_integration.ps1 tests/integration/test_frozen_source_cli.py -q --disable-warnings
```

Le test utilise un sous-processus exécutant l'entrée `run_etl`, sa CLI et les
hooks par défaut avec HTTP interdit. Sous Windows seulement, la sonde disque
du runner lit la capacité mesurée dans son conteneur PG Linux jetable ; aucune
substitution de download, transform, swap, hook ou garde. Les fixtures publiques
gzip sont suivies et vérifiées par taille/hash avant décompression bornée.
