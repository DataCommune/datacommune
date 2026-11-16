from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.commune import queries
from app.commune.indicator_manifest import ManifestRefused
from app.commune.indicator_reader import ActiveIndicators

# Every source of the synthesis hosted: the historical behaviour.
ALL_SOURCES = list(queries.COMMUNE_SOURCES)


class _Mappings:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self.rows


class _Session:
    async def execute(self, *_args, **_kwargs):
        return _Mappings(
            [{"ready": True, "document": {"code_insee": "44109", "nom": "Nantes"}}]
        )


@pytest.fixture(autouse=True)
def no_lock(monkeypatch):
    async def acquire(_session):
        return None

    monkeypatch.setattr(queries, "acquire_publication_read_lock", acquire)


@pytest.mark.asyncio
async def test_no_hosted_source_is_a_programming_error():
    # IN-05: an empty list would disable the availability barrier.
    session = AsyncMock()
    with pytest.raises(ValueError, match="hosted sources"):
        await queries.commune_synthese_par_codes(session, ["44109"], [])
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_rollout_disabled_serves_an_explicit_absence(monkeypatch):
    # Todo 2026-11-01: a closed gate is never a bare null without a state.
    monkeypatch.setattr(queries.settings, "commune_indicators_enabled", False)
    read = AsyncMock()
    monkeypatch.setattr(queries, "read_active_preproduction_build", read)
    rows = await queries.commune_synthese_par_codes(_Session(), ["44109"], ALL_SOURCES)
    absent = {"etat": "not_qualified", "motif": "indicateurs_communaux_non_publies"}
    assert rows == [
        {
            "code_insee": "44109",
            "nom": "Nantes",
            "indicateurs_dvf": absent,
            "indicateurs_dpe": absent,
        }
    ]
    read.assert_not_called()


@pytest.mark.asyncio
async def test_rollout_merges_exact_public_blocks_for_all_consumers(monkeypatch):
    monkeypatch.setattr(queries.settings, "commune_indicators_enabled", True)
    monkeypatch.setattr(queries.settings, "app_environment", "preproduction")
    monkeypatch.setattr(queries.settings, "commune_indicator_policy", "reviewed")
    monkeypatch.setattr(
        queries.RuntimeIndicatorPolicy,
        "from_json",
        lambda _raw: SimpleNamespace(),
    )

    async def read(_session, **kwargs):
        assert kwargs["codes"] == ["44109"]
        return ActiveIndicators(
            {
                "44109": {
                    "code_insee": "44109",
                    "indicateurs_dvf": {"etat": "not_qualified", "motif": "pending"},
                    "indicateurs_dpe": {"etat": "not_qualified", "motif": "pending"},
                }
            },
            frozenset(),
        )

    monkeypatch.setattr(queries, "read_active_preproduction_build", read)
    rows = await queries.commune_synthese_par_codes(_Session(), ["44109"], ALL_SOURCES)
    assert rows[0]["indicateurs_dvf"]["etat"] == "not_qualified"
    assert rows[0]["indicateurs_dpe"]["etat"] == "not_qualified"


def _enabled(monkeypatch, read):
    monkeypatch.setattr(queries.settings, "commune_indicators_enabled", True)
    monkeypatch.setattr(queries.settings, "app_environment", "preproduction")
    monkeypatch.setattr(queries.settings, "commune_indicator_policy", "reviewed")
    monkeypatch.setattr(
        queries.RuntimeIndicatorPolicy, "from_json", lambda _raw: SimpleNamespace()
    )
    monkeypatch.setattr(queries, "read_active_preproduction_build", read)


@pytest.mark.asyncio
async def test_commune_outside_the_build_is_not_qualified_not_an_outage(monkeypatch):
    async def read(_session, **kwargs):
        return ActiveIndicators({}, frozenset(kwargs["codes"]))

    _enabled(monkeypatch, read)
    rows = await queries.commune_synthese_par_codes(_Session(), ["44109"], ALL_SOURCES)
    absent = {"etat": "not_qualified", "motif": "commune_hors_build_qualifie"}
    assert rows == [
        {
            "code_insee": "44109",
            "nom": "Nantes",
            "indicateurs_dvf": absent,
            "indicateurs_dpe": absent,
        }
    ]


@pytest.mark.asyncio
async def test_commune_of_the_build_without_blocks_is_503(monkeypatch):
    async def read(_session, **_kwargs):
        return ActiveIndicators({}, frozenset())

    _enabled(monkeypatch, read)
    with pytest.raises(HTTPException) as raised:
        await queries.commune_synthese_par_codes(_Session(), ["44109"], ALL_SOURCES)
    assert raised.value.status_code == 503


@pytest.mark.asyncio
async def test_enabled_invalid_policy_is_503(monkeypatch):
    monkeypatch.setattr(queries.settings, "commune_indicators_enabled", True)
    monkeypatch.setattr(queries.settings, "commune_indicator_policy", "")
    monkeypatch.setattr(
        queries.RuntimeIndicatorPolicy,
        "from_json",
        lambda _raw: (_ for _ in ()).throw(ManifestRefused("invalid")),
    )
    with pytest.raises(HTTPException) as raised:
        await queries.commune_synthese_par_codes(_Session(), ["44109"], ALL_SOURCES)
    assert raised.value.status_code == 503
