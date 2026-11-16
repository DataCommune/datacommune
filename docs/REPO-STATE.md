# État du dépôt

Ce qui est là, d'où ça vient, ce qui a été retiré et pourquoi, ce qui reste faux. Trois pages au
plus (D-10). Détail par composant : [`INVENTORY-3394395a.md`](INVENTORY-3394395a.md) ;
raisonnement des retraits : les bilans de phase du suivi interne ;
problèmes ouverts : [`KNOWN-ISSUES.md`](KNOWN-ISSUES.md) ; décisions :
[`DECISIONS.md`](DECISIONS.md).

## Contenu et provenance

Le code vient de `DataCommune`, `origin/dev` @ `3394395a32e9a778c89930ad7544406da265163e`
(10/09/2026), copié **sans historique** le 19/09/2026 — commit de transfert `1d1b344`, tag local
`transfer-3394395a`. `docs/audit-recentrage-2026-09-10/` vient de la révision **`9d903a46`** du
même dépôt (commit `ee8e691f`) : spécification DPE documentaire, utilisée en phase 4.

### Transférés hors liste D-01

| Fichier | Origine | Commit | Pourquoi |
|---|---|---|---|
| `security/dependency-exceptions.json` | `3394395a` | `9c3fdc8` | Lu par `validate_dependency_audit.py --exceptions` ; sans lui le job `dependency-audit`, **fail-closed**, échoue. Aucun secret ; registre **vide** depuis le 2026-08-23 |
| 29 fixtures sous `docs/audit-stabilisation-2026-09-08/` | `3394395a` | `1d1b344` | Lues par des tests transférés. Rien d'autre de l'ancien `docs/` |
| `frontend/src/lib/data/__fixtures__/57-COPIE-DVF.md` | `3394395a` | `1d1b344` | Dossier lu par `copie-dvf.test.ts`, relocalisé depuis le suivi de l'ancien dépôt : un test ne lit jamais le suivi interne |

### Non transférés

Suivi de l'ancien dépôt, `docs/` ancien (ADR, runbooks, architecture, captures), `tools/` (12
fichiers), `mockups/` (4), `TASKS.md`, `CLAUDE.md` racine, `.cbmignore`, `.tbls.yml`, et
`infra/vault.yml` (vault Ansible chiffré : « secrets hors dépôt » prime sur la copie brute).

**Retirés à la copie, dérogation D-01 explicite** (option `remove`, arbitrage de Ianis,
2026-09-20) : `api/CLAUDE.md`, `frontend/CLAUDE.md`, `api/.claude/`, `frontend/.claude/`,
`frontend/.impeccable/`, `frontend/.vscode/` — Claude Code les aurait chargés comme consigne
courante. Blobs d'origine dans `01-02-SUMMARY.md`.

Les fichiers non suivis de l'ancien checkout sont dans l'archive privée
`DataCommune-Archives/2026-09-19-untracked/` : `INVENTORY.json` (205 fichiers) et
`tracked-modifications.patch` (ARC-01, 19/09).

**Conséquence assumée** : le vérificateur de preuves gitleaks de `tools/` n'est pas là, donc la
garde « les `[[allowlists]]` mordent encore » n'existe plus — **KI-P4**, pas remplacée par un
step vide.

## Retraits

Chaque retrait est **réversible** : `git show 3394395a:<chemin>` dans l'ancien dépôt, ou
`git revert` du commit ici (D-05). Un retrait = recherche d'appelants à résultat vide +
suppression + quatre suites vertes + un commit (D-03).

| Composant | Commit | Motif |
|---|---|---|
| `explorer/` (5 modules, 3 tests, 1 entrée CI, 3 chemins du contrat) | `aeb92dc` | Exploration générique remplacée par les Fiches ; aucun client frontend |
| Cartes logement (`LogementItem`, champ `logements`) | `da598c4` | Servi `[]` en dur depuis le 2026-08-19 ; **l'endpoint `/v1/bien/logements` reste** |
| `sentry-sdk[fastapi]` (`common/sentry.py`, 18 tests, `SENTRY_*`) | `a07247f` | DSN vide partout : le SDK ne s'initialisait jamais ; seule sortie de données hors du poste |
| `@sentry/sveltekit` (module, hooks, greffon Vite, déclarations RGPD) | `ed70222` | Même motif ; `/confidentialite` ne déclare plus un sous-traitant non appelé (sentinelle T10 retournée) |
| Processus APScheduler (`scheduler/main.py`, service des 7 compose, 7 tests) | `f61fb0e` | Hors service en production, remplacé par 7 timers systemd ; `run_job.py` et la dépendance restent |
| Adaptateur Worker Cloudflare (5 fichiers, 61 entrées de lockfile) | `cf8a1bd` | Sortie du verrouillage d'hébergement ; remplacé par `@sveltejs/adapter-node@5.5.7`, approuvé. Perte : KI-P3 |
| 13 workflows CD et « live », `actionlint.yaml`, 4 tests de contrat | `c748a00`, `12575d7` | Label auto-hébergé, URL de production, Cloudflare, secret ou déploiement ; plus aucun `secrets.` |

