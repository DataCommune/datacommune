"""DVF-specific configuration: URLs, expected columns, column mappings."""

from datetime import date

import httpx

# -- Download settings --------------------------------------------------------

BASE_URL = "https://files.data.gouv.fr/geo-dvf/latest/csv/"
# Dated release folder next to `latest` (index files.data.gouv.fr/geo-dvf/ on
# 2026-09-24: `2025-12` and `latest`). Plan 02-11, D-27. Choosing a release
# never widens the years read (YEARS): no pre-2021 history (DVF: open-data).
DATED_URL_TEMPLATE = "https://files.data.gouv.fr/geo-dvf/{millesime}/csv/"

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=300.0)
MAX_RETRIES = 3

# Years to ingest, from 2020 up to and including the current year (P38
# DATA-03): a fixed upper bound silently drops the current year once it
# rolls over (live bug found 2026-07-04, YEARS excluded 2026). The current
# year's file may 404 if data.gouv hasn't published it yet — that's handled
# gracefully by the existing per-(year, dept) 404 tolerance below
# (_not_found_count / _MAX_NOT_FOUND_RATIO in pipeline.py), not here.
YEARS: list[int] = list(range(2020, date.today().year + 1))

# -- CSV format ---------------------------------------------------------------

# ~39 columns in the geo-DVF source CSV (subset used for validation).
EXPECTED_COLUMNS: list[str] = [
    "id_mutation",
    "date_mutation",
    "nature_mutation",
    "valeur_fonciere",
    "adresse_numero",
    "adresse_nom_voie",
    "code_postal",
    "code_commune",
    "nom_commune",
    "id_parcelle",
    "code_type_local",
    "type_local",
    "surface_reelle_bati",
    "nombre_pieces_principales",
    "surface_terrain",
    "code_nature_culture",
    "nature_culture",
    "code_nature_culture_speciale",
    "nature_culture_speciale",
    "longitude",
    "latitude",
]

# D-02/DVF-02: 12 source columns that were silently dropped by the pipeline
# (disposition + lot/Carrez metadata). Without these in `mutations`, the
# qualification step (D-05) cannot audit a disposition or expose a lot
# perimeter, and the only substitute would be a COUNT(*) of staging rows —
# exactly what DVF-02 forbids. These 12 columns are appended in the same
# order to both COLUMNS_TO_KEEP and OUTPUT_COLUMNS below (mirrored lists).
_D02_LOT_DISPOSITION_COLUMNS: list[str] = [
    "numero_disposition",
    "nombre_lots",
    "lot1_numero",
    "lot1_surface_carrez",
    "lot2_numero",
    "lot2_surface_carrez",
    "lot3_numero",
    "lot3_surface_carrez",
    "lot4_numero",
    "lot4_surface_carrez",
    "lot5_numero",
    "lot5_surface_carrez",
]

# C44 (plan 04.1-14): `adresse_suffixe` (« 2 B ») is kept next to the
# number in both mirrored lists below; `adresse_code_voie` is not (the
# imported BAN has no street code to compare it with, measured on
# 2026-10-19, mesures/dvf-suffixe/correspondance.json).
#
# Comment left on `mutations.adresse_suffixe` by the DVF import, then on
# `mutations_grain.adresse_suffixe` by a reconstruction of such a source.
# A migrated base that was not reimported has the column without it: the
# suffix is then unknown (a missing value), never « no suffix »
# (app.adresse.ventes).
MARQUEUR_ADRESSE_SUFFIXE = "dc:dvf-adresse-suffixe:v1"

# Columns we keep from the source CSV (mapped 1:1 to table columns)
COLUMNS_TO_KEEP: list[str] = [
    "id_mutation",
    "date_mutation",
    "nature_mutation",
    "valeur_fonciere",
    "adresse_numero",
    "adresse_suffixe",
    "adresse_nom_voie",
    "code_postal",
    "code_commune",
    "nom_commune",
    "id_parcelle",
    "code_type_local",
    "type_local",
    "surface_reelle_bati",
    "nombre_pieces_principales",
    "surface_terrain",
    "code_nature_culture",
    "nature_culture",
    "code_nature_culture_speciale",
    "nature_culture_speciale",
    "longitude",
    "latitude",
    *_D02_LOT_DISPOSITION_COLUMNS,
]

# Output columns written to the transformed CSV (order matters for COPY)
# NOTE: EXPECTED_COLUMNS (above) is intentionally NOT extended with the D-02
# columns. It is the schema-validation subset only, and validate_schema()
# already tolerates extra source columns — the D-02 columns are additions
# to what we KEEP, not a new validation requirement.
OUTPUT_COLUMNS: list[str] = [
    "code_departement",
    "id_mutation",
    "date_mutation",
    "nature_mutation",
    "valeur_fonciere",
    "adresse_numero",
    "adresse_suffixe",
    "adresse_nom_voie",
    "code_postal",
    "code_commune",
    "nom_commune",
    "id_parcelle",
    "code_type_local",
    "type_local",
    "surface_reelle_bati",
    "nombre_pieces_principales",
    "surface_terrain",
    "code_nature_culture",
    "nature_culture",
    "code_nature_culture_speciale",
    "nature_culture_speciale",
    "longitude",
    "latitude",
    *_D02_LOT_DISPOSITION_COLUMNS,
]


def get_csv_url(year: int, dept: str, millesime: str | None = None) -> str:
    """Build the download URL for a specific year and department.

    Example: https://files.data.gouv.fr/geo-dvf/latest/csv/2023/departements/75.csv.gz
    With *millesime* (``AAAA-MM``), the dated release folder instead of latest.
    """
    if millesime is None:
        base = BASE_URL
    else:
        base = DATED_URL_TEMPLATE.format(millesime=millesime)
    return f"{base}{year}/departements/{dept}.csv.gz"
