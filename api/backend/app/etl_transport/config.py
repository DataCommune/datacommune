"""Transport ETL specific configuration: URLs, expected columns."""

# -- Download settings --------------------------------------------------------

# Consolidated GTFS stops CSV from transport.data.gouv.fr
# Dataset: https://transport.data.gouv.fr/datasets/arrets-de-transport-en-france
# vintage pinned to source resource (Hydra-style timestamped resource path,
# not derivable by formula); not auto-extensible like DVF — bump requires
# manually finding the next published resource + verifying schema (P38
# DATA-03 audit)
ARRETS_CSV_URL = (
    "https://static.data.gouv.fr/resources/"
    "arrets-de-transport-en-france/"
    "20260113-104155/"
    "gtfs-stops-france-export-2026-01-13.csv"
)

# SNCF train stations — data.gouv.fr GeoJSON mirror (SNCF API rate-limits)
# The SNCF export API (ressources.data.sncf.com) returns 429 aggressively.
# data.gouv.fr caches a GeoJSON copy via Hydra that is always available.
GARES_GEOJSON_URL = (
    "https://object.files.data.gouv.fr/hydra-geojson/"
    "hydra-geojson/d22ba593-90a4-4725-977c-095d1f654d28.geojson"
)

# Fallback: original SNCF CSV export (may 429)
GARES_CSV_URL_SNCF_FALLBACK = (
    "https://ressources.data.sncf.com/api/explore/v2.1/"
    "catalog/datasets/liste-des-gares/exports/csv"
    "?use_labels=true"
)

# -- Schema settings ----------------------------------------------------------

# Expected columns in the GTFS consolidated stops CSV
ARRETS_EXPECTED_COLUMNS: list[str] = [
    "stop_id",
    "stop_name",
    "stop_lat",
    "stop_lon",
    "location_type",
]

# Expected columns in the SNCF gares CSV
# Note: separator may be comma or semicolon (SNCF convention varies)
# Column names changed in 2025+ (NOM_GARE->LIBELLE, CODE_INSEE removed)
GARES_EXPECTED_COLUMNS: list[str] = [
    "LIBELLE",
    "COMMUNE",
    "X_WGS84",
    "Y_WGS84",
    "CODE_UIC",
    "CODE_LIGNE",
]

# Output columns for the transformed arrets CSV (used by COPY)
ARRETS_OUTPUT_COLUMNS: list[str] = [
    "stop_id",
    "stop_name",
    "stop_lat",
    "stop_lon",
    "location_type",
    "agency_name",
]

# Output columns for the transformed gares CSV (used by COPY)
GARES_OUTPUT_COLUMNS: list[str] = [
    "nom_gare",
    "commune",
    "code_insee",
    "x_wgs84",
    "y_wgs84",
    "code_uic",
    "code_ligne",
    "trigramme",
]

# Column mapping for gares (source CSV -> internal names)
GARES_COLUMN_MAPPING: dict[str, str] = {
    "LIBELLE": "nom_gare",
    "COMMUNE": "commune",
    "X_WGS84": "x_wgs84",
    "Y_WGS84": "y_wgs84",
    "CODE_UIC": "code_uic",
    "CODE_LIGNE": "code_ligne",
}
