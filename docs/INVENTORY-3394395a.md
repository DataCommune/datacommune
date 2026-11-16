# Inventaire décidé de `3394395a`

Base : `3394395a32e9a778c89930ad7544406da265163e` (`origin/dev` du dépôt d'origine
`DataCommune/datacommune`, 10/09/2026), copiée sans historique le 19/09/2026
(tag local `transfer-3394395a`). Inventaire écrit le 20/09/2026 au plan 01-08.

**Règle de lecture.** Une ligne = un composant et **la décision prise à son sujet**,
pas une description.

| Décision | Sens | SHA de la colonne 5 |
|---|---|---|
| `garder` | Transféré et conservé tel quel | `3394395a` — ou le commit de création quand le composant a été **écrit dans ce dépôt** |
| `adapter` | Transféré puis modifié ici | Commit d'adaptation |
| `retirer` | Absent de ce dépôt : retiré après transfert, ou jamais transféré | Commit de retrait, ou `3394395a` pour un composant jamais transféré |

Un chemin suivi de `#ancre` désigne un **retrait partiel** : le fichier reste, le
symbole nommé après le `#` est parti (ou, pour `garder`, doit toujours y être).

`python scripts/inventory-check.py docs/INVENTORY-3394395a.md` contrôle chaque
ligne contre l'arbre : existence pour `garder` / `adapter`, absence pour
`retirer`, présence ou absence de l'ancre quand il y en a une.

Le détail d'un retrait — recherche d'appelants, mesure, non-régression — vit dans
`docs/REPO-STATE.md` et dans les SUMMARY de
le suivi interne de la phase 1. Cet inventaire dit **quoi** ;
REPO-STATE dit **pourquoi**.

## Modules ETL (23)

22 modules par dataset plus le noyau d'orchestration. Aucun n'a été retiré : la parité de phase 2 passe par eux.

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| api/backend/app/etl | module-etl | garder | Noyau d'orchestration : `runner.py`, `publication.py`, `hooks.py`, protocole de swap scellé | `3394395a` |
| api/backend/app/etl_antennes | module-etl | garder | Antennes relais ANFR | `3394395a` |
| api/backend/app/etl_arcep | module-etl | garder | Ma Connexion Internet (Arcep) | `3394395a` |
| api/backend/app/etl_ban | module-etl | garder | Base Adresse Nationale — socle d'identité adresse | `3394395a` |
| api/backend/app/etl_bdtopo | module-etl | garder | BD TOPO bâtiments | `3394395a` |
| api/backend/app/etl_cadastre | module-etl | garder | Cadastre Etalab (parcelles) | `3394395a` |
| api/backend/app/etl_copropriete | module-etl | garder | Registre national des copropriétés | `3394395a` |
| api/backend/app/etl_demographie | module-etl | garder | Recensement de la population | `3394395a` |
| api/backend/app/etl_dpe | module-etl | garder | Diagnostics de performance énergétique — pipeline, réconciliation, cycle de vie | `3394395a` |
| api/backend/app/etl_dvf | module-etl | garder | Demandes de valeurs foncières | `3394395a` |
| api/backend/app/etl_encadrement_loyers | module-etl | garder | Encadrement des loyers | `3394395a` |
| api/backend/app/etl_equipements | module-etl | garder | Base permanente des équipements | `3394395a` |
| api/backend/app/etl_fiscalite | module-etl | garder | Fiscalité locale des particuliers | `3394395a` |
| api/backend/app/etl_loyers | module-etl | garder | Carte des loyers | `3394395a` |
| api/backend/app/etl_majic_pm | module-etl | garder | Fichiers des personnes morales (MAJIC) | `3394395a` |
| api/backend/app/etl_monuments | module-etl | garder | Monuments historiques | `3394395a` |
| api/backend/app/etl_permis | module-etl | garder | Permis de construire — source en quarantaine 503 (COR-07) | `3394395a` |
| api/backend/app/etl_revenus | module-etl | garder | Filosofi (revenus et pauvreté) | `3394395a` |
| api/backend/app/etl_rnb_liens | module-etl | garder | Liens RNB ↔ bâtiments | `3394395a` |
| api/backend/app/etl_scolaire | module-etl | garder | Carte scolaire des collèges | `3394395a` |
| api/backend/app/etl_transport | module-etl | garder | Arrêts de transport et gares | `3394395a` |
| api/backend/app/etl_vacance | module-etl | garder | LOVAC — arrondi `Decimal` du correctif COR-08 inclus | `3394395a` |
| api/backend/app/etl_zonage_abc | module-etl | garder | Zonage ABC (tension immobilière) | `3394395a` |

## Routeurs API (37)

Un routeur par domaine exposé. 92 routes au contrat `contracts/openapi.v1.json`.

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| api/backend/app/antennes/router.py | routeur | garder | Antennes relais | `3394395a` |
| api/backend/app/auth/router.py | routeur | garder | Comptes, clés API, quotas — conservé (D-04) | `3394395a` |
| api/backend/app/ban/router.py | routeur | garder | Adresses | `3394395a` |
| api/backend/app/batiments/router.py | routeur | garder | Bâtiments BD TOPO | `3394395a` |
| api/backend/app/bdnb/router.py | routeur | garder | BDNB (API amont) | `3394395a` |
| api/backend/app/billing/router.py | routeur | garder | Facturation Stripe — conservé, retrait reporté hors phase 1 (décision Ianis 2026-09-20) | `3394395a` |
| api/backend/app/cadastre/router.py | routeur | garder | Parcelles cadastrales | `3394395a` |
| api/backend/app/commune/router.py | routeur | garder | Profil commune (synthèse multi-sources) | `3394395a` |
| api/backend/app/connectivite/router.py | routeur | garder | Connectivité Arcep | `3394395a` |
| api/backend/app/copropriete/router.py | routeur | garder | Copropriétés | `3394395a` |
| api/backend/app/datasets/router.py | routeur | garder | Catalogue, fraîcheur et état de pipeline | `3394395a` |
| api/backend/app/demographie/router.py | routeur | garder | Démographie | `3394395a` |
| api/backend/app/dpe/router.py | routeur | garder | DPE — type non réduit en phase 1 (D-06) | `3394395a` |
| api/backend/app/dvf/router.py | routeur | garder | Ventes DVF | `3394395a` |
| api/backend/app/encadrement_loyers/router.py | routeur | garder | Encadrement des loyers | `3394395a` |
| api/backend/app/equipements/router.py | routeur | garder | Équipements BPE | `3394395a` |
| api/backend/app/errial/router.py | routeur | garder | ERRIAL (API amont) | `3394395a` |
| api/backend/app/fiscalite/router.py | routeur | garder | Fiscalité locale | `3394395a` |
| api/backend/app/foncier_pm/router.py | routeur | garder | Personnes morales (MAJIC) | `3394395a` |
| api/backend/app/georisques/router.py | routeur | garder | Géorisques (API amont) | `3394395a` |
| api/backend/app/health/router.py | routeur | garder | Santé applicative et sondes | `3394395a` |
| api/backend/app/logements/router.py | routeur | garder | Logements d'un bien — champ `logements` mort retiré, endpoint conservé | `3394395a` |
| api/backend/app/loyers/router.py | routeur | garder | Carte des loyers | `3394395a` |
| api/backend/app/monuments/router.py | routeur | garder | Monuments historiques | `3394395a` |
| api/backend/app/permis/router.py | routeur | garder | Permis — répond 503 `indisponible` (quarantaine assumée) | `3394395a` |
| api/backend/app/resolve/router.py | routeur | garder | Résolution commune / adresse / bien — chemin public `/v1/resolve` | `3394395a` |
| api/backend/app/revenus/router.py | routeur | garder | Revenus Filosofi | `3394395a` |
| api/backend/app/rnb/router.py | routeur | garder | RNB (API amont) | `3394395a` |
| api/backend/app/scolaire/router.py | routeur | garder | Carte scolaire | `3394395a` |
| api/backend/app/scoring/router.py | routeur | garder | Scores composites de commune | `3394395a` |
| api/backend/app/sirene/router.py | routeur | garder | Sirene / recherche d'entreprises (API amont) | `3394395a` |
| api/backend/app/transport/router.py | routeur | garder | Transports et gares | `3394395a` |
| api/backend/app/urbanisme/router.py | routeur | garder | API Carto GPU (API amont) | `3394395a` |
| api/backend/app/vacance/router.py | routeur | garder | Logements vacants LOVAC | `3394395a` |
| api/backend/app/validation_reports/router.py | routeur | garder | Rapports de validation ETL | `3394395a` |
| api/backend/app/zonage_abc/router.py | routeur | garder | Zonage ABC | `3394395a` |
| api/backend/app/zone/router.py | routeur | garder | Zones et périmètres | `3394395a` |

