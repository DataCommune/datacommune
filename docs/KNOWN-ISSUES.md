# Problèmes connus

Problèmes de l'arbre transféré de `3394395a`, par nature : **code**, **données**,
**déploiement**, **interface**, **acceptation**. Corrigé en code n'est ni réparé en
données, ni déployé, ni accepté (I-8). Statuts : `corrigé (code)` · `réparé (données)` ·
`à investiguer` · `à traiter phase N` · `clos` (avec commande et SHA) · `à trancher` · `mesuré`, `vérifié`, `levé` · `historique`.

Détail : `DataCommune-Analyse-Contexte/annexes/exploration-2026-09-20/`
`B-INCIDENTS-CORRECTIONS-REGRESSIONS.md` (14 incidents, `#N`), archive privée
`DataCommune-Archives/2026-09-19-untracked/`, bilans de phase du suivi interne.

## Registre code

Défauts du code transféré et tests écartés. Tout `--deselect` cite un `KI-NN` d'ici ; un test
écarté sans ligne ici n'a pas le droit d'exister. **Zéro désélection et zéro `skip` ajoutés.**

| ID | Problème | Statut | Preuve ou renvoi |
|---|---|---|---|
| KI-01 | Dérivation du département copiée 10 fois dans les requêtes DPE : « incohérent » indiscernable de « rien ». Viole I-3 | mesuré phase 2, correction phase 4 | 544886 DPE (dpe `12-20260924T224808Z`) : `coherent` 544877, `incoherent` 9, autres états 0 (`02-FIDELITE.md` § KI-01) ; `dpe/queries.py:166-174` |
| KI-02 | Flake vitest `CommuneAutocomplete.test.ts:37`, non reproduit | historique | Test retiré en `614f855` (03-10, D-42) |
| KI-03 | La gate verte ne dit pas « tout s'exécute ici » : au SHA `607b502`, **10 tests sautés ici passent sur le runner Linux** (gardes héritées) | à investiguer | `01-07-SUMMARY.md` ; aucun `+skip` depuis `transfer-3394395a` |
| KI-04 | Le type DPE admet `exact`/`multiple`/`building` sans qu'aucun producteur ne les émette | à traiter phase 4 | D-06 ; `schemas.py:70-73`, `attribution.py:107-119` |
| KI-05 | `apscheduler` **gardée**, processus retiré : sans elle, le repli sert `fresh`/`sain` un dataset en retard de 9 jours | clos (code, non déployé) | 04.2-06 : un contrôle d'adhérence au cron qui ne peut pas se calculer (`apscheduler` absente, cron illisible, erreur du déclencheur) rend `fraicheur=unknown`, `etat_pipeline=degrade` — jamais `fresh`/`sain` ; un retard calendaire connu (`stale`/`outdated`) et un `echec` restent prioritaires ; seuil `None` inchangé (quarantaine, `(None, sain)`). `tests/test_freshness_fail_closed.py` : 4 échecs RED (`'fresh' != 'fresh'`) puis 6 verts ; suite backend locale 6713 passés, 22 sautés. Reste : `scheduler/jobs.py` `check_dataset_freshness` n'alerte que sur `stale`/`outdated` (sans service scheduler en prod) |
| KI-06 | `scheduler/jobs.py:692` `check_missing_runs` sans appelant, gardé (D-05 retire l'ordonnanceur, pas la détection) | à traiter phase 5 | À rebrancher ou supprimer avec l'ordonnanceur |
| KI-07 | Fins de ligne **mixtes** depuis `f92d460` : `contracts/openapi.v1.json` en LF, le reste de l'arbre transféré en CRLF | à investiguer | Politique non décidée |
| KI-08 | `#1` `/v1/dpe` 500 : code à alembic 070 sur une base à 067, sans `upgrade head` | corrigé (code) | `common/migration_check.py` ; test négatif jamais rejoué ici |
| KI-09 | `#4` `MAX(annee)` global : communes présentes rendues absentes | corrigé (code) | Annexe B |
| KI-10 | `#5` MRR indu : un droit gratuit compté 39 € | corrigé (code) | Annexe B |
| KI-11 | `#6` Arrondi LOVAC 14,37 au lieu de 14,38 | corrigé (code) | Annexe B |
| KI-12 | `#10` `COR-03` : filtre CI sans les tests PostGIS concernés | corrigé (code) | Annexe B ; garde rejouée en CI |
| KI-13 | `#11` `COR-01`/`COR-02` : contrôle inexécutable compté succès | corrigé (code) | Annexe B |
| KI-14 | `#12` `COR-04` : `ON CONFLICT` sans contrainte | corrigé (code) | Un doublon contradictoire **bloque** la publication (choix métier) |
| KI-15 | `#8` 90 `.map` publiés le 23/08 : un jeton présent seulement en CD allumait les sourcemaps | corrigé (code) | Gardes SEC-02 **gardées** ; leur appelant CI est orphelin (KI-P5) |
| KI-16 | `#3` `COR-07` permis : ressource locale au 93 déclarée 44/85 | corrigé (code) | Quarantaine 503 ; annexe B |
| KI-17 | `api/LICENSES.md` contredit le catalogue (DVF) | clos (doc) | Réduit le 31/10 à un renvoi vers `source_catalog.py` et `GET /v1/datasets` ; lié par `getting-started.md` |
| KI-18 | `DVF_NO_DATA_DEPARTEMENTS` sans 976 (Mayotte) | à investiguer | `etl_dvf/pipeline.py:32` |
| KI-19 | `test_public_national_switch_doubles` dépassait l'enveloppe de 300 s sous charge (263 s seul le 2026-11-13, poste, Git Bash) | corrigé (code), non rejoué en CI distante | Cause mesurée (traces `bash -x` horodatées) : chaque SQL simulé passe par cinq à six processus (`pct`, `sh`, `docker ps`, `head`, `docker exec`, plus `cat`, `basename`, `awk` dans les doubles), 367 ms par requête, ~70 requêtes par bascule. Doubles en builtins bash (184 ms par requête) et script joué en trois groupes (`SWITCH_GROUP` = `chaine`, `refus`, `replis`), un cas pytest chacun ; `test_every_scenario_belongs_to_a_group` exige que les douze scénarios soient joués. Mêmes entrées, même poste, seul : 263 s avant, cas de 54, 30 et 83 s après (175 s en tout) ; mutations du script testé (`NATIONAL_SWITCH_OK`, `ROLLED_BACK`) toujours prises ; groupe inconnu → code 2. Plus aucune désélection à justifier |
| KI-20 | Copropriétés : l'import ne lit que `reference_cadastrale_1` du registre ; une adresse posée sur la 2e ou la 3e parcelle d'une copropriété reçoit « Aucune copropriété immatriculée » | corrigé (code), non déployé ; réparé en données au prochain import de `copropriete` après la migration 116 | `7619623a` : `id_parcelle_2`, `id_parcelle_3`, `nb_parcelles`, jointure sur les trois ; `tests/test_etl_copropriete_mapping.py`, `test_logements_queries.py`, `test_etl_staging_index_contract.py` rouges puis verts ; test PostGIS `test_a_condominium_is_found_from_its_second_and_third_parcel` écrit, **non joué**. Registre du 2026-11-11 : 90 941 immatriculations sur 657 108 (13,8 %) citent une 2e parcelle, 41 920 une 3e (`tabular-api.data.gouv.fr`, filtres `reference_cadastrale_N__isnotnull`) ; `etl_copropriete/config.py` `PARCELLE_1_COLUMNS`, `logements/queries.py` `copropriete_par_parcelle` |
| KI-21 | Copropriétés : une cellule de parcelle vide, lue `NaN` par pandas, devenait un identifiant fabriqué de 14 caractères (`00NANNANNA0NAN`) au lieu de NULL | corrigé (code) `7619623a`, réparé en données au prochain import | Base de travail, 2026-11-11 : 204 278 lignes sur 647 751 portent « NAN » dans `id_parcelle_1` (aucune jointure ne les atteint, mais le compte « avec parcelle » est faux) ; `test_une_cellule_vide_lue_nan_ne_fabrique_jamais_un_identifiant` |
| KI-22 | `test_workbase_deploy_doubles` (212 s seul) et `test_public_deploy_doubles` (74 s seul), même forme que KI-19 : presque tout le temps passe à lancer les processus des doubles sous Git Bash | corrigé (code), non rejoué en CI distante | Scénarios joués en groupes (`DEPLOY_GROUP`), tous lancés côte à côte par `run_groups` (`tests/shell_doubles.py`) ; `test_every_scenario_belongs_to_a_group` exige que chaque scénario `# (x)` soit dans un groupe ; groupe inconnu → code 2 ; sans groupe, le script entier joue tout comme avant (vert). Même poste, cas pytest : 211,6 → 55,3 s et 74,5 → 29,1 s. Mutations toujours prises dans les derniers groupes (`history` : ligne sans flèche acceptée ; `publication` : CT protégé visé). Le découpage a révélé `REF`, défini en (l2) et lu en (n), remonté avant les groupes |
| KI-23 | `test_comparaisons_lieux_contract` rouge une fois dans une passe PostGIS (1 transaction vue pendant l'appel amont) | corrigé (code) `20e7f62a` | La sonde comptait les `autovacuum worker` (`xact_start` posé) : 1 090 sondes sur 2 668 en voient un sous écriture continue (`autovacuum_naptime=1`). Sonde limitée à `backend_type = 'client backend'` ; sans le `rollback()` avant l'amont (`app/adresse/comparaison.py`), toujours rouge ; trois fichiers PostGIS ciblés, 150 passés |

## Registre données

Écarts entre code corrigé et données servies. Phase 2 : base de travail sur l'hyperviseur (D-16), reconstruite par l'ETL, **non déployée** en production.

| ID | Problème | Statut | Preuve ou renvoi |
|---|---|---|---|
| KI-D1 | `#2` LOVAC : 18 321 taux zéro à dénominateur NULL dans l'ancienne base (15 / 20 sur 44/85) ; code corrigé `5ca55129` | réparé (données, base de travail), non déployé | Recompte `taux_vacance = 0 AND (nb_logements_prives IS NULL OR <= 0)` : 0 / 0 après `2-20260923T001821Z` |
| KI-D2 | DPE : registre de présence temporaire, tombstones comptées ; swap non annulable | mesuré phase 2 ; swap à investiguer | `29-20260925T200621Z` : 545 030 présents, 0 tombstone, 0 absent (sources identiques ; absent conservé prouvé sur harnais). 144 DPE sans géométrie conservés, comptés, non consultables par adresse (clé BAN vide à la source) |
| KI-D3 | Dump de l'ancienne base | levé | Sauvegarde PBS du 18/09 de l'ancien conteneur de production restaurée isolée, conteneur temporaire détruit (02-03) ; `pg_dump -Fc` 1 928 727 185 o, SHA-256 `374aaacf…1a29`, sur le disque D: du poste et au PBS ; instantané protégé |
| KI-D4 | `#14` « Zéro mesuré » et « hors couverture » confondus (COR-14) | corrigé (code et données) | 02-09 : hors couverture → `null` + motif, vue 098 ; quatre états servis par la base de travail (02-18) ; non déployé |
| KI-D5 | Permis : le volet **données** de `#3` | vérifié (base de travail) | 02-18 : `/v1/permis` 503 avec `raison`, `resolve` `out_of_coverage` / `source_sans_couverture` |
| KI-D6 | Réimport sans `--millesime` : `source_period_label` vidé (`ban`, `batiments`, `dvf`, 02-17), millésime `inconnue` sur `/v1/datasets` | à investiguer | Lisible par les empreintes des reçus seulement |
| KI-D7 | DVF : les ventes d'une commune fusionnée ou recodée récemment restent sous son ancien code (`mutations_grain.code_commune`). La fiche de la commune actuelle ne les compte pas : série annuelle des prix tronquée (Orée d'Anjou, 49069 → 49126) ou périmètre qui change en cours de série (Saint-Denis sans Pierrefitte-sur-Seine jusqu'en 2024). La fenêtre de 12 mois est touchée de la même façon | corrigé (code), non déployé ; réparé en données au prochain import DVF | Table `app/etl_dvf/codes_communes_actuels.csv` (COG INSEE 2026, accord d'Ianis du 2026-11-13 pour le téléchargement ; `scripts/build_codes_communes_actuels.py`) : 1 808 anciens codes vers une seule commune d'aujourd'hui du même département, appliqués par `etl_dvf/transform.py` ; quatre changements de département gardent leur ancien code (02344, 14513, 44060, 49144), aucune scission. `tests/etl/test_dvf_codes_communes_actuels.py` rouge puis vert. Lecture seule de la base de travail, 2026-11-13 : 8 995 mutations sous 104 anciens codes ; hors `geo_communes` 360 764 avant, 351 769 après (restent Paris, Lyon, Marseille par arrondissement, 97123, 97127). Le nom de commune servi reste celui de la source. Mesure du 2026-11-11 : suivi interne (tendance des prix), § (c) |
| KI-D8 | Limite de la source, copropriétés : le registre publie au plus trois références cadastrales par immatriculation, et seulement les copropriétés immatriculées | limite de la source | 24 110 immatriculations déclarent `nombre_parcelles` > 3 (registre du 2026-11-11) : leurs parcelles suivantes restent sans rattachement |
| KI-D9 | DPE : seul le jeu de l'ADEME ouvert en juillet 2021 est importé ; la fiche dit « Aucun DPE déclaré » sans dire depuis quand | texte corrigé (code) `f507ea58`, non déployé ; import du jeu ancien écarté pour l'instant | Base de travail : 16 667 078 documents, le plus ancien du 2021-07-01. Jeu `dpe-france` (avant juillet 2021) : 10 728 950 lignes, autre méthode, tous périmés depuis le 2025-01-01 |
| KI-D10 | Limite de la source, parcelle : la surface servie est celle de la seule parcelle cadastrale du point ; un terrain réunit souvent plusieurs parcelles et la propriété des particuliers n'est pas en données ouvertes (`foncier_pm` : personnes morales seulement) | limite de la source ; texte corrigé (code) `de703d73`, non déployé | Meurthe-et-Moselle, base de travail : 11 % des adresses numérotées rattachées sont sur une parcelle de moins de 150 m², 23 % de moins de 250 m² (indice, pas une mesure des terrains à plusieurs parcelles) |
| KI-D11 | Limite de la source, bruit des avions : seuls les plans d'exposition au bruit des aérodromes sont lus ; le survol en altitude (couloirs aériens) n'est cartographié par aucune source ouverte nationale. « Aucun » et « Favorable » se lisent à tort « pas de bruit d'avion » | limite de la source ; texte, accroche « Hors plan » et ton neutre corrigés (code) `0e9a78b9`, non déployé | `rapport/themes/peb.py`, `rapport/alignement.py` |
| KI-D12 | Fibre : seule la technologie `FO` du fichier de l'ARCEP compte ; un réseau câblé à très haut débit (`COAX`), souvent vendu comme « fibre » par un opérateur local, rend « Pas d'offre fibre » avec une vigilance. Les déploiements en cours ne sont pas dans le fichier importé | corrigé (code) `e50f30b8`, non déployé : un câble à 100 Mbit/s ou plus est dit, sans vigilance | Base de travail, 2026 T2 : 1 430 799 immeubles sans fibre sur 25 782 858 ; 68 816 ont une offre câble, dont au moins 26 768 à 100 Mbit/s ou plus ; `adresse/lieu.py` `_champs_connectivite`, `rapport/themes/fibre.py` |

## Registre déploiement

Rien n'est déployé en production (la base de travail n'est pas la production). Maillons dont l'absence a
déjà produit un incident, à établir avant toute mise en service.

