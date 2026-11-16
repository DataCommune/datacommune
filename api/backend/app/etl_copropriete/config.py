"""RNIC copropriete configuration: URL, column mappings.

Data source: ANAH — Registre National des Coproprietes.
"""

import httpx

# -- Download settings --------------------------------------------------------

DOWNLOAD_URL = (
    "https://www.data.gouv.fr/api/1/datasets/r/3ea8e2c3-0038-464a-b17e-cd5c91f65ce2"
)

DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=600.0)
MAX_RETRIES = 3

# -- CSV format ---------------------------------------------------------------

CSV_SEPARATOR = ","

# Map columns — RNIC open data column names (2026 schema).
# Source renamed many columns between 2025 and 2026 release.
# D-20 root cause (measured at the source, 2026-07-27 prod run):
# code_officiel_commune carries the POSTAL code (~89% of rows equal to
# code_postal_adresse) — it must NEVER feed the INSEE key. The real
# INSEE code is derived from code_insee_parcelle_1 in pipeline.transform.
COL_MAP: dict[str, str] = {
    # numero_immatriculation is already the correct name in 2026 schema
    "numero_immatriculation": "numero_immatriculation",
    "nom_usage_copropriete": "nom_copropriete",
    "adresse_reference": "adresse",
    "code_postal_adresse": "code_postal",
    "nombre_total_lots": "nb_lots_total",
    "nombre_lots_habitation_bureaux_commerces": "nb_lots_habitation",
    # RNIC column 34 -- habitation lots ONLY, distinct from the
    # habitation+bureaux+commerces column mapped just above.
    "nombre_lots_habitation": "nb_lots_habitation_strict",
    "nombre_lots_stationnement": "nb_lots_stationnement",
    "raison_sociale_representant_legal": "nom_syndic",
    "type_syndic": "type_syndic",
    "periode_construction": "periode_construction",
    "date_reglement_copropriete": "annee_reglement",
    "longitude": "longitude",
    "latitude": "latitude",
    # Plan 04.7-08: syndic mandate status and end date of the last mandate.
    "mandat_en_cours": "mandat_en_cours",
    "date_fin_dernier_mandat": "date_fin_dernier_mandat",
    # KI-20: how many parcels the registration declares (it cites three at most).
    "nombre_parcelles": "nb_parcelles",
}

# Parcel-part columns consumed by derive_id_parcelle_1 AND by the
# code_insee derivation (pipeline.transform).
PARCELLE_1_COLUMNS: list[str] = [
    "reference_cadastrale_1",
    "code_insee_parcelle_1",
    "prefixe_parcelle_1",
    "section_parcelle_1",
    "numero_parcelle_1",
]

# KI-20: a registration cites up to three parcels; the second and the third
# are read by the same rule as the first (pipeline.derive_id_parcelle).
RANGS_PARCELLES_SECONDAIRES: tuple[int, ...] = (2, 3)
PARCELLES_SECONDAIRES_COLUMNS: list[str] = [
    colonne.replace("_1", f"_{rang}")
    for rang in RANGS_PARCELLES_SECONDAIRES
    for colonne in PARCELLE_1_COLUMNS
]

# Every column transform() consumes — validate_schema fails the run
# BEFORE loading anything if the source renames one of them (the guard
# only declared numero_immatriculation until 2026-07-27, which let the
# D-20 semantic drift load silently).
SOURCE_COLUMNS: list[str] = sorted(
    {*COL_MAP, *PARCELLE_1_COLUMNS, *PARCELLES_SECONDAIRES_COLUMNS}
)

OUTPUT_COLUMNS: list[str] = [
    "numero_immatriculation",
    "nom_copropriete",
    "adresse",
    "code_commune",
    "code_postal",
    "nb_lots_total",
    "nb_lots_habitation",
    "nb_lots_commerciaux",
    "nb_lots_stationnement",
    "nom_syndic",
    "type_syndic",
    "periode_construction",
    "annee_reglement",
    # D-20 (Phase 48): real INSEE code, exact parcel link, position and
    # habitation-strict lot count. Added at the end -- OUTPUT_COLUMNS order
    # is positional with the flat staging table + COPY (pipeline.py).
    "code_insee",
    "id_parcelle_1",
    "longitude",
    "latitude",
    "nb_lots_habitation_strict",
    # Plan 04.7-08 (same positional rule as above).
    "mandat_en_cours",
    "date_fin_dernier_mandat",
    # KI-20 (same positional rule as above).
    "id_parcelle_2",
    "id_parcelle_3",
    "nb_parcelles",
]
