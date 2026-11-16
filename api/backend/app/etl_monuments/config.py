"""Monuments Historiques specific configuration: URL, columns, mapping."""

# -- Download settings --------------------------------------------------------

# Merimee base published by the Ministere de la Culture on data.gouv.fr
# ("Immeubles proteges au titre des Monuments Historiques", Licence Ouverte
# 2.0), refreshed weekly. "|"-separated; column names are published in mixed
# case and compared lower-cased to EXPECTED_COLUMNS.
MONUMENTS_CSV_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv"
CSV_SEPARATOR = "|"

# -- Schema settings ----------------------------------------------------------

# Minimum required columns in the source CSV, canonical lower-case names
# (the producer's header is lower-cased before the comparison)
EXPECTED_COLUMNS: list[str] = [
    "reference",
    "titre_editorial_de_la_notice",
    "adresse_forme_editoriale",
    "commune_forme_editoriale",
    "cog_insee_lors_de_la_protection",
    "departement_format_numerique",
    "nature_de_la_protection",
    "date_et_typologie_de_la_protection",
    "coordonnees_au_format_wgs84",
]

# Source -> destination column mapping
COLUMN_MAPPING: dict[str, str] = {
    "reference": "reference",
    "titre_editorial_de_la_notice": "denomination",
    "adresse_forme_editoriale": "adresse",
    "commune_forme_editoriale": "commune",
    "cog_insee_lors_de_la_protection": "code_insee",
    "departement_format_numerique": "code_departement",
    "nature_de_la_protection": "protection",
    "date_et_typologie_de_la_protection": "date_protection",
}

# Output columns for the transformed CSV (used by COPY)
OUTPUT_COLUMNS: list[str] = [
    "reference",
    "denomination",
    "adresse",
    "commune",
    "code_insee",
    "code_departement",
    "protection",
    "date_protection",
    "latitude",
    "longitude",
]
