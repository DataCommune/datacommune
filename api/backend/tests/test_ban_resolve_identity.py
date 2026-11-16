"""Resolve's pre-LIMIT number constraints never change autocomplete defaults."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ban import queries

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "template",
    [
        queries._STRATEGY_A,
        queries._STRATEGY_B,
        queries._STRATEGY_C,
        queries._STRATEGY_D,
    ],
)
@pytest.mark.parametrize("mode", ["numbered", "unnumbered"])
async def test_identity_constraints_are_applied_before_the_candidate_limit(
    template, mode
):
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=[SimpleNamespace(_mapping={"ban_id": "synthetic"})]
    )
    params = {
        "query": "synthetic",
        "identity_numero": "12",
        "identity_rep": "b",
        "limit": 21,
    }

    rows = await queries._execute_query(session, template, params, "", "", mode)

    assert rows == [{"ban_id": "synthetic"}]
    sql = str(session.execute.await_args.args[0])
    expected = (
        "btrim(numero) = :identity_numero"
        if mode == "numbered"
        else ("nullif(btrim(numero), '') IS NULL")
    )
    assert expected in sql
    assert sql.index(expected) < sql.index("LIMIT :limit")
    if mode == "numbered":
        assert "lower(btrim(coalesce(rep, ''))) IN ('', :identity_rep)" in sql
        assert session.execute.await_args.args[1]["identity_rep"] == "b"
    assert "AND :identity_query IN (" in sql
    assert sql.index("AND :identity_query IN (") < sql.index("LIMIT :limit")
    assert ":identity_has_postal" in sql
    assert "' arrondissement$'" in sql
    assert "END) IS NOT NULL THEN" in sql


@pytest.mark.parametrize(
    ("query", "number", "repetition", "mode"),
    [
        ("12bis rue synthétique 44170", "12", "bis", "numbered"),
        ("12 b rue synthétique 44170", "12", "b", "numbered"),
        ("lieu synthétique 44170", "None", "", "unnumbered"),
    ],
)
async def test_resolve_enables_number_constraints_for_every_fallback(
    query, number, repetition, mode
):
    execute = AsyncMock(return_value=[])
    city = AsyncMock(return_value=("rue synthétique", "sentinelle", ["44001"]))
    with (
        patch("app.ban.queries._detect_city", city),
        patch("app.ban.queries._execute_query", execute),
    ):
        await queries.geocoder(MagicMock(), query, limit=21, strict_identity=True)

    assert execute.await_count == 2
    city.assert_not_awaited()
    for call in execute.await_args_list:
        assert call.args[5] == mode
        assert call.args[2]["identity_numero"] == number
        assert call.args[2]["identity_rep"] == repetition
        assert call.args[2]["code_postal"] == "44170"
        assert call.args[2]["limit"] == 21
        assert call.args[2]["identity_query"] == queries.normalize_address_text(
            query.replace(" 44170", "")
        )
        assert call.args[2]["identity_has_postal"] is True


async def test_autocomplete_still_returns_numbered_suggestions_for_a_street():
    suggestions = [{"numero": "12"}, {"numero": "14"}]
    execute = AsyncMock(return_value=suggestions)
    with (
        patch("app.ban.queries._detect_city", AsyncMock(return_value=None)),
        patch("app.ban.queries._execute_query", execute),
    ):
        rows = await queries.geocoder(MagicMock(), "rue synthétique 44170", limit=5)

    assert rows == suggestions
    assert execute.await_args.args[5] == ""
    assert "identity_numero" not in execute.await_args.args[2]
