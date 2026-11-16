"""ANFR antennes configuration: URL, column mappings.

Data source: ANFR — Observatoire 2G 3G 4G 5G.
"""

import httpx

DOWNLOAD_URL = (
    "https://data.anfr.fr/d4c/api/records/2.0/"
    "downloadfile?format=csv&resource_id="
    "88ef0887-6b0f-4d3f-8545-6d64c8f597da"
)

# Fallback: pre-staged CSV when ANFR blocks server IPs (OVH range).
# Upload fresh CSV here via SCP before running ETL if direct download
# returns 403.
LOCAL_FALLBACK_PATH = "/opt/datacommune/data/anfr_antennes.csv"

# Headers to mimic browser requests (ANFR blocks bare httpx UA)
DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=600.0)
MAX_RETRIES = 3

CSV_SEPARATOR = ";"

SOURCE_COLUMNS: list[str] = [
    "adm_lb_nom",
]

OUTPUT_COLUMNS: list[str] = [
    "operateur",
    "generation",
    "bande_frequence",
    "numero_station",
    "numero_support",
    "code_insee",
    "adresse",
    "latitude",
    "longitude",
    "date_mise_en_service",
    "statut",
]
