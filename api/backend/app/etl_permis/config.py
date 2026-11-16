"""Quarantined local permits resource; no national coverage contract."""

import httpx

DOWNLOAD_URL = (
    "https://www.data.gouv.fr/api/1/datasets/r/314a3610-53fe-4594-ae8d-a0789f6fa67b"
)
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=300.0)
MAX_RETRIES = 3
CSV_SEPARATOR = ","
SOURCE_COLUMNS: list[str] = [
    "nom_dossier",
    "type_dossier",
    "date_commencement",
    "date_decision",
    "n_commune",
    "nom_commune",
    "departement",
    "nature_construction",
    "nb_logements",
    "sdp_creee",
    "designation",
    "annee",
]
OUTPUT_COLUMNS: list[str] = [
    "numero_dossier",
    "type_autorisation",
    "date_depot",
    "date_commencement",
    "source_sha256",
    "date_decision",
    "code_commune",
    "nom_commune",
    "code_departement",
    "nature_projet",
    "nb_logements_crees",
    "surface_plancher",
    "type_construction",
    "destination",
    "annee",
]
