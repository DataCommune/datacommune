"""A9-01 (plan 04.4-07): ``mutations_grain.valeur_fonciere`` gets the type of
``mutations.valeur_fonciere`` (NUMERIC(15,2), migration 015).

At revision 107 a source amount of 10^10 EUR or more, accepted by
``mutations``, makes the reconstruction of ``mutations_grain`` fail with
``numeric field overflow``; after migration 108 the same reconstruction
passes and keeps the exact amount. The migration recreates the commune view
chain without changing a value of ``v_commune_dvf``, re-anchors the
publications so the publication guard still admits them, and its downgrade
refuses, naming the count, while an amount exceeds NUMERIC(12,2). Fixture
data are synthetic (``SYN-``).

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_migration_108_dvf_grain_valeur_fonciere.py
"""

import asyncio
import importlib.util
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.publication_readiness import guard_publication
from app.config import settings
from app.etl_dvf.reconstruction import reconstruit_mutations_grain

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
AVANT = "107_drop_unused_indexes"
APRES = "108_dvf_grain_valeur_fonciere"
DEPARTEMENTS = ("44", "85")
COMMUNE = "44109"
# Above NUMERIC(12,2) (max 9 999 999 999,99), within NUMERIC(15,2).
GRAND_MONTANT = Decimal("12345678901.00")


def _config(dsn: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


def _alembic(dsn: str, action: str, revision: str) -> None:
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", dsn)
        context.setattr(settings, "database_url", dsn)
        getattr(command, action)(_config(dsn), revision)


@pytest.fixture(scope="module")
def base(isolated_pg_dsn):
    _alembic(isolated_pg_dsn, "upgrade", AVANT)
    return isolated_pg_dsn


def _vente(
    conn, id_mutation: str, valeur: Decimal, surface: int = 80, type_local="1"
) -> None:
    """One source line: a dwelling sold alone, a mono-local residential sale."""
    conn.execute(
        "INSERT INTO mutations (code_departement, id_mutation, date_mutation, "
        "nature_mutation, valeur_fonciere, code_type_local, code_commune, "
        "nom_commune, id_parcelle, surface_reelle_bati, nombre_pieces_principales, "
        "numero_disposition, adresse_numero, adresse_nom_voie, code_postal) VALUES "
        "('44', %s, '2025-03-01', 'Vente', %s, %s, %s, 'Commune synthetique', "
        "%s, %s, 4, '1', '1', 'RUE SYNTHETIQUE', '44000')",
        (
            id_mutation,
            valeur,
            type_local,
            COMMUNE,
            f"{COMMUNE}000AA{id_mutation[-4:]}",
            surface,
        ),
    )


def _type(conn, table: str) -> str:
    return conn.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = %s::regclass AND attname = 'valeur_fonciere' "
        "AND NOT attisdropped",
        (table,),
    ).fetchone()[0]