## Modules applicatifs sans routeur (6)

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| api/backend/app/admin | module | garder | Administration : utilisateurs, abonnements, quotas, attribution manuelle de plan (D-04) | `3394395a` |
| api/backend/app/common | module | garder | Socle : catalogue de sources, schémas, contrôle de migration au boot, matviews | `3394395a` |
| api/backend/app/data_quality | module | garder | Contrôles de qualité de données | `3394395a` |
| api/backend/app/golden | module | garder | Cas golden et rejeu de référence | `3394395a` |
| api/backend/app/scheduler | module | garder | `run_job.py`, `jobs.py`, `locks.py`, `monitoring.py` — seule voie d'exécution planifiée depuis le retrait du processus | `3394395a` |
| api/backend/app/validation | module | garder | Validation d'import (contre-exemples COR-01 / COR-02) | `3394395a` |

## Catalogue des sources (29 entrées)

Autorité statique unique de provenance : identité, producteur, licence, couverture, cadence (`api/backend/app/common/source_catalog.py`). 22 datasets à stockage local, 6 API amont proxifiées, 1 synthèse multi-sources. Le chiffre « 27 datasets en V1 » du cadrage du projet (suivi interne) est un périmètre produit et ne se déduit pas mécaniquement de cette table — écart signalé, à trancher en phase 2.

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| api/backend/app/common/source_catalog.py#"ban": DatasetCatalogEntry | dataset | garder | Base Adresse Nationale (BAN) — DINUM/IGN, Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"cadastre": DatasetCatalogEntry | dataset | garder | Cadastre Etalab — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"dvf": DatasetCatalogEntry | dataset | garder | Demandes de valeurs foncières — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"dpe": DatasetCatalogEntry | dataset | garder | DPE ADEME — Licence Ouverte 2.0, ETL local, cadence officielle hebdomadaire | `3394395a` |
| api/backend/app/common/source_catalog.py#"loyers": DatasetCatalogEntry | dataset | garder | Carte des loyers — licence non spécifiée, mention de source obligatoire | `3394395a` |
| api/backend/app/common/source_catalog.py#"batiments": DatasetCatalogEntry | dataset | garder | BD TOPO bâtiments — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"monuments": DatasetCatalogEntry | dataset | garder | Monuments historiques — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"connectivite": DatasetCatalogEntry | dataset | garder | Ma Connexion Internet (Arcep) — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"scolaire": DatasetCatalogEntry | dataset | garder | Carte scolaire des collèges — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"transports": DatasetCatalogEntry | dataset | garder | Arrêts de transport — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"gares": DatasetCatalogEntry | dataset | garder | Gares SNCF — ODbL 1.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"encadrement_loyers": DatasetCatalogEntry | dataset | garder | Encadrement des loyers — ODbL 1.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"foncier_pm": DatasetCatalogEntry | dataset | garder | Personnes morales MAJIC — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"fiscalite": DatasetCatalogEntry | dataset | garder | Fiscalité locale des particuliers — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"zonage_abc": DatasetCatalogEntry | dataset | garder | Zonage ABC — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"revenus": DatasetCatalogEntry | dataset | garder | Filosofi — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"demographie": DatasetCatalogEntry | dataset | garder | Recensement de la population — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"copropriete": DatasetCatalogEntry | dataset | garder | Registre national des copropriétés — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"permis": DatasetCatalogEntry | dataset | garder | Permis de construire — couverture `unknown`, quarantaine 503 (COR-07) | `3394395a` |
| api/backend/app/common/source_catalog.py#"antennes": DatasetCatalogEntry | dataset | garder | Antennes relais ANFR — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"equipements": DatasetCatalogEntry | dataset | garder | Base permanente des équipements — Licence Ouverte 2.0, ETL local | `3394395a` |
| api/backend/app/common/source_catalog.py#"vacance": DatasetCatalogEntry | dataset | garder | LOVAC — Licence Ouverte 2.0, ETL local ; stock de données non réparé (KI-D1) | `3394395a` |
| api/backend/app/common/source_catalog.py#"bdnb": DatasetCatalogEntry | dataset | garder | BDNB — API amont proxifiée, couverture suivant le fournisseur | `3394395a` |
| api/backend/app/common/source_catalog.py#"errial": DatasetCatalogEntry | dataset | garder | ERRIAL — API amont proxifiée | `3394395a` |
| api/backend/app/common/source_catalog.py#"georisques": DatasetCatalogEntry | dataset | garder | Géorisques — API amont proxifiée | `3394395a` |
| api/backend/app/common/source_catalog.py#"rnb": DatasetCatalogEntry | dataset | garder | Référentiel national des bâtiments — API amont proxifiée | `3394395a` |
| api/backend/app/common/source_catalog.py#"sirene": DatasetCatalogEntry | dataset | garder | API Sirene — API amont proxifiée | `3394395a` |
| api/backend/app/common/source_catalog.py#"urbanisme": DatasetCatalogEntry | dataset | garder | API Carto GPU — API amont proxifiée | `3394395a` |
| api/backend/app/common/source_catalog.py#"commune": DatasetCatalogEntry | dataset | garder | Profil commune — synthèse multi-sources, pas une source primaire | `3394395a` |

