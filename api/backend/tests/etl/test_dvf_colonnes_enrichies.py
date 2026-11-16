"""Unit tests locking the D-02/DVF-02 column parity across the DVF pipeline.

No database connection is opened: these tests read source code (via
inspect.getsource) and exercise transform_dvf_csv against a synthetic CSV
written to tmp_path. Fixture data is fully synthetic (D-31): SYN-* ids,
no real address or id_parcelle.

Covers:
1. Strict COLUMNS_TO_KEEP <-> OUTPUT_COLUMNS list parity.
2. Presence of the 12 D-02 columns in both lists, absence from EXPECTED_COLUMNS.
3. DDL/COPY coherence: every OUTPUT_COLUMNS name appears in the flat staging
   DDL and in the INSERT INTO mutations_staging column list (pipeline.py).
4. Anti-Pitfall-3 gate: no SUM(nombre_lots)/MAX(nombre_lots) aggregation
   anywhere in pipeline.py/transform.py, comment lines ignored.
5. End-to-end transform: a synthetic geo-dvf CSV (real header order, RESEARCH
   Code Examples) with 3 rows (1 appartement + 2 dependances) survives
   transform_dvf_csv with all 12 D-02 columns intact, notably
   numero_disposition "000001" not stripped of its leading zeros.
"""

import inspect
import re
from pathlib import Path

import pandas as pd
import pytest

from app.etl_dvf import pipeline as pipeline_module
from app.etl_dvf import transform as transform_module
from app.etl_dvf.config import (
    COLUMNS_TO_KEEP,
    EXPECTED_COLUMNS,
    OUTPUT_COLUMNS,
)
from app.etl_dvf.transform import transform_dvf_csv

pytestmark = pytest.mark.unit

D02_COLUMNS: list[str] = [
    "numero_disposition",
    "nombre_lots",
    "lot1_numero",
    "lot1_surface_carrez",
    "lot2_numero",
    "lot2_surface_carrez",
    "lot3_numero",
    "lot3_surface_carrez",
    "lot4_numero",
    "lot4_surface_carrez",
    "lot5_numero",
    "lot5_surface_carrez",
]

_AGGREGATION_PATTERN = re.compile(r"SUM\(nombre_lots\)|MAX\(nombre_lots\)")


def _strip_comment_lines(source: str) -> str:
    """Remove Python comment-only lines (mirrors `grep -vE '^\\s*#'`)."""
    return "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )


# -- 1. Strict list parity ----------------------------------------------------


def test_output_columns_subset_of_columns_to_keep_except_code_departement():
    """Every OUTPUT_COLUMNS entry (bar the injected code_departement) must be
    read from the source CSV via COLUMNS_TO_KEEP -- otherwise the COPY would
    write an empty/garbage column."""
    extra = [
        c
        for c in OUTPUT_COLUMNS
        if c != "code_departement" and c not in COLUMNS_TO_KEEP
    ]
    assert extra == []


def test_columns_to_keep_subset_of_output_columns():
    """Every COLUMNS_TO_KEEP entry must be written back out -- otherwise a
    column is read from the source CSV and silently dropped before COPY."""
    missing = [c for c in COLUMNS_TO_KEEP if c not in OUTPUT_COLUMNS]
    assert missing == []


# -- 2. D-02 columns present in both lists, absent from EXPECTED_COLUMNS -----


def test_d02_columns_present_in_columns_to_keep():
    missing = [c for c in D02_COLUMNS if c not in COLUMNS_TO_KEEP]
    assert missing == []


def test_d02_columns_present_in_output_columns():
    missing = [c for c in D02_COLUMNS if c not in OUTPUT_COLUMNS]
    assert missing == []


def test_d02_columns_absent_from_expected_columns():
    """EXPECTED_COLUMNS is the validation subset only -- validate_schema()
    already tolerates extra source columns, D-02 must not be added here."""
    leaked = [c for c in D02_COLUMNS if c in EXPECTED_COLUMNS]
    assert leaked == []


# -- 2b. C44 (plan 04.1-14): the address suffix is kept --------------------------


def test_adresse_suffixe_kept_in_both_mirrored_lists():
    """The suffix of the declared address (« 2 B ») travels from the source
    file to `mutations`, next to the number, in both mirrored lists."""
    assert "adresse_suffixe" in COLUMNS_TO_KEEP
    assert "adresse_suffixe" in OUTPUT_COLUMNS
    assert COLUMNS_TO_KEEP.index("adresse_suffixe") == (
        COLUMNS_TO_KEEP.index("adresse_numero") + 1
    )
    assert "adresse_suffixe" not in EXPECTED_COLUMNS


