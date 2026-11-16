"""City detection whatever the punctuation typed (second k6 firing, 2026-11-02).

« 12 rue de la mairie les sables-d olonne »: the commune is « Les
Sables-d'Olonne », typed with a space for the apostrophe. City detection
compared ``unaccent(lower())`` only and missed it; when the full-text search
found nothing the query fell into the national trigram fallback (Strategy D),
cancelled by its 2 s budget: a 503 for 9 of the 300 searches of the firing.
Proven against a real PostGIS at migration head, ``communes_lookup``
refreshed inside the test transaction (as ``test_ban_rep_ordering.py``):

1. a candidate typed with a hyphen or an apostrophe matches the commune
   whatever the punctuation (space, apostrophe, typographic apostrophe, hyphen);
2. a candidate without any punctuation is compared as before: « sainte anne »
   at the end of a street name never becomes the commune Sainte-Anne. On the
   street names of 44 and 85, the narrow rule detects a commune in 0.07 % more
   of them, a rule ignoring punctuation everywhere in 0.87 %;
3. the exact names keep their detection;
4. the search that was a 503 is answered from the commune (Strategy A).

Former communes (quick 261102-xaj): « 12 rue de la mairie ancenis » named
Ancenis, merged in 2019 into Ancenis-Saint-Géréon (44003). No current commune
bears that name: the query fell into Strategy D, a 503 too. The former names
of the INSEE COG are read after the current ones, under the same punctuation
rule:

5. a former name, punctuated or not, names its current commune;
6. a former name typed without its hyphens is compared as before: « saint
   gereon » ending a street name never becomes Saint-Géréon; nor does a
   former name after a linking word or a street type (« chemin de
   saint-gereon »), as in the address search. On the street names of 44 and
   85, the former names detect a commune in 0.19 % more of them;
7. the search that was a 503 is answered from the current commune.

A commune name alone (searches checked on datacommune.fr, 2026-11-09):
« Olonne-sur-Mer », « Château-d'Olonne », « Ancenis-Saint-Géréon » typed with
nothing else answered 503 after 2.1 s. The city detection keeps at least one
token for the street, so the whole query is never compared to the communes:
full-text found nothing and Strategy D was cut by its budget. A query that
names a commune, current or former, whatever its punctuation, holds no street:

8. it never reaches Strategy D and answers no address, without an outage;
9. a query naming no commune still reaches Strategy D;
10. a street followed by a former commune name keeps its detection: it is
    served from the current commune.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 -Paths
        tests/integration/test_detection_commune.py
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ban import queries as ban_queries
from app.ban.queries import GeocodageDefautError, _detect_city, geocoder

# (id, departement, ban_id, nom_voie, code_commune, nom_commune)
_ADRESSES = (
    (
        91_100_001,
        "85",
        "85194_0001_00012",
        "Rue des Mimosas",
        "85194",
        "Les Sables-d'Olonne",
    ),
    (
        91_100_002,
        "85",
        "85191_0001_00012",
        "Rue des Lilas",
        "85191",
        "La Roche-sur-Yon",
    ),
    (91_100_003, "44", "44997_0001_00012", "Rue du Bourg", "44997", "Sainte-Anne"),
    (
        91_100_004,
        "44",
        "44003_0001_00012",
        "Place de la Mairie",
        "44003",
        "Ancenis-Saint-Géréon",
    ),
)
_INSERT = text("""
    INSERT INTO adresses (id, code_departement, ban_id, numero, nom_voie,
                          code_postal, code_commune, nom_commune, position)
    VALUES (:id, :dep, :ban_id, '12', :voie, '85100', :code, :nom,
            ST_SetSRID(ST_MakePoint(-1.78, 46.5), 4326))
