"""Plan 04.1-13 on disposable PostGIS: orphans attached by exact raw address.

Block H of 04-MESURE-DPE, applied by the DPE import on its candidate
(``app.etl_dpe.adresse_brute.rattacher_candidate``, called by
``DpePipeline.swap`` before the indexes). Expected behaviour:

- an orphan whose ADEME key names a commune or a street is attached to the
  local BAN address its raw address names by strict equality (commune of the
  key, normalised street, number, suffix), single candidate, same street
  code; the ADEME key is kept as published;
- a range, an unreadable number, no number, two candidates, a different
  street code, a detached letter: never attached;
- a numbered orphan key: never attached, even when the rule alone would
  accept its candidate (deduced in 04-MESURE-DPE);
- a document whose key is in the BAN, and a tombstone: never attached.

The rule is the measured one, unchanged: « 12 bis » names the BAN suffix
``bis`` (not ``b``) and a leading complement (« Appartement 3, 14 … ») is an
unreadable format unless ``adresse_complete_brut`` carries the number.

Run only through the disposable PostGIS harness::

    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_dpe_rattachement_adresse_brute.py
"""

from __future__ import annotations

from collections import Counter

import psycopg
import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.etl_dpe import adresse_brute
from app.etl_dpe.adresse_brute import classer_document, indexer_adresses
from app.etl_dpe.pipeline import DpePipeline
from tests.integration.test_dpe_source_staging import (
    _migrate,
    _prepare_pipeline,
    _stage_all,
    source_database,  # noqa: F401 -- fixture reused by the wiring test
)

MIGRATION = "104_dpe_rattachement_adresse_brute"


@pytest.fixture(autouse=True)
def _dpe_scope(bind_departements):
    bind_departements(("44", "85"), DpePipeline)


@pytest.fixture(scope="module")
def pg_dsn(isolated_pg_dsn):
    """Keep this module's destructive public fixtures out of the shared schema."""
    return isolated_pg_dsn


# Synthetic BAN (code_departement, ban_id, code_commune, numero, rep, nom_voie, cp).
BAN = (
    ("44", "44109_lila_00012", "44109", "12", None, "Rue des Lilas", "44000"),
    ("44", "44109_lila_00012_bis", "44109", "12", "bis", "Rue des Lilas", "44000"),
    ("44", "44109_lila_00012_b", "44109", "12", "b", "Rue des Lilas", "44000"),
    ("44", "44109_lila_00020", "44109", "20", None, "Rue des Lilas", "44000"),
    ("44", "44109_port_00014", "44109", "14", None, "Avenue du Port", "44000"),
    ("44", "44109_chen_00003", "44109", "3", None, "Allée des Chênes", "44000"),
    # Two streets of the same name in the commune: two candidates.
    ("44", "44109_moul_00005", "44109", "5", None, "Rue du Moulin", "44000"),
    ("44", "44109_mou2_00005", "44109", "5", None, "Rue du Moulin", "44300"),
    ("85", "85191_gare_00001", "85191", "1", None, "Rue de la Gare", "85000"),
)
# numero_dpe -> (departement, ADEME key, adresse_brut, adresse_complete_brut,
# code_postal_brut, nom_commune_brut, tombstone, expected ban_id_adresse_brute)
DOCUMENTS = {
    "T13-ABREGE": ("44", "44109", "12 r des lilas", None, None, None, False,
                   "44109_lila_00012"),
    "T13-BIS": ("44", "44109_lila", "12 bis rue des Lilas", None, None, None,
                False, "44109_lila_00012_bis"),
    "T13-LETTRE": ("44", "44109", "12B Rue des Lilas", None, None, None, False,
                   "44109_lila_00012_b"),
    "T13-PLAGE": ("44", "44109", "12-14 rue des Lilas", None, None, None, False,
                  None),
    "T13-COMPLEMENT": ("44", "44109", "Appartement 3, 14 av du Port", None, None,
                       None, False, None),
    "T13-COMPLETE": ("44", "44109", "Appartement 3, 14 av du Port",
                     "14 av du Port 44000 Nantes", "44000", "Nantes", False,
                     "44109_port_00014"),
    "T13-AUTRE-VOIE": ("44", "44109_xxxx", "3 all des chenes", None, None, None,
                       False, None),
    "T13-DEUX": ("44", "44109", "5 rue du Moulin", None, None, None, False, None),
    "T13-NUMEROTEE": ("44", "44109_LILA_00012", "12 rue des Lilas", None, None,
                      None, False, None),
    "T13-CLE-BAN": ("44", "44109_lila_00020", "12 rue des lilas", None, None,
                    None, False, None),
    "T13-RETIRE": ("44", "44109", "12 rue des lilas", None, None, None, True,
                   None),
    "T13-VENDEE": ("85", "85191", "1 r de la gare", None, None, None, False,
                   "85191_gare_00001"),
    "T13-SANS-NUMERO": ("44", "44109", "rue des Lilas", None, None, None, False,
                        None),
    "T13-LETTRE-DETACHEE": ("44", "44109", "12 b rue des lilas", None, None, None,
                            False, None),
}  # fmt: skip


