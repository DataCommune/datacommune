# etl/ -- Framework ETL

## Architecture

`BasePipeline` (base.py) definit le cycle en 5 etapes :
1. **download()** -- telechargement streaming (httpx, retry tenacity, resume partiel)
2. **validate()** -- integrite fichier (checksum, taille min)
3. **transform()** -- nettoyage/normalisation pandas
4. **load()** -- COPY bulk via psycopg (sync, staging → swap)
5. **post_load()** -- indexes, VACUUM, hooks alerting

## Fichiers

| Fichier | Role |
|---------|------|
| `base.py` | `BasePipeline` -- classe abstraite, 102 departements, staging lifecycle |
| `download.py` | `StreamingDownloader` -- httpx streaming, retry tenacity, resume partiel |
| `hooks.py` | Post-ETL hooks (alerting Telegram via apprise) |
| `registry.py` | `PipelineRegistry` -- decouverte auto des pipelines via decorateur |
| `runner.py` | `PipelineRunner` -- orchestration, advisory locks PostgreSQL, error isolation |
| `schemas.py` | `PipelineConfig`, `PipelineResult` (pydantic) |

## Ajouter un pipeline

1. Creer `app/etl_{nom}/` avec `__init__.py`, `config.py`, `pipeline.py`
2. Heriter de `BasePipeline`, implementer `transform()`
3. Enregistrer via `@PipelineRegistry.register("nom_cli")`
4. Creer migration Alembic pour la table cible
5. Ajouter le router dans `app/{nom}/router.py` + import dans `main.py`
6. Ajouter le schedule dans `scheduler/config.py`