## Groupes de routes frontend (5)

Premier niveau de `frontend/src/routes/`.

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| frontend/src/routes/(app) | routes-front | garder | Application connectée : fiches, comparateur, compte, clés API, facturation | `3394395a` |
| frontend/src/routes/(auth) | routes-front | garder | Connexion, inscription, réinitialisation | `3394395a` |
| frontend/src/routes/(marketing) | routes-front | garder | Site public prérendu : accueil (recherche directe depuis 03-05), datasets, mentions légales, confidentialité | `3394395a` |
| frontend/src/routes/__api | routes-front | garder | Points d'entrée internes du serveur SvelteKit | `3394395a` |
| frontend/src/routes/__version | routes-front | garder | Empreinte de build servie au client | `3394395a` |

## Répertoires de tests

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| api/backend/tests | tests | garder | 279 fichiers Python ; 4 210 tests collectés à HEAD, 4 185 passés sur ce poste (KI-03) | `3394395a` |
| api/backend/tests/integration | tests | garder | 49 fichiers — joués par le harnais PostGIS jetable, liste et ordre lus dans `ci-data-contracts.yml` | `3394395a` |
| api/backend/tests/oracles | tests | garder | 5 fichiers — oracles stdlib, valeur principale de la base | `3394395a` |
| api/backend/tests/fixtures | tests | garder | 40 fichiers ; deux transférées ici (`51-09-SUMMARY.md`, `cor06-api-fixture.json`) | `3394395a` |
| api/backend/tests/etl | tests | garder | 13 fichiers | `3394395a` |
| api/backend/tests/data_quality | tests | garder | 17 fichiers | `3394395a` |
| api/backend/tests/scheduler | tests | garder | 7 fichiers ; les tests du jobstore persisté sont partis avec le processus | `3394395a` |
| frontend/tests/e2e | tests | garder | 40 specs Playwright ; 486 tests joués en `-Matrix` (Chromium, Firefox, WebKit) | `3394395a` |
| frontend/src | tests | garder | 89 fichiers `*.test.ts` vitest ; 1 487 tests verts | `3394395a` |

## Migrations et contrats

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| api/backend/migrations/versions | migration | garder | 96 révisions Alembic — aucune supprimée, aucune réécrite (prohibition de phase 1) | `3394395a` |
| contracts/fiche-fields.v1.json | contrat | garder | Contrat sémantique versionné : 76 champs, 9 formateurs, 5 états d'absence — repris comme acquis (I-5) | `3394395a` |
| contracts/openapi.v1.json | contrat | adapter | Instantané OpenAPI : 3 chemins `/v1/explorer/*` et le schéma `LogementItem` retirés, fichier normalisé en LF | `f92d460` |
| contracts/golden-cases/v1/manifest.json | contrat | garder | Manifeste des cas golden | `3394395a` |
| contracts/golden-cases/v1/schema.json | contrat | garder | Schéma des cas golden | `3394395a` |
| contracts/public-demo/v1/snapshot.json | contrat | retirer | Phase 3, D-38 : instantané des compteurs et de la fiche de démonstration de la landing, supprimée ; son générateur et ses deux validateurs sont partis avant lui, ses deux chemins surveillés en CI avec lui | `3bb63da` |

## Infrastructure

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| infra/ansible.cfg | infra | garder | Configuration Ansible de l'ancien hébergement | `3394395a` |
| infra/site.yml | infra | garder | Playbook de production — décrit un hôte arrêté, conservé comme référence de phase 5 | `3394395a` |
| infra/preproduction.yml | infra | garder | Playbook de préproduction — même statut | `3394395a` |
| infra/backup-accounts.yml | infra | retirer | Playbook de sauvegarde des comptes hérité, retiré par 05-01 (KI-P13) : remplacé par la sauvegarde quotidienne des comptes | `82aa0be6` |
| infra/group_vars | infra | garder | Variables Ansible (sans secret : `vault.yml` non transféré) | `3394395a` |
| infra/inventory | infra | privé | Privé depuis le 2026-11-12 (D-140, 05-07 lot 4), absent de l'export public. Écrit ici : Inventaires Ansible (préproduction, VPS démo) | `3394395a` |
| infra/systemd | infra | garder | 7 timers systemd — unique voie d'exécution planifiée depuis le retrait d'APScheduler | `3394395a` |
| infra/templates | infra | garder | Gabarits d'environnement ; `env.j2` garde ses entrées `STRIPE_*` (report Stripe) | `3394395a` |
| infra/preproduction | infra | garder | Scripts de qualification de préproduction ; orphelins depuis le retrait des workflows `cd-*` (KI-P5) | `3394395a` |
| infra/README.md | infra | garder | Description de l'exploitation d'origine | `3394395a` |
| infra/preproduction-seed-tables.json | infra | garder | Liste des tables semées en préproduction | `3394395a` |
| infra/.ansible-lint | infra | garder | Configuration de lint Ansible | `3394395a` |
| infra/vault.yml | infra | retirer | Vault Ansible chiffré de l'ancien hébergement : non transféré, « secrets hors dépôt » (AGENTS.md) prime sur la copie brute (D-01) | `3394395a` |

## Workflows GitHub Actions — 5 conservés sur 18

