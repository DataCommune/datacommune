"""DPE-specific configuration: API URL, expected columns, column mappings."""

import httpx

# -- Download settings --------------------------------------------------------

# Closed scope measured in phase 58.2: every import requires all four pairs.
ETL_DPE_SOURCES: tuple[str, ...] = ("dpe03existant", "dpe02neuf")


def api_base_url(source: str) -> str:
    """Build the data-fair ``lines`` endpoint URL for a given ADEME source."""
    if source not in ETL_DPE_SOURCES:
        raise ValueError("Unsupported DPE source")
    return f"https://data.ademe.fr/data-fair/api/v1/datasets/{source}/lines"


# Alias kept for existing callers (e.g. etl_dpe/pipeline.py) -- additive
# contract, nothing that already imports API_BASE_URL breaks.
API_BASE_URL = api_base_url(ETL_DPE_SOURCES[0])

CSV_PAGE_SIZE = 5000  # records per JSON page; CSV export failed in the measurement
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=300.0)
# Run 61 (2026-10-29): 5 attempts gave 30 s of backoff, too short for a slow
# national download; 10 attempts capped at 300 s wait about 13.5 min.
MAX_RETRIES = 10
RETRY_MAX_WAIT_SECONDS = 300

# Columns to request from API (select= parameter).
# Reduces payload ~15x vs fetching all 226 columns.
API_SELECT: str = ",".join(
    [
        "numero_dpe",
        "date_etablissement_dpe",
        "annee_construction",
        "periode_construction",
        "type_batiment",
        "surface_habitable_logement",
        "etiquette_dpe",
        "etiquette_ges",
        "type_installation_chauffage",
        "type_energie_principale_chauffage",
        "conso_5_usages_ep",
        "emission_ges_5_usages",
        "cout_total_5_usages",
        "qualite_isolation_enveloppe",
        "code_postal_ban",
        "code_insee_ban",
        "code_departement_ban",
        "nom_commune_ban",
        "coordonnee_cartographique_x_ban",
        "coordonnee_cartographique_y_ban",
        # Phase 48 (Multi-logements honnete, Wave 0) -- logement identity fields.
        "numero_etage_appartement",
        "complement_adresse_logement",
        "identifiant_ban",
        "numero_dpe_remplace",
        "date_fin_validite_dpe",
        # R4-01 (Plan A'', RESEARCH-L1 §1.4) -- RNB/BAN confidence fields.
        "id_rnb",
        "provenance_id_rnb",
        "score_ban",
        # Phase 58 (Rattachement des maisons, D-02/D-16) -- appended at the END, same
        # positional-contract rule.
        "adresse_brut",
        "adresse_complete_brut",
        "nom_commune_brut",
        "code_postal_brut",
        "adresse_ban",
        "numero_voie_ban",
        "nom_rue_ban",
        "statut_geocodage",
        "date_derniere_modification_dpe",
        "date_reception_dpe",
        "date_visite_diagnostiqueur",
        "version_dpe",
        # Plan 04-15 (owner decisions C2/C3, 04-DECISIONS-COLONNES-DPE.md) --
        # appended at the END, same positional-contract rule. Per-m2 values of
        # landmarks 1 and 2, then two identifiers stored but never served (C3).
        "conso_5_usages_par_m2_ep",
        "emission_ges_5_usages_par_m2",
        "numero_dpe_immeuble_associe",
        "numero_immatriculation_copropriete",
    ]
)

# -- CSV format ---------------------------------------------------------------

# Columns expected in the downloaded CSV (after select= filtering).
EXPECTED_COLUMNS: list[str] = [
    "numero_dpe",
    "date_etablissement_dpe",
    "annee_construction",
    "periode_construction",
    "type_batiment",
    "surface_habitable_logement",
    "etiquette_dpe",
    "etiquette_ges",
    "type_installation_chauffage",
    "type_energie_principale_chauffage",
    "conso_5_usages_ep",
    "emission_ges_5_usages",
    "cout_total_5_usages",
    "qualite_isolation_enveloppe",
    "code_postal_ban",
    "code_insee_ban",
    "code_departement_ban",
    "nom_commune_ban",
    "coordonnee_cartographique_x_ban",
    "coordonnee_cartographique_y_ban",
    # Phase 48 (Multi-logements honnete, Wave 0) -- logement identity fields.
    "numero_etage_appartement",
    "complement_adresse_logement",
    "identifiant_ban",
    "numero_dpe_remplace",
    "date_fin_validite_dpe",
    # R4-01 (Plan A'', RESEARCH-L1 §1.4) -- RNB/BAN confidence fields.
    "id_rnb",
    "provenance_id_rnb",
    "score_ban",
    # Phase 58 (Rattachement des maisons, D-02/D-16) -- appended at the END, same
    # positional-contract rule.
    "adresse_brut",
    "adresse_complete_brut",
    "nom_commune_brut",
    "code_postal_brut",
    "adresse_ban",
    "numero_voie_ban",
    "nom_rue_ban",
    "statut_geocodage",
    # Source lifecycle evidence, stored since plan 04-15 (owner decision C1).
    "date_derniere_modification_dpe",
    "date_reception_dpe",
    "date_visite_diagnostiqueur",
    "version_dpe",
    # Plan 04-15 (C2/C3) -- appended at the END, same order as API_SELECT.
    "conso_5_usages_par_m2_ep",
    "emission_ges_5_usages_par_m2",
    "numero_dpe_immeuble_associe",
    "numero_immatriculation_copropriete",
]

