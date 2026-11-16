"""Row-level semantics of the Rule 2 widening on rnb_liens (Lot C1).

``app.logements.queries.cles_ban_batiment_rnb`` decides everything it decides
IN SQL: which buildings belong to the parcel, which of them carry the resolved
BAN key, and which keys that set of carriers adds. None of that can be proven
with a fabricated result set -- a fake session would only replay whatever rows
the test itself wrote. So the five load-bearing properties are proven here,
against a REAL PostGIS carrying real ``TEXT[]`` values and the real GIN
containment operator:

1. building partition -- the "Guerande" regression (2026-08-04): two buildings
   on one parcel must never merge their keys;
2. multi-carrier UNION (product decision 2026-08-05): when several RNB
   buildings carry the SAME resolved key, every carrier's keys are returned,
   not the first carrier's -- the live RNB call this replaced was
   order-dependent and under-collecting;
3. parcel scoping: a building carrying the same key on ANOTHER parcel widens
   nothing (the ``cle_seule`` variant the equivalence gate measured as
   strictly worse, 204 vs 218 identical fiches);
4. the resolved key is never in the result (contract of
   ``marque_rattachement_ban``), and an unknown key widens nothing;
5. the width ceiling recalibrated 2026-08-06 counts each CARRIER separately,
   never their union: two narrow carriers widen even when their union is wide
   (the multi-wing residence the union decision protects), one carrier past
   the width threshold cancels everything. The width is measured in the SQL
   (``largeurs`` CTE) and the ceiling applied in the Python consuming it, so
   only a real query joins the two halves. The ceiling itself is pinned
   exhaustively offline in ``tests/test_logements_plafond_largeur.py``.

Synthetic rows only -- ids are fabricated, no real address is stored. They are
written inside the session's own transaction and rolled back on teardown, so
nothing survives the test even before the container is destroyed.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/run_contract_integration.ps1 \
        tests/integration/test_rnb_liens_elargissement.py
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.logements.queries import cles_ban_batiment_rnb

# Parcel carrying TWO distinct buildings at two street numbers (the shape of
# the 2026-08-04 regression).
PARCELLE = "44109000HN0129"
# Parcel carrying ONE entrance shared by two building footprints (multi-wing
# residence) -- the multi-carrier case.
PARCELLE_MULTI = "44109000HN0130"
# Third parcel, only there to prove the parcel scoping bites.
PARCELLE_AILLEURS = "44109000HN0131"
# Fourth parcel: two carriers of one key whose UNION is wide (5 keys) while
# neither carrier is -- the shape that proves the ceiling counts per carrier.
PARCELLE_LARGE = "44109000HN0132"
# Fifth parcel: ONE carrier of 10 keys, one past the width ceiling -- the
# gallery-artefact shape (angle case measured at 16 keys over 4 streets).
PARCELLE_GALERIE = "44109000HN0133"

CLE_A1 = "44109_3912_00001"
CLE_A2 = "44109_3236_00002"
CLE_B1 = "44109_3912_00003"
CLE_B2 = "44109_3236_00004"
CLE_PARTAGEE = "44109_5000_00010"
CLE_AILE_1 = "44109_5000_00011"
CLE_AILE_2 = "44109_5000_00012"
CLE_HORS_PARCELLE = "44109_9999_00077"
CLE_INCONNUE = "44109_0000_00000"
# PARCELLE_LARGE: one shared entrance, two carriers of 2 extra keys each --
# 3 keys per carrier, 5 for the union. Under the 2026-08-05 ceiling the union
# crossed the key threshold and cancelled everything; under the per-carrier
# width ceiling both carriers are narrow and the widening must survive.
CLE_LARGE_PARTAGEE = "44109_6000_00001"
CLES_LARGE_AILE_1 = ("44109_6000_00003", "44109_6001_00005")
CLES_LARGE_AILE_2 = ("44109_6000_00007", "44109_6001_00009")
# PARCELLE_GALERIE: one carrier of 10 keys (the entrance + 9 others), one past
# the 9-key width ceiling.
CLE_GALERIE_ENTREE = "44109_7000_00001"
CLES_GALERIE_AUTRES = tuple(f"44109_7000_{n:05d}" for n in range(3, 21, 2))

# (id_rnb, cle_ban, parcelles)
_LIENS: tuple[tuple[str, str, list[str]], ...] = (
    # Building A of PARCELLE: corner building, two entrances.
    ("BATA00000001", CLE_A1, [PARCELLE]),
    ("BATA00000001", CLE_A2, [PARCELLE]),
    # Building B, same parcel, another street number: its DPE are NOT A's.
    ("BATB00000002", CLE_B1, [PARCELLE]),
    ("BATB00000002", CLE_B2, [PARCELLE]),
    # Two footprints of PARCELLE_MULTI sharing ONE entrance.
    ("MULT00000001", CLE_PARTAGEE, [PARCELLE_MULTI]),
    ("MULT00000001", CLE_AILE_1, [PARCELLE_MULTI]),
    ("MULT00000002", CLE_PARTAGEE, [PARCELLE_MULTI]),
    ("MULT00000002", CLE_AILE_2, [PARCELLE_MULTI]),
    # A building on ANOTHER parcel that happens to carry A's key too.
    ("HORS00000003", CLE_A1, [PARCELLE_AILLEURS]),
    ("HORS00000003", CLE_HORS_PARCELLE, [PARCELLE_AILLEURS]),
    # Two carriers of one entrance on PARCELLE_LARGE, 2 extra keys each.
    ("LARG00000001", CLE_LARGE_PARTAGEE, [PARCELLE_LARGE]),
    ("LARG00000001", CLES_LARGE_AILE_1[0], [PARCELLE_LARGE]),
    ("LARG00000001", CLES_LARGE_AILE_1[1], [PARCELLE_LARGE]),
    ("LARG00000002", CLE_LARGE_PARTAGEE, [PARCELLE_LARGE]),
    ("LARG00000002", CLES_LARGE_AILE_2[0], [PARCELLE_LARGE]),
    ("LARG00000002", CLES_LARGE_AILE_2[1], [PARCELLE_LARGE]),
    # One 10-key carrier on PARCELLE_GALERIE.
    *(
        ("GALE00000001", cle, [PARCELLE_GALERIE])
        for cle in (CLE_GALERIE_ENTREE, *CLES_GALERIE_AUTRES)
    ),
)

_INSERT = text("""
    INSERT INTO rnb_liens (id_rnb, cle_ban, code_departement, statut, parcelles)
    VALUES (:id_rnb, :cle_ban, '44', 'constructed', :parcelles)
