# Instance publique 44/85

`https://datacommune.fr` (frontend) et `https://api.datacommune.fr` (API), servis par un conteneur dédié de l'hyperviseur (`datacommune-public`, le conteneur public) et exposés par un Tunnel Cloudflare : aucun port entrant. Les fichiers : `api/docker-compose.public.yml`, `api/Caddyfile.public`, `api/.env.public.example`, `frontend/Dockerfile`, et les scripts de ce dossier. C'est une sous-section de la procédure du projet (README racine, « Procédure locale »), pas une procédure à part.

```
visiteur -> Cloudflare (TLS) -> cloudflared -> caddy:80 -> frontend:3000 (datacommune.fr)
                                                        -> api:8000     (api.datacommune.fr)
                                         db (PostGIS 17) : réseau compose seulement
```

Les scripts tournent sur le poste, joignent l'hyperviseur par ssh et le CT par `pct exec` (conventions des scripts privés de l'hyperviseur). Chacun accepte `--dry-run` (imprime ce qu'il ferait, n'exécute rien à distance) et ne manipule aucun secret : ceux-ci vivent dans `/opt/datacommune/.env` du CT (mode 600, root). Identifiants non secrets de la machine : `public.local.env`, ignoré par git (D-140), à remplir depuis `public.env.example` ; `PUBLIC_SETTINGS_FILE` en nomme un autre, et chaque valeur reste surchargeable par l'environnement. Un script refuse de démarrer (2) tant qu'une valeur manque.

Les gestes marqués **[MANUEL]** sont à faire par Ianis (Cloudflare, secrets).

## 1. CT (déjà créé : le conteneur public)

Créé par `provision-workbase.sh`, script privé de l'hyperviseur (Debian 12, non privilégié, nesting+keyctl, Docker et compose installés, secrets générés dans le CT) :

```bash
ssh <hyperviseur> 'bash -s' -- --hostname datacommune-public --ip <ip-du-ct> \
  --cores 4 --memory 8192 --swap 1024 --disk 60 < <chemin de provision-workbase.sh>
```

`/opt/datacommune/.env` contient alors `POSTGRES_PASSWORD`, `JWT_SECRET_KEY`, `API_KEY_HMAC_SECRET`, `ADMIN_PASSWORD`, `EMAIL_ENABLED=false`, `STRIPE_ENABLED=false`, `DEBUG=false`, `APP_ENVIRONMENT=workbase`, `API_BASE_URL=http://datacommune-public:8000`. La pile publique n'utilise **pas** ce fichier comme `env_file` : il ne sert qu'à l'interpolation de compose, et les valeurs publiques (`API_BASE_URL=https://api.datacommune.fr`, `CORS_ORIGINS`, `TRUST_PROXY_HEADERS=true`, `DEBUG=false`) sont posées en dur dans `docker-compose.public.yml`. Les anciennes valeurs `workbase` y sont ignorées. Seules deux lignes manquent, à ajouter par vous (étapes 4 et 5) : `CF_TUNNEL_TOKEN` et `PUBLIC_DC_API_KEY`.

Le disque de 60 Go doit contenir la base 44/85, un dump transitoire de même ordre, les images et 3 dumps de sauvegarde : vérifier la taille de la base source avant l'étape 3 (`wb-psql.sh "SELECT pg_size_pretty(pg_database_size(current_database()))"`, script privé de l'hyperviseur).

## 2. Base de données : démarrer le db, puis copier la base 44/85

```bash
infra/public/public-deploy.sh --dry-run --db-only      # relire
infra/public/public-deploy.sh --db-only                # pose la release, démarre db seul
infra/public/public-db-restore.sh --dry-run
infra/public/public-db-restore.sh
```

`public-db-restore.sh` lit la base de travail en lecture seule (`pg_dump -Fc`, transaction en lecture seule), diffuse le dump de la base de travail vers le conteneur public sur l'hyperviseur (rien ne passe par le poste), vérifie l'archive (`pg_restore --list`), recrée la base depuis `template0`, restaure sans propriétaires ni privilèges, `ANALYZE`, réancre les publications (`public-reanchor.sql`, sortie `REANCHORED …` puis `PUBLISHED … STALE 0`), imprime la révision Alembic. Rejouable : une base déjà restaurée est laissée telle quelle (`ALREADY_RESTORED`) ; `--reset-db` la détruit puis la refait.

