# Deploy runbook — backend (ancien conteneur de production)

Procédure canonique de déploiement du backend sur **l'ancien conteneur de production** (`@<ip>`, LXC de l'hyperviseur,
derrière Cloudflare Tunnel). Cette procédure existe sous deux formes :

- **Automatisée** : le workflow coordinateur `.github/workflows/cd-release.yml`
  déploie d'abord le frontend, puis appelle `.github/workflows/cd-api.yml` sur le
  self-hosted runner de ce conteneur à chaque push `master`. Les deux composants sont
  systématiquement déployés au même SHA, même après un push documentaire, pour
  récupérer les changements d'un éventuel run échoué ou évincé. Voir **ADR-0005**.
- **Manuelle** : ci-dessous — filet de secours quand le runner est indisponible.

L'ordre est imposé par **ADR-0003** (gate migrations). Le point non négociable :
**migrer AVANT la bascule**, jamais après. C'est ce qui a manqué lors de l'incident
dpe-500 (2026-07-02→03 : code à la révision 070, DB à 067, `/v1/dpe` 500).

> ⚠️ **Piège DB-wipe** : toujours `-p datacommune-api` (ou `COMPOSE_PROJECT_NAME=datacommune-api`)
> sur **chaque** commande compose. Sans lui, compose crée des volumes vides depuis le
> basename du CWD → **DB perdue**. Même registre que le wipe 2026-04-03.

## Variables

```bash
export COMPOSE_PROJECT_NAME=datacommune-api      # garde-fou anti DB-wipe
ENV_FILE=/opt/datacommune/api/.env               # secrets host, hors git (path confirmé SSH 2026-07-03)
COMPOSE="docker compose --env-file $ENV_FILE -f docker-compose.yml -f docker-compose.vps.yml"
```

Toutes les commandes ci-dessous se lancent depuis le dossier `api/` (checkout du repo, ou
`/opt/datacommune/api` en secours).

## Procédure (ordre imposé — ADR-0003 D1)

### 1. Pré-checks

```bash
df -h /var/lib/docker            # marge disque suffisante (build + migration) — abort si < 5 Go
# Confirmer un backup PBS < 24h existe (restore = filet si la migration tourne mal).
stat -c '%y %s' "$ENV_FILE"      # noter mtime/taille du .env avant (anti-incident 2026-04-03)
```

### 2. Sauvegarde rollback historique (diagnostic)

```bash
docker image inspect datacommune-api:dev >/dev/null 2>&1 \
  && docker tag datacommune-api:dev datacommune-api:previous
```

Depuis la migration 073, `:previous` n'est plus une cible de rollback autorisée :
l'image pré-Phase-43 contient l'ancien logger et l'ancien ETL fiscal incompatible avec
le stockage en points. Le CD crée `:privacy-safe-baseline` uniquement après un
déploiement entièrement vérifié. Si le premier déploiement Phase 43 échoue avant cette
gate, le seul filet sûr est le backup PBS confirmé avant merge ou un correctif forward.

### 3. Sync code (manuel uniquement)

Le CD automatisé saute cette étape (le runner a le checkout git). En manuel :

```bash
git archive dev -- api infra | ssh root@<ip-ct-275> 'tar -x -C /opt/datacommune'
stat -c '%y %s' "$ENV_FILE"      # vérifier que le .env n'a PAS bougé (mtime/taille identiques)
```

### 4. Build

```bash
$COMPOSE build api
```

### 5. Migrations AVANT bascule (⚠️ étape critique)

```bash
$COMPOSE run --rm api alembic upgrade head
```

Un container jetable de la **nouvelle** image migre pendant que l'**ancien** container sert
toujours (hors lock). Les migrations doivent être **additives / backward-compatibles**. Une
migration lourde en lock (type **065**) = **fenêtre de maintenance** planifiée, signalée dans
son propre docstring — la jouer hors trafic.

### 6. Bascule

```bash
$COMPOSE up -d
```

### 7. Vérification

> ⚠️ **Piège Host header** : l'API ne publie aucun port host, tout passe par Caddy. Son
> vhost par défaut répond 200 (corps vide) pour un `Host` non reconnu — sans le header
> ci-dessous le check est **vacuous** (confirmé en production le 2026-07-03 : sans header,
> 200 size=0 ; avec header, 200 size=15 réel).

```bash
curl -sf -H "Host: api.datacommune.fr" http://localhost:80/v1/health/live   # doit répondre 200
$COMPOSE exec -T api alembic current                                       # doit contenir "(head)"
```

