# Go-Live Checklist (Interne)

Checklist operationnelle pour valider que DataCommune est exploitable en interne sans faux positifs.

## 1) Quality gates (obligatoire)

- [ ] Lancer `./scripts/go-live-verify.sh`
- [ ] Lancer `./scripts/preflight.sh` localement (lint + format + tests)
- [ ] Verifier CI verte sur PR (`Lint & Tests`)
- [ ] Confirmer qu'aucun test n'est skippe silencieusement

## 2) Health & monitoring

- [ ] `GET /v1/health/live` retourne 200
- [ ] `GET /v1/health` retourne 200 si OK, 503 si degrade/down
- [ ] Uptime Kuma pointe sur `/v1/health` (readiness)
- [ ] Docker healthcheck API pointe sur `/v1/health/live`

## 3) Database migrations & scheduler

- [ ] `alembic upgrade head` applique toutes les migrations (incl. 020)
- [ ] Timers systemd `datacommune-job@*.timer` actifs (`systemctl list-timers`) et derniere execution sans echec

## 4) ETL production verification (Phase 10-02)

Runbook detaille: [docs/phase-10-02-runbook.md](phase-10-02-runbook.md)

- [ ] Executer `python /app/run_etl.py` sur les 10 datasets
- [ ] Executer `python -m validate_etl` et obtenir 0 echec
- [ ] Verifier `dataset_metadata.row_count` > seuils attendus
- [ ] Verifier `etl_runs` : dernier status `success` pour chaque dataset
- [ ] (Option auto) Executer `/opt/datacommune/scripts/phase-10-02-check.sh` sur LXC et archiver le rapport

## 5) Backup / restore drill

- [ ] Backup manuel: `/opt/datacommune/scripts/backup-pg.sh`
- [ ] Controle du fichier `.dump` cree + taille non nulle
- [ ] Restore de test: `/opt/datacommune/scripts/restore-pg.sh <dump>` sur cible non-prod
- [ ] Verifier que `/v1/health` et `/v1/datasets` restent coherents apres restore

## 6) Deployment consistency

- [ ] `scripts/deploy.sh` redemarre bien `api` + `scheduler`
- [ ] Tests smoke post-deploy: `/v1/health/live`, `/v1/health`, `/v1/datasets`
- [ ] Logs applicatifs visibles (api + scheduler)

## 7) Security & config

- [ ] `POSTGRES_PASSWORD` fort en prod (pas de valeur par defaut)
- [ ] Secrets CI/CD configures (`LXC_HOST`, `LXC_USER`, `LXC_SSH_KEY`, `LXC_PORT`)
- [ ] Aucune fuite de secret dans repo/historique

## Exit criteria (go interne)

Go interne valide si:

1. Quality gates vertes (local + CI)
2. Health/readiness conforme (503 en degrade/down)
3. ETL validee (10/10 datasets)
4. Backup/restore drill execute avec succes
5. Deploiement api+scheduler reproductible

---

## 8) OPS-01 host-apply — scheduler off + systemd timers (Phase 37, Plan 05)

Applique le 2026-07-02 (21:00–21:05 UTC) sur LXC 275 (`root@<ip-ct-275>`), supervise.

### Deploiement code Wave 1 (mecanisme reel = copie, pas de repo git sur le LXC)

- Snapshot rollback pris avant toute ecriture : `/root/datacommune-predeploy-2026-07-02.tgz` (3.5M)
- Sync : `git archive dev(HEAD=87728b3) -- api infra | ssh root@<ip-ct-275> 'tar -x -C /opt/datacommune'`
- Derive live verifiee avant ecrasement : seuls diffs = fins de ligne CRLF (copie Windows) + profile scheduler Wave 1 absent — zero hotfix live ecrase
- Rebuild : `docker compose -p datacommune-api -f docker-compose.yml -f docker-compose.vps.yml build api` → image `datacommune-api:dev` (sha256:bb849e7e...)
- **Anti-incident 2026-04-03** : `.env` stat AVANT = 2322 bytes / mtime 2026-07-02 12:09:11.575835997 +0000 ; APRES extract, APRES build, APRES up = identique. Volume pgdata non touche (db container reste Up 2 days, jamais recree).

