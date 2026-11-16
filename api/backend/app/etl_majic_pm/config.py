"""MAJIC PM configuration: URLs, expected columns, download settings."""

import httpx

# -- Download settings --------------------------------------------------------

# data.gouv.fr API endpoint to resolve ZIP resource URLs at runtime
DATASET_API_URL = (
    "https://www.data.gouv.fr/api/1/datasets/"
    "fichiers-des-locaux-et-des-parcelles-des-personnes-morales/"
)

# Fallback static URLs for 2025 parcelles ZIP files (data.economie.gouv.fr)
PARCELLES_ZIP_URLS_FALLBACK: list[str] = [
    (
        "https://data.economie.gouv.fr/api/v2/catalog/datasets/"
        "fichiers-des-locaux-et-des-parcelles-des-personnes-morales/"
        "attachments/fichier_des_parcelles_situation_2025_dpts_01_a_56_zip"
    ),
    (
        "https://data.economie.gouv.fr/api/v2/catalog/datasets/"
        "fichiers-des-locaux-et-des-parcelles-des-personnes-morales/"
        "attachments/fichier_des_parcelles_situation_2025_dpts_57_a_976_zip"
    ),
]

# ZIP files are ~250 MB each, needs generous read timeout
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=600.0)
MAX_RETRIES = 3

# -- CSV source columns -------------------------------------------------------

# Column names in the source CSVs (semicolon-delimited, UTF-8)
# Note: there are TWO "Contenance" columns (index 13 = parcelle, index 16 = SUF)
SOURCE_COLUMNS: list[str] = [
    "Département",
    "Code Direction",
    "Code Commune",
    "Nom de la commune",
    "Préfixe",
    "Section",
    "N° plan",
    "N° voirie",
    "Indice de répétition",
    "Code voie MAJIC",
    "Code voie Rivoli",
    "Nature voie",
    "Nom voie",
    "Contenance",  # index 13 — parcelle-level contenance
    "SUF",
    "Nature culture",
    "Contenance",  # index 16 — SUF-level contenance (ignored)
    "Code droit - par",
    "N° Majic - par",
    "N° SIREN - par",
    "Groupe personne - par",
    "Forme juridique - par",
    "Forme juridique abrégée - par",
    "Dénomination - par",
]

# Minimum required source columns (by unique names)
REQUIRED_SOURCE_COLUMNS: set[str] = {
    "Département",
    "Code Commune",
    "Nom de la commune",
    "Section",
    "N° plan",
    "Code droit - par",
    "N° SIREN - par",
    "Dénomination - par",
}

# -- Output schema ------------------------------------------------------------

# Expected columns in the pipeline's internal schema (after transform)
EXPECTED_COLUMNS: list[str] = [
    "code_departement",
    "code_commune",
    "nom_commune",
    "adresse",
    "code_parcelle",
    "contenance_parcelle",
    "nature_culture",
    "code_droit",
    "numero_siren",
    "groupe_personne",
    "forme_juridique_abregee",
    "denomination",
]

# Output columns written to the transformed CSV (order matters for COPY)
OUTPUT_COLUMNS: list[str] = list(EXPECTED_COLUMNS)