# Source columns we keep (API response names)
COLUMNS_TO_KEEP: list[str] = list(EXPECTED_COLUMNS)

# Both official schemas measured on 2026-09-05 have these same 40 field types;
# the 4 fields of plan 04-15 were read in the metadata of 2026-10-06 (04-14).
# Descriptions and unrelated extra columns are intentionally not pinned.
SOURCE_FIELD_TYPES: dict[str, str] = dict.fromkeys(EXPECTED_COLUMNS, "string")
SOURCE_FIELD_TYPES.update(
    dict.fromkeys(
        [
            "version_dpe",
            "surface_habitable_logement",
            "conso_5_usages_ep",
            "emission_ges_5_usages",
            "cout_total_5_usages",
            "score_ban",
            "coordonnee_cartographique_x_ban",
            "coordonnee_cartographique_y_ban",
            "conso_5_usages_par_m2_ep",
            "emission_ges_5_usages_par_m2",
        ],
        "number",
    )
)
SOURCE_FIELD_TYPES.update(
    dict.fromkeys(
        [
            "annee_construction",
            "code_postal_brut",
            "numero_etage_appartement",
        ],
        "integer",
    )
)
SOURCE_FIELD_FORMATS: dict[str, str] = dict.fromkeys(
    [
        "date_etablissement_dpe",
        "date_fin_validite_dpe",
        "date_derniere_modification_dpe",
        "date_reception_dpe",
        "date_visite_diagnostiqueur",
    ],
    "date",
)

# -- Column mapping (API name -> table column name) ---------------------------