Le périmètre 44/85 est dans la base (`python -m app.scope`), donc le dump complet le porte. **À vérifier avant ouverture** : ce que la base de travail contient d'autre (comptes `users`, clés `api_keys`, journaux de requêtes, tables d'import) ne doit pas devenir public ni exploitable. Exclure des données de table avec `PG_DUMP_EXTRA='--exclude-table-data=<table>'` (options pg_dump simples). Les clés d'API de la base de travail sont à révoquer ou à ne pas reprendre : ne garder que la clé anonyme de l'étape 5.

## 2 bis. Publication nationale : clone ZFS de la base de travail

C'est le chemin de publication de la base **nationale** (levier L3 de l'étude d'hébergement de la phase 4.2, tâche 0 du plan 04.2-05). Il complète l'étape 2 au lieu de la doubler : même script, mode `--from-zfs-clone`, même lib, même réancrage. Le dump de l'étape 2 reste le chemin 44/85 et le repli (voie 2 du plan). Le conteneur public garde sa pile, son tunnel et `public-deploy.sh` ; seule sa base change de support.

```bash
infra/public/public-db-restore.sh --from-zfs-clone --deploy <commit>            # à blanc (défaut) : relevés et refus réels, écritures imprimées
infra/public/public-db-restore.sh --from-zfs-clone --deploy <commit> --apply    # bascule ; imprime NATIONAL_SWITCH_OK <stamp>
infra/public/public-db-restore.sh --from-zfs-clone --rollback <stamp> --apply   # retour au montage d'avant ; ROLLBACK_OK <stamp>
infra/public/public-db-restore.sh --from-zfs-clone --drop-previous <stamp> --apply   # seul geste qui supprime : clone puis instantané
```

Ce que fait `--apply`, dans l'ordre (en-tête du script pour le détail) :

1. **Refus (80), rien de changé** : import en cours dans la base de travail (unité `dc-national` active, conteneur `dcimport-*` ou `dc-national-*`), aucune sauvegarde PBS du conteneur public de moins de 24 h (`pvesm list pbs-main --vmid <ct-public>`), `dcpool` absent ou au-dessus de 85 %, `pgdata` de la base de travail hors de `dcpool/work/pg`, CT non privilégié, superutilisateur ou nom de base différents entre travail et public, rôle du cluster de travail autre que son superutilisateur et `wb_reader`, clone déjà monté au conteneur public et non vérifié, instantané ou clone du stamp déjà présent, USB de sauvegarde non monté (`mountpoint`) ou d'espace libre inférieur à la taille de la base publique × `PUBLIC_DUMP_SIZE_FACTOR` (0,5) + `PUBLIC_DUMP_RESERVE_MB` (2048). Ces contrôles se font site en service : un refus ne coupe rien.
2. `pg_dump -Fc` (lecture seule) de la base publique vers `/mnt/usb-8tb/datacommune-backups/public-pre-national-<stamp>.dump` : taille non nulle, en-tête `PGDMP`.
3. **Arrêt de l'API publique (début de la coupure)**, puis export des lignes applicatives publiques vers `/mnt/usb-8tb/datacommune-backups/public-app-rows-<stamp>/` (liste de colonnes, compte, `COPY` texte par table ; compte vérifié). Tables : `APP_TABLES` du script, définie une seule fois (`users`, `api_keys`, `subscriptions`, `saved_searches`, `quota_usage`, `quota_usage_account`, `admin_audit_log`, `stripe_events`, `chat_sessions`, `chat_messages`, `api_request_log`, `signalements`).
4. `CHECKPOINT` dans le db de la base de travail, `zfs snapshot dcpool/work/pg@pub-<stamp>`, `dcpool/public` créé s'il manque, `zfs clone -o recordsize=32K` vers `dcpool/public/pg-<stamp>`. L'instantané est cohérent comme après une panne (PGDATA et `pg_wal` dans le même jeu) : PostgreSQL rejoue son WAL au démarrage.
5. État d'avant écrit dans `/root/dc-public/switch-<stamp>` (montage `mpN` du volume `datacommune-public_pgdata` ou aucun, mémoire, SHA servi) ; arrêt des conteneurs (API, db, le reste) ; `pct stop 103` ; `pct set 103 -mpN /dcpool/public/pg-<stamp>,mp=/var/lib/docker/volumes/datacommune-public_pgdata/_data -memory 12288` ; `pct start 103` ; db seul, attendu sain. L'ancien volume ext4 reste intact sous le montage.
6. Remise en état public de la copie, par la socket locale du db : `public-reset-temoin.sql` (`DROP DATABASE datacommune_temoin`) ; mot de passe du superutilisateur repris du `POSTGRES_PASSWORD` du conteneur (`\getenv`, jamais affiché) ; lignes exportées chargées dans un schéma de transit puis `public-reset.sql`, en une transaction : rôles de travail supprimés, tables applicatives vidées par un seul `TRUNCATE` puis rechargées, séquences replacées, chaque table comparée ligne à ligne à l'export ; `api_keys` doit valoir le compte exporté. Aucune clé de la base de travail ne peut rester.
7. `public-reanchor.sql`, **toujours** joué et affiché (`publication guard: …`) : `STALE 0` exigé. Le clone garde les OID, le réancrage ne devrait rien déplacer.
8. `public-deploy.sh --dry-run <commit>` puis `public-deploy.sh <commit>` : il voit le clone monté et prend l'instantané `dcpool/public/pg-<stamp>@pre-<sha12>-<date>` au lieu du dump avant migration, qui ne tient pas sur le rootfs du conteneur public (144 Go, incident du 2026-11-02, 04.2-05) (il démarre l'API : l'ancien code ne lit jamais le schéma du clone) ; contrôles par la boucle locale de caddy avec la clé anonyme du `.env` : `/v1/health`, commune 44109 et trois adresses (Paris 75001, Lyon 69002, Saint-Denis 97400), en 200 avec données. Puis `/root/dc-public/verified-<stamp>` et `NATIONAL_SWITCH_OK <stamp>`.

