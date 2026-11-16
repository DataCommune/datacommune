"""Cadastre-specific configuration: URLs, expected properties, departements."""

import httpx

# -- Download settings --------------------------------------------------------

CADASTRE_BASE_URL = (
    "https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/geojson/departements"
)
# Quarterly dated folders stay online next to `latest`, which redirects to the
# newest one (checked on 2026-09-24 by `curl -I -L`: 2026-03-01 served, HTTP
# 200). Plan 02-11, D-27.
CADASTRE_DATED_URL_TEMPLATE = "https://cadastre.data.gouv.fr/data/etalab-cadastre/{millesime}/geojson/departements"


def cadastre_base_url(millesime: str | None) -> str:
    """Folder of the parcel files: `latest`, or the dated folder *millesime*."""
    if millesime is None:
        return CADASTRE_BASE_URL
    return CADASTRE_DATED_URL_TEMPLATE.format(millesime=millesime)


# Cadastre files are larger than BAN; read timeout 10 min
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=600.0)
MAX_RETRIES = 3

# -- GeoJSON schema -----------------------------------------------------------

# 9 expected properties in each GeoJSON Feature for parcelles.
EXPECTED_PROPERTIES: list[str] = [
    "id",
    "commune",
    "prefixe",
    "section",
    "numero",
    "contenance",
    "arpente",
    "created",
    "updated",
]