| ID | Problème | Statut | Preuve ou renvoi |
|---|---|---|---|
| KI-P1 | Rupture post-swap à corriger (protocole transféré : verrou, snapshot, OID de génération). Viole I-7 | clos (code, exercé en test) | `test_flat_publication.py` exécuté. 04.2-06 : `test_failure_after_swap_is_never_served_and_next_run_restarts` (`tests/integration/test_cor20_publication.py`, swap réel `swap_tables`, pannes `observe`/`required`/`crash` puis reprise) **exécuté le 2026-10-29** : `scripts/test-postgis.ps1 -Paths tests/integration/test_cor20_publication.py`, 74 passés (harnais jetable, poste, Docker 29.1.3) ; mutation `p.status <> 'published' OR` retiré de `publication_readiness.py` → 3 échecs. Jamais injecté sur la base de travail réelle |
| KI-P2 | `#7` Rollback d'image incompatible avec le contrat de publication : aucun des trois états refusés détecté | à investiguer | Garde `publication-contract=1`, **jamais exercée ici** |
| KI-P3 | Statiques et prérendues sans **aucun** en-tête S4 : `_headers` venait du routeur Workers (retiré en 01-06) ; `adapter-node` sert `build/` par `sirv`, hors `hooks.server.ts` | clos (code, non déployé) | `31f45756` : `frontend/server.js`, entrée de l'image, pose la liste canonique avant le handler. `npm --prefix frontend run test:served` (build local, Node 22, 2026-10-21) : `/`, `/developpeurs`, `robots.txt`, un actif portent les 5 en-têtes ; 5 échecs contre `build/index.js` avant correction. Sur le conteneur public, Caddy les posait déjà |
| KI-P4 | Plus de preuve que les `[[allowlists]]` de `.gitleaks.toml` **mordent encore** (utilitaire de `tools/` non transféré, D-09) | à investiguer | Reste le scan épinglé 8.29.1, **sans filtre `paths`**. Contrôle humain seul |
| KI-P5 | Chaîne de préproduction **orpheline** (`scripts/*_preproduction*`, `verify-release-live.sh`, `provision_internal_account.py`, `infra/preproduction/*.sh`, `deploy-runbook.md`) ; deux scripts **cassés** | à traiter phase 5 | `01-07-SUMMARY.md` ; leurs tests de logique restent |
| KI-P6 | **Trois avis non triés** laissaient `security-audit.yml` **rouge sur `master`** : `CVE-2026-63374`, `CVE-2026-64847` (`anyio 4.12.1`), `GHSA-9rgm-9g3h-6x36` (`devalue 5.8.1`), transitifs | clos (code) | Les trois partis avec `ab878bd8` ; quatre nouveaux avis corrigés par montée de version en `4b54e520` (vite 7.3.7, apprise 1.13.1, certifi 2026.10.19). Rejeu local des étapes du workflow (pip-audit + npm audit + `validate_dependency_audit.py`, 2026-10-21) : `PASS: 0 finding(s)`, registre d'exceptions vide. CI distante non rejouée (pas de push) |
| KI-P7 | `gitleaks dir .` sort 1 dès qu'un build frontend existe : 86 constats sous `frontend/build/`, non suivi | à investiguer | Publié vert (`git archive HEAD`, `gitleaks git .` → 0). **Aucune allowlist** |
| KI-P8 | Flake sur runner hébergé : `test_cor20_publication.py`, barrière codée à 5 s contre un verrou tenu | à investiguer | 1 échec sur 3 passages (`01-07-SUMMARY.md`). **Test non modifié** |
| KI-P9 | `#9` Topologie : au 05/09, CI, production et supervision sur le **même hôte** (l'hyperviseur), coupées ensemble | historique | CI sortie du homelab ; production : phase 5 |
| KI-P10 | Le suivi interne est poussé sur le distant privé (D-07) et **rien d'automatique ne rappellera de l'en sortir** avant un passage public | à traiter phase 5 | Contrôle humain seul |
| KI-P11 | Bind-mount `D:/DataCommune/pgdata` (dérogation D-13), jamais exercé | historique | Retiré par D-17 (`2a95b8a`) : volume nommé |
| KI-P12 | Indexabilité des pages DVF : sur `/bien/*`, `robots.txt` masquait le `noindex`, sans `X-Robots-Tag` | clos (code, non déployé) | Code en `dc48b4f3` ; preuve HTTP en `38a155b9` : `npm --prefix frontend run test:served` vérifie `x-robots-tag: noindex, nofollow` sur `/bien` et `/bien/<jeton>` et l'absence de `Disallow` couvrant `/bien/` ; mutation (Disallow + ligne du hook retirée) → 3 échecs. Mesure anti-robot : quota H-3 (`175753ec`) |
| KI-P13 | Sauvegarde héritée (prod01, ancien conteneur de production) **retirée** en 05-01 : `api/scripts/backup-pg.sh`, `restore-pg.sh`, `backup-accounts.sh`, `api/docs/backup-restore-runbook.md`, `infra/backup-accounts.yml`, son gabarit et ses unités `infra/systemd/datacommune-backup-accounts.*` (appelants morts laissés pour 05-06 : `deploy-runbook.md`, `go-live-checklist.md`, `deploy-full.sh`, `go-live-verify.sh`, `phase-10-02-check.sh`, `docker-compose.prod.yml`) | clos (code, exploitation) | Base de travail : sauvegarde hebdomadaire réelle sur l'hyperviseur depuis le 2026-11-02 (`caaa9255`, `dc-workbase-backup.timer`). Comptes : script de sauvegarde de l'hyperviseur (privé) + `infra/public/accounts-dump.sh`/`accounts-load.sh`, prouvés par doublons (`ACCOUNTS_DUMP_LOAD_OK`, `ACCOUNTS_BACKUP_OK`, `ACCOUNTS_RECEIVE_OK`) et sur PostgreSQL (`test_accounts_backup_restore.py`). Exécution réelle le 2026-11-05 : conteneur public → USB de l'hyperviseur + VPS, `ACCOUNTS_VERIFY ok tables=13 rows=247511`, alerte `DcAccountsBackupFailed` provoquée puis levée, `dc-accounts-backup.timer` actif (05-01-SUMMARY, tâche 4). Reste : identité age dans le coffre (Ianis) |
| KI-P14 | Durcissement d'avant l'ouverture du dépôt (cadrage du 2026-11-10 § 5) : HSTS du site d'un jour, CSP du site avec `script-src 'unsafe-inline'` posée par Caddy, inscription fermée au seul proxy, pas de `/.well-known/security.txt` | corrigé (code), non déployé : HSTS d'un an sans `includeSubDomains` (`security-headers.js`, `Caddyfile.public`, `Caddyfile.prod`), CSP écrite par SvelteKit (`kit.csp` : hachage par script en ligne sur les pages prérendues, nonce sur les autres ; plus de CSP de Caddy sur le site), `REGISTRATION_ENABLED=false` dans `docker-compose.public.yml` (404 de l'API sur `/auth/register`, `/auth/quick-key`, `/auth/resend-verification`), `security.txt` | `frontend/scripts/security-gates.test.mjs`, `served-headers.test.mjs` (« lot 5 »), `api/backend/tests/test_inscription_fermee.py` ; `style-src` garde `'unsafe-inline'` (attributs `style` de Svelte) ; l'alias `security@datacommune.fr` reste à créer |

## Registre interface

Relectures D-15 de la phase 3, sans plan porteur ; détail au SUMMARY cité.

| ID | Problème | Statut | Où regarder |
|---|---|---|---|
| KI-U1 | UI-SPEC en retard sur le code : règle `perimetre-en-dur`, alias `marble-dark`, départements statiques, `inset: 0`, zone 44 px, fil d'Ariane, textes hors contrat, `<pre>` curl | à traiter | `03-UI-SPEC.md` ; SUMMARY 03-03, 04, 07, 10, 11 |
| KI-U2 | `SourcesPrint.svelte` imprime « non disponible » pour une source ou un millésime absent | à traiter | l. 128 ; `03-08-SUMMARY.md` |
| KI-U3 | `ficheDepth` à déplacer en aide de test | à traiter | `03-10-SUMMARY.md` |
| KI-U4 | Icônes sans appelant : `IconCible`, `IconGraphique`, `IconRecherche` (retrait D-03) | résolu le 2026-10-30 (04.5-14 : retirées avec `IconChevronDroite`) | `03-03`, `03-05-SUMMARY.md` |
| KI-U5 | `test_zone_communes.py` l. 71 attend 4 clés au lieu de 5 (cassera sur base réelle) ; motif périmé de `EXCLUDED_ROUTES["/v1/communes"]` | à traiter | `test_route_scope_conformity.py` ; `03-04-SUMMARY.md` |
| KI-U6 | `/v1/zone/stats` sans `etats` | à investiguer | `zone/router.py` ; `03-06-SUMMARY.md` |
| KI-U7 | Blocs `indicateurs_dvf`/`indicateurs_dpe` nuls non typés par l'API : repli de la tendance | à traiter | `commune-kpi.ts` ; `03-07-SUMMARY.md` |
| KI-U8 | `comparerAuReferentiel` s'exécute sans être rendu | à traiter phase 4 | `commune-fiche.ts` ; `03-10-SUMMARY.md` |
| KI-U9 | `stop-resources.ps1` arrête tout `dc-contract-test-*` : un e2e lancé pendant PostGIS détruit son harnais | corrigé (manifeste de run) | Lot 0 (suivi interne : vérification du lot 0, 2026-10-05) |
| KI-U10 | Specs `manual` périmées : `shell-legacy`, tests `/datasets` d'`exploration-2026-04-17` | à traiter | `e2e-manifest.json` ; `03-05`, `03-11-SUMMARY.md` |
| KI-U11 | Champs de maquette non servis par `/v1/commune` (résidences, santé, arrêts, urbanisme, risques, copropriétés, référentiel national) | à traiter phase 4 | `03-CONTEXT.md` § Deferred ; `03-08-SUMMARY.md` |
| KI-U12 | ROADMAP l. 11 et l. 157 nomment encore des personnes extérieures (contredit D-36) | à trancher | `03-01-SUMMARY.md` |
| KI-U13 | Questions à Ianis : H7, lien `/mon-espace`, pages `cgu`/`confidentialite`/`mentions-legales`/`ds/*` sans propriétaire, annuaire France sur `/communes`, libellé H6 | à trancher | `03-03`, `03-04-SUMMARY.md` |

## Registre acceptation

Recettes et validations attendues sur **ce** dépôt. Aucune acceptation n'est déduite d'une barre verte.

| ID | Problème | Statut | Preuve ou renvoi |
|---|---|---|---|
| KI-A1 | `#13` Les UAT de l'ancien dépôt sont **historiques** : quatre défauts au moins trouvés par un humain **après** des barres vertes | à traiter phase 3 | Rejeu de parcours, jamais la seule suite API ; `03-RECETTE.md` |
| KI-A2 | Recettes de Ianis : téléphone `8b1e5c0`, portable `f5c7606` ; RES-1 à 3 | consigné, avec réserves | Clôture du 30/09 (suivi interne : vérification de la phase 3) ; recette extérieure attendue avant publication |
| KI-A3 | Le cas fondateur de I-2 (« le DPE du voisin ») n'est **pas rejouable** sans adresse réelle : condition d'entrée du chantier de fiabilité | à traiter phase 4 | Archétype A-1 : 78,1 % des clés lieu-dit du 44 ont une adresse numérotée dans la voie |

---

*Voir aussi : `DECISIONS.md`, `REPO-STATE.md`, `INVENTORY-3394395a.md`.*