Un échec des étapes 5 à 8 déclenche le retour arrière automatique : arrêt, montage et mémoire d'avant remis, montages comparés à l'état d'avant, `public-deploy.sh <SHA servi avant>`, contrôles. Quand le dump `public-pre-national-<stamp>.dump` est sur l'hôte, il remplace le dump avant migration (`--backup-ref file:<dump>`) : il porte la base d'avant, sauf les lignes écrites entre ce dump et l'arrêt de l'API (étape 3), et ce déploiement ne migre rien. `--drop-previous` détruit aussi les instantanés `@pre-*` du clone. Le clone et l'instantané restent jusqu'à `--drop-previous`, refusé tant que ce clone est monté ou que le clone monté n'est pas vérifié.

Codes de sortie : 0 fait (ou à blanc imprimé) ; 2 usage ; 80 refus sans effet ; 81 échec avant la bascule, conteneur public sur sa base, API relancée (dump, export, instantané et clone déjà faits restent, nommés) ; 82 échec après la bascule, retour arrière fait et vérifié ; 83 retour arrière en échec, relancer `--rollback <stamp> --apply`.

À savoir : les sauvegardes PBS du conteneur public n'incluent pas un point de montage lié (`mpN` vers l'hôte) : la base publique nationale se reconstruit depuis la base de travail, et le dump d'avant bascule reste sur l'USB. Preuves locales : `infra/public/tests/test-public-national-switch.sh` (doubles, imprime `PUBLIC_SWITCH_TESTS_OK`) et `api/backend/tests/integration/test_public_national_reset.py` (SQL sur PostGIS jetable).

## 3. Cloudflare **[MANUEL]**

Dans Zero Trust, Networks, Tunnels :

