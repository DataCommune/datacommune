# DataCommune API

Plateforme SaaS pour donnees publiques francaises geolocalisees.
**27 datasets**, **37,5M lignes sur l'instance locale auditee le 13 juin 2026**,
**37 routers REST** avec entree polymorphe (adresse, GPS, cadastre, SIRENE).

Instance publique : `api.datacommune.fr` — backend actif, couverture initiale 44/85.
Developpement local : `http://127.0.0.1:8000`.

## Fonctionnalites

- **Entree polymorphe** &mdash; auto-detection du type de requete (adresse texte, GPS, reference cadastrale, SIRENE)
- **27 datasets** &mdash; 21 stockes localement + proxies temps reel (Georisques, IGN, Sirene, Errial)
- **37 routers REST** &mdash; organises en 3 tags OpenAPI (Parcelle, Commune, Proximite)
- **Enrichissement multi-dataset** &mdash; `/v1/resolve?q=48.856,2.352&datasets=ban,dvf,dpe,risques`
- **Scoring renovation** &mdash; croisement DPE passoires thermiques x Cadastre x DVF, export CSV
- **Billing SaaS** &mdash; Stripe Checkout (Decouverte 500/mois, Pro 5K, Business 25K, Enterprise 100K)
- **Auth** &mdash; JWT cookie HttpOnly (SPA) + X-API-Key header (API), registration, quota par plan
- **Exports** &mdash; PDF (WeasyPrint), CSV (BOM UTF-8), Excel (.xlsx)
- **ETL automatise** &mdash; 21 pipelines avec atomic table swap (zero downtime)
- **Partitionnement** &mdash; 102 partitions par departement, partition pruning sur requetes spatiales
- **CI/CD** &mdash; CI GitHub Actions ; CD API actif (runner self-hosted, deploy auto sur master)

## Stack

| Composant | Technologie |
|-----------|-------------|
| API | Python 3.12, FastAPI 0.135+, Uvicorn |
| BDD | PostgreSQL 17 + PostGIS 3.5 |
| ORM | SQLAlchemy 2.0 async + asyncpg + GeoAlchemy2 |
| ETL | psycopg 3 (COPY), pandas 3+, httpx async |
| Auth | JWT cookie HttpOnly + X-API-Key, pwdlib bcrypt, PyJWT |
| Billing | Stripe Checkout + webhooks |
| Logging | structlog JSON + RequestLoggingMiddleware |
| Rate limit | Sliding window in-memory (120 req/min IP, 500 burst/key) |
| Planification | Timers systemd hote -> `python -m app.scheduler.run_job <job>` (`infra/systemd/`) ; aucun ordonnanceur en processus |
| Reverse proxy | Caddy 2 (derriere Cloudflare Tunnel) |
| Infra | Docker Compose sur un LXC de l'hyperviseur (Cloudflare Tunnel) |
| CI/CD | GitHub Actions (ruff + pytest) ; deploy auto sur master via runner self-hosted |

## Demarrage rapide

```bash
# Cloner
git clone https://github.com/DataCommune/datacommune.git
cd DataCommune

# Configurer
cp .env.example .env
# Editer .env avec un mot de passe securise

# Lancer
docker compose up -d

# Migrations
docker exec datacommune-api-1 alembic upgrade head

# Creer une cle API
docker exec datacommune-api-1 python -m app.auth.cli create-key "mon-app"

# Tester
curl http://localhost:8000/v1/health
```

Documentation API : `http://localhost:8000/docs` (Scalar API Reference)

## Endpoints (selection)

### Parcelle

| Endpoint | Description | Source |
|----------|-------------|--------|
| `GET /v1/parcelle` | Parcelle cadastrale par GPS ou ref | Cadastre |
| `GET /v1/dvf` | Transactions immobilieres | DVF |
| `GET /v1/dpe` | Diagnostics energetiques | ADEME |
| `GET /v1/batiments` | Batiments IGN (BD TOPO) | IGN |
| `GET /v1/copropriete` | Registre des coproprietes | RNIC |
| `GET /v1/permis` | Permis de construire | Sitadel |
| `GET /v1/bdnb` | Base nationale des batiments | BDNB |
| `GET /v1/rnb` | Referentiel national batiments | RNB |

### Commune

| Endpoint | Description | Source |
|----------|-------------|--------|
| `GET /v1/geocode` | Adresse &rarr; GPS (pg_trgm) | BAN |
| `GET /v1/reverse` | GPS &rarr; adresse (KNN PostGIS) | BAN |
| `GET /v1/commune` | Synthese multi-dataset par commune | Vue materialisee |
| `GET /v1/loyers` | Indicateurs de loyers | cartedesloyers.fr |
| `GET /v1/encadrement-loyers` | Encadrement des loyers | Prefectures |
| `GET /v1/demographie` | Indicateurs demographiques | INSEE |
| `GET /v1/revenus` | Revenus fiscaux | DGFiP |
| `GET /v1/fiscalite` | Fiscalite locale | DGFiP |
| `GET /v1/vacance` | Logements vacants | Lovac |
| `GET /v1/zonage-abc` | Zone de tension immobiliere | DHUP |
| `GET /v1/equipements` | Equipements publics (BPE) | INSEE |