Critère de tri appliqué fichier par fichier : retiré s'il dépend d'un label auto-hébergé, d'une URL de production ou de préproduction, de Cloudflare, d'un secret de service vivant ou d'un déploiement ; conservé sinon.

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| .github/workflows/ci-api.yml | workflow | adapter | Lint ruff + pytest hors base ; porté sur `ubuntu-latest`, aligné sur `scripts/test-backend.ps1` | `9c3fdc8` |
| .github/workflows/ci-frontend.yml | workflow | adapter | Gates node, `svelte-check`, vitest, build strict, prerender, Playwright ; clé publique littérale | `9c3fdc8` |
| .github/workflows/ci-data-contracts.yml | workflow | adapter | Suites hors ligne + harnais PostGIS jetable + build de l'image de récupération | `9c3fdc8` |
| .github/workflows/secret-scan.yml | workflow | adapter | gitleaks 8.29.1 épinglé et vérifié par somme, sans filtre `paths` | `9c3fdc8` |
| .github/workflows/security-audit.yml | workflow | adapter | `pip-audit` + `npm audit` fail-closed ; **rouge sur `master`**, trois avis réels (KI-P6) | `9c3fdc8` |
| .github/workflows/cd-api.yml | workflow | retirer | `self-hosted, datacommune-prod`, `environment: production` : déploiement de l'ancien conteneur de production arrêté | `c748a00` |
| .github/workflows/cd-api-dev.yml | workflow | retirer | `self-hosted, datacommune-preprod` : déploiement du CT 277 arrêté | `c748a00` |
| .github/workflows/cd-frontend.yml | workflow | retirer | `wrangler deploy` vers le Worker supprimé à `cf8a1bd` ; `CLOUDFLARE_*`, `SENTRY_*` | `c748a00` |
| .github/workflows/cd-frontend-dev.yml | workflow | retirer | `wrangler deploy --config wrangler.dev.jsonc`, fichier supprimé à `cf8a1bd` | `c748a00` |
| .github/workflows/cd-preproduction.yml | workflow | retirer | Coordinateur des deux `*-dev` ; garde sur `github.repository` de l'ancien dépôt | `c748a00` |
| .github/workflows/cd-release.yml | workflow | retirer | Coordinateur de mise en production : dispatche `cd-frontend` puis `cd-api`, tous deux retirés | `c748a00` |
| .github/workflows/privacy-canary.yml | workflow | retirer | Sonde la production vivante (`api.datacommune.fr`), API Cloudflare, secret de compte interne | `c748a00` |
| .github/workflows/provision-internal-account.yml | workflow | retirer | Crée un compte sur l'API vivante avec trois secrets de service | `c748a00` |
| .github/workflows/rollback-api.yml | workflow | retirer | Retour arrière d'un déploiement qui n'existe plus | `c748a00` |
| .github/workflows/select-golden-live.yml | workflow | retirer | Lit la base de production en lecture seule (`DC_GOLDEN_READONLY_DSN`) | `c748a00` |
| .github/workflows/verify-exact-resolution-live.yml | workflow | retirer | Rejeu base → API sur l'instance vivante | `c748a00` |
| .github/workflows/verify-golden-live.yml | workflow | retirer | Rejeu base → API sur l'instance vivante | `c748a00` |
| .github/workflows/verify-phase44-live.yml | workflow | retirer | Rejeu base → API sur l'instance vivante | `c748a00` |
| .github/actionlint.yaml | workflow | retirer | Son seul contenu était les trois labels de runner auto-hébergé | `c748a00` |

