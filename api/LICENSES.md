# Licences des données

La licence, l'attribution imposée et les restrictions de chaque jeu de données ont une seule source : le catalogue `backend/app/common/source_catalog.py` (`DATASET_CATALOG`). L'API le sert sur `GET /v1/datasets`, champ `reutilisation` : licence, lien de la licence, mention imposée, restrictions, date de vérification. Le site l'affiche sur `/datasets`.

Ce fichier ne recopie plus de tableau. Sa version du 2026-02-15 contredisait le catalogue sur la carte des loyers, le cadastre et DVF (KI-17 de `docs/KNOWN-ISSUES.md`).

Licence du code : AGPL-3.0-or-later, texte dans [`../LICENSE`](../LICENSE).