def _chaine_106() -> tuple[str, ...]:
    """The commune chain frozen by 106, read from its file as 108 reads it."""
    path = BACKEND / "migrations" / "versions" / "106_eligibilite_fixe_par_immeuble.py"
    spec = importlib.util.spec_from_file_location("migration_106", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STATEMENTS


def _reconstruire_et_publier(dsn: str) -> None:
    """Reconstruction, then the chain rebuild of a base at 107 or 108.

    Not the application chain: it gained ``v_departement_synthese`` with 112
    (plan 04.7-10), a view reading ``mutations_grain`` that 108 does not know
    and must refuse. A publication at 107 or 108 rebuilt the chain of 106.
    """
    reconstruit_mutations_grain(dsn, DEPARTEMENTS)
    with psycopg.connect(dsn) as conn:
        for statement in _chaine_106():
            conn.execute(statement)


def test_108_follows_107_on_the_single_chain():
    script = ScriptDirectory.from_config(_config("postgresql://unused"))
    heads = script.get_heads()
    assert len(heads) == 1
    assert APRES in {rev.revision for rev in script.walk_revisions("base", heads[0])}
    assert script.get_revision(APRES).down_revision == AVANT


def test_a_source_amount_of_ten_billion_passes_the_reconstruction_after_108(base):
    with psycopg.connect(base) as conn:
        assert _type(conn, "mutations") == "numeric(15,2)"
        assert _type(conn, "mutations_grain") == "numeric(12,2)"
        _vente(conn, "SYN-A901-0001", GRAND_MONTANT)

    # A9-01 observed at revision 107: `mutations` holds it, the grain refuses.
    with pytest.raises(psycopg.errors.NumericValueOutOfRange) as refus:
        reconstruit_mutations_grain(base, DEPARTEMENTS)
    assert "numeric field overflow" in str(refus.value)

    # 108 and not head: the module's tests step between 107 and 108 only.
    _alembic(base, "upgrade", APRES)
    reconstruit_mutations_grain(base, DEPARTEMENTS)
    with psycopg.connect(base) as conn:
        rows = conn.execute(
            "SELECT id_mutation, valeur_fonciere, classe_mutation FROM mutations_grain"
        ).fetchall()
        assert _type(conn, "mutations_grain") == _type(conn, "mutations")
    assert rows == [("SYN-A901-0001", GRAND_MONTANT, "residentiel_maison")]


# Every view reading `mutations_grain`, directly or through another view.
_VUES_DEPENDANTES = """
    WITH RECURSIVE dependantes(oid) AS (
        SELECT r.ev_class FROM pg_depend d
        JOIN pg_rewrite r ON r.oid = d.objid
        WHERE d.classid = 'pg_rewrite'::regclass
          AND d.refobjid = 'mutations_grain'::regclass
          AND r.ev_class <> d.refobjid
        UNION
        SELECT r.ev_class FROM dependantes v
        JOIN pg_depend d ON d.refobjid = v.oid AND d.classid = 'pg_rewrite'::regclass
        JOIN pg_rewrite r ON r.oid = d.objid
        WHERE r.ev_class <> v.oid
    )
    SELECT c.relname, c.oid::bigint FROM dependantes v JOIN pg_class c ON c.oid = v.oid
"""
# Latest publication of two datasets naming relations of the commune chain.
PUBLICATIONS = {
    "dvf": (
        "mutations_grain",
        [
            "mutations",
            "mutations_grain",
            "mutation_parcelles",
            "v_commune_dvf",
            "v_commune_synthese",
        ],
    ),
    "dpe": (
        "diagnostics_dpe",
        ["diagnostics_dpe", "v_commune_dpe", "v_commune_synthese"],
    ),
}


_FICHIER_44 = "SELECT pg_relation_filenode('mutations_grain_44')"


def _vues_dependantes(conn) -> dict[str, int]:
    return dict(conn.execute(_VUES_DEPENDANTES).fetchall())


def _dvf(conn) -> list[tuple]:
    return conn.execute("SELECT * FROM v_commune_dvf ORDER BY code_insee").fetchall()


def _publier(conn) -> None:
    for dataset, (table, noms) in PUBLICATIONS.items():
        conn.execute(
            "INSERT INTO etl_publications (generation, dataset_name, table_name, "
            "status, relation_oids) VALUES (gen_random_uuid(), %s, %s, 'published', "
            "(SELECT jsonb_object_agg(n, to_regclass(n)::oid::bigint) "
            "FROM unnest(%s::text[]) n))",
            (dataset, table, noms),
        )


async def _garde(dsn: str, datasets: tuple[str, ...] = tuple(PUBLICATIONS)) -> bool:
    """The publication guard of the served routes admits *datasets*."""
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with async_sessionmaker(engine)() as session:
            try:
                await guard_publication(session, list(datasets))
            except HTTPException:
                return False
            return True
    finally:
        await engine.dispose()


def _reinitialiser(dsn: str) -> None:
    """Empty DVF tables and journal, base back at revision 107."""
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE mutations, mutations_grain, mutation_parcelles")
        conn.execute("DELETE FROM etl_publications")
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    if revision == (APRES,):
        _alembic(dsn, "downgrade", AVANT)


def _ventes_ordinaires(conn) -> None:
    """Six houses and five flats under 10^10 EUR: both medians are computed."""
    for i in range(6):
        _vente(conn, f"SYN-MAIS-{i:04d}", Decimal(200000 + 25000 * i), 80 + 5 * i)
    for i in range(5):
        _vente(conn, f"SYN-APPT-{i:04d}", Decimal(150000 + 10000 * i), 50 + 3 * i, "2")


def test_the_chain_its_values_and_the_publications_survive_both_directions(base):
    _reinitialiser(base)
    with psycopg.connect(base) as conn:
        _ventes_ordinaires(conn)
    _reconstruire_et_publier(base)
    with psycopg.connect(base) as conn:
        _publier(conn)
        avant_vues = _vues_dependantes(conn)
        avant_dvf = _dvf(conn)
        avant_fichier = conn.execute(_FICHIER_44).fetchone()
    assert set(avant_vues) == {"v_commune_dvf", "v_commune_synthese"}
    assert len(avant_dvf) == 1 and None not in avant_dvf[0]
    assert asyncio.run(_garde(base))

    _alembic(base, "upgrade", APRES)
    with psycopg.connect(base) as conn:
        assert _type(conn, "mutations_grain") == "numeric(15,2)"
        apres_vues = _vues_dependantes(conn)
        assert _dvf(conn) == avant_dvf
        # Wider precision, same scale: the partition is not rewritten.
        assert conn.execute(_FICHIER_44).fetchone() == avant_fichier
    assert set(apres_vues) == set(avant_vues)
    # Recreated views: without re-anchoring the guard would refuse both.
    assert all(apres_vues[nom] != avant_vues[nom] for nom in avant_vues)
    assert asyncio.run(_garde(base))

    _alembic(base, "downgrade", AVANT)
    with psycopg.connect(base) as conn:
        assert _type(conn, "mutations_grain") == "numeric(12,2)"
        assert set(_vues_dependantes(conn)) == set(avant_vues)
        assert _dvf(conn) == avant_dvf
    assert asyncio.run(_garde(base))


def test_the_downgrade_refuses_while_an_amount_exceeds_numeric_12_2(base):
    _reinitialiser(base)
    _alembic(base, "upgrade", APRES)
    with psycopg.connect(base) as conn:
        _ventes_ordinaires(conn)
        _vente(conn, "SYN-A901-0001", GRAND_MONTANT)
    _reconstruire_et_publier(base)
    with psycopg.connect(base) as conn:
        _publier(conn)
        avant_vues = _vues_dependantes(conn)
        avant_dvf = _dvf(conn)

    with pytest.raises(RuntimeError, match=r"^Downgrade refused: 1 mutations_grain "):
        _alembic(base, "downgrade", AVANT)

    with psycopg.connect(base) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            APRES,
        )
        assert _type(conn, "mutations_grain") == "numeric(15,2)"
        # Same OIDs: nothing was dropped nor recreated.
        assert _vues_dependantes(conn) == avant_vues
        assert _dvf(conn) == avant_dvf
    assert asyncio.run(_garde(base))


