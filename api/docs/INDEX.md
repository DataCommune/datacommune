# docs/ -- Documentation

## Fichiers

| Fichier | Contenu | Audience |
|---------|---------|----------|
| `getting-started.md` | Guide demarrage rapide (clone, .env, docker compose, migrations) | Nouveaux devs |
| `api-deprecation-policy.md` | Politique de depreciation /v1 (RFC 8594) + contrat d'erreur stable | Devs API + clients |
| `dpe-inventory-v2.md` | Contrat de l'inventaire DPE V2, références temporaires et migration | Devs API + clients |
| `go-live-checklist.md` | Checklist mise en prod publique | Ops interne |
| `etl-runbook.md` | Procedure ETL (lancement, monitoring, troubleshooting) | Ops interne |

## Ou trouver quoi

| Besoin | Ou chercher |
|--------|-------------|
| Endpoints API | Swagger `/docs` en dev, ou `/v1/datasets` en prod |
| ETL pipelines | `backend/app/etl/` + `etl-runbook.md` |
| Planning projet | suivi interne (hors dépôt public) |
| Historique milestones | suivi interne (hors dépôt public) |
| Architecture & stack | `api/README.md` |
