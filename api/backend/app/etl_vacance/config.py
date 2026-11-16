"""LOVAC vacance configuration: URL, column mappings.

Data source: CEREMA — LOVAC logements vacants du parc prive,
donnees ouvertes par commune.
"""

import httpx

DOWNLOAD_URL = (
    "https://www.data.gouv.fr/api/1/datasets/r/2e0417b4-902d-4c60-90e7-bf5df148cb87"
)

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=120.0)
MAX_RETRIES = 3

CSV_SEPARATOR = ";"

SOURCE_COLUMNS: list[str] = [
    "CODGEO_25",
]

OUTPUT_COLUMNS: list[str] = [
    "code_commune",
    "nom_commune",
    "code_departement",
    "nom_departement",
    "nom_region",
    "nb_logements_prives",
    "nb_logements_vacants",
    "nb_vacants_moins_2ans",
    "nb_vacants_plus_2ans",
    "taux_vacance",
    "taux_vacance_moins_2ans",
    "taux_vacance_plus_2ans",
    "annee",
]
