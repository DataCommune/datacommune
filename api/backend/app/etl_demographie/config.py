"""Recensement demographie configuration: URLs, column mappings.

Three INSEE sources combined at transform time:
  1. Populations legales — authoritative PMUN (code INSEE + name + dept + region).
  2. Base Logement 2022 — households + dwellings + RP/vacant/secondary counts.
  3. Base Evolution-Structure Population 2022 — sex split (hommes/femmes).

Merged on 5-char commune code (COM for pop legales, CODGEO for the two others).
"""

import httpx

# -- Download settings --------------------------------------------------------

# Populations legales (ensemble.zip) — existing source, kept for PMUN + name/dept/region
DOWNLOAD_URL_POP_LEGALES = (
    "https://www.insee.fr/fr/statistiques/fichier/8290591/ensemble.zip"
)

# Base Logement 2022 — household + dwelling counts
DOWNLOAD_URL_LOGEMENT = (
    "https://www.insee.fr/fr/statistiques/fichier/8581474/base-cc-logement-2022_csv.zip"
)

# Base Evolution-Structure Population 2022 — sex split
DOWNLOAD_URL_POP_STRUCT = (
    "https://www.insee.fr/fr/statistiques/fichier/8581696/"
    "base-cc-evol-struct-pop-2022_csv.zip"
)

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=300.0)
MAX_RETRIES = 3

# -- CSV format ---------------------------------------------------------------

CSV_SEPARATOR = ";"

# Columns validated in each source CSV header
SOURCE_COLUMNS_POP_LEGALES: list[str] = [
    "COM",
    "Commune",
    "PMUN",
]

SOURCE_COLUMNS_LOGEMENT: list[str] = [
    "CODGEO",
    "P22_MEN",
    "P22_LOG",
    "P22_RP",
    "P22_LOGVAC",
    "P22_RSECOCC",
]

SOURCE_COLUMNS_POP_STRUCT: list[str] = [
    "CODGEO",
    "P22_POPH",
    "P22_POPF",
]

# Columns written to the transformed CSV (order for COPY)
OUTPUT_COLUMNS: list[str] = [
    "code_insee",
    "nom_commune",
    "code_departement",
    "code_region",
    "population",
    "population_hommes",
    "population_femmes",
    "nb_menages",
    "nb_logements",
    "nb_residences_principales",
    "nb_logements_vacants",
    "nb_residences_secondaires",
    "superficie",
    "densite",
    "annee",
]