1. Créer un tunnel « datacommune-public » (type cloudflared, Docker). Copier le **jeton** : il ne se colle jamais dans le chat ni dans le dépôt.
2. Public hostnames du tunnel : `datacommune.fr` -> `HTTP` `caddy:80` ; `api.datacommune.fr` -> `HTTP` `caddy:80`. (Cloudflare crée les CNAME proxifiés si la zone `datacommune.fr` y est ; sinon créer deux CNAME proxifiés vers `<id-du-tunnel>.cfargotunnel.com`.)
3. SSL/TLS de la zone en mode **Full** ou plus (le tunnel parle HTTP à caddy ; TLS s'arrête chez Cloudflare). Vérifier qu'aucun enregistrement A/AAAA n'existe déjà pour ces deux hôtes vers une ancienne origine.
4. Ajouter le jeton dans le CT, sans l'afficher :

```bash
ssh -t <hyperviseur> 'pct enter <ct-public>'      # puis, dans le CT :
read -rs -p "CF_TUNNEL_TOKEN: " t && printf 'CF_TUNNEL_TOKEN=%s\n' "$t" >> /opt/datacommune/.env && unset t
```

Désactiver aussi tout ancien tunnel/route qui pointait `datacommune.fr` ou `api.datacommune.fr` ailleurs (ancien CT, Workers) avant de basculer.

## 4. Clé anonyme publique **[MANUEL]**

La clé `dc_anon_*` est publique par conception : elle est incrustée dans le JS au moment du build de l'image. Elle doit exister **dans la base du conteneur public** (donc après l'étape 2), avec un quota plafonné :

```bash
ssh -t <hyperviseur> 'pct enter <ct-public>'      # dans le CT :
R=/opt/datacommune-public/releases/<sha12>        # release posée par l'étape 2
export IMAGE_TAG=<sha12> DEPLOYMENT_SHA=<sha complet> PUBLIC_DC_API_KEY=dc_anon_placeholder CF_TUNNEL_TOKEN=placeholder
dc() { docker compose -p datacommune-public --env-file /opt/datacommune/.env -f $R/api/docker-compose.public.yml "$@"; }
dc build api                                   # l'image API n'existe pas avant le premier déploiement
dc run --rm --no-deps -e ANON_KEY_QUOTA=500 api python -m scripts.rotate_anon_key
```

Les variables factices n'existent que dans cette session shell, pour l'interpolation de compose ; rien n'est écrit.

Le script demande de taper `ROTATE`, insère une clé `dc_anon_*` (quota mensuel `ANON_KEY_QUOTA`, 500 par défaut) et l'affiche **une fois**. Ne pas l'écrire dans le chat. Ajouter la clé au `.env` du CT :

```bash
read -rs -p "PUBLIC_DC_API_KEY: " k && printf 'PUBLIC_DC_API_KEY=%s\n' "$k" >> /opt/datacommune/.env && unset k
```

**Rotation** : relancer le script (nouvelle clé), mettre à jour `PUBLIC_DC_API_KEY` dans le `.env`, redéployer (`public-deploy.sh`, ce qui reconstruit le frontend avec la nouvelle clé), vérifier le bundle (`curl -s https://datacommune.fr/ | grep -o 'dc_anon_[A-Za-z0-9]*'` ne doit jamais montrer `dc_live_`), puis révoquer l'ancienne clé par l'administration. Le validateur du build refuse toute clé hors `dc_anon_`.

## 5. Construire et lancer

```bash
infra/public/public-deploy.sh --dry-run
infra/public/public-deploy.sh            # HEAD ; ou: infra/public/public-deploy.sh <commit>
```

Refus (80), avant toute action, si le commit n'est pas sur `master` du dépôt public (D-135 : rien n'est servi qui n'y soit pas) : ancêtre de `PUBLIC_PUBLISHED_REF`, par défaut `refs/remotes/public/master`, donc `git fetch public` d'abord ; référence inconnue : refus aussi. `--allow-unpublished` passe outre en secours (ligne `WARNING` dans le journal) ; `--rollback` revient à une release déjà servie et n'est pas concerné. Puis refus (80) tant que la base n'est pas restaurée, que `CF_TUNNEL_TOKEN` manque ou que `PUBLIC_DC_API_KEY` n'est pas `dc_anon_*`. Sinon : build des deux images (`datacommune-public-{api,frontend}:<sha12>`), db, dump de sécurité avant migration (`/opt/datacommune-public/backups`, 3 conservés ; refus 80, API en service, si l'espace libre est inférieur à la taille de la base × `PUBLIC_DUMP_SIZE_FACTOR` (0,5) + `PUBLIC_DUMP_RESERVE_MB` (2048) ; un dump échoué est supprimé ; sur une base montée depuis un clone `dcpool/public/pg-*`, pas de dump : instantané `<clone>@pre-<sha12>-<date>` pris sur l'hôte, API en service, et nommé dans le journal et `backups/pre-<sha12>-<date>.ref` ; `--backup-ref file:<dump>` le remplace par un dump déjà détenu, ce que seul le retour arrière de `public-db-restore.sh --from-zfs-clone` passe), **arrêt de l'API publique** (vérifié : plus aucun conteneur `api` en marche), `alembic upgrade head`, `up -d`, attente des healthchecks, fumée par la boucle locale de caddy (`127.0.0.1:8080` dans le CT, en-têtes Host) : santé de l'API, SHA servi par `/__version`, HSTS sur une page statique, `X-Robots-Tag` de l'API. Sortie `DEPLOYED <sha>` et `PUBLIC_EXIT=0`.

