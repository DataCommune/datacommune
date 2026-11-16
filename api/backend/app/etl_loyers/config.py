"""Carte des Loyers configuration: URLs, expected columns, column mappings.

Data source: Estimations ANIL, a partir des donnees du Groupe SeLoger
et de leboncoin. Published by Ministere de la Transition Ecologique.
"""

import httpx

# -- Download settings --------------------------------------------------------

# data.gouv.fr resource base URL
BASE_URL = "https://www.data.gouv.fr/fr/datasets/r/"

# Resource UUIDs mapping housing type to CSV download IDs.
# Dataset: https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025
# Each CSV contains rental price indicators for one housing type.
CSV_RESOURCES: dict[str, str] = {
    "appartement": "55b34088-0964-415f-9df7-d87dd98a09be",
    "maison": "129f764d-b613-44e4-952c-5ff50a8c9b73",
    "1-2_pieces": "14a1fe11-b2d1-49b3-9f6b-83d12df9482c",
    "3+_pieces": "5e3b28a4-cf56-43a3-ae79-43cceeb27f8c",
}

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=120.0)
MAX_RETRIES = 3

# -- CSV format ---------------------------------------------------------------

# Expected columns in the source CSVs (exact header names).
# Separator may be comma or semicolon -- detected at validation time.
EXPECTED_COLUMNS: list[str] = [
    "INSEE_C",
    "LIBGEO",
    "EPCI",
    "DEP",
    "REG",
    "loypredm2",
    "lwr.IPm2",
    "upr.IPm2",
    "id_zone",
    "TYPPRED",
    "nbobs_com",
    "nbobs_mail",
    "R2_adj",
]

# -- Column mapping (source CSV -> table column name) -------------------------

COLUMN_MAPPING: dict[str, str] = {
    "INSEE_C": "code_insee",
    "LIBGEO": "nom_commune",
    "DEP": "code_departement",
    "EPCI": "code_epci",
    "REG": "code_region",
    "loypredm2": "loyer_m2",
    "lwr.IPm2": "loyer_m2_bas",
    "upr.IPm2": "loyer_m2_haut",
    "TYPPRED": "type_prediction",
    "nbobs_com": "nb_observations_commune",
    "nbobs_mail": "nb_observations_maille",
    "R2_adj": "r2_ajuste",
}

# Columns written to the transformed CSV (order matters for COPY)
OUTPUT_COLUMNS: list[str] = [
    "code_insee",
    "nom_commune",
    "code_departement",
    "code_epci",
    "code_region",
    "type_habitat",
    "loyer_m2",
    "loyer_m2_bas",
    "loyer_m2_haut",
    "r2",
    "nb_observations",
    "annee",
    "id_zone",
    "type_prediction",
    "nb_observations_commune",
    "nb_observations_maille",
    "r2_ajuste",
    "trimestre_reference",
    "charges_comprises",
    "meuble",
    "surface_reference_m2",
    "surface_piece_reference_m2",
]

# -- Vintage ------------------------------------------------------------------

# vintage pinned to source resource (no per-year URL formula, data.gouv.fr
# slug/name reference only); not auto-extensible like DVF — bump requires
# manually finding the next published resource + verifying schema (P38
# DATA-03 audit)
ANNEE = 2025

# The December 2025 official guide defines these unfurnished reference goods.
# A new vintage requires an explicitly reviewed reference contract.
REFERENCE_SURFACES = {
    "appartement": (52, 22.2),
    "maison": (92, 22.4),
    "1-2_pieces": (37, 22.9),
    "3+_pieces": (72, 21.3),
}