**Trois retraits listés par D-03 n'ont pas eu lieu** (RNB sans objet, `dpe_par_identifiant_ban`
refusé, dépendance `apscheduler` gardée) : `DECISIONS.md` § « D-03 amendée ». Le job
`preproduction-config` de `ci-api.yml` est parti avec son sujet, déjà rouge à ce SHA (`f61fb0e`).
Recherches d'appelants (vides après retrait) et mesures : `01-04` à `01-07-SUMMARY.md`.

### Retraits de la phase 2

Même procédure : `ETL_DEPARTEMENTS` et le seuil de 96 départements (`d04eb6e`) ; bind-mount
`D:` (`2a95b8a`, D-17) ; chaîne de sauvegarde hors site restic (`7563438`, OPS-03 amendé) ;
canal `rejected` de la réconciliation DPE (`3dc65e2`) ; `_refuse_out_of_coverage` de
`/v1/bien/logements` (`f19c772`). Appelants : SUMMARY 02-08, 02-14, 02-16, 02-18.

### Retraits de la phase 3

Même procédure ; appelants, mesures et commits au SUMMARY cité (`03-*-SUMMARY.md`).

| Retrait | SUMMARY |
|---|---|
| Code mort D-41 : `CarteLogement.svelte`, `utils/export.ts` et `xlsx`, `tests/e2e-mobile.ts`, `marked`, `@tanstack/table-core`, specs `legacy` 17 à 19 | `03-01` |
| Routes `docs` (Scalar depuis un CDN), `playground`, `reference`, `guides` avec `mdsvex` et `@tailwindcss/typography`, `roadmap`, `prospection` ; remplacées par `/developpeurs` | `03-02` |
| Ancien pied `marketing/Footer`, police Cinzel, ruban 44/85 | `03-03` |
| `CommunesTable.svelte` | `03-04` |
| Landing, sept composants marketing, `marketing-proof.ts`, chaîne de l'instantané public (`snapshot.json`, export, deux tests de contrat, entrées CI) | `03-05` |
| `Theme.svelte` | `03-08` |
| Autocomplétion tierce, ancien chargeur et ancienne composition du comparateur | `03-10` |
| Catalogue frontend `marketing-datasets.ts` (1 136 l.), remplacé par `/v1/datasets` ; 27 pages de détail en 308 | `03-11` |

## À rediscuter

**Facturation Stripe : reportée hors de la phase 1** — Ianis, 2026-09-20, **option (c)**.
`app/billing/` et `stripe[async]` restent **intacts** : le lot borné D-04 est dépassé sur quatre
mesures (`01-06-SUMMARY.md`). Suivi :
suivi interne (tâche du 2026-09-20).

**À ne pas « corriger » par erreur** : `api/.env.example`, `infra/templates/env.j2` et
`contracts/openapi.v1.json` gardent leurs `STRIPE_*` et leurs chemins `/billing/*`. Ce n'est pas
un oubli : elles alimentent du code vivant et partiront **avec** lui.

## Ce qui reste faux

[`KNOWN-ISSUES.md`](KNOWN-ISSUES.md) conserve les écarts hérités et les renvois de phase.
Les preuves sont datées ; les points de déploiement ne sont pas levés par les tests locaux.

- **Données** : LOVAC réparé (KI-D1), dump levé (KI-D3), zéro et hors couverture séparés
  (KI-D4). Constats historiques à requalifier : swap DPE (KI-D2), millésime vidé (KI-D6).
  Cadastre et connectivité réintégrés le 26/09 (02-21/22), sans nouveau contrôle distant ici.
- **Sens** : la dérivation du département transforme une incohérence en absence (KI-01) ; le
  type DPE admet trois états qu'aucun producteur n'émet (KI-04).
- **Couverture de la gate** : 10 tests sautés ici passent sur le runner Linux (KI-03).
- **CI** : au SHA historique `607b502`, `security-audit` était rouge sur trois avis non triés
  et quatre autres workflows étaient verts (KI-P6) ; état distant courant non contrôlé ici.
  Le scan de l’arbre de travail inclut les builds ignorés (KI-P7) ; procédure au README.
- **Déploiement** : statiques sans en-tête de sécurité (KI-P3) ; préproduction orpheline, deux
  scripts cassés (KI-P5) ; sauvegarde héritée dépréciée (KI-P13).
- **Publication du suivi** : rien ne rappellera de sortir le suivi avant un passage public (KI-P10).
- **Interface** : UI-SPEC en retard sur le code, questions ouvertes à Ianis (KI-U1 à KI-U13).
- **Acceptation** : recettes communes consignées en phase 3 avec réserves RES-1 à RES-3 ;
  aucun nouveau verdict humain déduit de cette campagne (KI-A1 à KI-A3).