## Surfaces et dépendances retirées après transfert (D-03)

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| api/backend/app/explorer | surface | retirer | Exploration multi-datasets générique, remplacée par les Fiches en v3.0 : aucun client frontend, aucun appelant hors `main.py` | `aeb92dc` |
| api/backend/tests/test_explorer_contract.py | tests | retirer | Test de contrat parti avec sa surface | `aeb92dc` |
| api/backend/tests/test_explorer_csv_escape.py | tests | retirer | Test d'échappement CSV parti avec sa surface | `aeb92dc` |
| api/backend/tests/integration/test_explorer_contract.py | tests | retirer | Test d'intégration parti avec sa surface | `aeb92dc` |
| api/backend/app/logements/schemas.py#class LogementItem(BaseModel) | surface | retirer | Carte logement servie `[]` en dur depuis le 2026-08-19, avec trois gardes sur un champ que rien ne pouvait remplir ; l'endpoint `/v1/bien/logements` reste | `da598c4` |
| frontend/src/lib/api/logements.ts#export interface LogementItem | surface | retirer | Le client remettait le champ mort à `[]` une troisième fois | `da598c4` |
| api/backend/app/common/sentry.py | dependance | retirer | Télémétrie tierce jamais armée (DSN vide partout) et seule sortie de données du serveur ; `capture_alert` sans appelant applicatif | `a07247f` |
| api/backend/tests/test_sentry.py | tests | retirer | 18 tests partis avec le module | `a07247f` |
| api/backend/pyproject.toml#sentry-sdk | dependance | retirer | Dépendance backend retirée de `pyproject.toml` et de `uv.lock` | `a07247f` |
| frontend/src/lib/utils/sentry-options.ts | dependance | retirer | Même motif côté navigateur : seule sortie de données du visiteur vers un tiers ; `/confidentialite` déclarait un sous-traitant que le code n'appelait pas | `ed70222` |
| frontend/package.json#@sentry/sveltekit | dependance | retirer | Dépendance frontend retirée ; 103 entrées de `package-lock.json` avec elle | `ed70222` |
| api/backend/app/scheduler/main.py | dependance | retirer | Processus APScheduler déjà hors service en production (`profiles: [disabled-in-prod]`), remplacé par 7 timers systemd ; la fonction reste, le processus part (D-05) | `f61fb0e` |
| api/backend/app/scheduler/__main__.py | dependance | retirer | Point d'entrée `python -m app.scheduler` parti avec le processus | `f61fb0e` |
| api/backend/tests/test_heartbeat_atomic.py | tests | retirer | 3 tests du heartbeat du processus retiré | `f61fb0e` |
| frontend/wrangler.jsonc | dependance | retirer | Sortie du verrouillage d'hébergement Worker : la cible est décidée en phase 5 (D-03) | `cf8a1bd` |
| frontend/wrangler.dev.jsonc | dependance | retirer | Configuration Worker de développement | `cf8a1bd` |
| frontend/_headers | dependance | retirer | En-têtes de sécurité S4 appliqués par le routeur d'actifs Workers — **couverture perdue, consignée en KI-P3**, pas remplacée en silence | `cf8a1bd` |
| frontend/scripts/clean-cloudflare-output.mjs | dependance | retirer | Nettoyage de la sortie Worker | `cf8a1bd` |
| frontend/scripts/deploy-through-ci.mjs | dependance | retirer | Déploiement Worker par la CI | `cf8a1bd` |
| frontend/package.json#adapter-cloudflare | dependance | retirer | Adaptateur remplacé par `@sveltejs/adapter-node@5.5.7`, épinglé, seul paquet ajouté de toute la phase (approuvé par Ianis le 2026-09-20) | `cf8a1bd` |
| api/backend/tests/test_release_recovery_workflow.py | tests | retirer | Décrivait `cd-release.yml`, retiré | `c748a00` |
| api/backend/tests/test_golden_workflow_contract.py | tests | retirer | Décrivait `verify-golden-live.yml` ; sa garde CR-02 a été **déplacée** dans `test_snapshot_coverage_contract.py`, pas supprimée (retirée à son tour en 03-05 avec l'instantané qu'elle gardait, D-38) | `c748a00` |
| api/backend/tests/test_golden_selection_workflow_contract.py | tests | retirer | Décrivait `select-golden-live.yml`, retiré | `c748a00` |
| api/backend/tests/test_privacy_canary_workflow.py | tests | retirer | Décrivait `privacy-canary.yml`, retiré | `c748a00` |
| api/backend/tests/test_provision_internal_account.py#provision-internal-account.yml | tests | adapter | Amputé de ses seules assertions de workflow retiré ; ses tests du script `provision_internal_account.py`, toujours présent, sont conservés | `12575d7` |
| frontend/src/lib/components/fiche/CarteLogement.svelte | surface | retirer | Phase 3, D-41 : composant orphelin depuis le retrait des cartes logement (`da598c4`), aucun import ; le type `CarteLogementModel` de `logements-view.ts` reste | `7d09120` |
| frontend/src/lib/components/fiche/CarteLogement.test.ts | tests | retirer | Test parti avec son composant (D-41) | `7d09120` |
| frontend/src/lib/utils/export.ts | surface | retirer | Phase 3, D-41 : export CSV/XLSX de l'ancien explorateur, aucun appelant ; seul importateur de `xlsx` | `0461c1f` |
| frontend/src/lib/utils/export.test.ts | tests | retirer | Test parti avec son module (D-41) | `0461c1f` |
| frontend/package.json#"xlsx" | dependance | retirer | Phase 3, D-41 : installée depuis `cdn.sheetjs.com`, hors registre npm, sans autre importateur ; verrou modifié par soustraction seule | `0461c1f` |
| frontend/tests/e2e-mobile.ts | tests | retirer | Phase 3, D-41 : script manuel visant `/workspace` (supprimée) et écrivant hors du dépôt, lancé par aucune suite | `79268e2` |
| frontend/package.json#"marked" | dependance | retirer | Phase 3, D-41 : aucun import ni `require`, aucun paquet ne l'exige | `00c2cd8` |
| frontend/package.json#"@tanstack/table-core" | dependance | retirer | Phase 3, D-41 : aucun import, aucun paquet ne l'exige | `27843ff` |
| frontend/tests/e2e/phase17-uat.spec.ts | tests | retirer | Phase 3, D-41 : spec `legacy` (backend local réel, captures sous chemin Windows absolu), jamais rejouée ; entrée du manifeste retirée | `f59824d` |
| frontend/tests/e2e/phase18-uat.spec.ts | tests | retirer | Même motif que `phase17-uat` (D-41) | `f59824d` |
| frontend/tests/e2e/phase19-uat.spec.ts | tests | retirer | Même motif que `phase17-uat` (D-41) | `f59824d` |
| frontend/src/routes/(marketing)/docs | surface | retirer | Phase 3, D-39 : chargeait la référence Scalar depuis `cdn.jsdelivr.net`, script tiers sur une page prérendue sans en-têtes de sécurité (KI-P3) ; remplacée par `/developpeurs`, qui renvoie à la référence servie par l'API | `dead2d9` |
| frontend/src/lib/config.ts#SCALAR_CDN_URL | dependance | retirer | Adresse CDN de Scalar, seul appelant `/docs` (D-39) | `dead2d9` |
| frontend/src/lib/config.ts#DOCS_URL | surface | retirer | Chemin `/docs`, liens repointés vers `/developpeurs` (D-39) | `dead2d9` |
| frontend/src/lib/config.ts#QUICK_KEY_URL | surface | retirer | Constante sans appelant, relevée par la recherche d'appelants du retrait de `/docs` | `dead2d9` |
| frontend/src/routes/(marketing)/playground | surface | retirer | Phase 3, D-39 : bac à sable appelant l'API avec la clé publique ; remplacé par `/developpeurs` | `d130b95` |
| frontend/src/routes/(marketing)/playground/page.test.ts | tests | retirer | 4 tests vitest partis avec la route (D-39) | `d130b95` |
| frontend/tests/e2e/playground.spec.ts | tests | retirer | Spec `manual` (3 rouges déclarés) partie avec sa surface ; entrée du manifeste retirée, ce n'est pas un déclassement (D-39, D-52) | `d130b95` |
| frontend/src/routes/(marketing)/reference | surface | retirer | Phase 3, D-39 : référence rédigée à la main à côté de l'OpenAPI servie par l'API | `90e0551` |
| frontend/src/lib/data/endpoint-groups.ts | surface | retirer | Seuls importateurs : les pages `reference` (D-39) | `90e0551` |
| frontend/src/routes/(marketing)/guides | surface | retirer | Phase 3, D-39 : index et trois guides `.svx` à persona de l'ancienne landing | `49c85fb` |
| frontend/src/lib/layouts/GuideLayout.svelte | surface | retirer | Layout mdsvex des guides (D-39) | `49c85fb` |
| frontend/src/lib/components/marketing/Prose.svelte | surface | retirer | Seul appelant : `GuideLayout` (D-39) | `49c85fb` |
| frontend/src/lib/components/DevJourney.svelte | surface | retirer | Rail des pages développeur `docs`, `reference`, `playground`, `guides`, toutes retirées ; `guides` était le dernier appelant (D-39) | `49c85fb` |
| frontend/package.json#"mdsvex" | dependance | retirer | Ne servait que les `.svx` des guides ; préprocesseur et extension retirés de `svelte.config.js` ; verrou modifié par soustraction seule (D-39) | `49c85fb` |
| frontend/package.json#"@tailwindcss/typography" | dependance | retirer | Plugin d'`app.css` dont les classes `prose` n'avaient qu'une cible, `Prose.svelte` (D-39) | `49c85fb` |
| frontend/svelte.config.js#mdsvex | config | retirer | Préprocesseur `mdsvex` et extension `.svx` partis avec les guides (D-39) | `49c85fb` |
| frontend/src/routes/(marketing)/roadmap | surface | retirer | Phase 3, D-39 : feuille de route datée publique ; les liens des écrans commune et dataset sont réécrits sans promesse de calendrier | `70cb9ec` |
| frontend/src/lib/components/marketing/RoadmapTimeline.svelte | surface | retirer | Seul appelant : la route `roadmap` (D-39) | `70cb9ec` |
| frontend/src/routes/(app)/prospection | surface | retirer | Phase 3, D-39 : tableau de prospection des passoires ; `/v1/zone/resolve` reste servie par l'API | `5793139` |
| frontend/src/routes/(app)/prospection/page.test.ts | tests | retirer | 2 tests vitest partis avec la route (D-39) | `5793139` |
| frontend/src/lib/api/zone.ts | surface | retirer | Client de `/v1/zone/resolve`, seul appelant : la route `prospection` (D-39) | `5793139` |
| frontend/src/lib/api/zone.test.ts | tests | retirer | 6 tests partis avec leur module (D-39) | `5793139` |
| frontend/src/lib/data/prospection-levier.ts | surface | retirer | Levier réglementaire de la table de prospection (D-39) | `5793139` |
| frontend/src/lib/data/prospection-levier.test.ts | tests | retirer | 5 tests partis avec leur module (D-39) | `5793139` |
| frontend/src/lib/types/prospection.ts | surface | retirer | Contrat de la table de prospection (`PROSPECTION_COLUMNS`) (D-39) | `5793139` |
| frontend/src/app.css#.levier-fort | surface | retirer | Badges `.levier-*`, seule cible : la table de prospection (D-39) | `5793139` |
| frontend/src/lib/components/address-search-navigation.test.ts | tests | retirer | Phase 3, D-38/D-50 : ses deux surfaces étaient le `Hero` de la landing et le routage d'adresse de `HeroSearch` dans `mon-espace`, qui passe à `CommuneSearch` | `ef17b3c` |
| frontend/src/lib/components/marketing/Hero.svelte | surface | retirer | Phase 3, D-38 : héro de la landing ; dernier appelant, le test précédent | `5f0054d` |
| frontend/src/lib/components/marketing/Hero.test.ts | tests | retirer | 3 tests partis avec leur composant (D-38) | `5f0054d` |
| frontend/src/lib/components/marketing/HeroSearch.svelte | surface | retirer | Phase 3, D-38/D-50 : recherche de la landing ; appelants `Hero` et `mon-espace`, remplacée par `CommuneSearch` | `5f0054d` |
| frontend/src/lib/components/marketing/HeroSearch.test.ts | tests | retirer | 5 tests partis avec leur composant (D-38) | `5f0054d` |
| frontend/src/lib/components/marketing/FicheApercu.svelte | surface | retirer | Phase 3, D-38 : aperçu de fiche construit depuis l'instantané ; seul appelant `Hero` | `5f0054d` |
| frontend/src/lib/components/marketing/FAQ.svelte | surface | retirer | Phase 3, D-38 : FAQ de la landing, aucun appelant après la réécriture de l'accueil | `3c2f920` |
| frontend/src/lib/components/marketing/CTA.svelte | surface | retirer | Phase 3, D-38 : appel final de la landing, aucun appelant | `3c2f920` |
| frontend/src/lib/components/marketing/TrustBanner.svelte | surface | retirer | Phase 3, D-38 : bandeau de compteurs tirés de l'instantané, aucun appelant | `3c2f920` |
| frontend/src/lib/components/marketing/FicheIndex.svelte | surface | retirer | Phase 3, D-38 : index de fiche tiré de l'instantané, aucun appelant | `3c2f920` |
| frontend/src/lib/data/surfaces-coherence.test.ts#buildPublicSnapshot | tests | retirer | Phase 3, D-38 : quatre cas qui lisaient la projection de la landing retirés ; les cas des deux fiches restent | `3d2a81c` |
| frontend/scripts/export-public-snapshot.mjs | dependance | retirer | Phase 3, D-38 : régénérait l'instantané public depuis `marketing-proof.ts`, aucun appelant | `e48f29c` |
| api/backend/tests/test_marketing_facts_contract.py | tests | retirer | Phase 3, D-38 : verrouillait les compteurs de l'instantané contre le catalogue ; compteurs partis avec la landing | `9c8a312` |
| frontend/src/lib/data/marketing-proof.ts | surface | retirer | Phase 3, D-38 : compteurs et fiche de démonstration de la landing construits depuis une fixture golden ; plus aucun importateur | `95d1399` |
| frontend/src/lib/data/marketing-proof.test.ts | tests | retirer | 25 tests partis avec leur module : gel de l'instantané, catégories de compteurs, scan de clés ré-identifiantes (D-38) | `95d1399` |
| frontend/src/lib/data/commune-comparison-load.ts | surface | retirer | Phase 3, D-03 (03-10) : deux lectures `/v1/commune` et relance par commune ; le comparateur lit `/v1/communes/comparaison` en un appel (D-48) | `0eb062b` |
| frontend/src/lib/data/commune-comparison-load.test.ts | tests | retirer | Parti avec son module ; invariants de panne et de relance repris dans `comparer/page.test.ts` (03-10) | `0eb062b` |
| frontend/tests/e2e/commune-comparison-real.spec.ts | tests | retirer | Spec `manual` de capture réelle (corpus jamais commité, lot 60-05 de l'ancien dépôt) ; entrée du manifeste retirée, ce n'est pas un déclassement ; invariant repris par le rejeu sur données réelles de 03-13 | `646a8c2` |
| frontend/tests/e2e/fixtures/commune-comparison-real.ts | tests | retirer | Fixture de la spec précédente (03-10) | `646a8c2` |
| frontend/src/lib/data/commune-comparison-real-harness.test.ts | tests | retirer | Harnais vitest de la même capture réelle (03-10) | `646a8c2` |
| frontend/src/lib/data/commune-comparison.ts#composeCommuneComparison | surface | retirer | Phase 3, D-03 (03-10) : composition à deux colonnes, remplacée par `composeComparaison` sur la réponse de l'API | `ba8c022` |
| frontend/src/lib/data/commune-comparison-indicators.test.ts | tests | retirer | Tests de la composition à deux colonnes ; sa fixture reste (cinq autres appelants) (03-10) | `ba8c022` |
| frontend/src/lib/components/CommuneAutocomplete.svelte | surface | retirer | Phase 3, D-42 (03-10) : autocomplétion par `geo.api.gouv.fr` depuis le navigateur ; dernier appelant, l'ancien comparateur | `614f855` |
| frontend/src/lib/components/CommuneAutocomplete.test.ts | tests | retirer | Parti avec son composant (03-10) | `614f855` |
| frontend/src/lib/api/geo.ts | dependance | retirer | Phase 3, D-42 (03-10) : client de l'API Découpage administratif tierce ; dernier appelant, `CommuneAutocomplete` | `1f123df` |
| frontend/src/lib/data/commune-grid.ts#communeRowsToCsv | surface | retirer | Phase 3, D-03 (03-10) : constructeur CSV par thème sans appelant d'exécution ; l'export de la fiche passe par `themesPourExport` | `97453bf` |
| frontend/src/routes/(marketing)/datasets/[slug]/+page.svelte | surface | retirer | Phase 3, H4 (03-11) : page de détail d'un jeu remplacée par une redirection 308 vers sa carte `/datasets#{clé}` ; la documentation d'endpoint qu'elle portait est celle de l'OpenAPI servie (`/developpeurs`) | `1cad3e4` |
| frontend/src/lib/data/marketing-datasets-licences.test.ts | tests | retirer | Rendu LIC-01 de la page de détail ; invariants repris sur la carte servie par `/v1/datasets` (`DatasetCard.test.ts`) (03-11) | `1cad3e4` |
| frontend/src/lib/data/marketing-datasets.ts | surface | retirer | Phase 3, D-40 (03-11) : catalogue frontend de 1 136 lignes, double vérité avec `/v1/datasets` ; quarantaines et limites de la source DVF reportées mot pour mot au catalogue backend, servies par l'API | `924437c` |
| frontend/src/lib/data/marketing-datasets.test.ts | tests | retirer | Parti avec son module : il vérifiait la copie d'endpoint des pages de détail, disparue avec elles (03-11) | `924437c` |
| api/backend/tests/test_marketing_datasets_openapi_contract.py | tests | retirer | Phase 3, D-40 (03-11) : garde de dérive du catalogue frontend contre l'OpenAPI ; son objet a disparu, le contrat reste gardé par `test_openapi_snapshot.py` | `924437c` |

## Composants non transférés (D-01 / D-09)

Présents à `3394395a`, absents ici par décision : ils n'ont jamais été copiés. Le suivi de l'ancien dépôt en fait partie ; il n'a pas de ligne ci-dessous parce que ce dépôt a son propre suivi interne, neuf, qui n'en reprend rien. Récupérables en une commande : `git -C <ancien dépôt> show 3394395a:<chemin>`.

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| docs/adr | non-transféré | retirer | 8 ADR de l'ancien dépôt : non transféré (D-01) ; les décisions en vigueur sont réécrites dans `docs/DECISIONS.md` | `3394395a` |
| docs/ARCHITECTURE.md | non-transféré | retirer | Documentation d'architecture périmée : non transférée (D-01) | `3394395a` |
| docs/database | non-transféré | retirer | Documentation de schéma générée : non transférée (D-01) | `3394395a` |
| docs/etl-runbook.md | non-transféré | retirer | Runbook ETL de l'ancien hébergement : non transféré (D-01) | `3394395a` |
| docs/preproduction-runbook.md | non-transféré | retirer | Runbook de préproduction : non transféré (D-01) | `3394395a` |
| docs/reprise-2026-09-09 | non-transféré | retirer | Notes de reprise : non transférées (D-01) | `3394395a` |
| docs/reprise-2026-09-10 | non-transféré | retirer | Notes de reprise : non transférées (D-01) | `3394395a` |
| docs/stabilisation-2026-09-08 | non-transféré | retirer | Notes de stabilisation : non transférées (D-01) ; seules les **fixtures** lues par les tests ont été transférées | `3394395a` |
| docs/open-source-landscape | non-transféré | retirer | Étude de paysage : non transférée (D-01) | `3394395a` |
| docs/OPEN-SOURCE-PUBLIC-DATA-LANDSCAPE.md | non-transféré | retirer | Étude de paysage : non transférée (D-01) | `3394395a` |
| docs/screenshots | non-transféré | retirer | Captures d'écran : non transférées (D-01) | `3394395a` |
| tools | non-transféré | retirer | 12 fichiers, dont le vérificateur de preuves gitleaks : non transférés (D-09) — **garde perdue, consignée en KI-P4** | `3394395a` |
| mockups | non-transféré | retirer | 4 maquettes : non transférées (D-01) ; la refonte d'interface est un brief Astra de phase 3 | `3394395a` |
| TASKS.md | non-transféré | retirer | Suivi de tâches de l'ancien dépôt : non transféré (D-01) | `3394395a` |
| CLAUDE.md | agents | privé | Celui de l'ancien dépôt n'a pas été transféré (`3394395a`). Index privé écrit ici le 2026-11-04 pour les sessions d'agents (chemins du poste, machines) ; `AGENTS.md` fait foi ; absent de l'export public | `56d48891` |
| .cbmignore | non-transféré | retirer | Configuration d'outil non reprise (D-01) | `3394395a` |
| .tbls.yml | non-transféré | retirer | Configuration de documentation de schéma non reprise (D-01) | `3394395a` |

## Éléments d'instructions d'agents retirés à la copie — dérogation à D-01

D-01 dit « pas de sélection à la copie ». Ces six éléments y dérogent explicitement, sur arbitrage de Ianis du 2026-09-20 (option `remove`) : Claude Code charge automatiquement tout `CLAUDE.md` et tout `.claude/` des répertoires parents, et ces fichiers auraient décrit l'ancien dépôt comme consigne courante aux exécuteurs des plans 01-03 à 01-08. Blobs d'origine notés dans `01-02-SUMMARY.md` ; coût d'annulation nul.

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| api/CLAUDE.md | agents | retirer | Retiré de l'arbre de travail avant indexation (option `remove`, dérogation D-01 datée du 2026-09-20) | `1d1b344` |
| frontend/CLAUDE.md | agents | retirer | Retiré de l'arbre de travail avant indexation (option `remove`, dérogation D-01 datée du 2026-09-20) | `1d1b344` |
| api/.claude | agents | retirer | Retiré de l'arbre de travail avant indexation (option `remove`, dérogation D-01 datée du 2026-09-20) | `1d1b344` |
| frontend/.claude | agents | retirer | Retiré de l'arbre de travail avant indexation (option `remove`, dérogation D-01 datée du 2026-09-20) | `1d1b344` |
| frontend/.impeccable | agents | retirer | Retiré de l'arbre de travail avant indexation (option `remove`, dérogation D-01 datée du 2026-09-20) | `1d1b344` |
| frontend/.vscode | agents | retirer | Retiré de l'arbre de travail avant indexation (option `remove`, dérogation D-01 datée du 2026-09-20) | `1d1b344` |

## Racine, configuration et documentation de ce dépôt

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| README.md | racine | adapter | Réécrit ici : provenance nommée (D-08) et procédure locale unique ; AGENTS.md y renvoie | `e7ded57` |
| AGENTS.md | racine | garder | Écrit dans ce dépôt avant le transfert ; renvoie au README pour la procédure | `9f9d8b2` |
| .gitignore | racine | adapter | Union de `3394395a:.gitignore` et de l'existant, plus `.gsd/` et le verrou du suivi interne | `1d1b344` |
| .gitleaks.toml | racine | adapter | Une entrée `[[allowlists]]` ajoutée, bornée à un chemin, une règle et une valeur, avec date et preuve du faux positif | `1d1b344` |
| .gitattributes | racine | garder | Transféré tel quel | `3394395a` |
| security/dependency-exceptions.json | racine | garder | Registre d'exceptions de l'audit de dépendances, hors liste D-01 mais requis par `security-audit.yml` (fail-closed) ; registre **vide** depuis le 2026-08-23 | `3394395a` |
| api/docker-compose.yml | config | adapter | Volume nommé `pgdata` à la place du bind-mount `D:` (D-17, fin de la dérogation D-13) ; service `scheduler` retiré | `2a95b8a` |
| frontend/svelte.config.js | config | adapter | Import d'adaptateur : `@sveltejs/adapter-cloudflare` → `@sveltejs/adapter-node` | `cf8a1bd` |
| frontend/package.json | config | adapter | Scripts `wrangler` retirés, `deploy:guard` renommé `release:guard`, deux devDependencies remplacées par une | `cf8a1bd` |
| frontend/tests/e2e/honnetete-publique.spec.ts | tests | adapter | Sentinelle T10 **retournée, pas affaiblie** : interdit désormais qu'un sous-traitant reste déclaré après son retrait | `ed70222` |
| frontend/scripts/security-gates.test.mjs | tests | adapter | Sentinelle S4 retournée : interdit le retour d'un `_headers` inappliqué et exige que le hook lise la liste canonique entière | `cf8a1bd` |
| api/backend/tests/test_snapshot_coverage_contract.py | tests | retirer | Accueillait la garde CR-02 déplacée depuis `test_golden_workflow_contract.py` ; son seul objet, l'instantané public de la landing, est retiré (D-38) ; chemin sorti de `ci-data-contracts.yml` et du tuple de `test_resolve_logging.py` | `1ecfad1` |
| frontend/src/lib/data/__fixtures__/57-COPIE-DVF.md | tests | garder | Dossier d'arbitrage lu par `copie-dvf.test.ts`, relocalisé du suivi de l'ancien dépôt vers une fixture du dépôt | `1d1b344` |
| docs/audit-recentrage-2026-09-10 | docs | garder | Spécification DPE documentaire, utilisée en phase 4 — vient de la révision `9d903a46` (commit `ee8e691f`), pas de la base | `9d903a46` |
| docs/audit-stabilisation-2026-09-08 | docs | garder | 29 fichiers de fixtures lus par les tests transférés ; rien d'autre de l'ancien `docs/` | `3394395a` |
| docs/REPO-STATE.md | docs | garder | État du dépôt — écrit ici (D-10) | `b0db73a` |
| docs/KNOWN-ISSUES.md | docs | garder | Problèmes connus par registre — écrit ici (D-10) | `bfd915d` |
| docs/DECISIONS.md | docs | garder | Décisions en vigueur, invariants, points à revoir — écrit ici (D-10) | `plan 01-08` |
| docs/INVENTORY-3394395a.md | docs | garder | Le présent inventaire (DOC-01) | `plan 01-08` |

## Ajouts de la phase 2

Écrits dans ce dépôt ; motif et appelants aux SUMMARY de la phase 2.

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| api/backend/app/scope | module | garder | Écrit ici : Périmètre d'instance persisté en base (migration 097), CLI `python -m app.scope`, seule source de périmètre (D-21 à D-24, DATA-03) | `1b62e90` |
| (scripts de l'hyperviseur) | infra | privé | Privé depuis le 2026-11-10 (D-140), absent de l'export public. Écrit ici : base de travail sur l'hyperviseur : provisionnement, déploiement, imports mesurés, lectures `wb_reader`, archive des sources, rejeu golden (D-16, D-19, D-29) | `86b2445` |
| api/backend/scripts/extract_fidelity_reference.py | script | garder | Écrit ici : Références de fidélité 44/85 figées avec manifeste SHA-256 (D-25, D-26) | `c2f119f` |
| api/backend/scripts/measure_db_sizes.py | script | garder | Écrit ici : Tailles par relation et partition (PERF-00, D-33) | `c2f119f` |
| api/backend/scripts/check_commune_views.py | script | garder | Écrit ici : Contrôle des vues communales migrées (KI-D4) | `22a1729` |
| api/backend/scripts/export_import_manifest.py | script | garder | Écrit ici : Reçu immuable et mesuré de chaque import (PERF-00) | `2e82f77` |
| api/backend/scripts/compare_fidelity.py | script | garder | Écrit ici : Comparaison à trois niveaux contre les références figées (D-25, D-28) | `c350738` |
| api/backend/scripts/table_digest.py | script | garder | Écrit ici : Condensé de table du rejeu identique (DATA-02) | `9827128` |
| api/backend/scripts/audit_semantic_live.py | script | garder | Écrit ici : Audit sémantique des réponses réelles (SEM-02) | `150ad2b` |
| api/backend/scripts/semantic_live_demo.py | script | garder | Écrit ici : Démonstration du périmètre et des quatre états sur l'API réelle (DATA-03, SEM-01) | `fada0a9` |
| api/backend/scripts/measure_logical_bytes.py | script | garder | Écrit ici : Octets logiques par département (PERF-00) | `fd764e9` |
| api/backend/scripts/measure_route_latencies.py | script | garder | Écrit ici : Latences des routes principales (PERF-00) | `51ac7dd` |
| api/backend/scripts/measurement_key.py | script | garder | Écrit ici : Clé d'API de mesure créée puis révoquée (PERF-00) | `fada0a9` |
| api/backend/scripts/perf00_routes.json | script | garder | Écrit ici : Routes mesurées par `measure_route_latencies.py` (PERF-00) | `fada0a9` |

## Scripts

| Chemin | Type | Décision | Motif | SHA |
|---|---|---|---|---|
| scripts/chat_datacommune.py | script | garder | Client de conversation transféré, sans appelant de CI | `3394395a` |
| scripts/mapping/codebase.py | script | garder | Cartographie de code transférée | `3394395a` |
| scripts/mapping/export_database.py | script | garder | Export de schéma transféré | `3394395a` |
| scripts/deploy_preproduction.sh | script | garder | Chaîne de préproduction — **orpheline** depuis le retrait des workflows `cd-*` (KI-P5) | `3394395a` |
| scripts/preproduction_candidate.py | script | garder | Chaîne de préproduction — orpheline (KI-P5) | `3394395a` |
| scripts/qualify_preproduction.py | script | garder | Chaîne de préproduction — orpheline (KI-P5) | `3394395a` |
| scripts/seed_preproduction.py | script | garder | Chaîne de préproduction — orpheline (KI-P5) | `3394395a` |
| scripts/verify_preproduction.py | script | garder | Chaîne de préproduction — orpheline (KI-P5) | `3394395a` |
| scripts/verify-release-live.sh | script | garder | Contrôle post-publication SEC-02 — orphelin et décrivant un déploiement Worker retiré (KI-P5) | `3394395a` |
| scripts/check-toolchain.ps1 | script | garder | Écrit ici : relevé versionné des outils et de l'emplacement des données Docker | `546f35d` |
| scripts/test-backend.ps1 | script | garder | Écrit ici : lint, contrat sémantique, audit golden, pytest | `426ebbe` |
| scripts/test-postgis.ps1 | script | garder | Écrit ici : rejoue les appels de `ci-data-contracts.yml`, liste et ordre lus dans le workflow | `426ebbe` |
| scripts/test-frontend.ps1 | script | garder | Écrit ici : gates node, type-check, vitest, build + prerender | `426ebbe` |
| scripts/test-e2e.ps1 | script | garder | Écrit ici : specs `critical` sur Chromium, `-Matrix` ajoute Firefox et WebKit | `426ebbe` |
| scripts/test-all.ps1 | script | garder | Écrit ici : les quatre suites puis l'arrêt des ressources ; imprime la ligne `RESULT` | `426ebbe` |
| scripts/stop-resources.ps1 | script | garder | Écrit ici : détruit les projets compose jetables et libère 5173 / 4174 | `426ebbe` |
| scripts/inventory-check.py | script | garder | Écrit ici : contrôle de cohérence inventaire ↔ arbre (DOC-01) | `plan 01-08` |
| scripts/doc-code-ratio.py | script | garder | Écrit ici : mesure versionnée du ratio documentation / code (DOC-03, D-11) | `plan 01-08` |

---

*Contrôle : `python scripts/inventory-check.py docs/INVENTORY-3394395a.md`.*
*Pourquoi de chaque retrait : `docs/REPO-STATE.md`. Problèmes connus : `docs/KNOWN-ISSUES.md`.*