def test_adresse_code_voie_not_kept():
    """Measured on 2026-10-19 (correspondance.json, code_voie_decision): the
    imported BAN carries no street code to compare it with."""
    assert "adresse_code_voie" not in COLUMNS_TO_KEEP
    assert "adresse_code_voie" not in OUTPUT_COLUMNS


# -- 3. DDL / COPY coherence --------------------------------------------------


def test_flat_staging_ddl_contains_all_output_columns():
    source = inspect.getsource(pipeline_module.DvfPipeline.stage)
    missing = [c for c in OUTPUT_COLUMNS if c not in source]
    assert missing == [], f"missing from _mutations_staging_flat DDL: {missing}"


def test_insert_select_contains_all_output_columns():
    source = inspect.getsource(pipeline_module.DvfPipeline.swap)
    # The INSERT INTO mutations_staging (...) column list must name every
    # OUTPUT_COLUMNS entry (code_departement included) -- a drift here means
    # the flat->partitioned transfer silently drops a staged column.
    missing = [c for c in OUTPUT_COLUMNS if c not in source]
    assert missing == [], f"missing from INSERT INTO mutations_staging: {missing}"


# -- 4. Anti-Pitfall-3 gate: never aggregate nombre_lots ----------------------


def test_no_nombre_lots_aggregation_in_pipeline_or_transform():
    pipeline_source = _strip_comment_lines(inspect.getsource(pipeline_module))
    transform_source = _strip_comment_lines(inspect.getsource(transform_module))
    assert not _AGGREGATION_PATTERN.search(pipeline_source)
    assert not _AGGREGATION_PATTERN.search(transform_source)


def test_aggregation_gate_ignores_comment_lines():
    """Prove the gate is comment-line-blind: a pattern hit that lives only in
    a comment must NOT trip the assertion above."""
    synthetic_source = "\n".join(
        [
            "def f():",
            "    # this comment mentions SUM(nombre_lots) on purpose",
            "    return 1",
        ]
    )
    stripped = _strip_comment_lines(synthetic_source)
    assert not _AGGREGATION_PATTERN.search(stripped)
    # Sanity: the raw (unstripped) source DOES contain the pattern, proving
    # the strip step is what neutralizes it (not an accidental non-match).
    assert _AGGREGATION_PATTERN.search(synthetic_source)


# -- 5. End-to-end transform: 12 D-02 columns survive intact ------------------

# Real geo-dvf header order (RESEARCH.md § Code Examples, VERIFIED download
# 2026-08-24, dept 44/2024). code_departement is present in the raw file but
# excluded by COLUMNS_TO_KEEP -- transform_dvf_csv re-derives it from
# code_commune, matching production behaviour.
_RAW_HEADER = [
    "id_mutation",
    "date_mutation",
    "numero_disposition",
    "nature_mutation",
    "valeur_fonciere",
    "adresse_numero",
    "adresse_suffixe",
    "adresse_nom_voie",
    "adresse_code_voie",
    "code_postal",
    "code_commune",
    "nom_commune",
    "code_departement",
    "ancien_code_commune",
    "ancien_nom_commune",
    "id_parcelle",
    "ancien_id_parcelle",
    "numero_volume",
    "lot1_numero",
    "lot1_surface_carrez",
    "lot2_numero",
    "lot2_surface_carrez",
    "lot3_numero",
    "lot3_surface_carrez",
    "lot4_numero",
    "lot4_surface_carrez",
    "lot5_numero",
    "lot5_surface_carrez",
    "nombre_lots",
    "code_type_local",
    "type_local",
    "surface_reelle_bati",
    "nombre_pieces_principales",
    "code_nature_culture",
    "nature_culture",
    "code_nature_culture_speciale",
    "nature_culture_speciale",
    "surface_terrain",
    "longitude",
    "latitude",
]