""")


@pytest.fixture()
def _schema_at_head(alembic_config: AlembicConfig) -> None:
    """Migrate the shared disposable DB to head (idempotent no-op if there).

    ``rnb_liens`` is created by migration 079 -- the query under test has no
    table to read otherwise.
    """
    command.upgrade(alembic_config, "head")


@pytest.fixture()
async def session_rnb(
    pg_dsn: str, _schema_at_head: None
) -> AsyncIterator[AsyncSession]:
    """AsyncSession preloaded with the synthetic links, rolled back after.

    The rows are inserted on the SAME connection the query runs on and never
    committed: the test sees them, no other connection ever does, and teardown
    cannot leave residue behind.
    """
    engine = create_async_engine(
        pg_dsn.replace("postgresql://", "postgresql+asyncpg://")
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for id_rnb, cle_ban, parcelles in _LIENS:
                await session.execute(
                    _INSERT,
                    {"id_rnb": id_rnb, "cle_ban": cle_ban, "parcelles": parcelles},
                )
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


async def test_cloisonnement_entre_batiments_de_la_meme_parcelle(session_rnb):
    """Regression 2026-08-04: A's fiche must never inherit B's keys.

    Measured in production right after the R4-02 deploy: the fiche of ``1 rue
    de Guerande`` (3 appartement DPE) displayed 9 cards because the widening
    pulled in the 8 DPE of ``3 rue de Guerande``, another building on the same
    parcel. The widening unit is the BUILDING, never the parcel.
    """
    cles = await cles_ban_batiment_rnb(session_rnb, PARCELLE, CLE_A1)

    assert cles == (CLE_A2,)
    assert CLE_B1 not in cles
    assert CLE_B2 not in cles


async def test_le_batiment_voisin_reste_isole(session_rnb):
    """Symmetric: entering by B's key widens to B only."""
    cles = await cles_ban_batiment_rnb(session_rnb, PARCELLE, CLE_B1)

    assert cles == (CLE_B2,)


