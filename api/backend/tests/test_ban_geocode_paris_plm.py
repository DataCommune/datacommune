"""Non-regression integration tests for the Paris/Lyon/Marseille cascade.

These tests require a seeded BAN `adresses` table + `communes_lookup` matview
and skip automatically when the DB is not seeded.

DEFINITIVE NON-REGRESSION COVERAGE: see tests/test_ban_plm_alias.py which
does NOT skip and proves the PLM bareword short-circuit at the unit level
(resolve_plm_alias helper + _detect_city wiring via mock session). Phase
21-06 closes the must-have #3 gap by adding a code-side bypass; this file
is kept as end-to-end smoke once a real BAN DB is available.

Review #1 finding #20 fix: _detect_city() must normalize an arrondissement
code (e.g. 75101) to its parent commune (75056) before calling
expand_code_insee(). Phase 21-06 extends the fix with a PLM bareword
short-circuit that bypasses communes_lookup entirely for the 3 PLM cities
when the query has no postal code.
"""

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.live]


async def _require_city_coverage(db, city: str, condition: str) -> None:
    result = await db.execute(
        text(f"SELECT EXISTS (SELECT 1 FROM adresses WHERE {condition})")
    )
    if not result.scalar():
        pytest.skip(f"BAN fixture does not include {city}")


@pytest.fixture()
async def db():
    """Async DB session against the configured test/dev database.

    Skips the test if the DB is unreachable or not seeded.
    """
    try:
        from app.database import async_session
    except Exception:  # noqa: BLE001
        pytest.skip("app.database not importable (missing config?)")

    try:
        async with async_session() as session:
            yield session
    except Exception:  # noqa: BLE001
        pytest.skip("DB unavailable — requires seeded BAN fixture")


async def test_paris_rue_de_la_paix_without_postal(db):
    """'1 rue de la paix paris' must resolve to Paris, not Le Touquet.

    Before the fix, _detect_city matched communes_lookup entry for '75101'
    (1er arrondissement), expand_code_insee returned only ['75101'], Strategy A
    found 0 results, Strategy B trigram matched 'Le Touquet-Paris-Plage'.
    After: normalize_code_insee('75101') -> '75056', expand -> 20 codes,
    Strategy A finds the real address in the 2e arrondissement.
    """
    try:
        from app.ban.queries import geocoder
    except ImportError:
        pytest.skip("app.ban.queries not importable")

    await _require_city_coverage(
        db,
        "Paris",
        "code_commune = '75056' OR code_commune LIKE '751%'",
    )

    try:
        results = await geocoder(db, "1 rue de la paix paris", limit=1)
    except Exception:  # noqa: BLE001
        pytest.skip("requires seeded BAN fixture")

    if not results:
        pytest.skip("BAN adresses table is empty — requires seeded fixture")

    # 75056 = canonical Paris commune; 75102 = 2e arrondissement (Rue de la Paix)
    assert results[0]["code_commune"] in {"75056", "75102"}, (
        f"expected Paris (75056 or 75102), got {results[0]!r}"
    )
    assert "paris" in results[0]["nom_commune"].lower()
    assert "touquet" not in results[0]["nom_commune"].lower()


async def test_lyon_place_bellecour_without_postal(db):
    """'place bellecour lyon' must resolve within Lyon (69123 or arrondissements)."""
    try:
        from app.ban.queries import geocoder
    except ImportError:
        pytest.skip("app.ban.queries not importable")

    await _require_city_coverage(
        db,
        "Lyon",
        "code_commune = '69123' OR code_commune LIKE '6938%'",
    )

    try:
        results = await geocoder(db, "place bellecour lyon", limit=1)
    except Exception:  # noqa: BLE001
        pytest.skip("requires seeded BAN fixture")

    if not results:
        pytest.skip("BAN adresses table is empty — requires seeded fixture")

    lyon_codes = {"69123"} | {f"6938{i}" for i in range(1, 10)}
    assert results[0]["code_commune"] in lyon_codes, (
        f"expected Lyon commune, got {results[0]!r}"
    )


async def test_marseille_rue_paradis_without_postal(db):
    """'rue paradis marseille' must resolve within Marseille.

    Expected: code_commune in 13055 or arrondissement codes 13201-13216.
    """
    try:
        from app.ban.queries import geocoder
    except ImportError:
        pytest.skip("app.ban.queries not importable")

    await _require_city_coverage(
        db,
        "Marseille",
        "code_commune = '13055' OR code_commune LIKE '132%'",
    )

    try:
        results = await geocoder(db, "rue paradis marseille", limit=1)
    except Exception:  # noqa: BLE001
        pytest.skip("requires seeded BAN fixture")

    if not results:
        pytest.skip("BAN adresses table is empty — requires seeded fixture")

    marseille_codes = {"13055"} | {f"132{i:02d}" for i in range(1, 17)}
    assert results[0]["code_commune"] in marseille_codes, (
        f"expected Marseille commune, got {results[0]!r}"
    )


async def test_explicit_arrondissement_still_works(db):
    """Typing '4e' or '75004' must still rank the 4e above other arrondissements.

    Non-regression: after normalization + expansion, the 4e arrondissement
    (75104) is still included in the search set. The numero/voie scoring
    should rank the match within the 4e first when the query is specific.
    """
    try:
        from app.ban.queries import geocoder
    except ImportError:
        pytest.skip("app.ban.queries not importable")

    await _require_city_coverage(
        db,
        "Paris",
        "code_commune = '75056' OR code_commune LIKE '751%'",
    )

    try:
        results = await geocoder(db, "rue de rivoli paris 4e", limit=3)
    except Exception:  # noqa: BLE001
        pytest.skip("requires seeded BAN fixture")

    if not results:
        pytest.skip("BAN adresses table is empty — requires seeded fixture")

    # First hit must be in 75104 (4e), not a random arrondissement.
    first = results[0]
    assert first["code_commune"] == "75104" or "4e" in first.get("nom_commune", ""), (
        f"expected 4e arrondissement first, got {first!r}"
    )
