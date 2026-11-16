# DataCommune

> **In English.** DataCommune is a French website and API that brings together public open data
> about a place — a commune, an address, a building — and shows each figure with its source, its
> date and how it was computed. This repository holds the code only: no data is redistributed here,
> it is imported from the public producers (BAN, DVF, ADEME DPE, IGN, INSEE, Géorisques…). Code
> under AGPL-3.0-or-later; contributions under the Developer Certificate of Origin (sign off your
> commits). The documentation and the discussions are in French; issues and pull requests in
> English are welcome. Live site: <https://datacommune.fr>.

DataCommune rassemble les données publiques ouvertes sur un lieu — une commune, une adresse, un
bâtiment — et montre chaque chiffre avec sa source, sa date et sa méthode de calcul. Le site public
est <https://datacommune.fr> ; l'API qui le sert est documentée sur
<https://api.datacommune.fr/docs>.

## Ce que contient ce dépôt

- `api/` : l'API (Python, FastAPI, PostgreSQL/PostGIS) et les imports des sources ;
- `frontend/` : le site (SvelteKit) ;
- `contracts/` : le contrat de l'API (OpenAPI) et les exemples de réponses ;
- `infra/` : le déploiement d'une instance (scripts paramétrables, sans valeur de machine) ;
- `docs/` : décisions, problèmes connus, exploitation ;
- `scripts/` : les commandes de test et de contrôle du projet.

**Aucune donnée n'est redistribuée ici** : le dépôt contient le code qui télécharge et importe les
jeux publics, pas les jeux eux-mêmes. Les fixtures de test sont synthétiques ou anonymisées.

## Essayer

Prérequis, installation et commandes de test : section « Procédure locale » ci-dessous.

## Héberger une instance

Le déploiement de l'instance publique est décrit dans `infra/public/README.md` (Docker Compose,
Caddy, tunnel Cloudflare). Les valeurs propres à une machine se placent dans des fichiers
`*.local.env` ignorés par git, à partir des gabarits `*.example`. L'import de la France entière
demande environ 150 Go et une journée.

## API

Référence interactive : <https://api.datacommune.fr/docs>. Contrat versionné :
`contracts/openapi.v1.json`. L'usage de l'API est limité par adresse IP et par clé ; les
conditions d'accès sont sur <https://datacommune.fr/developpeurs>.

### Serveur MCP (agents)

L'API porte aussi un serveur [MCP](https://modelcontextprotocol.io) pour les assistants et agents :
`POST /mcp` (HTTP en streaming, sans état, réponses JSON). Six outils en lecture seule
(`chercher_adresse`, `lire_adresse`, `comparer_adresses`, `lire_commune`, `comparer_communes`,
`lister_jeux_de_donnees`) rejouent les routes `/v1` ; chaque valeur rendue porte sa source, sa date,
son échelle et un état parmi `servi`, `zero`, `manquant`, `hors_couverture`, `panne` (seuls les deux premiers
portent une valeur). Ce n'est pas un service à part : il tourne dans le conteneur de l'API.