Si l'un des deux échoue → **rollback** (section ci-dessous).

## Rollback

Revenir à une image précédente qualifiée (le code seulement — **pas** les migrations).
L'invariant **D-02** ADR-0003 impose un schéma inchangé ; des migrations additives ne
garantissent pas la compatibilité des lecteurs ou du scheduler avec les données publiées.
Le workflow est manuel ; le CD ne déclenche aucun rollback automatique.

La présence réelle de `public.etl_publications` impose le contrat de publication `1`
sur l'image de secours : label Docker `org.datacommune.publication-contract=1`, inspecté
par ID immuable. Label absent, inconnu ou erreur d'inspection/SQL : arrêt avant modification
de `IMAGE_TAG` ou cutover. Le prédécesseur `a5ea02f` ne possède pas ce contrat : sa lecture
ignore les générations pending/failed et peut servir une vue liée à une source retirée.
Voir [la preuve différentielle L10](../../docs/audit-stabilisation-2026-09-08/lot-10/rollback/README.md).
Le label déclare une capacité ; il ne remplace pas les tests API/scheduler de publication,
de concurrence et de restauration, ni les preuves de release de l'image choisie.
Sans cible qualifiée, suivre le correctif en avant ou la restauration supervisée d'une
sauvegarde compatible avec son binaire. Ne pas réécrire le journal pour contourner le garde.

### Procédure canonique — `rollback-api.yml`

Déclenchement manuel, sans SSH, via GitHub Actions (canal **D-01**) :

1. Onglet **Actions** du repo → workflow **Rollback API** → **Run workflow** sur la branche
   `master` uniquement. Le job refuse tout autre ref et checkout explicitement `master`.
2. Préconditions du drill **D-07** avant de lancer : fenêtre calme (peu de trafic), backup
   PBS < 24h confirmé (`backup-restore-runbook.md`).
