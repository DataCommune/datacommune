"""Unit tests for common/geo.py -- arrondissement expansion & normalization."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

from app.common.geo import (
    ARRONDISSEMENT_TO_COMMUNE,
    COMMUNE_TO_ARRONDISSEMENTS,
    expand_code_insee,
    normalize_code_insee,
    resolve_location,
)


class TestNormalizeCodeInsee:
    """normalize_code_insee: arrondissement -> commune code."""

    @pytest.mark.parametrize(
        "arrond,commune",
        [
            ("75101", "75056"),
            ("75120", "75056"),
            ("69381", "69123"),
            ("69389", "69123"),
            ("13201", "13055"),
            ("13216", "13055"),
        ],
    )
    def test_arrondissement_mapped(self, arrond, commune):
        assert normalize_code_insee(arrond) == commune

    def test_commune_code_passthrough(self):
        assert normalize_code_insee("44109") == "44109"

    def test_paris_commune_passthrough(self):
        assert normalize_code_insee("75056") == "75056"

    def test_unknown_passthrough(self):
        assert normalize_code_insee("99999") == "99999"


class TestExpandCodeInsee:
    """expand_code_insee: commune -> [commune, arrondissements...]."""

    def test_paris_expands_to_21(self):
        codes = expand_code_insee("75056")
        assert len(codes) == 21  # commune + 20 arrondissements
        assert "75056" in codes
        assert "75101" in codes
        assert "75120" in codes

    def test_lyon_expands_to_10(self):
        codes = expand_code_insee("69123")
        assert len(codes) == 10  # commune + 9 arrondissements
        assert "69123" in codes
        assert "69381" in codes
        assert "69389" in codes

    def test_marseille_expands_to_17(self):
        codes = expand_code_insee("13055")
        assert len(codes) == 17  # commune + 16 arrondissements
        assert "13055" in codes
        assert "13201" in codes
        assert "13216" in codes

    def test_regular_commune_single_element(self):
        codes = expand_code_insee("44109")
        assert codes == ["44109"]

    def test_arrondissement_code_not_expanded(self):
        """An arrondissement code itself should not expand."""
        codes = expand_code_insee("75101")
        assert codes == ["75101"]

    def test_commune_is_first(self):
        """Commune code should always be first in the list."""
        for commune in ("75056", "69123", "13055"):
            codes = expand_code_insee(commune)
            assert codes[0] == commune


class TestMappingConsistency:
    """Verify forward and reverse mappings are consistent."""

    def test_all_arrondissements_have_commune(self):
        for arrond in ARRONDISSEMENT_TO_COMMUNE:
            commune = ARRONDISSEMENT_TO_COMMUNE[arrond]
            assert commune in COMMUNE_TO_ARRONDISSEMENTS

    def test_all_communes_have_arrondissements(self):
        for commune, arronds in COMMUNE_TO_ARRONDISSEMENTS.items():
            for arrond in arronds:
                assert ARRONDISSEMENT_TO_COMMUNE[arrond] == commune

    def test_paris_has_20_arrondissements(self):
        assert len(COMMUNE_TO_ARRONDISSEMENTS["75056"]) == 20

    def test_lyon_has_9_arrondissements(self):
        assert len(COMMUNE_TO_ARRONDISSEMENTS["69123"]) == 9

    def test_marseille_has_16_arrondissements(self):
        assert len(COMMUNE_TO_ARRONDISSEMENTS["13055"]) == 16

    def test_total_45_arrondissements(self):
        assert len(ARRONDISSEMENT_TO_COMMUNE) == 45


class TestResolveLocation:
    """resolve_location: GPS/code_insee -> ResolvedLocation."""

    @pytest.mark.asyncio
    async def test_raises_422_when_no_params(self):
        session = AsyncMock()
        with pytest.raises(Exception) as exc_info:
            await resolve_location(session)
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_code_insee_mode_regular(self):
        session = AsyncMock()
        loc = await resolve_location(session, code_insee="44109")
        assert loc.codes == ["44109"]
        assert loc.mode == "code_insee"
        assert loc.lat is None
        assert loc.lon is None

    @pytest.mark.asyncio
    async def test_code_insee_mode_paris(self):
        session = AsyncMock()
        loc = await resolve_location(session, code_insee="75056")
        assert len(loc.codes) == 21  # commune + 20 arrondissements
        assert loc.mode == "code_insee"

    @pytest.mark.asyncio
    async def test_code_insee_preferred_over_gps(self):
        session = AsyncMock()
        loc = await resolve_location(
            session, lat=48.8566, lon=2.3522, code_insee="44109"
        )
        assert loc.mode == "code_insee"
        assert loc.lat is None  # GPS ignored

    @pytest.mark.asyncio
    async def test_gps_mode_resolved(self):
        # The referential geo_communes answers, never the cadastre source.
        result = MagicMock()
        result.first.return_value = ("44109",)
        session = AsyncMock()
        session.execute.return_value = result

        loc = await resolve_location(session, lat=47.2184, lon=-1.5536)
        statement = str(session.execute.call_args.args[0])
        assert "geo_communes" in statement and "parcelles" not in statement
        assert loc.codes == ["44109"]
        assert loc.mode == "gps"
        assert loc.lat == 47.2184
        assert loc.lon == -1.5536

    @pytest.mark.asyncio
    async def test_gps_mode_no_match(self):
        result = MagicMock()
        result.first.return_value = None
        session = AsyncMock()
        session.execute.return_value = result

        with pytest.raises(HTTPException) as exc_info:
            await resolve_location(session, lat=0.0, lon=0.0)
        assert exc_info.value.status_code == 404