async def test_multi_porteur_rend_l_union_de_tous_les_porteurs(session_rnb):
    """Product decision 2026-08-05: UNION, not the first carrier.

    Two footprints share one entrance. The live RNB call returned the other
    keys of whichever building the API listed first -- non deterministic and
    under-collecting. The local join takes every carrier: deterministic, and a
    superset, so no card can be lost. Over-collection stays contained
    downstream by the street-number filter.
    """
    cles = await cles_ban_batiment_rnb(session_rnb, PARCELLE_MULTI, CLE_PARTAGEE)

    assert cles == (CLE_AILE_1, CLE_AILE_2)


async def test_le_cadrage_parcelle_ecarte_un_porteur_d_une_autre_parcelle(session_rnb):
    """A carrier of the same key on ANOTHER parcel widens nothing here.

    This is the difference between the retained variant and the parcel-free
    ``cle_seule`` one: without the ``batiments_parcelle`` CTE, CLE_A1 would
    also drag in the keys of the building standing on PARCELLE_AILLEURS.
    """
    cles = await cles_ban_batiment_rnb(session_rnb, PARCELLE, CLE_A1)

    assert CLE_HORS_PARCELLE not in cles

    # ... and entering by that other parcel does widen to it: the scoping is a
    # filter on the parcel asked for, not a blanket exclusion of that building.
    ailleurs = await cles_ban_batiment_rnb(session_rnb, PARCELLE_AILLEURS, CLE_A1)
    assert ailleurs == (CLE_HORS_PARCELLE,)


async def test_cle_absente_de_la_parcelle_n_elargit_rien(session_rnb):
    """No carrier -> (), i.e. the strict single-key behaviour, never an error."""
    assert await cles_ban_batiment_rnb(session_rnb, PARCELLE, CLE_INCONNUE) == ()
    assert await cles_ban_batiment_rnb(session_rnb, "44109000ZZ9999", CLE_A1) == ()


async def test_le_plafond_se_compte_par_porteur_jamais_sur_l_union(session_rnb):
    """Width ceiling (2026-08-06) x multi-carrier union: one interaction, one test.

    Two carriers share the entrance, 3 keys each, 5 for their union: the union
    is the multi-wing residence the union decision protects, so it must widen
    -- capping it was exactly what the 2026-08-05 ceiling got wrong. One
    10-key carrier, past the width threshold, cancels everything instead.
    Only a real query can show which set the ceiling is applied to, because
    the width is measured in the SQL (``largeurs`` CTE, whole-table count)
    and the ceiling applied in the Python that consumes it -- a fabricated
    result set would beg the question.

    Fail-closed on the WIDENING only: ``()`` is exactly the strict single-key
    behaviour, the resolved key keeps attaching its own diagnostics.
    """
    assert await cles_ban_batiment_rnb(
        session_rnb, PARCELLE_LARGE, CLE_LARGE_PARTAGEE
    ) == tuple(sorted((*CLES_LARGE_AILE_1, *CLES_LARGE_AILE_2)))

    assert (
        await cles_ban_batiment_rnb(session_rnb, PARCELLE_GALERIE, CLE_GALERIE_ENTREE)
        == ()
    )


async def test_la_cle_demandee_n_est_jamais_dans_le_resultat(session_rnb):
    """Contract of marque_rattachement_ban: ``exact`` vs ``elargi_batiment``."""
    for parcelle, cle in (
        (PARCELLE, CLE_A1),
        (PARCELLE, CLE_B1),
        (PARCELLE_MULTI, CLE_PARTAGEE),
    ):
        assert cle not in await cles_ban_batiment_rnb(session_rnb, parcelle, cle)


# -- Publication guard (04.7-19 review, Opus P2) ------------------------------
#
# ``rnb_liens`` is swapped like ``bdnb_batiment_parcelle``: when a step after
# the swap fails, the swapped table stays in place while its publication is
# ``failed``. The state is written here in the session's transaction: the last
# acted publication names a table OID that is no longer the live one (the
# table was swapped since), or the very OID of the live table.

_PUBLIE = text("""
    INSERT INTO etl_publications
        (generation, dataset_name, table_name, status, relation_oids)
    VALUES (gen_random_uuid(), 'rnb_liens', 'rnb_liens', :status,
            jsonb_build_object('rnb_liens',
                               to_regclass('rnb_liens')::oid::bigint + :decalage))
""")


async def _journal(session, *publications: tuple[str, int]) -> None:
    for status, decalage in publications:
        await session.execute(_PUBLIE, {"status": status, "decalage": decalage})


