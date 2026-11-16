# backend/app/ -- Carte des Modules

## Infrastructure

| Fichier | Role |
|---------|------|
| `main.py` | Factory FastAPI, lifespan, montage des 17 routers |
| `config.py` | pydantic-settings (`Settings`), env vars |
| `database.py` | AsyncSession factory (asyncpg + SQLAlchemy) |
| `logging_config.py` | structlog config (JSON prod, colored dev) |
| `middleware.py` | CORS + RequestLoggingMiddleware |
| `rate_limit.py` | RateLimitMiddleware (sliding window par IP/cle API) |

## Routers API

### Datasets locaux (ETL)

| Module | Route principale | Table(s) PostgreSQL |
|--------|-----------------|---------------------|
| `ban/` | `/v1/geocode`, `/v1/reverse` | `adresses` |
| `adresse/` | `/v1/adresses/recherche`, `/v1/adresse/{ban_id}`, `/v1/adresse/{ban_id}/dpe` | `adresses`, `parcelles`, `geo_communes`, `geo_departements`, `diagnostics_dpe`, `mutations_grain`, `mutation_parcelles`, `copropriete`, `v_commune_synthese`, `dataset_metadata` ; Géorisques (amont) |
| `cadastre/` | `/v1/parcelle` | `parcelles` |
| `dvf/` | `/v1/dvf` | `mutations` |
| `dpe/` | `/v1/dpe` | `diagnostics_dpe` |
| `loyers/` | `/v1/loyers` | `indicateurs_loyers` |
| `batiments/` | `/v1/batiments` | `batiments` |
| `connectivite/` | `/v1/connectivite` | `eligibilite_fixe` |
| `monuments/` | `/v1/monuments` | `monuments_historiques` |
| `scolaire/` | `/v1/scolaire` | `carte_scolaire` |
| `transport/` | `/v1/transports` | `arrets_transport`, `gares` |

### Proxies externes (zero stockage)

| Module | Route | API source |
|--------|-------|------------|
| `georisques/` | `/v1/risques`, `/v1/pollutions` | georisques.gouv.fr |
| `urbanisme/` | `/v1/urbanisme` | apicarto.ign.fr |
| `sirene/` | `/v1/entreprises` | recherche-entreprises.api.gouv.fr |

### Fonctionnalites transverses

| Module | Role |
|--------|------|
| `resolve/` | Entree polymorphe `/v1/resolve` (detector.py, multi.py) |
| `scoring/` | Scoring renovation `/v1/scoring/renov` + CSV export |
| `health/` | `/v1/health` endpoint |
| `datasets/` | `/v1/datasets` (metadata fraicheur par dataset) |

## Utilitaires

| Module | Role |
|--------|------|
| `auth/` | Cles API, quotas mensuels, CLI creation cle |
| `common/` | Exceptions RFC 9457, pagination cursor, schemas partages, sources |
| `scheduler/` | Jobs, locks, alerting et execution unitaire `run_job` (timers systemd) ; ordonnanceur a choisir en phase 2 ou 5 |

## ETL

| Module | Nom CLI | Classe |
|--------|---------|--------|
| `etl_ban/` | `ban` | BanPipeline |
| `etl_cadastre/` | `cadastre` | CadastrePipeline |
| `etl_dvf/` | `dvf` | DvfPipeline |
| `etl_dpe/` | `dpe` | DpePipeline |
| `etl_loyers/` | `loyers` | LoyersPipeline |
| `etl_bdtopo/` | `batiments` | BdTopoPipeline |
| `etl_arcep/` | `connectivite` | ArcepPipeline |
| `etl_monuments/` | `monuments` | MonumentsPipeline |
| `etl_scolaire/` | `scolaire` | ScolairePipeline |
| `etl_transport/` | `transports` | TransportPipeline |

Chaque pipeline : `__init__.py` + `config.py` + `pipeline.py` (+ `transform.py` optionnel).
Framework ETL : voir [`etl/INDEX.md`](etl/INDEX.md).

## Tests

`tests/` -- 5 fichiers, E2E smoke tests prioritaires sur unit tests.
