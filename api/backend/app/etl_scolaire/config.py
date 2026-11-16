"""Carte Scolaire specific configuration: URL, expected columns, output columns."""

# -- Download settings --------------------------------------------------------

# CSV resource from data.gouv.fr (Carte Scolaire des Colleges Publics)
CARTE_SCOLAIRE_CSV_URL = (
    "https://www.data.gouv.fr/api/1/datasets/r/36ace01a-2d9c-4001-9473-39e6fc7a0ad8"
)

# -- Schema settings ----------------------------------------------------------

# Expected columns in the source CSV
EXPECTED_COLUMNS: list[str] = [
    "code_region",
    "code_departement",
    "code_postal",
    "code_insee",
    "libelle_commune",
    "lieu_dit",
    "type_et_libelle",
    "No_de_voie_debut",
    "No_de_voie_fin",
    "parite",
    "code_rne",
    "secteur_unique",
]

# Source -> destination column mapping (for columns that changed names)
COLUMN_MAPPING: dict[str, str] = {
    "No_de_voie_debut": "n_de_voie_debut",
    "No_de_voie_fin": "n_de_voie_fin",
}

# Columns written to the transformed CSV (order matters for COPY)
OUTPUT_COLUMNS: list[str] = [
    "code_region",
    "code_departement",
    "code_postal",
    "code_insee",
    "libelle_commune",
    "lieu_dit",
    "type_et_libelle",
    "n_de_voie_debut",
    "n_de_voie_fin",
    "parite",
    "code_rne",
    "secteur_unique",
]