### Proximite

| Endpoint | Description | Source |
|----------|-------------|--------|
| `GET /v1/risques` | Risques naturels/industriels | Georisques* |
| `GET /v1/errial` | Etat des risques reglementaires | Errial* |
| `GET /v1/urbanisme` | Zones PLU | API Carto IGN* |
| `GET /v1/connectivite` | Eligibilite fibre/ADSL | Arcep |
| `GET /v1/antennes` | Antennes mobiles a proximite | ANFR |
| `GET /v1/entreprises` | Entreprises a proximite | Sirene* |
| `GET /v1/scolaire` | College de secteur | Education nationale |
| `GET /v1/transports` | Arrets + gares a proximite | GTFS + SNCF |
| `GET /v1/monuments` | Monuments historiques | data.gouv.fr (Merimee) |

### Transversal

| Endpoint | Description |
|----------|-------------|
| `GET /v1/resolve` | Entree polymorphe + enrichissement multi-dataset |
| `GET /v1/scoring/renov` | Passoires thermiques (DPE F/G) |
| `GET /v1/datasets` | Statut et metadonnees des datasets |
| `GET /v1/health` | Readiness probe |
| `GET /v1/health/live` | Liveness probe |

\* Proxy temps reel (pas de donnees stockees localement)

## Architecture

```
              Cloudflare Tunnel
              (cloudflared)
                    |
                    v
               Caddy :80
                    |
               api:8000
                    |
              PostgreSQL 17
              + PostGIS 3.5
```

**3 services Docker** : `db` (PostgreSQL + PostGIS), `api` (FastAPI + Uvicorn), `caddy` (reverse proxy) — plus `init-volumes` (init one-shot). Les jobs de maintenance sont declenches par les timers systemd de l'hote (`infra/systemd/`), pas par un conteneur ordonnanceur.

## ETL

21 pipelines d'ingestion, chacun en 5 etapes : download &rarr; validate &rarr; transform &rarr; stage (COPY) &rarr; swap (atomic rename).

```bash
# Tous les datasets
docker exec datacommune-api-1 python /app/run_etl.py

# Datasets specifiques
docker exec datacommune-api-1 python /app/run_etl.py ban dvf dpe
```

## Operations

### CI/CD

Les pushes et pull requests touchant `api/` declenchent le gate CI : Ruff puis
pytest. Un push sur `master` declenche en plus `cd-api.yml` sur le runner
self-hosted de l'ancien conteneur de production (LXC de l'hyperviseur) : build de l'image taguee
`sha-<court>`, migrations Alembic appliquees avant la bascule, puis health
check. Rollback via `rollback-api.yml` (`IMAGE_TAG=previous`).

Voir `docs/deploy-runbook.md` pour le detail de la procedure.

### Health endpoints

- `GET /v1/health/live` : liveness probe (200 si process HTTP vivant)
- `GET /v1/health` : readiness probe (503 si BDD indisponible ou datasets degrades)

### Backup PostgreSQL

La base nationale se reconstruit depuis la base de travail (sauvegarde
hebdomadaire par un script privé de l'hyperviseur). Les comptes, cles et reglages de
l'hote de production sont sauvegardes chaque jour, chiffres, et restaures a
l'identique par un script privé de l'hyperviseur (plan 05-01).

### Verification pre-deploiement

```bash
./scripts/preflight.sh
```

## Versions

| Version | Contenu | Statut |
|---------|---------|--------|
| v1.0 | 14 datasets, API REST, ETL framework, auth quotas | Shipped |
| v1.1 | ETL resilient, scheduling, structlog, alertes, CI/CD, HTTPS | Shipped |
| v1.2 | Scoring renovation, vue materialisee, CSV export | Shipped |
| v1.3 | Domaine public, landing, docs, auth JWT, Stripe, RGPD, exports | Shipped |
| v2.0 | Carte interactive MapLibre, tuiles vectorielles | Retiré (TABLE-FIRST) |
| v3.0 | Chat IA agentic (10 tools, SSE, OpenRouter) | Retiré du projet |
| v5.0 | 13 nouveaux datasets, CRON scheduling, scoring | Shipped |
| v5.1 | Consolidation API (3 tags OpenAPI, geocoding <1s, v_commune_synthese) | Shipped |

## Licence

Proprietary &mdash; All rights reserved.
