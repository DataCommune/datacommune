"""Filosofi revenus configuration: URL, column mappings.

Data source: INSEE — Filosofi, revenus medians et indicateurs
de pauvrete par commune.
"""

import httpx

# -- Download settings --------------------------------------------------------

# ZIP from INSEE containing the commune-level CSV
DOWNLOAD_URL = (
    "https://www.insee.fr/fr/statistiques/fichier/"
    "7756855/indic-struct-distrib-revenu-2021-COMMUNES_csv.zip"
)

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=300.0)
MAX_RETRIES = 3

# -- CSV format ---------------------------------------------------------------

CSV_SEPARATOR = ";"

# CSV files we read from the ZIP.
# DEC_COM (declared income) carries the median, Gini and the share of
# taxed households. The poverty rate (TP6021) is *not* in DEC_COM — it
# is read from DISP_PAUVRES (after redistribution), joined on CODGEO.
MAIN_CSV_NAME = "FILO2021_DEC_COM.csv"
POVERTY_CSV_NAME = "FILO2021_DISP_PAUVRES_COM.csv"

# Columns we validate exist in the main source CSV (FILO2021_DEC_COM).
# CODGEO + numeric suffixed columns. No LIBGEO in this file.
SOURCE_COLUMNS: list[str] = [
    "CODGEO",
    "Q221",
    "PMIMP21",
]

# Columns written to the transformed CSV (order for COPY)
OUTPUT_COLUMNS: list[str] = [
    "code_insee",
    "nom_commune",
    "code_departement",
    "nom_departement",
    "nom_region",
    "nb_menages_fiscaux",
    "revenu_median",
    "revenu_q1",
    "revenu_q3",
    "revenu_d1",
    "revenu_d9",
    "indice_gini",
    "taux_pauvrete",
    "part_imposee",
    "annee",
]
