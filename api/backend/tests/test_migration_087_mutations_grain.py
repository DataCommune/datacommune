"""Static DDL tests for migration 087 (mutations_grain schema, D-01/D-02/D-09).

No database access: assertions are made on the raw source text of
``migrations/versions/087_dvf_mutations_grain.py``. See
``test_migration_check.py`` for the separate DB-facing boot gate (ADR-0003
D2) this migration feeds once shipped.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "087_dvf_mutations_grain.py"
)

# D-02: the 12 source columns the ETL currently throws away (DVF-02),
# added to `mutations`. Order mirrors the plan's <interfaces> block.
D02_COLUMNS = [
    "numero_disposition",
    "nombre_lots",
    "lot1_numero",
    "lot2_numero",
    "lot3_numero",
    "lot4_numero",
    "lot5_numero",
    "lot1_surface_carrez",
    "lot2_surface_carrez",
    "lot3_surface_carrez",
    "lot4_surface_carrez",
    "lot5_surface_carrez",
]

# mutations_grain structuring columns from the plan <interfaces> block --
# explicit list, not an approximate substring check.
MUTATIONS_GRAIN_COLUMNS = [
    "classe_mutation",
    "mono_local",
    "est_vente",
    "hors_stats_vefa",
    "valeur_divergente",
    "nb_dispositions",
    "nb_communes",
    "nb_parcelles",
    "surface_bati_totale",
    "nb_locaux_maison",
    "nb_locaux_appartement",
    "nb_locaux_commercial",
    "nb_locaux_dependance",
    "nb_lignes",
    "surface_terrain_totale",
    "nombre_pieces_totales",
]


@pytest.fixture(scope="module")
def source() -> str:
    """Raw source text of the migration under test."""
    return MIGRATION_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def source_no_comments(source: str) -> str:
    """Source with '#' comment-only lines stripped (docstrings NOT stripped).

    Isolates the anti-Pitfall-3 gate from prose that legitimately discusses
    the forbidden pattern (this migration's own docstring explains why
    nombre_lots is never aggregated) -- if that explanation used a bare
    comment line the gate would false-positive on itself. Docstrings must
    simply never spell the literal forbidden aggregation string.
    """
    return "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )


def test_revision_chain_is_pinned(source: str):
    """Anti-fork gate: revision and down_revision are the exact expected link."""
    assert 'revision: str = "087_dvf_mutations_grain"' in source
    assert 'down_revision: str = "086_enable_pg_stat_statements"' in source


def test_d02_columns_added_and_removed(source: str):
    """Upgrade/downgrade symmetry for the 12 D-02 source columns on mutations."""
    for column in D02_COLUMNS:
        assert f"ADD COLUMN IF NOT EXISTS {column} " in source, (
            f"missing ADD COLUMN for {column}"
        )
        assert f"DROP COLUMN IF EXISTS {column}" in source, (
            f"missing DROP COLUMN for {column}"
        )


def test_mutations_grain_structuring_columns_present(source: str):
    """Every structuring column from the plan <interfaces> block is in the DDL."""
    for column in MUTATIONS_GRAIN_COLUMNS:
        assert column in source, f"missing column {column} in mutations_grain DDL"


def test_no_nombre_lots_aggregation(source_no_comments: str):
    """Pitfall 3 gate: nombre_lots is never SUM'd or MAX'd.

    It varies per LINE in the real geo-dvf file, not per disposition as the
    DGFiP notice describes for the raw file -- aggregating it would silently
    fabricate a lot count.
    """
    forbidden = ["SUM(nombre_lots)", "MAX(nombre_lots)"]
    for pattern in forbidden:
        assert pattern not in source_no_comments, (
            f"forbidden aggregation pattern found: {pattern}"
        )


def test_no_data_population(source: str):
    """Schema only -- the ETL (57-06) populates the tables, never the migration."""
    assert "INSERT INTO" not in source.upper()