""")


@pytest.fixture()
def _schema_at_head(alembic_config: AlembicConfig) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
async def session(pg_dsn: str, _schema_at_head: None) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        pg_dsn.replace("postgresql://", "postgresql+asyncpg://")
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            for id_, dep, ban_id, voie, code, nom in _ADRESSES:
                await session.execute(
                    _INSERT,
                    {
                        "id": id_,
                        "dep": dep,
                        "ban_id": ban_id,
                        "voie": voie,
                        "code": code,
                        "nom": nom,
                    },
                )
            await session.execute(text("REFRESH MATERIALIZED VIEW communes_lookup"))
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "commune",
    ["les sables-d olonne", "les sables-d\u2019olonne", "les-sables-d-olonne"],
)
async def test_a_commune_typed_with_other_punctuation_is_detected(session, commune):
    detectee = await _detect_city(session, f"12 rue de la mairie {commune}")

    assert detectee == ("12 rue de la mairie", commune, ["85194"])


@pytest.mark.parametrize(
    ("requete", "attendu"),
    [
        ("12 rue des mimosas les sables-d'olonne", ["85194"]),
        ("12 rue des lilas la roche-sur-yon", ["85191"]),
        ("12 rue du bourg sainte-anne", ["44997"]),
    ],
)
async def test_exact_names_keep_their_detection(session, requete, attendu):
    detectee = await _detect_city(session, requete)

    assert detectee is not None and detectee[2] == attendu


async def test_a_candidate_without_punctuation_is_compared_as_before(session):
    # « sainte anne » ends a street name: no commune is detected from it.
    assert await _detect_city(session, "12 allee sainte anne") is None


async def test_the_search_that_was_an_outage_is_served_from_the_commune(
    session, monkeypatch
):
    # Strategy D must not be reached: here it fails, as it did past its
    # budget on the national base (full-text finds nothing for the typo).
    monkeypatch.setattr(
        ban_queries, "_STRATEGY_D", text("SELECT 1/0 -- {postal_filter} {numero_order}")
    )

    lignes = await geocoder(
        session,
        "12 rue des mimozas les sables-d olonne",
        8,
        departements_heberges=["85"],
        erreurs="lever",
    )

    assert [ligne["ban_id"] for ligne in lignes] == ["85194_0001_00012"]


@pytest.mark.parametrize("commune", ["ancenis", "saint-gereon", "Saint-Géréon"])
async def test_a_commune_typed_under_its_former_name_is_detected(session, commune):
    detectee = await _detect_city(session, f"12 rue de la mairie {commune}")

    assert detectee == ("12 rue de la mairie", commune, ["44003"])


async def test_a_former_name_without_its_punctuation_is_compared_as_before(session):
    # « saint gereon » ends a street name: Saint-Géréon is not detected from it.
    assert await _detect_city(session, "12 allee saint gereon") is None


@pytest.mark.parametrize("voie", ["chemin de saint-gereon", "chemin saint-gereon"])
async def test_a_former_name_after_a_linking_word_belongs_to_the_street(session, voie):
    assert await _detect_city(session, f"12 {voie}") is None


async def test_the_former_name_search_that_was_an_outage_is_served_from_the_commune(
    session, monkeypatch
):
    # The k6 query of 2026-11-02: no current commune ends it, and full-text
    # finds nothing (Ancenis-Saint-Géréon has no Rue de la Mairie in the BAN):
    # Strategy D failed past its budget on the national base.
    monkeypatch.setattr(
        ban_queries, "_STRATEGY_D", text("SELECT 1/0 -- {postal_filter} {numero_order}")
    )

    lignes = await geocoder(
        session,
        "12 rue de la mairie ancenis",
        8,
        departements_heberges=["44"],
        erreurs="lever",
    )

    assert [ligne["ban_id"] for ligne in lignes] == ["44003_0001_00012"]


@pytest.mark.parametrize(
    "requete",
    [
        "Olonne-sur-Mer",
        "Château-d'Olonne",
        "chateau d olonne",
        "Ancenis-Saint-Géréon",
        "les sables d olonne",
    ],
)
async def test_a_commune_name_alone_never_reaches_strategy_d(
    session, monkeypatch, requete
):
    # Strategy D fails here, as it did past its budget on the national base.
    monkeypatch.setattr(
        ban_queries, "_STRATEGY_D", text("SELECT 1/0 -- {postal_filter} {numero_order}")
    )

    lignes = await geocoder(
        session, requete, 8, departements_heberges=["44", "85"], erreurs="lever"
    )

    # Full-text may still find addresses of the commune; never an outage.
    assert all(ligne["code_commune"] in {"44003", "85194"} for ligne in lignes)


async def test_a_query_naming_no_commune_still_reaches_strategy_d(session, monkeypatch):
    monkeypatch.setattr(
        ban_queries, "_STRATEGY_D", text("SELECT 1/0 -- {postal_filter} {numero_order}")
    )

    # The stub of Strategy D divides by zero: reaching it raises.
    with pytest.raises(GeocodageDefautError):
        await geocoder(
            session, "mimozaz", 8, departements_heberges=["44", "85"], erreurs="lever"
        )


async def test_a_street_and_a_commune_name_keep_the_city_detection(
    session, monkeypatch
):
    monkeypatch.setattr(
        ban_queries, "_STRATEGY_D", text("SELECT 1/0 -- {postal_filter} {numero_order}")
    )

    assert await _detect_city(session, "12 rue des mimozas olonne-sur-mer") == (
        "12 rue des mimozas",
        "olonne-sur-mer",
        ["85194"],
    )
    lignes = await geocoder(
        session,
        "12 rue des mimozas olonne-sur-mer",
        8,
        departements_heberges=["85"],
        erreurs="lever",
    )
    assert [ligne["ban_id"] for ligne in lignes] == ["85194_0001_00012"]