## Preuves

Une preuve nomme sa commande, son SHA, ses versions et son registre (I-8) ; local, CI et
déploiement sont trois registres qui ne se déduisent pas l'un de l'autre.

**Gate initiale D-02**, SHA `d87f6e3` : quatre suites vertes ; détails et versions
dans `01-03-SUMMARY.md`. La preuve finale de phase 1 est ci-dessous.

**CI GitHub Actions**, SHA `607b502`, 5 workflows `ubuntu-latest`, actionlint 1.7.12 (0 erreur,
binaire épinglé et vérifié) : `ci-api`, `ci-frontend`, `ci-data-contracts` (PostGIS jetable,
689 tests en 5 min 35) et `secret-scan` **verts**, `security-audit` **rouge** sur trois avis
réels (KI-P6) — **4 verts sur 5** ; coût et runs : `01-07-SUMMARY.md`.

### Preuve finale de la phase 1

**SHA `8f38a09`, 2026-09-20**, arbre propre : `scripts/test-all.ps1 -Matrix` → exit 0 en 1 585 s,
`RESULT backend=PASS postgis=PASS frontend=PASS e2e=PASS` (4 185 backend, 692 PostGIS, 1 477
vitest, 486 Playwright) ; `gitleaks git .` et `git archive HEAD` extrait → `no leaks found`.
Détail de cette preuve historique : `01-VERIFICATION.md`. Des modifications applicatives
ont suivi en phases 2, 3 et 4 ; ce vert ne s’étend pas automatiquement à HEAD.

### Preuve de la phase 2 (base de travail)

Premier état de clôture : base de travail, 25 clés × 44/85 (avant réintégrations), déploiement
`f19c772` : backend 5 044 et PostGIS 819/1/2 verts en local à ce SHA. Rejeu final (SHA servi
`a62da95`) : golden en ligne 3 verts, 8 rouges dérogés ; `SOURCE_ARCHIVE_OK records=29 objets=147
orphelins=0` ; réimport `vacance` depuis l'archive aux mêmes comptages (02-14). Budgets PERF-00 :
suivi interne. Gate locale au SHA `6b0b527`, 2026-09-26 :
`scripts/test-all.ps1 -Matrix` → exit 0 en 1 783 s, `RESULT backend=PASS postgis=PASS frontend=PASS e2e=PASS`
(5 044 backend, 819/1/2 PostGIS, 1 487 vitest, 484 Playwright) ; gitleaks `git .` et `git archive HEAD` → 0 (`30065f5`).

**Addenda du 26/09 :** cadastre (02-21) puis connectivité (02-22) réintégrés ;
périmètre à 27 clés, golden 11/11 au SHA servi `20b0602`. La réserve d’archive des
octets producteur reste ouverte pour d’autres pipelines. Détail : addenda de
`02-VERIFICATION.md`, `02-21-SUMMARY.md`, `02-22-SUMMARY.md`. Aucun nouveau relevé distant ici.

### Preuve de la phase 3 (locale, source synthétique)

**SHA `e796c77`, 2026-09-24**, arbre propre : `scripts/test-all.ps1 -Matrix` → exit 0 en 1 684 s,
`RESULT backend=PASS postgis=PASS frontend=PASS e2e=PASS` (4 790 backend, 804 PostGIS, 1 487
vitest, 484 Playwright dont `recette-communes` à 375, 768 et 1 280 px). Relevé, versions et
entrées : `03-RECETTE.md`. Données réelles 44/85 (03-13) : rejeu headless au SHA déployé `9adaf37`,
13 tests verts, réserves R1 à R3 consignées : il s’agit de l’état avant recette.

**Clôture du 30/09 :** `03-VERIFICATION.md` à `51ceec5`, code servi `f5c7606`,
35/36 contrôles, neuf réserves, golden 11/11 et matrice rapportée verte. Recettes de Ianis
consignées : téléphone `8b1e5c0`, portable `f5c7606` ; réserves RES-1 à RES-3 maintenues.
UI-01, SEM-01 et SEM-02 restent ouverts pour l’ensemble des parcours en phase 4.

## Mesure documentation / code

`python scripts/doc-code-ratio.py` (stdlib, fichiers suivis, lignes non vides, binaires exclus),
sur l'arbre propre du SHA `42c1951` (03-13, avant la recette de Ianis ; fin de phase 2 : 0,02 au
SHA `6b0b527` ; phase 1 : 0,03 au SHA `8f38a09`) :

```
RATIO doc/code=0.02 pilotage/code=0.11 (doc=5588 pilotage=26086 code=233663 content=0 config=2758 data=145462) at 42c1951
```

L'ancien régime servait **1,21** ligne de doc par ligne de code, dont 93 % de suivi de projet
(annexe E du dossier d'analyse) : DOC-03 — « la documentation de pilotage ne dépasse pas ce que
le suivi exige » — reste tenue ; le pilotage monte à 0,11 avec les phases 2 et 3.