| Réglage | Défaut | Effet |
|---|---|---|
| `MCP_ENABLED` | `false` | `true` ouvre `/mcp` ; fermé, la route répond 404. |
| `MCP_ORIGINES` | `[]` | Origines admises en plus de `CORS_ORIGINS` quand un client envoie un en-tête `Origin` (un agent côté serveur n'en envoie pas). |
| `ANON_RATE_LIMIT_PER_IP_MINUTE`, `ANON_RATE_LIMIT_PER_IP_DAY` | `120`, `2000` | Plafonds par visiteur d'un appel d'outil, partagés avec la lecture `/v1` sans clé. |
| `RATE_LIMIT_PER_IP` | `120` | Plafond global par IP, qui compte chaque `POST /mcp`. |
| `TRUST_PROXY_HEADERS` | `false` | Derrière un mandataire de confiance, l'IP du visiteur est lue dans `CF-Connecting-IP` ou `X-Forwarded-For`. |

La lecture MCP se fait sans clé, quel que soit `KEYLESS_ACCESS_ENABLED`. Pour une instance : mettre
`MCP_ENABLED=true` dans l'environnement de l'API (variable, ou fichier `.env` qu'elle lit), puis
donner à un client MCP l'adresse de l'API suivie de `/mcp` (par exemple `http://localhost:8000/mcp`,
connecteur « HTTP en streaming »). Derrière Caddy, la route publique est `handle /mcp` dans
`api/Caddyfile.public` ; dans la pile publique, `MCP_ENABLED` est un littéral de
`api/docker-compose.public.yml`.

## Données et licences

- **Code** : GNU Affero General Public License v3.0 ou toute version ultérieure
  (`AGPL-3.0-or-later`, fichier `LICENSE`). Toute instance modifiée et accessible en ligne doit
  offrir son code source à ses utilisateurs.
- **Données** : elles restent sous la licence de leur producteur, le plus souvent la Licence
  Ouverte 2.0 ; les gares SNCF sont sous ODbL. La liste à jour, jeu par jeu, est sur
  <https://datacommune.fr/datasets> ; voir aussi `api/LICENSES.md`. Les ventes DVF ne doivent
  jamais être réutilisées pour réidentifier des personnes ni être indexées par un moteur de
  recherche.

## Provenance

Le code de ce dépôt reprend le commit `3394395a32e9a778c89930ad7544406da265163e` (branche `dev`,
10/09/2026) du dépôt d'origine du projet, non public, copié sans historique le 19/09/2026, puis
retravaillé. Le dépôt public a été ouvert sans historique : son premier commit nomme le commit du
dépôt de travail dont il est l'arbre.

## Contribuer

Les contributions sont les bienvenues, par pull request vers `master` : lire `CONTRIBUTING.md` (installation, tests, branches,
signature `Signed-off-by` exigée par un contrôle automatique). Pour signaler une erreur dans une
donnée, ouvrir une issue « Écart de donnée » ou écrire à signalement@datacommune.fr (lien
« Signaler une erreur » en pied de page). Pour une faille de
sécurité, **ne pas ouvrir d'issue** : suivre `SECURITY.md`. Les échanges respectent le
`CODE_OF_CONDUCT.md`.

## État et maintenance

Projet maintenu par Ianis, seul mainteneur à ce jour. Le site est en service ; la liste des
problèmes connus est dans `docs/KNOWN-ISSUES.md`, les décisions dans `docs/DECISIONS.md`.

## Procédure locale

C'est **la** procédure du projet. AGENTS.md y renvoie ; ne pas en créer une seconde.

### Prérequis

Windows 11, PowerShell 7, Python 3.12, uv, Node 22, Docker Desktop, git.

```powershell
pwsh -NoProfile -File scripts/check-toolchain.ps1
```

Le script imprime une ligne `TOOL <nom> <version>` par outil plus trois lignes d'état Docker, et sort 0. Versions relevées sur le poste de référence le 20/09/2026 : python 3.12.10, uv 0.11.3, node 22.23.0, npm 10.9.8, pwsh 7.6.6, git 2.54.0, docker 29.1.3, docker compose 5.0.0-desktop.1, gitleaks 8.29.1.

L'interrupteur optionnel `-RequireD` exige en plus que le disque de Docker Desktop soit sur `D:`. **Il sort 1 sur le poste de référence, par construction** : Docker Desktop reste sur `C:` (dérogation D-13, plan 01-01 — la relocalisation a été tentée, a échoué sur Docker Desktop 29.1.3, et a été annulée). Seules les données PostgreSQL de DataCommune vont sur `D:`. Ce code de retour 1 est l'état attendu ici, pas un défaut à corriger.

### Checkout et environnement

```powershell
uv sync --locked --group dev          # depuis api/backend
npm ci                                # depuis frontend
npx playwright install chromium       # depuis frontend, sans --with-deps sur Windows
```

Les dépendances sont résolues par `uv.lock` et `package-lock.json` : aucune résolution libre. Aucun fichier `.env` n'est versionné ; `api/.env.example` et `frontend/.env.example` servent de gabarits. Les secrets restent hors du dépôt.

### Données isolées

Les tests PostGIS passent par un harnais **jetable** (`api/backend/scripts/run_contract_integration.ps1`) : image épinglée `postgis/postgis:17-3.5` (digest relevé le 20/09/2026 : `sha256:09e171a1b5604d8ff22528eab3429b6d1237af4b629c239123a31b2deedff8dd`), répertoire de données en tmpfs, port éphémère publié sur `127.0.0.1` seulement, conteneur détruit dans un `finally`. Aucune base partagée ; chaque projet est enregistré avant `up`, puis nettoyé dans le `finally`, y compris après un démarrage partiel. Un arrêt brutal du processus parent ou du poste peut empêcher le `finally` : le manifeste imprimé permet une récupération ciblée ensuite.

Les tests hors intégration refusent les connexions psycopg non simulées pendant les fixtures, le test et son nettoyage. Une tentative fait échouer le test, même si le code applicatif intercepte les exceptions ordinaires. Les tests peuvent installer leurs doubles ; les tests PostgreSQL déclarés conservent leur opt-in et leur harnais. Cette garde ne couvre pas les imports avant exécution, les alias déjà capturés, les sous-processus ou les autres pilotes. Les tests de géométrie cadastre sont dans la suite PostGIS jetable ; une connexion défaillante du harnais fait échouer leur contrôle.

La pile de développement `api/docker-compose.yml` garde ses données dans un volume Docker nommé.

### Commandes

| Commande | Ce qu'elle joue |
|---|---|
| `pwsh -NoProfile -File scripts/test-backend.ps1 [-Paths <chemins>] [-SkipLint]` | ruff, contrat sémantique, audit des artefacts golden, pytest (mêmes `--ignore` et `-m` que la CI) |
| `pwsh -NoProfile -File scripts/tests/test-resource-isolation.ps1` | contrats de propriété des ressources avec toutes les commandes de service interceptées ; aucun service réel, rapports sous `reports/reliability-lot0/` ; même contrôle requis par `ci-data-contracts` |
| `pwsh -NoProfile -File scripts/test-postgis.ps1 [-Paths <chemins>]` | tests d'intégration via le harnais jetable |
| `pwsh -NoProfile -File scripts/test-frontend.ps1 [-Gate all\|gates\|check\|unit\|build]` | gates node, type-check, vitest, build + prerender |
| `pwsh -NoProfile -File scripts/test-e2e.ps1 [-Specs <specs>] [-Matrix]` | specs `critical` de `frontend/e2e-manifest.json` sur Chromium ; `-Matrix` ajoute Firefox et WebKit |
| `pwsh -NoProfile -File scripts/test-all.ps1 [-Matrix] [-FailFast]` | les quatre suites dans l'ordre, puis l'arrêt des ressources |
| `pwsh -NoProfile -File scripts/test-recette.ps1 [-ApiTarget <url>] [-Grep <motif>]` | recette de la phase 3 sur **données réelles** (`frontend/tests/recette/`, hors gate) : Playwright headless contre l'API de la base de travail ouverte par `workbase-recette.sh up`, clé de la séance dans `PUBLIC_DC_API_KEY` ; imprime `RECETTE ok` |
| `pwsh -NoProfile -File scripts/serve-recette.ps1 [-LanHost <ipv4>] [-ApiTarget <url>] [-ResolveOnly]` | sert la recette de Ianis sur **une** adresse privée du poste, port alloué à cette exécution, jusqu'à Ctrl+C ; sans `-LanHost`, l'unique interface LAN candidate, refus si aucune ou plusieurs, jamais une interface tunnel/VPN ; imprime `RECETTE host …` puis l'URL à ouvrir sur le téléphone ; `-ResolveOnly` s'arrête après `RECETTE host` ; nettoyage de cette invocation en `finally` ; tout ce qu'il lance vit dans un objet job Windows fermé avec lui et il s'arrête à la fin du processus qui l'a lancé (C43) : `RECETTE stop listeners=0` en fin d'invocation, sortie non nulle si un écouteur reste sur `<ip>:<port>` ; preuve : `pwsh -NoProfile -File scripts/tests/test-serve-recette-stop.ps1` |
| `uv run python -m app.scope set --datasets … --departements …` | depuis `api/backend`, **avant tout import** : écrit le périmètre hébergé en base (seule source de périmètre des imports et de l'API) ; signale les verrous de schéma dépassés (`SCOPE warning`) |
| `uv run python -m app.scope show` | depuis `api/backend` : périmètre hébergé en base et verrous de schéma |

Les tests web tournent uniquement par runner Playwright headless en contexte isolé : jamais de navigateur personnel, jamais d'interface graphique, jamais de CDP sur une session existante.

### Services et arrêt

```powershell
pwsh -NoProfile -File scripts/stop-resources.ps1 -RunManifest <chemin-imprime-run.json>
```

Arrête uniquement les projets et processus inscrits dans ce manifeste, après vérification de leur identité. Sans manifeste, refuse tout nettoyage. Le créateur du run assure le nettoyage final ; un script enfant transmet `-RunManifest` et ne nettoie que ses propres ressources. Les projets `dc-verify-<run>-<projet>` sont indépendants de l'ancien nettoyage `dc-contract-test-*`. Les serveurs locaux utilisent deux ports distincts alloués dans 15000–24999 et 25000–34999, hors des plages réservées par Windows (`netsh int ipv4 show excludedportrange protocol=tcp`) ; 5173 et 4174 sont refusés ; le démarrage refuse un port occupé et Playwright ne réutilise aucun serveur. Les PID sont vérifiés avec leur date de création et la commande réellement observée, jamais avec le seul port. Les erreurs d'inspection ou d'arrêt rendent le run en échec ; un nettoyage déjà terminé reste idempotent.

Le manifeste et les logs serveur sont sous `%TEMP%/dc-test-runs/<run>/`. Chaque nouveau run supprime les registres qui ne possèdent rien depuis un jour et les runs arrêtés depuis sept jours ; un run en échec ou encore propriétaire est conservé. Les traces, captures et rapports Playwright locaux sont séparés par run, invocation et passe (`chromium`/`matrix`) dans ce répertoire. Les configurations directes de CI Linux conservent le cycle de vie Playwright, avec ports par défaut 15173/14174 et `--strictPort` ; la procédure locale Windows passe par les runners ci-dessus. Après une interruption brutale, conserver le chemin `TEST_RUN ... manifest=...` et utiliser la commande ciblée ci-dessus ; elle ne recherche aucune ressource tierce.

**Règle : les ressources créées sont arrêtées après chaque contrôle.** Le nettoyage vérifie l’absence des conteneurs et processus enregistrés pour cette invocation ; les ressources d’une autre exécution restent intactes.

### Imports

**Reprise après interruption.** Le checkpoint v2 lie les fichiers et leurs empreintes à la source, au millésime et aux départements. Un ancien checkpoint, des entrées différentes, une table temporaire absente ou un stock incohérent déclenchent une reconstruction du candidat temporaire. Une interruption pendant un chargement ou un filtre laisse une intention persistée : la reprise reconstruit aussi ce candidat, afin de ne pas répéter un chargement déjà commité. Le stock publié reste soumis aux gates habituelles. `total_rows_loaded` et les gates comptent le stock candidat après filtre ; les étapes `stage:*` décrivent les chargements de la seule invocation. Ces contrôles ajoutent une relecture des fichiers et des recomptages ; leurs coûts aux volumes nationaux restent à mesurer.

**Génération précédente (plan 04.1-04).** La candidate est créée journalisée et publiée telle quelle, sans réécriture ; les tables plates sont non journalisées et, pour ARCEP, BD TOPO, cadastre et DPE, supprimées avant la construction des index. Après une publication réussie, la table `<table>_old` du jeu est supprimée dans la même transaction que la réparation des vues matérialisées ; si cette réparation échoue, rien n'est supprimé et la publication reste acquise. Il n'y a donc plus de retour arrière instantané par `_old` : on revient à une génération antérieure en rejouant son import (`run_etl --from-archive` pour les pipelines qui l'acceptent, sinon téléchargement du millésime nommé). Les `_old` laissées par du code antérieur se purgent par `python -m app.scheduler.run_job purge-old-tables` (sans délai, même règle de transaction).

**BD TOPO (plan 04.1-05).** Le téléchargement rend les `.7z` du producteur (`bdtopo_<dept>.7z`), archivés tels quels (D-124). Chaque département est ensuite listé par `7z l -slt` (refus avant écriture : chemin absolu ou remontant, lien, taille déclarée au-delà de `ETL_MAX_DECOMPRESSED_BYTES`, zéro ou plusieurs `.gpkg`), extrait par le `7z` natif (paquet Debian `7zip` de l'image) dans `<cache>/work/<dept>/`, contrôlé (colonnes de la couche `batiment`), compté (`Feature Count` d'`ogrinfo`, étape `count:producer_features_<dept>` du run) et chargé ; l'extraction et le `.7z` sont supprimés dès le département chargé : un seul GPKG sur disque à la fois. Le rejeu `--from-archive` lie les objets vérifiés au lieu de les copier (`SourceReplay.link`) et se déroule comme un téléchargement ; un record ancien (imports 44/85 de la phase 2, GPKG archivés) charge le GPKG lié tel quel, sans extraction ni conversion. Un lien retiré ne touche jamais l'objet. Pics de `/etl_tmp` et durées : à mesurer au plan 20.

**Archives DPE.** Une génération archivée conserve le manifeste original, les quatre CSV de traitement et leurs preuves JSON : métadonnées, pages et éventuelles partitions. À la restauration, un répertoire privé reçoit les fichiers ; les chemins du manifeste sont adaptés uniquement en mémoire. Ses octets, la génération et les versions sources restent conservés. Une ancienne archive contenant seulement les CSV ne suffit pas et est refusée avant le journal d’import, la création du candidat et la reprise du checkpoint. Le répertoire privé est libéré en fin d’exécution. Ce mécanisme n’établit pas la disponibilité des archives historiques : vérifier le run choisi avant une reconstruction.

### Rapports

`test-all.ps1` termine après nettoyage par une ligne `RESULT backend=<PASS|FAIL> postgis=<…> frontend=<…> e2e=<…> cleanup=<PASS|FAIL>`. Les artefacts Playwright du runner local sont sous le répertoire du manifeste décrit ci-dessus ; la CI directe conserve `frontend/playwright-report/` et `frontend/test-results/`. Ces artefacts ne sont pas des preuves versionnées.

Deux mesures versionnées, en lecture seule, sans dépendance hors stdlib :

```powershell
python scripts/doc-code-ratio.py                              # ratio documentation / code (D-11)
python scripts/inventory-check.py docs/INVENTORY-3394395a.md  # inventaire décidé ↔ arbre (DOC-01)
```

La première imprime un JSON détaillé puis une ligne `RATIO doc/code=… at <sha>` ; la seconde imprime `INVENTORY ok: <n> rows (…)` et sort 1 en listant les écarts. Leurs résultats au SHA de référence sont recopiés dans `docs/REPO-STATE.md`.

Une preuve nomme la commande, le SHA du dépôt, les versions d'outils et les entrées. Contrôle local, CI et déploiement sont trois registres distincts et ne se déduisent pas l'un de l'autre.

### Secret scan

gitleaks 8.29.1, installé hors du dépôt. Le contrôle porte sur **ce qui est publié** — l'historique suivi et le contenu qu'un `push` livre — et non sur l'arbre de travail. C'est la méthode établie au plan 01-07 et reprise à chaque preuve de phase ; sur l'export public, les deux contrôles doivent sortir 0 avant tout distant public ; l'état de l'historique privé est donné plus bas.

```powershell
$gl = $env:GITLEAKS ?? (Get-Command gitleaks -ErrorAction Stop).Source   # variable GITLEAKS, sinon le PATH

# 1. Historique suivi : tous les commits du dépôt, par le script qui refuse un scan aveugle
#    (Git Bash ; GITLEAKS_BIN désigne le binaire s'il n'est pas dans le PATH).
bash scripts/secret-scan-commits.sh --config .gitleaks.toml --log-opts="--all"

# 2. Contenu publié : ce que `git archive HEAD` livre, extrait puis scanné. Doit sortir 0.
$tmp = Join-Path $env:TEMP ("dc-archive-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
try {
  git archive --format=tar HEAD | tar -x -C $tmp
  Push-Location $tmp          # obligatoire : les [[allowlists]] sont ancrées (`^`) sur des
  & $gl dir . --config .gitleaks.toml --redact --no-banner   # chemins relatifs à la racine
  Pop-Location
} finally { Remove-Item -Recurse -Force $tmp }
```

**Un scan qui n'a lu aucun commit ne prouve rien.** gitleaks lit la sortie de `git log` ; si `git log` échoue ou ne rend rien, il annonce `0 commits scanned`, « no leaks found », et sort 0. C'est arrivé jusqu'au 2026-11-10 : une référence cassée (`.git/refs/heads/master (1)`, copie de fichier) faisait échouer `git log --all`, et tout l'historique passait pour propre sans avoir été lu. `scripts/secret-scan-commits.sh` enveloppe `gitleaks git .` et sort 1 avec `SECRET_SCAN_BLIND` quand le nombre de commits scannés est nul ou absent ; sinon il imprime `SECRET_SCAN_COMMITS=<n>`. Le workflow `secret-scan.yml` passe par lui, et rejoue son test (`bash scripts/tests/test-secret-scan-commits.sh` : dépôt jetable, référence cassée, plage inexistante, vrai constat) avec le binaire qu'il vient d'installer. Avant de croire un « 0 constat », lire le nombre de commits : `git log --all >/dev/null; echo $?` doit rendre 0.

État de l'historique du dépôt de travail privé au 2026-11-11 (gitleaks 8.30.1, configuration du dépôt, `--log-opts="--all"`) : 2 744 commits scannés, **60 constats**, donc sortie 1. 51 sont dans le suivi interne du projet, 9 dans deux fichiers de test ; le tri du 2026-11-10 n'y a trouvé aucun secret actif (empreintes de fichiers, exemples de documentation, sentinelles de test). Cet historique ne sort pas : le dépôt public part d'un export d'un seul commit, scanné à part (`scripts/export-public.sh`), qui doit annoncer `1 commits scanned` et 0 constat.

**`gitleaks dir .` sur l'arbre de travail sort 1, et c'est l'état attendu ici — pas un défaut à corriger.** Dès que vous avez lancé `test-frontend.ps1 -Gate all`, le répertoire **non suivi** `frontend/build/` existe et produit **86 constats**, tous sur le même placeholder de documentation `X-API-Key` rendu dans les pages d'exemple. Aucun fichier suivi n'est concerné : `git ls-files frontend/build` rend 0 ligne et `frontend/.gitignore` l'exclut. C'est **KI-P7** de [docs/KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md), ouvert et non masqué : **ne pas élargir l'allowlist à `build/`** pour le faire taire, cela aveuglerait le scanner sur tout répertoire de build.

Une entrée `[[allowlists]]` est toujours bornée à un chemin, une règle et une valeur exacte, avec sa date et son motif.

## Dossiers

- `api/` — backend FastAPI, ETL, migrations Alembic
- `frontend/` — site SvelteKit 5
- `contracts/` — contrats de données versionnés (`fiche-fields.v1.json`, `openapi.v1.json`, cas golden)
- `infra/` — déploiement et exploitation
- `scripts/` — commandes versionnées du poste et anciens scripts transférés
- `docs/` — les documents d'état : [DECISIONS.md](docs/DECISIONS.md), [KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md), [REPO-STATE.md](docs/REPO-STATE.md) et l'inventaire décidé [INVENTORY-3394395a.md](docs/INVENTORY-3394395a.md) ; plus la spécification DPE documentaire et les fixtures lues par les tests
