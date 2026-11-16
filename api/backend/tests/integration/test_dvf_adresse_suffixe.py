"""C44 (plan 04.1-14): the DVF address suffix, from migration 105 to `mutations_grain`.

Migration ``105_dvf_adresse_suffixe`` adds ``adresse_suffixe`` to `mutations`
and `mutations_grain` (NULL: filled by the next DVF import, never invented).
The reconstruction keeps the suffix of the line whose number and street it
keeps (lowest id) and comments the column of the candidate with the marker
that tells the address sales route a generation imported with the suffix
from one migrated but not reimported. Fixture data are synthetic (D-31).

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_dvf_adresse_suffixe.py
"""

from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import settings
from app.etl_dvf.config import MARQUEUR_ADRESSE_SUFFIXE
from app.etl_dvf.reconstruction import reconstruit_mutations_grain

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = "105_dvf_adresse_suffixe"
PRECEDENTE = "104_dpe_rattachement_adresse_brute"

_MARQUEUR_SQL = """
    SELECT col_description(a.attrelid, a.attnum)
    FROM pg_attribute a
    WHERE a.attrelid = 'mutations_grain'::regclass
      AND a.attname = 'adresse_suffixe'
"""


def _config(dsn: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        command.upgrade(_config(isolated_pg_dsn), "head")
        yield isolated_pg_dsn


def _colonnes(dsn: str) -> set[tuple[str, str]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT table_name, data_type FROM information_schema.columns "
            "WHERE column_name = 'adresse_suffixe' "
            "AND table_name IN ('mutations', 'mutations_grain')"
        ).fetchall()
    return set(rows)


def test_migration_105_is_in_the_single_linear_chain():
    # 106_eligibilite_fixe_par_immeuble (plan 04.1-16) now follows MIGRATION.
    script = ScriptDirectory.from_config(_config("postgresql://unused"))
    assert len(script.get_heads()) == 1
    assert (
        script.get_revision("106_eligibilite_fixe_par_immeuble").down_revision
        == MIGRATION
    )
    assert script.get_revision(MIGRATION).down_revision == PRECEDENTE


def test_migration_105_roundtrip(migrated):
    attendu = {("mutations", "text"), ("mutations_grain", "text")}
    assert _colonnes(migrated) == attendu
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", migrated)
        context.setattr(settings, "database_url", migrated)
        for _ in range(2):
            command.downgrade(_config(migrated), PRECEDENTE)
            assert _colonnes(migrated) == set()
            command.upgrade(_config(migrated), "head")
            assert _colonnes(migrated) == attendu
    with psycopg.connect(migrated) as conn:
        # A migrated base carries no marker: nothing was computed yet.
        assert conn.execute(_MARQUEUR_SQL).fetchone() == (None,)


def _ligne(conn, id_mutation: str, numero: str, suffixe: str | None, voie: str):
    conn.execute(
        "INSERT INTO mutations (code_departement, id_mutation, date_mutation, "
        "nature_mutation, valeur_fonciere, code_type_local, code_commune, "
        "nom_commune, id_parcelle, surface_reelle_bati, numero_disposition, "
        "adresse_numero, adresse_suffixe, adresse_nom_voie, code_postal) VALUES "
        "('44', %s, '2025-03-01', 'Vente', 200000, '1', '44999', "
        "'Commune synthetique', 'SYN000AA0001', 80, '1', %s, %s, %s, '44000')",
        (id_mutation, numero, suffixe, voie),
    )


def test_the_reconstruction_keeps_the_suffix_of_the_kept_address_line(migrated):
    with psycopg.connect(migrated) as conn:
        # Two lines: the first (lowest id) carries « 2 B », the second « 4 ».
        _ligne(conn, "SYN-SUF-1", "2", "B", "RUE SYNTHETIQUE")
        _ligne(conn, "SYN-SUF-1", "4", None, "RUE AUTRE")
        # The first line without suffix: none, even if a later line has one.
        _ligne(conn, "SYN-SUF-2", "6", None, "RUE SYNTHETIQUE")
        _ligne(conn, "SYN-SUF-2", "6", "T", "RUE SYNTHETIQUE")
        # What the DVF import leaves on the column it loaded with the suffix.
        conn.execute(
            "COMMENT ON COLUMN mutations.adresse_suffixe IS "
            f"'{MARQUEUR_ADRESSE_SUFFIXE}'"
        )

    reconstruit_mutations_grain(migrated, ("44", "85"))

    with psycopg.connect(migrated) as conn:
        rows = conn.execute(
            "SELECT id_mutation, adresse_numero, adresse_suffixe, adresse_nom_voie "
            "FROM mutations_grain ORDER BY id_mutation"
        ).fetchall()
        marqueur = conn.execute(_MARQUEUR_SQL).fetchone()
    assert rows == [
        ("SYN-SUF-1", "2", "B", "RUE SYNTHETIQUE"),
        ("SYN-SUF-2", "6", None, "RUE SYNTHETIQUE"),
    ]
    assert marqueur == (MARQUEUR_ADRESSE_SUFFIXE,)


def test_a_reconstruction_from_a_source_not_reimported_leaves_the_suffix_unknown(
    migrated,
):
    """Review WR-01: a rebuild after migration 105 but before a DVF reimport
    (indicator adoption) reads NULL suffixes; it must not mark them imported,
    or « 2 » would take the sales of « 2 B » under ``numero_et_suffixe``."""
    with psycopg.connect(migrated) as conn:
        conn.execute("COMMENT ON COLUMN mutations.adresse_suffixe IS NULL")

    reconstruit_mutations_grain(migrated, ("44", "85"))

    with psycopg.connect(migrated) as conn:
        assert conn.execute(_MARQUEUR_SQL).fetchone() == (None,)