async def test_une_table_echangee_non_actee_n_elargit_rien(session_rnb):
    """The last acted publication is another table (swapped since, then a
    failed publication): the links are not read, nothing is widened.

    Mutation it must catch: the guard of ``cles_ban_batiment_rnb`` removed
    (the widening then reads the swapped table, ``(CLE_A2,)``)."""
    await _journal(session_rnb, ("published", 1), ("failed", 0))

    assert await cles_ban_batiment_rnb(session_rnb, PARCELLE, CLE_A1) == ()


async def test_la_table_actee_elargit_meme_apres_un_refus(session_rnb):
    """The live table is the acted one; a later run refused before its swap
    (``failed``) does not withdraw it."""
    await _journal(session_rnb, ("published", 0), ("failed", 0))

    assert await cles_ban_batiment_rnb(session_rnb, PARCELLE, CLE_A1) == (CLE_A2,)


async def test_le_bloc_rnb_d_une_table_non_actee_est_en_panne(session_rnb):
    """The address page and the report read ``rnb_liens`` through
    ``lire_bloc``: a table not acted is the outage of the block
    (``publication_non_validee``), never a count."""
    from app.adresse.blocs import lire_bloc

    await _journal(session_rnb, ("published", 1))

    async def lecteur() -> int:
        return len((await session_rnb.execute(text("SELECT 1 FROM rnb_liens"))).all())

    bloc = await lire_bloc(session_rnb, ["rnb"], lecteur)
    assert (bloc.panne, bloc.valeur) == ("publication_non_validee", None)


_PUBLIE_OIDS = text("""
    INSERT INTO etl_publications
        (generation, dataset_name, table_name, status, relation_oids)
    VALUES (gen_random_uuid(), 'rnb_liens', 'rnb_liens', 'published',
            CAST(:oids AS jsonb)
              || CASE WHEN :avec_table
                      THEN jsonb_build_object('rnb_liens',
                                              to_regclass('rnb_liens')::oid::bigint)
                      ELSE '{}'::jsonb END)
""")


@pytest.mark.parametrize(
    "oids,avec_table,attendu",
    [
        # A publication that names no OID proves nothing about the live table.
        ("{}", False, ()),
        # Only another relation, whose OID matches: still nothing on rnb_liens.
        ('{"spatial_ref_sys": %d}', False, ()),
        # Another relation that has moved does not withdraw rnb_liens.
        ('{"autre_relation": 1}', True, (CLE_A2,)),
    ],
    ids=["sans_oid", "autre_table_seule", "autre_table_deplacee"],
)
async def test_seul_l_oid_de_la_table_lue_decide(
    session_rnb, oids, avec_table, attendu
):
    """The acted publication must carry the OID of ``rnb_liens`` itself; the
    other relations of its document decide nothing for it."""
    if "%d" in oids:
        oid = (
            await session_rnb.execute(
                text("SELECT to_regclass('spatial_ref_sys')::oid")
            )
        ).scalar_one()
        oids = oids % oid
    await session_rnb.execute(_PUBLIE_OIDS, {"oids": oids, "avec_table": avec_table})

    assert await cles_ban_batiment_rnb(session_rnb, PARCELLE, CLE_A1) == attendu


async def test_le_verrou_de_la_garde_fait_attendre_l_echange(
    pg_dsn: str, _schema_at_head: None
):
    """Between the guard and the read, no swap can slip in: the guard holds
    ACCESS SHARE on ``rnb_liens`` until the end of the reader's transaction,
    so the RENAME of a swap, on another connection, waits for it. A fresh
    session that runs the guard only: no other lock of the reader.

    Mutation it must catch: the LOCK of ``publication_actee`` removed (the
    RENAME then goes through while the reader is between check and read)."""
    import psycopg

    from app.common.imported_tables import publication_actee

    engine = create_async_engine(
        pg_dsn.replace("postgresql://", "postgresql+asyncpg://")
    )
    try:
        async with async_sessionmaker(engine)() as lecteur:
            await publication_actee(lecteur, ["rnb_liens"])
            with psycopg.connect(pg_dsn) as autre:
                autre.execute("SET lock_timeout = '300ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    autre.execute("ALTER TABLE rnb_liens RENAME TO rnb_liens_echange")
                autre.rollback()
            await lecteur.rollback()
    finally:
        await engine.dispose()