def _install(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("""
            DROP TABLE IF EXISTS public.adresses CASCADE;
            DROP TABLE IF EXISTS public.diagnostics_dpe_staging CASCADE;
            CREATE TABLE public.adresses (
                id bigserial, code_departement text NOT NULL, ban_id text,
                code_commune text, numero text, rep text, nom_voie text,
                code_postal text, PRIMARY KEY (id, code_departement)
            ) PARTITION BY LIST (code_departement);
            CREATE TABLE public.adresses_44
                PARTITION OF public.adresses FOR VALUES IN ('44');
            CREATE TABLE public.adresses_85
                PARTITION OF public.adresses FOR VALUES IN ('85');
            CREATE TABLE public.diagnostics_dpe_staging (
                numero_dpe text NOT NULL UNIQUE, code_departement text,
                identifiant_ban text, adresse_brut text,
                adresse_complete_brut text, code_postal_brut text,
                nom_commune_brut text, is_tombstone boolean NOT NULL,
                ban_id_adresse_brute text
            );
        """)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO adresses (code_departement, ban_id, code_commune, "
                "numero, rep, nom_voie, code_postal) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                BAN,
            )
            cur.executemany(
                "INSERT INTO diagnostics_dpe_staging (numero_dpe, code_departement, "
                "identifiant_ban, adresse_brut, adresse_complete_brut, "
                "code_postal_brut, nom_commune_brut, is_tombstone) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [(numero, *valeurs[:7]) for numero, valeurs in DOCUMENTS.items()],
            )


@pytest.fixture
def attached(pg_dsn):
    _install(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        counters = adresse_brute.rattacher_candidate(conn)
        conn.commit()
        rows = dict(
            conn.execute(
                "SELECT numero_dpe, ban_id_adresse_brute FROM diagnostics_dpe_staging"
            ).fetchall()
        )
        keys = dict(
            conn.execute(
                "SELECT numero_dpe, identifiant_ban FROM diagnostics_dpe_staging"
            ).fetchall()
        )
        marker = _marker(conn)
    return counters, rows, keys, marker


def _marker(conn) -> str | None:
    return conn.execute(
        "SELECT col_description('diagnostics_dpe_staging'::regclass, attnum) "
        "FROM pg_attribute WHERE attrelid = 'diagnostics_dpe_staging'::regclass "
        "AND attname = 'ban_id_adresse_brute'"
    ).fetchone()[0]


@pytest.mark.parametrize("numero", sorted(DOCUMENTS))
def test_each_case_is_attached_exactly_or_not_at_all(attached, numero):
    _counters, rows, *_ = attached
    assert rows[numero] == DOCUMENTS[numero][7]


def test_the_ademe_key_is_kept_as_published(attached):
    _counters, _rows, keys, _ = attached
    assert keys == {numero: valeurs[1] for numero, valeurs in DOCUMENTS.items()}


def test_counters_name_every_examined_orphan(attached):
    counters, *_ = attached
    assert counters == {
        "orphelins_examines": 12,
        "orphelins_cle_numerotee": 1,
        "rattaches_adresse_brute": 5,
        "indetermines_sans_numero": 1,
        "indetermines_format_non_reconnu": 1,
        "indetermines_plage_numeros": 1,
        "indetermines_aucune_correspondance": 1,
        "indetermines_correspondances_multiples": 1,
        "indetermines_contradiction": 1,
    }


def test_the_candidate_column_says_it_was_computed(attached):
    """The comment travels with the candidate renamed over the served table:
    the API tells a computed zero from a generation never computed."""
    *_rest, marker = attached
    assert marker == adresse_brute.MARQUEUR_CALCUL


def test_the_numbered_key_is_refused_by_the_step_not_by_the_rule():
    """Guard the guard: the rule alone accepts this numbered orphan's
    candidate (its key differs from the BAN key by case only), so only the
    explicit refusal of numbered keys keeps it unattached."""
    _dep, cle, brut, *_ = DOCUMENTS["T13-NUMEROTEE"]
    document = {"identifiant_ban": cle, "adresse_brut": brut}
    ban = [
        dict(zip(("code_departement", "ban_id", "code_commune", "numero", "rep",
                  "nom_voie", "code_postal"), ligne, strict=True))
        for ligne in BAN
    ]  # fmt: skip
    index = indexer_adresses(ban, {("44109", "rue des lilas")})
    assert adresse_brute.sans_contradiction(classer_document(document, index))
    compteurs: Counter[str] = Counter()
    assert list(adresse_brute.rattacher_commune([document], ban, compteurs)) == []
    assert compteurs["orphelins_cle_numerotee"] == 1


def test_without_a_ban_relation_nothing_is_attached(pg_dsn):
    _install(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("DROP TABLE adresses CASCADE")
        # Inherited from the served table by LIKE ... INCLUDING ALL.
        conn.execute(
            "COMMENT ON COLUMN diagnostics_dpe_staging.ban_id_adresse_brute "
            f"IS '{adresse_brute.MARQUEUR_CALCUL}'"
        )
        counters = adresse_brute.rattacher_candidate(conn)
        marker = _marker(conn)
        attached = conn.execute(
            "SELECT count(*) FROM diagnostics_dpe_staging "
            "WHERE ban_id_adresse_brute IS NOT NULL"
        ).fetchone()[0]
        conn.rollback()
    assert counters["ban_absente"] == 1
    assert counters["rattaches_adresse_brute"] == 0
    assert attached == 0
    assert marker is None


def test_a_departement_without_ban_rows_leaves_the_attachment_not_computed(pg_dsn):
    """04.1-REVIEW CR-01: DPE imported before the BAN on an empty base. The
    BAN relation exists but holds no address of a departement carrying DPE:
    computing would serve 0 attached documents as a value; the column must
    stay unmarked (not_available) until a DPE import runs over the BAN."""
    _install(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("TRUNCATE adresses_85")
        conn.execute(
            "COMMENT ON COLUMN diagnostics_dpe_staging.ban_id_adresse_brute "
            f"IS '{adresse_brute.MARQUEUR_CALCUL}'"
        )
        counters = adresse_brute.rattacher_candidate(conn)
        marker = _marker(conn)
        attached = conn.execute(
            "SELECT count(*) FROM diagnostics_dpe_staging "
            "WHERE ban_id_adresse_brute IS NOT NULL"
        ).fetchone()[0]
        conn.rollback()
    assert counters["ban_absente_departements"] == 1
    assert counters["rattaches_adresse_brute"] == 0
    assert attached == 0
    assert marker is None


def test_migration_104_is_in_the_single_linear_chain(alembic_config):
    # 105_dvf_adresse_suffixe (plan 04.1-14) now follows MIGRATION.
    script = ScriptDirectory.from_config(alembic_config)
    heads = script.get_heads()
    assert len(heads) == 1
    chain = {revision.revision for revision in script.walk_revisions("base", heads[0])}
    assert MIGRATION in chain
    assert script.get_revision("105_dvf_adresse_suffixe").down_revision == MIGRATION
    assert (
        script.get_revision(MIGRATION).down_revision
        == "103_commune_indicator_cells_national"
    )


def _columns_and_indexes(connection) -> tuple[set[str], set[str]]:
    columns = set(
        connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'diagnostics_dpe'"
            )
        ).scalars()
    )
    indexes = set(
        connection.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename='diagnostics_dpe'")
        ).scalars()
    )
    return columns, indexes


def test_migration_104_roundtrip(source_database):  # noqa: F811
    connection = source_database
    _migrate(connection)
    for _ in range(2):
        _migrate(connection, name=MIGRATION)
        columns, indexes = _columns_and_indexes(connection)
        assert "ban_id_adresse_brute" in columns
        assert "idx_dpe_ban_id_adresse_brute" in indexes
        assert connection.execute(
            text("SELECT count(*) FILTER (WHERE ban_id_adresse_brute IS NOT NULL) "
                 "FROM diagnostics_dpe")
        ).scalar_one() == 0  # fmt: skip
        _migrate(connection, "downgrade", name=MIGRATION)
        columns, indexes = _columns_and_indexes(connection)
        assert "ban_id_adresse_brute" not in columns
        assert "idx_dpe_ban_id_adresse_brute" not in indexes


def test_the_import_swap_attaches_an_orphan_on_the_served_relation(
    source_database,  # noqa: F811
    pg_dsn,
    monkeypatch,
    tmp_path,
):
    """Real four-pair staging and swap: the step runs on the candidate and
    its column and partial index reach the served relation."""
    connection = source_database
    orphan = "fixture-dpe03existant-44-0"

    def with_raw_address(row, _slug):
        if row["numero_dpe"] == orphan:
            row.update(
                identifiant_ban="44109",
                adresse_brut="12 r des lilas",
                adresse_complete_brut=None,
            )

    pipeline = _prepare_pipeline(
        connection, pg_dsn, monkeypatch, tmp_path, with_raw_address
    )
    connection.commit()
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute("""
            DROP TABLE IF EXISTS public.adresses CASCADE;
            CREATE TABLE public.adresses (
                id bigserial, code_departement text NOT NULL, ban_id text,
                code_commune text, numero text, rep text, nom_voie text,
                code_postal text, PRIMARY KEY (id, code_departement)
            ) PARTITION BY LIST (code_departement);
            CREATE TABLE public.adresses_44
                PARTITION OF public.adresses FOR VALUES IN ('44');
            CREATE TABLE public.adresses_85
                PARTITION OF public.adresses FOR VALUES IN ('85');
            INSERT INTO public.adresses (code_departement, ban_id, code_commune,
                numero, rep, nom_voie, code_postal)
            VALUES ('44', '44109_lila_00012', '44109', '12', NULL,
                    'Rue des Lilas', '44000'),
                   -- The fixture also carries 85 documents: a BAN of 85 must
                   -- exist for the step to compute (04.1-REVIEW CR-01).
                   ('85', '85191_quai_00001', '85191', '1', NULL,
                    'Quai Sans Rapport', '85000');
        """)
    try:
        _stage_all(pipeline, pg_dsn)
        pipeline.swap()
        stored = dict(
            connection.execute(
                text(
                    "SELECT numero_dpe, ban_id_adresse_brute FROM diagnostics_dpe "
                    "WHERE ban_id_adresse_brute IS NOT NULL"
                )
            ).all()
        )
        key = connection.execute(
            text("SELECT identifiant_ban FROM diagnostics_dpe WHERE numero_dpe = :n"),
            {"n": orphan},
        ).scalar_one()
        _columns, indexes = _columns_and_indexes(connection)
        connection.commit()
        marker = connection.execute(
            text(
                "SELECT col_description('diagnostics_dpe'::regclass, attnum) "
                "FROM pg_attribute WHERE attrelid = 'diagnostics_dpe'::regclass "
                "AND attname = 'ban_id_adresse_brute'"
            )
        ).scalar_one()
        connection.commit()
        assert marker == adresse_brute.MARQUEUR_CALCUL
        assert stored == {orphan: "44109_lila_00012"}
        assert key == "44109"
        assert "idx_dpe_ban_id_adresse_brute" in indexes
        assert pipeline.swap_counters()["rattaches_adresse_brute"] == 1
    finally:
        pipeline.close()
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS public.adresses CASCADE")
