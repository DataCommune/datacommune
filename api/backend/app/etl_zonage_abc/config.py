"""Zonage ABC configuration: URL, expected columns, column mappings.

Data source: Ministere de la Transition Ecologique.
Classement des communes en zones A bis, A, B1, B2, C.
"""

import httpx

# -- Download settings --------------------------------------------------------

# Direct download URL for the full commune list CSV
DOWNLOAD_URL = (
    "https://www.data.gouv.fr/api/1/datasets/r/13f7282b-8a25-43ab-9713-8bb4e476df55"
)

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=120.0)
MAX_RETRIES = 3

# -- CSV format ---------------------------------------------------------------

# Separator is semicolon
CSV_SEPARATOR = ";"

# Expected columns in the source CSV (exact header names)
EXPECTED_COLUMNS: list[str] = [
    "CODGEO",
    "DEP",
    "LIBGEO",
]
# Note: the zone column name changes with each decree date, so we match
# by position (4th column) rather than exact name.

# -- Column mapping (source CSV -> table column name) -------------------------

COLUMN_MAPPING: dict[str, str] = {
    "CODGEO": "code_insee",
    "DEP": "code_departement",
    "LIBGEO": "nom_commune",
}

# Columns written to the transformed CSV (order matters for COPY)
OUTPUT_COLUMNS: list[str] = [
    "code_insee",
    "nom_commune",
    "code_departement",
    "zone",
    "reclassement",
]

# -- Decree date --------------------------------------------------------------

# vintage pinned to source resource (ministerial decree date + fixed
# data.gouv.fr resource UUID, published irregularly); not auto-extensible
# like DVF — bump requires manually finding the next published resource +
# verifying schema (P38 DATA-03 audit)
DATE_ARRETE = "2025-09-05"
