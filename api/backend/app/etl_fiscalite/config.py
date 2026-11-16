"""Fiscalite locale configuration: URL, column mappings.

Data source: DGFiP — Fiscalite locale des particuliers.
Cleaner dataset than raw REI, with pre-aggregated global tax rates.
"""

import httpx

# -- Download settings --------------------------------------------------------

# CSV direct download from data.gouv.fr
DOWNLOAD_URL = (
    "https://www.data.gouv.fr/api/1/datasets/r/f48d0fcc-f732-445d-ba2d-886ec4952bce"
)

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=120.0)
MAX_RETRIES = 3

# -- CSV format ---------------------------------------------------------------

CSV_SEPARATOR = ";"

# Columns we validate exist in the source CSV
SOURCE_COLUMNS: list[str] = [
    "EXERCICE",
    "DEP",
    "LIBDEP",
    "LIBREG",
    "LIBCOM",
    "Q03",
    "INSEE COM",
    "Taux_Global_TFB",
    "Taux_Global_TFNB",
    "Taux_Global_TH",
    "Taux_Plein_TEOM",
    "IND_MAJOTHRS",
    "THSURTAXRSTAU",
]

# Columns written to the transformed CSV (order matters for COPY)
OUTPUT_COLUMNS: list[str] = [
    "code_insee",
    "nom_commune",
    "code_departement",
    "nom_departement",
    "nom_region",
    "nom_epci",
    "taux_tfpb",
    "taux_tfpnb",
    "taux_th_rs",
    "taux_teom",
    "base_tfpb",
    "produit_tfpb",
    "annee",
    # Plan 04.7-08: second-home (THRS) surtax. Added at the end -- the order
    # is positional with the flat staging table + COPY (pipeline.py).
    "majoration_thrs",
    "taux_majoration_thrs",
]
