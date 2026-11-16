"""BPE equipements configuration: URL, column mappings.

Data source: INSEE --- Base Permanente des Equipements 2025.
"""

import httpx

DOWNLOAD_URL = "https://www.insee.fr/fr/statistiques/fichier/8217525/BPE25.zip"
# vintage of the file named by DOWNLOAD_URL; bump both together
# (test_equipements_source)
SOURCE_VINTAGE = "2025"

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=600.0)
MAX_RETRIES = 3

CSV_SEPARATOR = ";"

SOURCE_COLUMNS: list[str] = [
    "TYPEQU",
    "DEPCOM",
]

# Columns transform() consumes. BPE25.csv ships 96 columns (1.5 GB); reading
# only these keeps the pandas frame bounded. Absent ones stay absent and the
# df.get() fallbacks of transform() apply unchanged.
READ_COLUMNS: frozenset[str] = frozenset(
    {
        "TYPEQU",
        "DEPCOM",
        "DEP",
        "SDOM",
        "LAMBERT_X",
        "LAMBERT_Y",
        "LONGITUDE",
        "LATITUDE",
        "EPSG",
        "DCIRIS",
        "NB_EQUIP",
    }
)

OUTPUT_COLUMNS: list[str] = [
    "type_equipement",
    "code_commune",
    "code_departement",
    "code_iris",
    "domaine",
    "sous_domaine",
    "nb_equipements",
    "latitude",
    "longitude",
    "annee",
]

# BPE domain mapping from SDOM codes
DOMAINE_MAP: dict[str, str] = {
    "A": "services_particuliers",
    "B": "commerces",
    "C": "enseignement",
    "D": "sante_social",
    "E": "transports",
    "F": "sports_loisirs_culture",
    "G": "tourisme",
}