### Scheduler off (D-01) + 7 timers (D-03)

```
$ docker exec datacommune-api-api-1 python -m app.scheduler.run_job --help
usage: run_job [-h]
               {check-freshness,cleanup-api-log,cleanup-old-tables,cleanup-orphan-staging,refresh-commune-synthese,refresh-communes-lookup,refresh-scoring-renov}

$ docker compose -p datacommune-api ps scheduler        # 2026-07-02 21:03:17 UTC
NAME      IMAGE     COMMAND   SERVICE   CREATED   STATUS    PORTS
(vide — D-01 effectif ; ancien container Exited(1) supprime)

$ systemctl list-timers 'datacommune-job@*'             # 2026-07-02 21:02 UTC
NEXT                        LEFT          LAST PASSED UNIT                                           ACTIVATES
Thu 2026-07-02 22:00:00 UTC 57min left    -    -      datacommune-job@cleanup-old-tables.timer       datacommune-job@cleanup-old-tables.service
Fri 2026-07-03 01:00:00 UTC 3h 57min left -    -      datacommune-job@cleanup-orphan-staging.timer   datacommune-job@cleanup-orphan-staging.service
Fri 2026-07-03 06:00:00 UTC 8h left       -    -      datacommune-job@refresh-commune-synthese.timer datacommune-job@refresh-commune-synthese.service
Fri 2026-07-03 06:10:00 UTC 9h left       -    -      datacommune-job@refresh-communes-lookup.timer  datacommune-job@refresh-communes-lookup.service
Fri 2026-07-03 06:20:00 UTC 9h left       -    -      datacommune-job@refresh-scoring-renov.timer    datacommune-job@refresh-scoring-renov.service
Fri 2026-07-03 09:00:00 UTC 11h left      -    -      datacommune-job@check-freshness.timer          datacommune-job@check-freshness.service
Sun 2026-07-05 01:00:00 UTC 2 days left   -    -      datacommune-job@cleanup-api-log.timer          datacommune-job@cleanup-api-log.service
7 timers listed.
```

### Purge api_request_log independante du scheduler (OPS-03)

Instance jeune (go-live 30/06) : 0 ligne >7j naturellement → ligne temoin synthetique
inseree en transaction (`ts = NOW() - 10 days`, `path = '/ops03-purge-proof'`).

- Baseline apres insert : `count(*) WHERE ts < NOW() - INTERVAL '7 days'` = **1**
- Run (scheduler off, 2026-07-02 21:03:17 UTC) : `docker exec datacommune-api-api-1 python -m app.scheduler.run_job cleanup-api-log` → **exit 0**
- Apres purge : count = **0** — purge prouvee independante du scheduler
- Observation : run_job n'emet pas la ligne de log "deleted N" sur stdout (logging non configure en contexte CLI ; le signal systemd = exit code). Amelioration possible, non bloquante.

### Check freshness 48h (OPS-01) — A REMPLIR le 2026-07-04 apres 21:00 UTC

Baseline matview_metadata (2026-07-02 21:05 UTC — gelees depuis la mort du scheduler) :

```
     matview_name     |        last_refresh_at
----------------------+-------------------------------
 communes_lookup      | 2026-06-30 08:43:41.885825+00
 scoring_renov_target | 2026-06-30 08:43:42.189133+00
 v_commune_synthese   | 2026-06-30 08:46:22.372699+00
```

SQL a executer (attendu : les 3 last_refresh_at < 25h, count >7j = 0, sans intervention manuelle) :

```
ssh root@<ip-ct-275> "docker exec -i datacommune-api-db-1 psql -U datacommune -d datacommune -c \
  \"SELECT matview_name, last_refresh_at, NOW() - last_refresh_at AS age FROM matview_metadata;
    SELECT count(*) FROM api_request_log WHERE ts < NOW() - INTERVAL '7 days';\""
```

- [ ] 2026-07-04 : matviews < 25h + log purge a 7j → coller le resultat ici

**Caveat RISK-1 / Open Q3** : `/v1/health` peut rester `degradee`/503 pour des datasets ETL
hors scope D-04 (fraicheur ETL, pas maintenance) — ce n'est PAS une regression OPS-01 et
"health = 200" n'est PAS un critere d'acceptation de cette section.