L'API est donc hors ligne de son arrêt jusqu'à ce que la nouvelle release soit saine (durée de la migration comprise) : l'ancien code ne lit jamais un schéma qu'il ne connaît pas, et une migration n'a pas à être rétrocompatible (106 ne l'est pas). Si la migration échoue (transaction unique annulée, schéma inchangé), l'ancien conteneur `api` est relancé.

Codes de sortie : 0 déployé ; 80 refus sans effet ; 81 échec avant bascule (build, db, dump, arrêt de l'API, migration) : schéma inchangé, l'ancienne API sert de nouveau ; 84 migration échouée (schéma inchangé) et l'ancienne API ne revient pas : redéployer le commit servi, comme le message l'indique ; 86 release non saine, pas de précédente ; 87 release non saine et schéma migré au-delà de la précédente : rien n'est relancé, restaurer le dump (§ 7) ; 90 release non saine, précédente relancée et saine (schéma inchangé par le déploiement) ; 91 retour arrière échoué (diagnostic : `ssh <hyperviseur> 'pct exec <ct-public> -- docker compose -p datacommune-public ps'`).

Vérification externe après la création des routes : `curl -sI https://datacommune.fr/` (HSTS, CSP, nosniff, X-Frame-Options, pas de `Server`), `curl -sI https://api.datacommune.fr/v1/health/live` (idem + `X-Robots-Tag: noindex, nofollow`).

## 6. Mise à jour

Même commande avec le nouveau commit. La base est migrée par `alembic upgrade head`, API arrêtée, avant le basculement ; la release précédente (images et sources) est conservée pour le retour arrière. Le site est hors ligne pendant la migration : choisir le créneau (101-106 : minutes, cf. IN-03 de `04.1-REVIEW-CIBLEE.md`).

**Release qui ajoute des sources au catalogue** (04.7-04 : `altimetrie`, `geologie` ; 04.7-05 : `axes_transport`, `peb` ; 04.7-06 : `delinquance`, `eau_potable`, `annuaire_education` ; 04.7-07 : `qualite_air`, `filosofi_carreaux`, `iris` ; 04.7-08 : `zone_tendue`, `encadrement_communes`). Le périmètre hébergé est en base, et `app.scope set` **remplace** tout le périmètre. On relit donc d'abord le périmètre en place, puis on le repose à l'identique avec les nouvelles clés. Sans cette étape, les thèmes de ces sources restent « hors couverture » (jamais zéro, jamais panne). Dans le conteneur public, après `public-deploy.sh <commit>` :

```bash
API=$(docker ps -q --filter label=com.docker.compose.project=datacommune-public --filter label=com.docker.compose.service=api | head -1)
docker exec "$API" python -m app.scope show --json
docker exec "$API" python -m app.scope set --datasets <datasets lus>,altimetrie,geologie,axes_transport,peb,delinquance,eau_potable,annuaire_education,qualite_air,filosofi_carreaux,iris,zone_tendue,encadrement_communes --departements <départements lus>
docker exec "$API" python -m app.scope show
```

Faire de même sur la base de travail (`wb-tool.sh python -m app.scope show --json`, puis `set`, script privé de l'hyperviseur), pour que le prochain clone national porte les clés. Non vérifié à l'écriture : les droits d'écriture du rôle de la base publique sur la table du périmètre.

**04.7-08, colonnes à réimporter.** La migration 110 ajoute quatre colonnes nullables : `fiscalite_locale.majoration_thrs` et `taux_majoration_thrs`, `copropriete.mandat_en_cours` et `date_fin_dernier_mandat`. Elles restent vides jusqu'au prochain import de `fiscalite` et de `copropriete` sur la base de travail (après la chaîne 04.2), et la fiche les dit « hors couverture » d'ici là, jamais « sans majoration » ni « sans mandat ».

## 7. Rollback

**Le schéma n'est jamais rétrogradé.** Une release n'est relancée que si la révision de la base est la tête de ses propres migrations (`api/backend/migrations/versions` de la release) : son code lit alors le schéma tel qu'il est.

* Automatique : si la nouvelle release n'est pas saine après `up`, le script relance la précédente quand la migration n'a rien changé pour elle (code 90). Si la base a été migrée au-delà de la précédente, rien n'est relancé (code 87) : le message nomme le dump `pre-<sha12>-*.pgdump` et le commit à redéployer.
* Manuel : `infra/public/public-deploy.sh --rollback` relance la release `previous_sha`, sans reconstruire ; refusé (80, rien touché) si la base est à une révision que cette release ne termine pas.
* Retour par le dump (après un 87, ou avant un `--rollback` refusé), API arrêtée (`docker stop` du conteneur `api` du projet `datacommune-public`) : dans le CT, `pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error` du dump `backups/pre-<sha12>-*.pgdump` dans le conteneur db, puis **réancrer les publications** avec `infra/public/public-reanchor.sql` (le même fichier que joue `public-db-restore.sh`) : une restauration recrée les tables sous de nouveaux OID et la garde de publication refuserait toutes les sources. Ensuite `infra/public/public-deploy.sh <commit>` avec le commit nommé par le message (ou `--rollback`). Sinon corriger vers l'avant. Ne jamais `alembic stamp`.
* Retour par l'instantané (base sur un clone `dcpool/public/pg-*`, après un 87 qui nomme `<clone>@pre-<sha12>-<date>`) : arrêter les conteneurs `api` et `db` du projet, puis sur l'hyperviseur `zfs rollback <clone>@pre-<sha12>-<date>` (sans `-r` : c'est l'instantané le plus récent du clone, sinon refus), redémarrer `db`, puis `infra/public/public-deploy.sh <commit>` avec le commit nommé par le message. Les OID ne bougent pas : pas de réancrage à faire. Ou revenir à la base d'avant la bascule : `public-db-restore.sh --from-zfs-clone --rollback <stamp> --apply`.

## 8. Arrêt

```bash
infra/public/public-down.sh              # stop : conteneurs conservés, site hors ligne
infra/public/public-down.sh --remove     # conteneurs et réseau supprimés, volumes conservés
infra/public/public-down.sh --purge-data # supprime aussi les volumes (base publique)
```

Détruire le CT lui-même reste un geste manuel sur l'hyperviseur (`pct stop <ct-public>`, `pct destroy <ct-public>`). Pour couper le site sans toucher au CT : désactiver les routes du tunnel dans Cloudflare.

## IP du visiteur et limitation par IP

L'API limite le débit **par IP** (`rate_limit_per_ip`, 120/min) et lit `cf-connecting-ip` avant `x-forwarded-for` (`api/backend/app/middleware.py`, `_get_client_ip`, `TRUST_PROXY_HEADERS=true`).

* **Navigateur -> API** (`https://api.datacommune.fr`, appels `api()` côté client) : Cloudflare pose `CF-Connecting-IP` = le visiteur ; caddy le conserve et le copie dans `X-Forwarded-For`/`X-Real-IP` (écrasement, un `X-Forwarded-For` forgé ne passe pas).
* **Navigateur -> frontend** : `ADDRESS_HEADER=CF-Connecting-IP` fait de `getClientAddress()` l'adresse du visiteur.
* **Frontend (SSR) -> API** : les appels des chargeurs ne repassent pas par Cloudflare. `API_INTERNAL_URL` (`http://api:8000`, réseau compose) remplace l'origine publique de l'API et l'adresse du visiteur part en `CF-Connecting-IP` (`versApiInterne` et `handleFetch`, `frontend/src/lib/server/api-route-fetch.ts`, depuis `dc4f349a`). L'API n'est joignable que par caddy et le frontend : cet en-tête n'est pas forgeable de l'extérieur. Chaque visiteur des pages rendues côté serveur a donc son propre seau. Sans `API_INTERNAL_URL` (développement, tests), les appels partent tels quels.