COLUMN_MAPPING: dict[str, str] = {
    "numero_dpe": "numero_dpe",
    "date_etablissement_dpe": "date_etablissement_dpe",
    "annee_construction": "annee_construction",
    "periode_construction": "periode_construction",
    "type_batiment": "type_batiment",
    "surface_habitable_logement": "surface_habitable",
    "etiquette_dpe": "etiquette_dpe",
    "etiquette_ges": "etiquette_ges",
    "type_installation_chauffage": "type_installation_chauffage",
    "type_energie_principale_chauffage": "type_energie_principale_chauffage",
    "conso_5_usages_ep": "conso_5_usages_ep",
    "emission_ges_5_usages": "emission_ges_5_usages",
    "cout_total_5_usages": "cout_total_5_usages",
    "qualite_isolation_enveloppe": "qualite_isolation_enveloppe",
    "code_postal_ban": "code_postal",
    "code_insee_ban": "code_insee",
    "code_departement_ban": "code_departement",
    "nom_commune_ban": "nom_commune",
    # Phase 48 (Multi-logements honnete, Wave 0) -- identity mapping, no rename.
    # numero_etage_appartement: floor -- D-21 (0 = unknown).
    "numero_etage_appartement": "numero_etage_appartement",
    # complement_adresse_logement: only usable floor source (D-21).
    "complement_adresse_logement": "complement_adresse_logement",
    "identifiant_ban": "identifiant_ban",  # future exact BAN join key
    "numero_dpe_remplace": "numero_dpe_remplace",  # exact dedup pass (D-05)
    # date_fin_validite_dpe: source-read expiry, never recomputed (D-08).
    "date_fin_validite_dpe": "date_fin_validite_dpe",
    # R4-01 (Plan A'', RESEARCH-L1 §1.4) -- identity mapping, no rename.
    # id_rnb: RNB building id, ~58,6 % coverage on dept 44. Its dominant
    # provenance ("Reprise RNB") is DERIVED from the same BAN geocoding as
    # identifiant_ban -- correlated, NEVER an independent confirmation of
    # the BAN key. Usage: building-level catch-up join, not proof.
    "id_rnb": "id_rnb",
    "provenance_id_rnb": "provenance_id_rnb",
    # score_ban: ADEME geocoding confidence (float 0..1, 0,52-0,98 observed).
    "score_ban": "score_ban",
    # Phase 58 (Rattachement des maisons, D-02/D-16) -- appended at the END, same
    # positional-contract rule. No rename: these are the raw D-02 oracle inputs.
    "adresse_brut": "adresse_brut",
    "adresse_complete_brut": "adresse_complete_brut",
    "nom_commune_brut": "nom_commune_brut",
    # code_postal_brut: kept TEXT end to end -- ADEME types it integer, which
    # would drop the leading zero of postal codes below 10000 (Pitfall 8).
    "code_postal_brut": "code_postal_brut",
    "adresse_ban": "adresse_ban",
    "numero_voie_ban": "numero_voie_ban",
    "nom_rue_ban": "nom_rue_ban",
    "statut_geocodage": "statut_geocodage",
    # Plan 04-15 (owner decisions C1 to C3, 04-DECISIONS-COLONNES-DPE.md) --
    # identity mapping, no rename. C1: lifecycle evidence of the document,
    # kept as published (text). C2: ADEME per-m2 values of landmarks 1 and 2.
    # C3: stored before the first archived DPE import, NEVER served without a
    # later decision (tests/test_dpe_unserved_columns.py). Not requested
    # (C4/C5): numero_rpls_logement (a dwelling identifier) and
    # complement_adresse_batiment (no use, data minimisation).
    "version_dpe": "version_dpe",
    "date_derniere_modification_dpe": "date_derniere_modification_dpe",
    "date_reception_dpe": "date_reception_dpe",
    "date_visite_diagnostiqueur": "date_visite_diagnostiqueur",
    "conso_5_usages_par_m2_ep": "conso_5_usages_par_m2_ep",
    "emission_ges_5_usages_par_m2": "emission_ges_5_usages_par_m2",
    "numero_dpe_immeuble_associe": "numero_dpe_immeuble_associe",
    "numero_immatriculation_copropriete": "numero_immatriculation_copropriete",
}

# Output columns written to the transformed CSV (order matters for COPY)
OUTPUT_COLUMNS: list[str] = [
    "numero_dpe",
    "code_departement",
    "date_etablissement_dpe",
    "annee_construction",
    "periode_construction",
    "type_batiment",
    "surface_habitable",
    "etiquette_dpe",
    "etiquette_ges",
    "type_installation_chauffage",
    "type_energie_principale_chauffage",
    "conso_5_usages_ep",
    "emission_ges_5_usages",
    "cout_total_5_usages",
    "qualite_isolation_enveloppe",
    "code_postal",
    "code_insee",
    "nom_commune",
    "latitude",
    "longitude",
    # Phase 48 (Multi-logements honnete, Wave 0) -- appended at the END: OUTPUT_COLUMNS
    # order is a positional contract with transform(), never insert mid-list.
    "numero_etage_appartement",
    "complement_adresse_logement",
    "identifiant_ban",
    "numero_dpe_remplace",
    "date_fin_validite_dpe",
    # R4-01 (Plan A'') -- appended at the END, same positional-contract rule.
    "id_rnb",
    "provenance_id_rnb",
    "score_ban",
    # Phase 58 (Rattachement des maisons, D-02/D-16) -- appended at the END, same
    # positional-contract rule.
    "adresse_brut",
    "adresse_complete_brut",
    "nom_commune_brut",
    "code_postal_brut",
    "adresse_ban",
    "numero_voie_ban",
    "nom_rue_ban",
    "statut_geocodage",
    # regime_dpe (D-14): NOT an ADEME field -- it is computed by the pipeline
    # (58-04) from which source ("dpe03existant"/"dpe02neuf") produced the row,
    # so it appears ONLY here, never in API_SELECT/EXPECTED_COLUMNS/
    # COLUMN_MAPPING.
    "regime_dpe",
    "source_observations",
    "source_state",
    "source_snapshot_at",
    "is_tombstone",
    "lifecycle_facts",
    "revision_evidence",
    # Plan 04-15 (C1 to C3) -- appended at the END, after the lifecycle block.
    "version_dpe",
    "date_derniere_modification_dpe",
    "date_reception_dpe",
    "date_visite_diagnostiqueur",
    "conso_5_usages_par_m2_ep",
    "emission_ges_5_usages_par_m2",
    "numero_dpe_immeuble_associe",
    "numero_immatriculation_copropriete",
]