# Synthetic mutation SYN-0001 (D-31): 1 appartement + 2 dependances, same
# disposition "000001" -- reproduces the multi-local shape documented in
# RESEARCH.md Pitfall 3 (nombre_lots varies per LINE, not per disposition)
# without using any real id_mutation/address/id_parcelle.
_SYNTHETIC_ROWS = [
    {
        "id_mutation": "SYN-0001",
        "date_mutation": "2024-01-03",
        "numero_disposition": "000001",
        "nature_mutation": "Vente",
        "valeur_fonciere": "200000",
        "adresse_numero": "10",
        "adresse_suffixe": "",
        "adresse_nom_voie": "Rue Synthetique",
        "adresse_code_voie": "0010",
        "code_postal": "44000",
        "code_commune": "44999",
        "nom_commune": "Commune Synthetique",
        "code_departement": "44",
        "ancien_code_commune": "",
        "ancien_nom_commune": "",
        "id_parcelle": "SYN000AA0001",
        "ancien_id_parcelle": "",
        "numero_volume": "",
        "lot1_numero": "133",
        "lot1_surface_carrez": "",
        "lot2_numero": "179",
        "lot2_surface_carrez": "",
        "lot3_numero": "",
        "lot3_surface_carrez": "",
        "lot4_numero": "",
        "lot4_surface_carrez": "",
        "lot5_numero": "",
        "lot5_surface_carrez": "",
        "nombre_lots": "2",
        "code_type_local": "3",
        "type_local": "Dépendance",
        "surface_reelle_bati": "",
        "nombre_pieces_principales": "",
        "code_nature_culture": "",
        "nature_culture": "",
        "code_nature_culture_speciale": "",
        "nature_culture_speciale": "",
        "surface_terrain": "",
        "longitude": "-1.5000",
        "latitude": "47.2500",
    },
    {
        "id_mutation": "SYN-0001",
        "date_mutation": "2024-01-03",
        "numero_disposition": "000001",
        "nature_mutation": "Vente",
        "valeur_fonciere": "200000",
        "adresse_numero": "10",
        "adresse_suffixe": "",
        "adresse_nom_voie": "Rue Synthetique",
        "adresse_code_voie": "0010",
        "code_postal": "44000",
        "code_commune": "44999",
        "nom_commune": "Commune Synthetique",
        "code_departement": "44",
        "ancien_code_commune": "",
        "ancien_nom_commune": "",
        "id_parcelle": "SYN000AA0001",
        "ancien_id_parcelle": "",
        "numero_volume": "",
        "lot1_numero": "221",
        "lot1_surface_carrez": "",
        "lot2_numero": "",
        "lot2_surface_carrez": "",
        "lot3_numero": "",
        "lot3_surface_carrez": "",
        "lot4_numero": "",
        "lot4_surface_carrez": "",
        "lot5_numero": "",
        "lot5_surface_carrez": "",
        "nombre_lots": "1",
        "code_type_local": "3",
        "type_local": "Dépendance",
        "surface_reelle_bati": "",
        "nombre_pieces_principales": "",
        "code_nature_culture": "",
        "nature_culture": "",
        "code_nature_culture_speciale": "",
        "nature_culture_speciale": "",
        "surface_terrain": "",
        "longitude": "-1.5000",
        "latitude": "47.2500",
    },
    {
        "id_mutation": "SYN-0001",
        "date_mutation": "2024-01-03",
        "numero_disposition": "000001",
        "nature_mutation": "Vente",
        "valeur_fonciere": "200000",
        "adresse_numero": "10",
        "adresse_suffixe": "",
        "adresse_nom_voie": "Rue Synthetique",
        "adresse_code_voie": "0010",
        "code_postal": "44000",
        "code_commune": "44999",
        "nom_commune": "Commune Synthetique",
        "code_departement": "44",
        "ancien_code_commune": "",
        "ancien_nom_commune": "",
        "id_parcelle": "SYN000AA0001",
        "ancien_id_parcelle": "",
        "numero_volume": "",
        "lot1_numero": "133",
        "lot1_surface_carrez": "71.00",
        "lot2_numero": "179",
        "lot2_surface_carrez": "5.00",
        "lot3_numero": "",
        "lot3_surface_carrez": "",
        "lot4_numero": "",
        "lot4_surface_carrez": "",
        "lot5_numero": "",
        "lot5_surface_carrez": "",
        "nombre_lots": "2",
        "code_type_local": "2",
        "type_local": "Appartement",
        "surface_reelle_bati": "71",
        "nombre_pieces_principales": "3",
        "code_nature_culture": "",
        "nature_culture": "",
        "code_nature_culture_speciale": "",
        "nature_culture_speciale": "",
        "surface_terrain": "",
        "longitude": "-1.5000",
        "latitude": "47.2500",
    },
]


@pytest.fixture
def raw_csv(tmp_path: Path) -> Path:
    df = pd.DataFrame(_SYNTHETIC_ROWS)[_RAW_HEADER]
    raw_dir = tmp_path / "dvf" / "raw"
    raw_dir.mkdir(parents=True)
    out = raw_dir / "44.csv"
    df.to_csv(out, index=False, sep=",")
    return out


def test_transform_preserves_all_d02_columns(raw_csv: Path):
    out_path = transform_dvf_csv(raw_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)

    for col in D02_COLUMNS:
        assert col in df.columns, col