3. Le workflow : valide le prédécesseur enregistré dans
   `/opt/datacommune/api/.release-rollback-state` et son ID Docker → crée l'alias
   `datacommune-api:privacy-safe-rollback` → lit
   `alembic_version` en base (baseline D-02, indépendant de l'image) → vérifie le contrat
   publication du prédécesseur contre la présence du journal en base → écrit
   `IMAGE_TAG=privacy-safe-rollback` dans `$ENV_FILE`
   (`sed -i` idempotent, jamais d'écrasement — Pitfall 1) → `up -d` (pas de build) → vérifie
   `/v1/health/live` avec le header `Host` → relit `alembic_version` et vérifie qu'il est
   **inchangé** vs la baseline. On lit la version directement en base, PAS via `alembic current`
   dans l'image rollbackée : celle-ci prédate la dernière révision et ne saurait pas la résoudre
   (elle échouerait à tort alors que le schéma est intact). L'ID réellement
   démarré doit correspondre à celui enregistré ; la baseline vérifiée est
   ensuite rétablie sur cette image restaurée.
4. Si `alembic_version` a bougé après rollback : **anomalie** — le schéma n'aurait jamais dû
   bouger (D-02). Investiguer avant tout nouveau déploiement, ne pas relancer le rollback.

### Filet manuel (runner indisponible)

Le prédécesseur est figé avant chaque nouveau candidat dans un fichier de deux
lignes : SHA Git complet, puis ID Docker `sha256:...` ou `none`. Un retry du
même candidat ne remplace pas cette cible, même si la baseline a été promue
après les contrôles techniques. Cela permet le rollback d'une régression
détectée ensuite dans le parcours métier. Un premier déploiement sans cible
certifiée exige le filet PBS/forward-fix ; ne jamais reprendre `:previous`.

```bash
set -euo pipefail
mapfile -t rollback_state < "${ENV_FILE%/*}/.release-rollback-state"
[[ "${rollback_state[0]:-}" =~ ^[0-9a-f]{40}$ ]]
rollback_id="${rollback_state[1]:-}"
[[ "$rollback_id" =~ ^sha256:[0-9a-f]{64}$ ]]
docker image inspect "$rollback_id" >/dev/null
# Same fail-closed admission as the workflow; no lexical migration comparison.
has_publications=$($COMPOSE exec -T db \
  sh -c 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT to_regclass('\''public.etl_publications'\'') IS NOT NULL"' | tr -d '[:space:]')
case "$has_publications" in
  f) ;;
  t)
    contract=$(docker image inspect --format '{{ index .Config.Labels "org.datacommune.publication-contract" }}' "$rollback_id")
    [ "$contract" = "1" ] || { echo "Publication contract unavailable: supervised forward-fix/backup recovery required"; exit 1; }
    ;;
  *) echo "Cannot establish database publication contract"; exit 1 ;;
esac
docker tag "$rollback_id" datacommune-api:privacy-safe-rollback
grep -q '^IMAGE_TAG=' "$ENV_FILE" || echo 'IMAGE_TAG=dev' >> "$ENV_FILE"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=privacy-safe-rollback/" "$ENV_FILE"
$COMPOSE up -d
curl -sf -H "Host: api.datacommune.fr" http://localhost:80/v1/health/live
# D-02 : lire la version en base (pas via l'image rollbackée) — doit être inchangée.
$COMPOSE exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"'
```

Toujours `COMPOSE_PROJECT_NAME=datacommune-api` (`-p datacommune-api`) sur ces commandes —
même garde anti-DB-wipe que la procédure de déploiement.

Le filet manuel exige un état valide et l'image du prédécesseur encore présente. Ne jamais
remplacer cette cible par `:previous` après 073. Après la bascule, valider/recharger Caddy
et purger uniquement `/data/access.log*` comme le workflow avant toute reprise du trafic
de résolution supervisé.

Rollback de schéma : **manuel et exceptionnel** (`alembic downgrade`) — à éviter, préférer
un restore PBS si la migration a corrompu des données. Les scripts historiques
`scripts/rollback.sh` / `deploy.sh` / `up-prod.sh` / `scripts/deploy-full.sh` sont
**hard-deprecated** (écrits pour le legacy `prod.yml` / tag `:latest` — ils affichent un
message d'erreur et sortent en `exit 1` immédiatement) — ne pas les utiliser pour ce host,
suivre cette procédure à la place.

### Deploy CD — preuve (OPS-05 tag SHA + OPS-06+ migration positive)

Premier deploy CD portant une vraie migration (072), prouvé le **2026-07-05 ~13:52 UTC**
(run cd-api.yml `28743032429`, déclenché par le merge de la PR #18 sur `master`).

| Champ | Valeur observée |
|-------|-----------------|
| Date du deploy | 2026-07-05 ~13:52 UTC |
| Ref / commit déployé | `master` `35483e8` (merge PR #18) |
| Tag de l'image qui tourne (OPS-05) | `datacommune-api:sha-35483e8` (plus `:dev`) |
| Migration en migrate-before-cutover (OPS-06+) | log : `Running upgrade 071_geo_communes_search_indexes -> 072_mutations_idx` (13:52:56, avant le cutover) |
| `alembic current` | `072_mutations_idx (head)` |
| `idx_mutations_id_parcelle` sur `mutations` | présent (btree, propagé aux partitions `mutations_44/85/default_id_parcelle_idx`) |
| `EXPLAIN ANALYZE … WHERE id_parcelle = …` (D-05) | `Index Scan using mutations_44_id_parcelle_idx` (+85), **pas** Seq Scan ; `Execution Time: 0.245 ms` |
| `/v1/health/live` (Host) / `/v1/health` | 200 / `statut:"ok"` |
| `.env` host (fix T1) | `stat` 2425 → 2447 o, une seule ligne `IMAGE_TAG` écrite **après** migration réussie ; aucun autre secret modifié |

### Drill rollback — preuve (D-07)

Premier `workflow_dispatch` réel de `rollback-api.yml`, prouvé le **2026-07-05 ~15:09 UTC**
(run `28745128258`). Rollback vers l'image **pré-072** contre une DB déjà à 072 — le cas
exact où l'ancien check `alembic current | grep (head)` aurait échoué à tort (corrigé, fix T2 :
la vérif lit `alembic_version` directement en base, indépendamment de l'image).

| Champ | Valeur observée |
|-------|-----------------|
| Date du drill | 2026-07-05 ~15:09 UTC |
| Image avant rollback (tag SHA) | `sha-35483e8` |
| Image après rollback (`:previous` / SHA N-1) | `datacommune-api:previous` (image pré-072) |
| `/v1/health/live` avec `Host: api.datacommune.fr` | 200 |
| `alembic_version` DB inchangé vs baseline (D-02) | `072_mutations_idx` avant = après (lu directement en base) |
| Durée de bascule (déclenchement → health OK) | < 1 min (dispatch 15:08:57 → run success ; bascule `up -d` + health en secondes) |
| Retour à l'état nominal historique | re-dispatch `cd-api.yml` (run `28745182008`) → `sha-35483e8` en service, `alembic 072 (head)`, health 200 `ok` |

Ce dispatch direct est une preuve historique. Désormais, tout retour à l'état
nominal passe par un dispatch de `cd-release.yml` sur `master`, qui redéploie et
vérifie le frontend avant l'API.

> Note rétention (C3, self-heal) : après le drill + restore, `:previous`/`sha-35483e8`/`dev`
> pointent la même image (le rebuild du restore a réécrit l'alias `:previous`) — cosmétique,
> corrigé au prochain vrai deploy qui repositionne `:previous` = SHA N-1 authentique.

### Test négatif migration (D-06)

Preuve du comportement d'**échec** de la chaîne migrate-before-cutover : une migration cassée
fait sortir `alembic upgrade head` en erreur AVANT le cutover, l'ancien conteneur continue de
servir, aucun état partiel ne fuit. Preuve par construction en appui : `set -euo pipefail` +
l'ordre des steps (migrate strictement avant `up -d`).

**Procédure (jamais sur le conteneur de production) :**

1. Restaurer un CT jetable depuis un snapshot PBS récent (`backup-restore-runbook.md §4`),
   **isolé** : `pct set <vmid> --onboot 0` + `net0 ...,link_down=1` (jamais de réseau, pas de
   conflit d'IP avec 275). Démarrer.
2. Injecter LOCALEMENT une migration Alembic volontairement échouante dans un conteneur
   éphémère (`docker compose run --rm --no-deps --user root --entrypoint sh api`), chaînée après
   la tête courante, avec une DDL invalide (ex. `CREATE INDEX … ON table_that_does_not_exist`).
   ⚠️ **Pitfall 4** : le fichier ne doit JAMAIS être `git add`/commité — ici il n'existe que dans
   le conteneur éphémère du clone (le clone `/opt/datacommune` n'est pas un repo git), il ne peut
   structurellement pas atteindre git.
3. Lancer `alembic upgrade head` → observer l'abort. Détruire le CT jetable (`pct stop` + `pct destroy`).
4. Confirmer `git status` propre dans le repo réel.

**Preuve (2026-07-05, CT jetable `9275`) :**

| Champ | Valeur observée |
|-------|-----------------|
| Date | 2026-07-05 ~15:25 UTC |
| Snapshot PBS restauré | sauvegarde PBS du conteneur de production, `2026-07-05T00:00:09Z` |
| CT jetable | VMID `9275`, isolé (`net0 link_down=1`, `onboot 0`), détruit après (`vm-9275-disk-0` supprimé) |
| Migration cassée | `999_broken_negtest` chaînée après `071_geo_communes_search_indexes` (image clone pré-072) |
| `alembic upgrade head` | **exit 1** — `psycopg2.errors.UndefinedTable: relation "table_that_does_not_exist" does not exist` |
| Ancien conteneur api | `datacommune-api-api-1 running` **avant ET après** l'abort (cutover jamais atteint) |
| `alembic_version` DB | `071` avant = après (DDL transactionnelle rollback — **aucun état partiel**) |
| `git status` repo réel | propre (`999_broken_negtest` absent de git — Pitfall 4 satisfait) |

**Rétention keep-last-3 (observation) :** au 2026-07-05, 1 seul tag `sha-*` en production
(`sha-35483e8`) → le sweep keep-last-3 n'a rien à balayer. **À re-observer après ≥4 deploys**
(compter exactement 3 `sha-*` + `:dev` + `:previous` survivants).

**OPS-07 (opportuniste, reporté — non bloquant) :** le projet compose `datacommune-api` en
production liste 4 config files (2 du workspace runner `/opt/actions-runner/_work/…/api`, 2 canoniques
`/opt/datacommune/api`), car le CD lance `docker compose` depuis le checkout du runner. Cosmétique
(containers corrects, volumes préservés par `COMPOSE_PROJECT_NAME=datacommune-api`). Nettoyage
reporté : il exigerait une recréation de stack hors canal CD (règle « jamais de modif directe en
prod via SSH »). Se corrige naturellement le jour où le CD lancera compose depuis un chemin unique.

## Escape hatch — `ALLOW_SCHEMA_DRIFT` (ADR-0003 D2)

Le gate fail-fast au boot (D2, `app/common/migration_check.py`, branché dans le lifespan)
fait **refuser le démarrage** d'un boot avec DB en retard (crash-loop, alerte UptimeRobot).
Un boot avec DB **en avance** (révision inconnue de l'image — chemin rollback) ne fait que
logger un WARNING et démarre. Pour une opération exceptionnelle (ex. restore + investigation
sous une image en retard), poser `ALLOW_SCHEMA_DRIFT=true` dans l'environnement du container —
**jamais** dans un fichier compose versionné. Retirer aussitôt.

## Échappement du lock — migration 077 (index sur parent partitionné)

**Dette OPS-01 (PLAN-RESTES-2026-08-02 §6).** La migration
`077_dpe_identifiant_ban_index` crée `idx_dpe_identifiant_ban` sur le parent
partitionné `diagnostics_dpe` (102 partitions par département) **sans
`CONCURRENTLY`** — non supporté en une instruction sur un parent partitionné,
et impossible dans la transaction Alembic de toute façon. Le build prend donc
un lock `ACCESS EXCLUSIVE` sur la table pendant toute sa durée.

Ce lock a été absorbé une fois en prod sur le périmètre 44/85 (fenêtre courte,
2 partitions peuplées). **Il se réveille** dans deux cas — précédent : lock
long de la migration 065 :

- **re-host** (replay `alembic upgrade head` sur une base pleine) ;
- **extension départementale** (volume par partition ×N, fenêtre de lock ×N).

**Procédure d'échappement** (à dérouler AVANT `alembic upgrade head` dans ces
deux cas — la migration est `IF NOT EXISTS`-safe, elle constate alors l'index
déjà présent et ne bloque rien) :

```bash
# 1. Créer l'index invalide sur le parent SEUL (métadonnée, instantané).
#    ON ONLY = ne cascade pas ; l'index parent reste "invalid" tant que
#    toutes les partitions ne sont pas attachées.
psql "$DB" -c "CREATE INDEX IF NOT EXISTS idx_dpe_identifiant_ban
               ON ONLY diagnostics_dpe (identifiant_ban);"

# 2. Construire l'index de CHAQUE partition en CONCURRENTLY (aucun lock
#    d'écriture ; hors transaction ; répétable partition par partition).
for dept in $(psql "$DB" -Atc "
    SELECT relname FROM pg_class c
    JOIN pg_inherits i ON i.inhrelid = c.oid
    WHERE i.inhparent = 'diagnostics_dpe'::regclass"); do
  psql "$DB" -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS ${dept}_identifiant_ban_idx
                 ON ${dept} (identifiant_ban);"
done

# 3. Attacher chaque index de partition au parent (métadonnée, instantané).
#    Quand la dernière est attachée, l'index parent devient valide.
for dept in $(...même liste...); do
  psql "$DB" -c "ALTER INDEX idx_dpe_identifiant_ban
                 ATTACH PARTITION ${dept}_identifiant_ban_idx;"
done

# 4. Vérifier la validité, PUIS lancer alembic upgrade head (no-op sur 077).
psql "$DB" -c "SELECT indexrelid::regclass, indisvalid FROM pg_index
               WHERE indexrelid = 'idx_dpe_identifiant_ban'::regclass;"
```

Pièges :

- `CREATE INDEX CONCURRENTLY` qui échoue laisse un index **invalide** → le
  supprimer (`DROP INDEX CONCURRENTLY`) et relancer, jamais l'attacher.
- Un `ATTACH PARTITION` exige que l'index de partition ait des colonnes/opclass
  strictement identiques — garder la définition minimale ci-dessus.
- Consigner la **fenêtre de lock** du dernier `alembic upgrade head` réel dans
  la note de release (baseline : 077 sur 44/85 = absorbée sans incident,
  2026-07-31 ; fenêtre non chronométrée — chronométrer la prochaine).
- Même procédure applicable à tout futur index posé sur un parent partitionné
  (`mutations`, `parcelles`, `adresses` partagent la topologie).

## Voir aussi

- **ADR-0005** — design du CD self-hosted runner + checklist d'installation host.
- **ADR-0003** — gate migrations (runbook D1 + fail-fast boot D2).
- `.github/workflows/rollback-api.yml` — workflow de rollback canonique (D-01/D-02).
- `docs/go-live-checklist.md §8` — ce qui a été fait au go-live (historique, pas procédure).
