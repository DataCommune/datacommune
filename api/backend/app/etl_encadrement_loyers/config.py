"""Encadrement des loyers configuration: URL, column mappings.

Data source: DRIHL — loyers de reference, majores, minores par quartier.
Currently covers Paris only; other cities to be added later.
"""

import httpx

# -- Download settings --------------------------------------------------------

DOWNLOAD_URL = (
    "https://www.data.gouv.fr/api/1/datasets/r/d72d537a-2f7f-40af-bf1a-289ee25d5ae9"
)

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=300.0)
MAX_RETRIES = 3

# -- CSV format ---------------------------------------------------------------

CSV_SEPARATOR = ";"

EXPECTED_COLUMNS: list[str] = [
    "Année",
    "Secteurs géographiques",
    "Nom du quartier",
    "Nombre de pièces principales",
    "Epoque de construction",
    "Type de location",
    "Loyers de référence",
    "Loyers de référence majorés",
    "Loyers de référence minorés",
    "Ville",
]

COLUMN_MAPPING: dict[str, str] = {
    "Année": "annee",
    "Secteurs géographiques": "secteur",
    "Numéro du quartier": "numero_quartier",
    "Nom du quartier": "nom_quartier",
    "Nombre de pièces principales": "nb_pieces",
    "Epoque de construction": "epoque_construction",
    "Type de location": "type_location",
    "Loyers de référence": "loyer_reference",
    "Loyers de référence majorés": "loyer_reference_majore",
    "Loyers de référence minorés": "loyer_reference_minore",
    "Ville": "ville",
    "Numéro INSEE du quartier": "code_insee_quartier",
}

# Columns written to the transformed CSV (order for COPY)
OUTPUT_COLUMNS: list[str] = [
    "annee",
    "secteur",
    "numero_quartier",
    "nom_quartier",
    "nb_pieces",
    "epoque_construction",
    "type_location",
    "loyer_reference",
    "loyer_reference_majore",
    "loyer_reference_minore",
    "ville",
    "code_insee_quartier",
    "latitude",
    "longitude",
]