def test_transform_preserves_numero_disposition_leading_zeros(raw_csv: Path):
    out_path = transform_dvf_csv(raw_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)

    assert (df["numero_disposition"] == "000001").all()


def test_transform_preserves_lot_numero_values(raw_csv: Path):
    out_path = transform_dvf_csv(raw_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)

    lot1_values = set(df["lot1_numero"])
    assert lot1_values == {"133", "221"}


def test_transform_preserves_nombre_lots_per_line_not_aggregated(raw_csv: Path):
    """Regression for Pitfall 3: nombre_lots must stay per-LINE (2, 1, 2),
    the transform must not collapse it into a single total for the mutation."""
    out_path = transform_dvf_csv(raw_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)

    assert list(df["nombre_lots"]) == ["2", "1", "2"]


def test_transform_preserves_lot_surface_carrez_values(raw_csv: Path):
    """lotN_surface_carrez is an untyped pass-through column (Task 1 doctrine:
    numeric conversion happens later, in the INSERT SELECT) -- pandas infers
    it as float64, so trailing zeros may be reformatted ("71.00" -> "71.0").
    The VALUE must still round-trip exactly; the DB-side
    NULLIF(x, '')::numeric(10,2) conversion is agnostic to this formatting."""
    out_path = transform_dvf_csv(raw_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)

    appartement_row = df[df["type_local"] == "Appartement"].iloc[0]
    assert float(appartement_row["lot1_surface_carrez"]) == 71.00
    assert float(appartement_row["lot2_surface_carrez"]) == 5.00


def test_transform_keeps_the_suffix_of_each_line(tmp_path: Path):
    """« 2 B » keeps its B; a line without suffix stays empty, never filled."""
    rows = [dict(row) for row in _SYNTHETIC_ROWS]
    rows[0]["adresse_suffixe"] = "B"
    raw_dir = tmp_path / "dvf" / "raw"
    raw_dir.mkdir(parents=True)
    raw = raw_dir / "44.csv"
    pd.DataFrame(rows)[_RAW_HEADER].to_csv(raw, index=False, sep=",")

    df = pd.read_csv(transform_dvf_csv(raw), dtype=str, keep_default_na=False)

    assert list(df["adresse_suffixe"]) == ["B", "", ""]
    assert list(df["adresse_numero"]) == ["10", "10", "10"]


# -- 6. Phase 04.4 (D-01): every source line is kept, coordinates or not -------


def test_transform_keeps_a_line_without_coordinates(tmp_path: Path):
    """D-01: a line without longitude nor latitude is loaded (position NULL)
    and counts in the reconstruction of its mutation -- never dropped here."""
    rows = [dict(row) for row in _SYNTHETIC_ROWS]
    rows[2]["longitude"] = ""
    rows[2]["latitude"] = ""
    raw_dir = tmp_path / "dvf" / "raw"
    raw_dir.mkdir(parents=True)
    raw = raw_dir / "44.csv"
    pd.DataFrame(rows)[_RAW_HEADER].to_csv(raw, index=False, sep=",")

    df = pd.read_csv(transform_dvf_csv(raw), dtype=str, keep_default_na=False)

    assert len(df) == 3
    appartement = df[df["type_local"] == "Appartement"].iloc[0]
    assert (appartement["longitude"], appartement["latitude"]) == ("", "")
    # Untyped pass-through (float "71.0"): cast at the INSERT SELECT.
    assert float(appartement["surface_reelle_bati"]) == 71


def test_transform_has_no_coordinate_row_filter():
    """Structural guard (D-01): no row filter on coordinates in the transform."""
    source = _strip_comment_lines(inspect.getsource(transform_dvf_csv))
    assert "dropna" not in source
    assert ".query(" not in source


def test_flat_to_partitioned_transfer_keeps_lines_without_coordinates():
    """Structural guard (D-01): the INSERT ... SELECT of the swap has no clause
    on longitude or latitude; an empty coordinate becomes NULL, and so does the
    position built from it."""
    source = inspect.getsource(pipeline_module.DvfPipeline.swap)
    transfert = source.split("FROM _mutations_staging_flat", 1)[1].split('"""', 1)[0]
    assert "WHERE" not in transfert
    assert "longitude" not in transfert
    assert "latitude" not in transfert
    assert "NULLIF(longitude, '')::double precision" in source
    assert "NULLIF(latitude, '')::double precision" in source


def test_no_database_imports_in_this_test_file():
    """This module must exercise pure transform/source-inspection logic only
    -- no psycopg/sqlalchemy import, no DB connection ever opened."""
    import ast

    this_source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(this_source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    assert "psycopg" not in imported_modules
    assert "sqlalchemy" not in imported_modules
