"""SQL-level proof that the geocoder uses `rep` to rank repetition addresses.

Measured 2026-08-07 (internal note MESURE-RESOLVE-REPETITIONS-2026-08-07):
9/14 repetition-bearing corpus addresses got a WRONG ban_id from
/v1/resolve, 7 announced ``resolution: "exact"`` with ``score: 1.0``. Root
cause was double: ``_RE_NUMERO`` swallowed the repetition letter (unit
coverage: tests/test_ban_extract_numero.py), and the SQL selected ``rep``
without ever using it — ``12``, ``12 bis`` and ``12 B`` were tied on both
the trigram score and the numero proximity, left to an arbitrary pick.

The regex half is provable offline; the ranking half only exists inside
PostgreSQL (word_similarity ties + the ORDER BY whitelist injection), so a
fabricated session would replay whatever the test wrote. Proven here against
a real PostGIS at migration head:

1. "12 b"   resolves to the ``b``   row, never the bare or ``bis`` one;
2. "12b"    (glued) resolves identically;
3. "12 bis" resolves to the ``bis`` row — never conflated with ``b``;
4. a bare "12" prefers the bare address over its repetitions;
5. numero proximity still wins over rep matching across numbers;
6. the SQL normalizes a stored uppercase ``rep`` (the ``b`` row is seeded
   as ``'B'``), since BAN CSVs carry the field verbatim.

Strategy A is exercised end-to-end: the synthetic commune is injected into
``communes_lookup`` by refreshing the matview INSIDE the test transaction,
so city detection routes the query through the commune-filtered ranking —
the exact path production fiches take. Synthetic rows only — no corpus
address ever leaves the private file.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/run_contract_integration.ps1 \
        tests/integration/test_ban_rep_ordering.py
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adresse.recherche import rechercher_adresses
from app.ban.queries import geocoder, normalize_address_text, reverse_geocoder

# Synthetic commune, absent from the migration-001 seed rows.
COMMUNE = "Villetest"
CODE_COMMUNE = "44999"

BAN_NU = "44999_0001_00012"
BAN_BIS = "44999_0001_00012_bis"
BAN_B = "44999_0001_00012_b"
BAN_14 = "44999_0001_00014"

# (id, ban_id, numero, rep) — the ``b`` row is stored UPPERCASE on purpose:
# BAN CSVs carry ``rep`` verbatim and the ranking must normalize it in SQL.
# Explicit ids: after the BAN atomic swap (migration 057 state) adresses.id
# has no serving default, exactly like production.
_ADRESSES: tuple[tuple[int, str, str, str | None], ...] = (
    (90_000_001, BAN_NU, "12", None),
    (90_000_002, BAN_BIS, "12", "bis"),
    (90_000_003, BAN_B, "12", "B"),
    (90_000_004, BAN_14, "14", None),
)

# Plan 04.1-15 (D-128): two BAN addresses tied on every ranking criterion
# (same number, street, commune and point: homonymous streets of one
# commune). Inserted with the higher ban_id first, so that the physical
# order contradicts the ban_id order the geocoder must apply.
EX_AEQUO_PREMIER = "44999_0002_00003"
EX_AEQUO_SECOND = "44999_0003_00003"
EX_AEQUO_POINT = (-1.56, 47.21)
_EX_AEQUO: tuple[tuple[int, str], ...] = (
    (90_000_011, EX_AEQUO_SECOND),
    (90_000_012, EX_AEQUO_PREMIER),
)
_INSERT_EX_AEQUO = text("""
    INSERT INTO adresses (id, code_departement, ban_id, numero, rep, nom_voie,
                          code_postal, code_commune, nom_commune, position)
    VALUES (:id, '44', :ban_id, '3', NULL, 'allee des ex aequo',
            '44999', :code_commune, :commune,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
""")

_INSERT = text("""
    INSERT INTO adresses (id, code_departement, ban_id, numero, rep, nom_voie,
                          code_postal, code_commune, nom_commune, position)
    VALUES (:id, '44', :ban_id, :numero, :rep, 'rue des mimosas',
            '44999', :code_commune, :commune,
            ST_SetSRID(ST_MakePoint(-1.55, 47.2), 4326))
""")


@pytest.fixture()
def _schema_at_head(alembic_config: AlembicConfig) -> None:
    """Migrate the shared disposable DB to head (idempotent no-op if there)."""
    command.upgrade(alembic_config, "head")


@pytest.fixture()
async def session_ban(
    pg_dsn: str, _schema_at_head: None
) -> AsyncIterator[AsyncSession]:
    """AsyncSession preloaded with the synthetic addresses, rolled back after.

    ``communes_lookup`` is refreshed on the SAME connection so the synthetic
    commune becomes detectable by ``_detect_city`` (Strategy A routing); the
    refresh sees this transaction's uncommitted rows and is rolled back with
    them — no other connection ever observes the commune.
    """
    engine = create_async_engine(
        pg_dsn.replace("postgresql://", "postgresql+asyncpg://")
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for id_, ban_id, numero, rep in _ADRESSES:
                await session.execute(
                    _INSERT,
                    {
                        "id": id_,
                        "ban_id": ban_id,
                        "numero": numero,
                        "rep": rep,
                        "code_commune": CODE_COMMUNE,
                        "commune": COMMUNE,
                    },
                )
            lon, lat = EX_AEQUO_POINT
            for id_, ban_id in _EX_AEQUO:
                await session.execute(
                    _INSERT_EX_AEQUO,
                    {
                        "id": id_,
                        "ban_id": ban_id,
                        "code_commune": CODE_COMMUNE,
                        "commune": COMMUNE,
                        "lon": lon,
                        "lat": lat,
                    },
                )
            await session.execute(text("REFRESH MATERIALIZED VIEW communes_lookup"))
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


async def _first_ban_id(session: AsyncSession, query: str) -> str:
    rows = await geocoder(session, query, limit=1)
    assert rows, f"geocoder returned nothing for a seeded street ({query!r})"
    return rows[0]["ban_id"]


async def test_la_repetition_lettree_gagne_sur_la_nue_et_sur_bis(session_ban):
    """The measured defect: "12 b" must return the ``b`` address.

    Before the fix the regex swallowed the ``b`` and the three numero-12
    rows tied on every ORDER BY criterion — production returned the bare
    address (or another repetition) as ``exact``/1.0.
    """
    assert await _first_ban_id(session_ban, "12 b rue des mimosas villetest") == BAN_B
    # Uppercase request form, lowercase target — the Python side normalizes.
    assert await _first_ban_id(session_ban, "12 B rue des mimosas villetest") == BAN_B


async def test_la_forme_collee_resout_comme_la_forme_detachee(session_ban):
    """Failure mode 2 of the regex: "12b" lost the numero entirely."""
    assert await _first_ban_id(session_ban, "12b rue des mimosas villetest") == BAN_B


async def test_bis_n_est_jamais_confondu_avec_b(session_ban):
    """ "12 bis" and "12 b" are two distinct BAN addresses, each wins its own."""
    assert (
        await _first_ban_id(session_ban, "12 bis rue des mimosas villetest") == BAN_BIS
    )


async def test_la_demande_nue_prefere_l_adresse_nue(session_ban):
    """A plain "12" must not land on "12 bis"/"12 B" when the bare row exists.

    The empty-repetition case rides the same tiebreak: ``target_rep = ''``
    matches the NULL-rep row only.
    """
    assert await _first_ban_id(session_ban, "12 rue des mimosas villetest") == BAN_NU


async def test_la_proximite_de_numero_prime_sur_la_repetition(session_ban):
    """Rep matching is a tiebreak AMONG same-number rows, never across numbers."""
    assert await _first_ban_id(session_ban, "14 rue des mimosas villetest") == BAN_14


# --- Plan 04.1-15 (D-128): a total order at equal score, on every route ---------

EX_AEQUO = [EX_AEQUO_PREMIER, EX_AEQUO_SECOND]


@pytest.mark.parametrize(
    "query",
    [
        # Strategy A (city detected): street score then number proximity tie.
        "3 allee des ex aequo villetest",
        # Strategy B (no city): full-text score tie.
        "3 allee des ex aequo",
    ],
    ids=["strategie-a", "strategie-b"],
)
async def test_two_addresses_tied_on_score_are_ordered_by_ban_id(session_ban, query):
    """``/v1/geocode`` (and the point of every page of ``/v1/dvf/adresse``,
    ``limit=1``): the lowest ``ban_id`` first, never the physical order."""
    rows = await geocoder(session_ban, query, limit=5)
    assert [row["ban_id"] for row in rows] == EX_AEQUO
    assert await _first_ban_id(session_ban, query) == EX_AEQUO_PREMIER


async def test_the_tie_order_is_the_same_on_resolve_and_the_search(session_ban):
    """``/v1/resolve`` (strict identity) and the address search read the same
    order: one rule for every route of the geocoder."""
    query = "3 allee des ex aequo villetest"
    identite = await geocoder(
        session_ban, normalize_address_text(query), limit=5, strict_identity=True
    )
    recherche = await rechercher_adresses(session_ban, query, ["44"], 5)

    assert [row["ban_id"] for row in identite] == EX_AEQUO
    assert [resultat.ban_id for resultat in recherche] == EX_AEQUO


async def test_the_reverse_geocoder_breaks_a_distance_tie_by_ban_id(session_ban):
    lon, lat = EX_AEQUO_POINT
    rows = await reverse_geocoder(session_ban, lat, lon, limit=5)
    assert [row["ban_id"] for row in rows] == EX_AEQUO