def test_a_publication_already_stale_stays_refused_after_the_migration(base):
    """WR-03 (review 04.4): only the entries holding the OID read before the
    DROP are re-anchored. A DPE publication made stale before the migration
    (an OID no relation has) stays refused; the DVF one is re-anchored."""
    _reinitialiser(base)
    with psycopg.connect(base) as conn:
        _ventes_ordinaires(conn)
    _reconstruire_et_publier(base)
    with psycopg.connect(base) as conn:
        _publier(conn)
        conn.execute(
            "UPDATE etl_publications SET relation_oids = "
            "jsonb_set(relation_oids, '{v_commune_dpe}', '1') "
            "WHERE dataset_name = 'dpe'"
        )
    assert asyncio.run(_garde(base, ("dvf",)))
    assert not asyncio.run(_garde(base, ("dpe",)))

    _alembic(base, "upgrade", APRES)

    assert asyncio.run(_garde(base, ("dvf",)))
    assert not asyncio.run(_garde(base, ("dpe",)))
    with psycopg.connect(base) as conn:
        stockes = conn.execute(
            "SELECT relation_oids FROM etl_publications WHERE dataset_name = 'dpe'"
        ).fetchone()[0]
        synthese = conn.execute(
            "SELECT 'v_commune_synthese'::regclass::oid::bigint"
        ).fetchone()[0]
    assert stockes["v_commune_dpe"] == 1
    # The entry that held the OID read before the DROP did move.
    assert stockes["v_commune_synthese"] == synthese
