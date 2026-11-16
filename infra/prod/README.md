# Serveur de production (phase 04.2)

Serveur loué chez OVH (datacenter en France), qui sert `https://prod.datacommune.fr` et `https://api-prod.datacommune.fr` (préproduction), puis l'apex `datacommune.fr` et `api.datacommune.fr` après la publication (plan 04.2-05). Même modèle que l'instance publique du conteneur public (`infra/public/README.md`, qui reste la référence pour le modèle, la clé anonyme, l'IP du visiteur et le retour par le dump), sans hyperviseur ni `pct` : les scripts tournent sur le poste et joignent le serveur directement par ssh. C'est une sous-section de la procédure du projet (README racine, « Procédure locale »), pas une procédure à part.

```
visiteur -> Cloudflare (TLS) -> cloudflared -> caddy:80 -> frontend:3000      (prod.datacommune.fr)
                                                        -> frontend-prod:3000 (datacommune.fr)
                                                        -> api:8000           (api-prod. et api.datacommune.fr)
                                         db (PostGIS 17) : réseau compose seulement
```

Pile : `api/docker-compose.public.yml` **puis** `api/docker-compose.prod.yml` (overlay : PostgreSQL 32 Go, archive des sources `/opt/datacommune/sources` montée en écriture sur `/sources`, deux frontends, `CORS_ORIGINS` des deux origines), `api/Caddyfile.prod` (cinq hôtes). Toujours dans cet ordre, toujours depuis la release :

```bash
cd /opt/datacommune/releases/<sha12>
dc() { IMAGE_TAG=<sha12> DEPLOYMENT_SHA=<sha> docker compose -p datacommune-prod --env-file /opt/datacommune/.env \
  -f api/docker-compose.public.yml -f api/docker-compose.prod.yml "$@"; }
dc ps
```

Seul port publié : `127.0.0.1:8080` (caddy) ; les ports 3000 et 8000 ne sont pas publiés. Aucun 80/443 entrant : HTTP passe par le Tunnel Cloudflare (connexion sortante).

Fichiers : `provision.sh` (sur le serveur), `env-bootstrap.sh` (sur le serveur), `deploy.sh`, `down.sh`, `psql.sh` (sur le poste, Git Bash), `lib.sh`, `prod.env.example`, `tests/test-prod-scripts.sh`. Chaque script du poste accepte `--dry-run` (imprime, n'exécute rien à distance). Réglages non secrets : `infra/prod/prod.env`, ignoré par git (D-140), copié de `prod.env.example` (`DEPLOYMENT_HOST` à remplir). Les secrets ne vivent que sur le serveur, dans `/opt/datacommune/.env` (mode 600, propriétaire `datacommune`) ; aucun agent ne lit ni ne recopie ce fichier.

Les gestes marqués **[IANIS]** sont les siens (location, Cloudflare, secrets).

## 1. Location **[IANIS]**

OVH, **datacenter en France** (critère du ROADMAP), Debian 12, au moins 32 Go de RAM. Recommandation mesurée de 4.1 (`04.1-DIMENSIONNEMENT.md` § 1) : 960 Go utiles, 32 Go (64 Go si le scénario haut DPE se confirme), 4 à 8 vCPU. Déclarer la clé SSH **publique** du poste à la commande ; vérifier l'accès à la console de secours (KVM) : `ufw` n'autorise SSH que depuis une IP, qui peut changer. Consigner offre, datacenter, IP, OS, RAM, disque et date dans `04.2-LIVRAISON.md` § « Serveur de production », sans aucun secret.

## 2. Tunnel **[IANIS]**

Zero Trust, Networks, Tunnels : tunnel « datacommune-prod » (cloudflared, Docker). Public hostnames `prod.datacommune.fr` et `api-prod.datacommune.fr` vers `HTTP` `caddy:80` ; CNAME proxifiés si la zone ne les crée pas. Ne pas toucher `datacommune.fr`, `api.datacommune.fr` ni `www.datacommune.fr`, servis par le tunnel du conteneur public jusqu'au plan 05. Le jeton ne se colle jamais dans le chat ni dans le dépôt : il est saisi à l'étape 4.

## 3. Provisionnement

Sur le serveur, en root par `sudo`, depuis le compte d'administration créé par OVH, connecté avec la clé du poste :

```bash
scp infra/prod/provision.sh ~/.ssh/id_ed25519.pub <admin>@<ip>:/tmp/
ssh <admin>@<ip> 'sudo bash /tmp/provision.sh --dry-run --ssh-key "$(cat /tmp/id_ed25519.pub)" --admin-ip <ip du poste> --client-ip "${SSH_CLIENT%% *}"'
ssh <admin>@<ip> 'sudo bash /tmp/provision.sh --ssh-key "$(cat /tmp/id_ed25519.pub)" --admin-ip <ip du poste> --client-ip "${SSH_CLIENT%% *}" && rm -f /tmp/provision.sh /tmp/id_ed25519.pub'
```

`--client-ip` est l'adresse de la session SSH en cours, développée par le shell du serveur avant `sudo` (les apostrophes simples comptent). Avant tout changement, le script refuse (80) si cette adresse n'est pas dans `--admin-ip` ou n'est pas de la même famille (IPv4/IPv6), et si le compte qui lance `sudo` est root ou ne porte pas déjà la même clé dans son `~/.ssh/authorized_keys` : le mot de passe et le login root sont coupés ensuite, et la console KVM d'OVH demande un mot de passe que ces comptes n'ont pas.

**Dans les 10 minutes qui suivent** : `ufw` se désactive tout seul au bout de 10 minutes (minuteur `dc-ufw-deadman`), sauf si une **nouvelle** session prouve que l'accès marche et l'annule :

```bash
ssh <admin>@<ip> true && ssh <admin>@<ip> 'sudo systemctl stop dc-ufw-deadman.timer'
```

Si la nouvelle connexion est refusée, attendre la fin du minuteur, se reconnecter (SSH par clé seule, `fail2ban` actif), corriger `--admin-ip` et relancer le script.

Idempotent ; refuse un OS autre que Debian 12. Crée `datacommune` (UID 1000, groupe `docker`), pose la clé puis coupe l'authentification par mot de passe et le login root, `ufw` (SSH depuis `--admin-ip` seulement), `fail2ban`, mises à jour de sécurité sans redémarrage automatique, Docker + compose (empreinte de la clé de dépôt vérifiée), `/opt/datacommune` (0700) avec `releases`, `imports`, `backups`, `sources` (0750) et la sentinelle `sources/.datacommune-source-archive` (`datacommune-source-archive v1` ; sans elle, tout import échoue : `sentinelle_absente`), swap 4 Go, journald borné à 2 Go, `tune2fs -m 1` si la racine est ext4.

**UID 1000** : l'image API tourne en `appuser`, UID 1000 ; `/sources` doit lui appartenir. Si le compte OVH par défaut (souvent `debian`) détient déjà l'UID 1000, `provision.sh` refuse (80) en le nommant : rien n'est créé. Le choix revient à Ianis (renuméroter le compte OVH depuis la console, ou ne garder que `datacommune` comme compte d'administration) ; le script n'en décide pas.

Preuves à consigner : `sshd -T | grep -E 'passwordauthentication|permitrootlogin'` (`no`), `ufw status verbose`, `docker compose version`, `findmnt -no FSTYPE,OPTIONS /`, `tune2fs -l $(findmnt -no SOURCE /) | grep 'Reserved block count'`, `id -u datacommune` (1000).

## 4. Fichier privé `.env` **[IANIS]**

Un seul geste, depuis un terminal (TTY explicite : mot de passe de `sudo`, saisie du jeton) :

```bash
scp infra/prod/env-bootstrap.sh <admin>@<ip>:/tmp/env-bootstrap.sh
ssh -t <admin>@<ip> 'sudo bash /tmp/env-bootstrap.sh; rm -f /tmp/env-bootstrap.sh'
```

Le script crée `/opt/datacommune/.env` par `install -m 600 -o datacommune -g datacommune /dev/null` s'il manque (un fichier existant garde ses valeurs ; seul son mode revient à 600), génère **sur la cible** `POSTGRES_PASSWORD`, `JWT_SECRET_KEY`, `API_KEY_HMAC_SECRET` (`openssl rand -hex 32`) et `ADMIN_PASSWORD` (`-hex 24`) seulement s'ils manquent, ajoute les lignes non secrètes (`POSTGRES_DB`, `POSTGRES_USER`, `DEBUG`, `APP_ENVIRONMENT`, `EMAIL_ENABLED`, `STRIPE_ENABLED`), puis lit `CF_TUNNEL_TOKEN` sans écho depuis `/dev/tty`, une seule fois (jamais remplacé s'il existe). Il n'imprime que des noms et des comptes. Sans terminal : jeton dans un fichier 0600 redirigé sur l'entrée standard (`--token-stdin`), détruit juste après (`shred -u`). Jamais par le chat, l'argv ou un journal.

`PUBLIC_DC_API_KEY` n'est **pas** posée ici : aucune clé factice. La vraie clé `dc_anon_*` est créée au premier déploiement (§ 5).

## 5. Déployer

```bash
infra/prod/deploy.sh --dry-run <sha>    # relire : git archive, release, bloc serveur
infra/prod/deploy.sh <sha>              # ou HEAD par défaut
```

Refus (80), avant toute action, si le commit n'est pas sur `master` du dépôt public (ancêtre de `PROD_PUBLISHED_REF`, par défaut `refs/remotes/public/master` : `git fetch public` d'abord ; D-135) ; `--allow-unpublished` passe outre en secours (ligne `WARNING`). Même ordre et mêmes gardes que `infra/public/public-deploy.sh` : `git archive <sha> api frontend` envoyé par `scp` (aucune credential GitHub sur le serveur), release `/opt/datacommune/releases/<sha12>` (`.complete`), build, contrôle de l'UID de l'image contre le propriétaire de `/sources` et de la sentinelle avant tout démarrage, db, dump `pre-<sha12>-*.pgdump` (3 conservés), **arrêt de l'API avant migration**, `alembic upgrade head`, `up -d`, santé par `127.0.0.1:8080` avec en-tête `Host:` (`api-prod.` et `api.` sur `/v1/health/live`, SHA servi par `/__version` sur `prod.` et sur l'apex, HSTS, `X-Robots-Tag` de l'API), `current_sha`/`previous_sha`. Sortie `DEPLOYED <sha>` et `PROD_EXIT=0`.

Gardes ajoutées après la revue ciblée (`04.2-REVIEW-CIBLEE.md`, WR-02 à WR-04) :
- un seul déploiement ou retour arrière à la fois : verrou `/opt/datacommune/.deploy.lock`, refus (80) sinon ;
- un déploiement refuse (80) tant qu'un conteneur ponctuel `api` tourne (import en cours) : il n'arrête jamais un import ;
- avant le dump, l'espace libre de `backups` doit être au moins égal à la taille de la base, sinon refus (80) ;
- le dump est écrit dans un fichier `.partial`, relu en entier par `pg_restore`, puis renommé : un `pre-*.pgdump` est toujours complet ;
- les dumps ne sont purgés qu'après un déploiement sain (les 3 plus récents gardés) : un échec ne supprime aucun dump.

Préconditions sur `/opt/datacommune/.env` (noms seulement, valeurs jamais imprimées) : présent, lisible, mode 600 (un mode dérivé est remis à 600 sur le fichier existant, sans réécrire ses valeurs), secrets et `CF_TUNNEL_TOKEN` présents une fois. `infra/prod/deploy.sh --check-env-only <fichier>` joue ce même bloc en local, sans connexion.

**Premier déploiement** (`PUBLIC_DC_API_KEY` absente) : reprise de la branche `--db-only` de `public-deploy.sh`. Des valeurs d'amorçage bornent l'interpolation de compose aux seules commandes build de l'API, démarrage de db, migration et création de clé ; les frontends et le tunnel ne sont jamais construits ni lancés avec elles. Après la migration, le CLI existant `scripts.rotate_anon_key` (quota `ANON_KEY_QUOTA` de `prod.env`, 500 par défaut) crée la clé dans la base ; sa sortie va dans un fichier temporaire 0600, la seule ligne `dc_anon_*` est ajoutée au `.env` (jamais deux fois), le temporaire est détruit (`shred -u`). Les deux frontends sont ensuite construits avec cette clé, lue par compose dans le fichier privé. La clé n'est jamais affichée. Plan 05 : cette clé est réutilisée telle quelle ; toute rotation suit `infra/public/README.md` § 4, met à jour cette unique ligne, puis reconstruit `frontend` et `frontend-prod`.

`--no-tunnel` : cloudflared reste arrêté, rien n'est public (contrôles par la boucle locale).

Codes de sortie : ceux de `public-deploy.sh` (0, 2, 80, 81, 84, 86, 87, 90, 91), détaillés en tête de `deploy.sh`.

## 6. Retour arrière

**Le schéma n'est jamais rétrogradé.** `infra/prod/deploy.sh --rollback` relance la release `previous_sha` sans reconstruire, seulement si la révision de la base est la tête de ses migrations ; sinon refus (80), rien n'est touché. Après une release non saine : retour automatique si la migration n'a rien changé pour la précédente (90), sinon rien n'est relancé (87) et le message nomme le dump `pre-<sha12>-*.pgdump`. Retour par le dump, puis réancrage des publications : `infra/public/README.md` § 7 (mêmes commandes, projet `datacommune-prod`, dumps dans `/opt/datacommune/backups`). Jamais `alembic stamp`.

## 7. Arrêt et SQL

```bash
infra/prod/down.sh            # docker compose -p datacommune-prod down : volumes conservés
infra/prod/psql.sh "SELECT version_num FROM alembic_version"
```

Pour couper l'accès public sans toucher au serveur : désactiver les routes du tunnel dans Cloudflare.

## 8. Contrôles locaux

```bash
bash infra/prod/tests/test-prod-scripts.sh    # Git Bash : C:/Logiciel/Git/bin/bash.exe ; Docker Desktop démarré
```

`bash -n`, dry-run de `provision.sh` (Debian 12 accepté, Ubuntu refusé, clé obligatoire, adresse du client et compte d'administration contrôlés), dry-run de `deploy.sh` sans contact distant, refus de `--check-env-only`, `env-bootstrap.sh` sur un fichier temporaire, bloc serveur de `deploy.sh` joué en local contre des doubles de `docker`, `curl`, `flock` et `df` (premier déploiement, retour arrière, import en cours, verrou, dumps), `docker compose config --format json` vérifié en Python, `caddy validate` et `caddy adapt` de `Caddyfile.prod` dans un `docker run --rm`. Docker absent : échec explicite. Sortie attendue `PROD_SCRIPTS_OK`.

## 9. Archive des sources : seconde copie (plan 04.2-02, D-126)

Destination retenue par Ianis le 2026-10-24 : le disque du serveur (`copie-serveur`). La copie va dans un répertoire **distinct** de l'archive nationale `/opt/datacommune/sources` (qui reste vide jusqu'à l'import du plan 03), par exemple `/opt/datacommune/archive-copies/archive-2026-10-13`.

`archive-copy.sh` tourne là où est la source (l'hyperviseur, en root : l'archive appartient à 101000) et pousse vers le serveur par `ssh -o BatchMode=yes` ; la clé de l'hyperviseur doit donc être autorisée pour `datacommune` sur le serveur (geste à faire à la location, puis à retirer après la copie).

```bash
scp infra/prod/archive-copy.sh <hyperviseur>:/tmp/
ssh <hyperviseur> 'bash /tmp/archive-copy.sh --source /mnt/usb-8tb/datacommune-sources --dest datacommune@<ip>:/opt/datacommune/archive-copies/archive-2026-10-13 --edition archive-2026-10-13 --dry-run'
ssh <hyperviseur> 'bash /tmp/archive-copy.sh --source /mnt/usb-8tb/datacommune-sources --dest datacommune@<ip>:/opt/datacommune/archive-copies/archive-2026-10-13 --edition archive-2026-10-13 --resume'
ssh <hyperviseur> 'bash /tmp/archive-copy.sh --dest datacommune@<ip>:/opt/datacommune/archive-copies/archive-2026-10-13 --edition archive-2026-10-13 --verify-only'
```

Refus avant toute écriture (80) : source absente, sentinelle absente ou invalide, disque USB non monté (`--usb-mount`, défaut `/mnt/usb-8tb`, sur le périphérique de `/`) ou source hors du disque, espace libre sous `--min-free-gb` (défaut 40) ou sous la taille de la source, destination non vide sans `--resume`, reçu existant de l'édition. Transport : `rsync -a --partial --inplace` s'il est présent (jamais `--delete`), sinon `tar` (sans reprise partielle : tout est recopié). La source est listée (taille, mtime) avant et après le transfert : un changement est `UNSTABLE <chemin>` (82), sans manifeste ni reçu. Chaque fichier de la copie est ensuite haché et comparé au manifeste de la source : `MISMATCH`, `MISSING`, `EXTRA <chemin>` (81), sans reçu. Sinon `ARCHIVE_COPY_OK` et, dans `<copie>/.archive-copy/` (ignoré par `app.etl.source_archive`), `manifest-<édition>.sha256` (`<sha256> <taille> <chemin relatif>` par ligne) et `receipt-<édition>.json` (date UTC, source, destination, édition, fichiers, octets, SHA-256 du manifeste, transport, durée). `--verify-only` rehache la copie, contrôle le manifeste contre le reçu (`MANIFEST_ALTERE`) et imprime `ARCHIVE_VERIFY_OK`.

La restauration prouvée (rejeu DVF du run 48 depuis la copie dans `datacommune_restore_proof`) est la tâche 3 du plan 02 ; elle se joue sur le serveur, commandes dans le plan.

```bash
bash infra/prod/tests/test-archive-copy.sh    # Git Bash ; aucune archive réelle, aucun ssh : ARCHIVE_COPY_SCRIPTS_OK
```

## 10. Import national (plan 04.2-03)

`import.sh` tourne **sur le serveur**, en `datacommune`, contre la release déployée (`/opt/datacommune/current_sha`) : la séquence dure environ une journée et ne doit pas dépendre d'une session ssh. Il ne fait partie d'aucune release (`git archive` ne livre que `api` et `frontend`) : il est copié à côté.

Préalable propre au serveur : les reçus sont exportés par `scripts/export_import_manifest.py`, qui vérifie `repo_sha` par `git cat-file` ; une release faite par `git archive` n'a pas d'historique. Comme `wb-tool.sh` (script privé de l'hyperviseur), `import.sh` construit une fois l'image outil `datacommune-prod-api:<sha12>-tool` (image API + `git`) et monte un dépôt nu `/opt/datacommune/repo.git` sur `/repo`. Le dépôt nu se pose depuis le poste par un paquet git (aucune credential GitHub sur le serveur) :

```bash
git bundle create /tmp/datacommune.bundle <sha>      # sur le poste ; <sha> = current_sha du serveur
scp /tmp/datacommune.bundle infra/prod/import.sh datacommune@<ip>:/opt/datacommune/
ssh datacommune@<ip> 'bash /opt/datacommune/import.sh --setup-repo /opt/datacommune/datacommune.bundle && rm /opt/datacommune/datacommune.bundle'
```

`--setup-repo` construit l'image outil si elle manque, clone le paquet en dépôt nu dans cette image (`git clone --bare`), refuse (80) un paquet absent ou un `repo.git` existant, puis contrôle que le commit déployé y est (`REPO_OK`). Après un nouveau déploiement, le commit doit y entrer aussi (nouveau paquet, `repo.git` retiré d'abord). Ordre et lancement :

```bash
ssh datacommune@<ip> 'bash /opt/datacommune/import.sh --dry-run'
ssh datacommune@<ip> 'nohup bash /opt/datacommune/import.sh > /opt/datacommune/imports/sequence.log 2>&1 < /dev/null &'
ssh datacommune@<ip> 'tail -f /opt/datacommune/imports/sequence.log'
```

Ordre : `monuments loyers transports scolaire zonage_abc fiscalite revenus demographie vacance copropriete permis antennes equipements`, puis `ban dpe batiments foncier_pm cadastre connectivite rnb_liens dvf` (BAN avant DPE ; DPE tôt, sur un disque presque vide). Aucun `--from-archive` : le premier import télécharge et archive sur `/sources`. Par jeu : `max(id)` d'`etl_runs`, échantillonnage toutes les 30 s (`IMPORT_SAMPLE_INTERVAL`) de `df -B1 /`, `du -sb` de `pg_wal` (conteneur db) et `memory.peak` du conteneur `dc-import-<jeu>` ; `docker compose run --rm api python -m run_etl <jeu>` ; reçu `imports/<jeu>/<run_id>.json` et `latest.json` ; mesures `<run_id>.mesures.json` ; `IMPORT <jeu> success|failed`. Un échec arrête la séquence (1) ; `--resume <jeu>` exige un reçu `success` pour chaque jeu antérieur et repart de `<jeu>`. `--replay <jeu> <run_id>` : seulement `vacance`, `dpe`, `batiments`, `dvf` (`archive_replay`), refusé (80) pour les 17 autres. Refus avant tout `run_etl` (80) : `python3` absent, release absente, import déjà en cours, reçus présents sans `--resume`, commit déployé absent de `repo.git`.

Le périmètre national (`app.scope set`), les mesures et la fidélité sont les tâches 2 et 3 du plan 03, sur le serveur.

```bash
bash infra/prod/tests/test-prod-import.sh    # Git Bash ; doubles de docker et df, aucun import : PROD_IMPORT_SCRIPTS_OK
```
