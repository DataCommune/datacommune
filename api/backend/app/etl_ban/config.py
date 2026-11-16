"""BAN-specific configuration: URLs, expected columns, column mappings."""

import httpx

# -- Download settings --------------------------------------------------------

BAN_BASE_URL = "https://adresse.data.gouv.fr/data/ban/adresses/latest/csv"

# Dated weekly folders stay online next to `latest` (checked on 2026-09-24 by
# `curl -I -L`: 2026-06-17 and 2026-07-01 served, HTTP 200). Plan 02-11, D-27.
BAN_DATED_URL_TEMPLATE = (
    "https://adresse.data.gouv.fr/data/ban/adresses/{millesime}/csv"
)


def ban_base_url(millesime: str | None) -> str:
    """Folder of the BAN files: `latest`, or the dated folder *millesime*."""
    if millesime is None:
        return BAN_BASE_URL
    return BAN_DATED_URL_TEMPLATE.format(millesime=millesime)


DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=300.0)

# -- CSV format ---------------------------------------------------------------

BAN_CSV_SEPARATOR = ";"

# 23 expected columns in the BAN CSV header (lowercase).
# cad_parcelles may be absent in older BAN releases -- treated as optional.
EXPECTED_BAN_COLUMNS: list[str] = [
    "id",
    "id_fantoir",
    "numero",
    "rep",
    "nom_voie",
    "code_postal",
    "code_insee",
    "nom_commune",
    "code_insee_ancienne_commune",
    "nom_ancienne_commune",
    "x",
    "y",
    "lon",
    "lat",
    "type_position",
    "alias",
    "nom_ld",
    "libelle_acheminement",
    "nom_afnor",
    "source_position",
    "source_nom_voie",
    "certification_commune",
    "cad_parcelles",
]

OPTIONAL_BAN_COLUMNS: set[str] = {"cad_parcelles"}

# -- Column mapping (BAN CSV -> table adresses) -------------------------------

BAN_COLUMN_MAPPING: dict[str, str] = {
    "id": "ban_id",
    "numero": "numero",
    "rep": "rep",
    "nom_voie": "nom_voie",
    "code_postal": "code_postal",
    "code_insee": "code_commune",
    "nom_commune": "nom_commune",
    "lon": "lon",
    "lat": "lat",
}

# Columns written to the transformed CSV (order matters for COPY)
BAN_OUTPUT_COLUMNS: list[str] = [
    "code_departement",
    "ban_id",
    "numero",
    "rep",
    "nom_voie",
    "code_postal",
    "code_commune",
    "nom_commune",
    "lon",
    "lat",
]
