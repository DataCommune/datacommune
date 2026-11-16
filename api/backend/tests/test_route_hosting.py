"""Route guard of the persisted scope (02-08): meta, refusal, territory.

Database-free: the scope is a double (``tests/scope_doubles.py``). The guard
wired to a real database is proven by
``tests/integration/test_route_scope_conformity.py``.
"""

import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from app.common.exceptions import ErreurApiError
from app.copropriete.router import _copropriete_departement
from app.errial.router import _errial_departement
from app.logements import router as logements
from app.scope.dependencies import code_insee_departement, hosted_datasets
from app.scope.model import InstanceScope
from app.zonage_abc.router import _zonage_departement
from app.zone.router import search_departement, served_departements, zone_departement
from tests.scope_doubles import SCOPE_44_85, route_hosting

WITHOUT_DPE = InstanceScope(
    datasets=SCOPE_44_85.datasets - {"dpe"}, departements=("44", "85")
)
NOTHING = InstanceScope(datasets=frozenset(), departements=("44", "85"))
META = {"source": {"nom": "x"}}


def test_meta_is_unchanged_while_every_served_key_is_hosted():
    assert route_hosting("dvf", "dpe", scope=SCOPE_44_85).meta(META) == META


def test_meta_names_every_served_key_once_one_is_out():
    hosting = route_hosting("dvf", "dpe", scope=WITHOUT_DPE)

    meta = hosting.meta(META)

    assert set(meta["hebergement"]) == {"dvf", "dpe"}
    assert meta["hebergement"]["dpe"].motif.value == "dataset_non_heberge"
    assert meta["hebergement"]["dvf"].etat == "heberge"
    assert not hosting.none_hosted
    # Restricted to the key a paginated zone answer serves.
    assert set(hosting.meta(META, keys=["dpe"])["hebergement"]) == {"dpe"}


@pytest.mark.parametrize("keys", [(), ("inconnu",), ("dvf", "inconnu")])
def test_hosted_datasets_refuses_keys_outside_the_catalogue(keys):
    with pytest.raises(KeyError):
        hosted_datasets(*keys)


def _request(query: str) -> Request:
    return Request({"type": "http", "query_string": query.encode(), "headers": []})


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("type=departement&code=33", "33"),
        ("type=departement&code=2a", "2A"),
        ("type=commune&code=44109", "44"),
        ("type=commune&code=2A004", "2A"),
        ("type=commune&code=97105", "971"),
        # A code naming no departement is never a proof of « hors couverture ».
        ("type=commune&code=99999", None),
        ("type=departement&code=00", None),
        ("type=region&code=52", None),
        ("code=44109", None),
    ],
)
def test_zone_departement_derives_the_territory_from_type_and_code(query, expected):
    assert zone_departement(_request(query)) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("code_insee=44109", "44"),
        ("code_insee=2A004", "2A"),
        ("code_insee=2b033", "2B"),
        ("code_insee=97105", "971"),
        # code_insee wins over lat/lon, as in resolve_location.
        ("lat=47.2&lon=-1.5&code_insee=33063", "33"),
        ("lat=47.2&lon=-1.5", None),  # GPS mode: territory unknown, unchanged
        ("code_insee=20004", None),
        ("code_insee=99999", None),
        ("code_insee=", None),
        ("", None),
    ],
)
def test_code_insee_departement(query, expected):
    assert code_insee_departement(_request(query)) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("q=le+pin&departement=44", "44"),
        ("q=le+pin&departement=2b", "2B"),
        ("q=le+pin&departement=974", "974"),
        # An unknown code is refused by the handler (422), never a territory.
        ("q=le+pin&departement=00", None),
        ("q=le+pin&departement=ZZ", None),
        ("q=le+pin", None),
        # A five-digit q is a postal code: its prefix names the territory (CR-01).
        ("q=33000", "33"),
        ("q=97400", "974"),
        ("q=+33000+", "33"),
        # The departement filter wins over the postal code, even an unknown one (422).
        ("q=33000&departement=44", "44"),
        ("q=33000&departement=ZZ", None),
        # Prefix 20 (Corsica) names no departement: unknown, never « hors couverture ».
        ("q=20000", None),
        ("q=3300", None),
        ("q=330000", None),
    ],
)
def test_search_departement_names_only_a_real_departement(query, expected):
    assert search_departement(_request(query)) == expected


def test_served_departements_are_the_hosted_ones_the_source_publishes():
    assert served_departements("ban", SCOPE_44_85) == ["44", "85"]
    assert served_departements("demographie", WITHOUT_DPE) == ["44", "85"]
    # Not hosted: no departement, so its table is never read.
    assert served_departements("dpe", WITHOUT_DPE) == []
    assert served_departements("ban", NOTHING) == []


def test_route_specific_precedence_follows_each_handler():
    # zonage-abc: code_departement wins; a fake one proves nothing.
    assert _zonage_departement(_request("code_departement=33&code_insee=44109")) == (
        "33"
    )
    assert _zonage_departement(_request("code_departement=00")) is None
    assert _zonage_departement(_request("code_insee=44109")) == "44"
    # copropriete: a registry number lookup carries no territory.
    assert _copropriete_departement(_request("numero=AA1&code_insee=33063")) is None
    assert _copropriete_departement(_request("code_insee=33063")) == "33"
    # errial: coordinates win over code_insee + code_parcelle.
    query = "lat=47.2&lon=-1.5&code_insee=33063&code_parcelle=000-AB-0069"
    assert _errial_departement(_request(query)) is None
    assert _errial_departement(_request("code_insee=33063")) == "33"


async def test_documentary_dpe_route_refuses_before_any_read_when_dpe_is_out():
    loader = AsyncMock()
    with (
        patch.object(logements, "load_dpe_inventory", loader),
        pytest.raises(ErreurApiError) as refused,
    ):
        await logements.get_dpe_inventory(
            hosting=route_hosting("dpe", "ban", scope=WITHOUT_DPE),
            response=AsyncMock(),
            contexte="opaque",
            curseur=None,
            limite=10,
        )

    assert refused.value.code == 409
    assert "dpe: dataset_non_heberge" in refused.value.detail
    loader.assert_not_called()


async def test_documentary_dpe_route_refuses_without_the_ban():
    # WR-04 sweep: the inventory target is a BAN address (adresses).
    without_ban = InstanceScope(
        SCOPE_44_85.datasets - {"ban"}, SCOPE_44_85.departements
    )
    loader = AsyncMock()
    with (
        patch.object(logements, "load_dpe_inventory", loader),
        pytest.raises(ErreurApiError) as refused,
    ):
        await logements.get_dpe_inventory(
            hosting=route_hosting("dpe", "ban", scope=without_ban),
            response=AsyncMock(),
            contexte="opaque",
            curseur=None,
            limite=10,
        )

    assert refused.value.code == 409
    assert "ban: dataset_non_heberge" in refused.value.detail
    assert "dpe" not in refused.value.detail.split("—")[1]
    loader.assert_not_called()


async def test_logements_never_reads_the_ban_target_without_the_ban():
    without_ban = InstanceScope(
        SCOPE_44_85.datasets - {"ban"}, SCOPE_44_85.departements
    )
    inventory = AsyncMock(side_effect=RuntimeError("stop after the call"))
    with (
        patch.object(logements, "_charge_inventaire", inventory),
        pytest.raises(RuntimeError, match="stop after the call"),
    ):
        await logements.get_logements(
            hosting=route_hosting(
                "dpe", "dvf", "copropriete", "rnb", "ban", scope=without_ban
            ),
            db=AsyncMock(),
            response=AsyncMock(),
            id_parcelle="44109000AB0001",
            ban_id="44109_0001_00001",
            adresse=None,
            limite=120,
        )
    # The inventory is asked with no BAN key: identity unavailable, no read.
    inventory.assert_awaited_once_with(None)


# Every read /v1/bien/logements may issue on dpe, dvf, copropriete (and the
# RNB widening built on the DPE inventory).
_LOGEMENTS_READERS = (
    "_charge_inventaire",
    "_charge_contexte_batiment",
    "load_dpe_inventory_result",
    "dpe_par_identifiants_ban",
    "cles_ban_batiment_rnb",
    "copropriete_par_parcelle",
    "dvf_appartements_par_parcelle",
)


def _without(*keys: str) -> InstanceScope:
    return InstanceScope(SCOPE_44_85.datasets - set(keys), SCOPE_44_85.departements)


async def _call_logements(scope: InstanceScope, readers: dict[str, AsyncMock]):
    with contextlib.ExitStack() as stack:
        for name, spy in readers.items():
            stack.enter_context(patch.object(logements, name, spy))
        return await logements.get_logements(
            hosting=route_hosting(
                "dpe", "dvf", "copropriete", "rnb", "ban", scope=scope
            ),
            db=AsyncMock(),
            response=AsyncMock(),
            id_parcelle="44109000AB0001",
            ban_id="44109_0001_00001",
            adresse=None,
            limite=120,
        )


def _spies() -> dict[str, AsyncMock]:
    # Well-typed answers: without the refusal the route runs to completion.
    spies = {name: AsyncMock(return_value=[]) for name in _LOGEMENTS_READERS}
    spies["_charge_inventaire"].return_value = logements._unavailable_inventory()
    return spies


@pytest.mark.parametrize(
    "out",
    [("dpe",), ("dvf",), ("copropriete",), ("dpe", "dvf", "copropriete")],
    ids=["dpe", "dvf", "copropriete", "all-three"],
)
async def test_logements_refuses_before_any_read_once_one_source_is_out(out):
    # Decision of 2026-09-26 (orchestrator, delegated by Ianis): the detection
    # signals and the inventory carry no out-of-coverage state, so one missing
    # source would read as a false absent/0 (SEM-01). Same refusal body as the
    # documentary DPE routes, before any read (D-23, DATA-03).
    spies = _spies()
    with pytest.raises(ErreurApiError) as refused:
        await _call_logements(_without(*out), spies)

    assert refused.value.code == 409
    assert refused.value.headers == {"Cache-Control": "no-store"}
    assert refused.value.detail == "Hors couverture — " + ", ".join(
        f"{key}: dataset_non_heberge" for key in out
    )
    for name, spy in spies.items():
        assert spy.await_count == 0, name


@pytest.mark.parametrize(
    "out", [(), ("rnb",), ("ban",)], ids=["all-hosted", "no-rnb", "no-ban"]
)
async def test_logements_reads_its_three_sources_while_they_are_hosted(out):
    # rnb and ban keep their own treatment (links unread, identity
    # unavailable): the route still answers from dpe, dvf and copropriete.
    spies = _spies()

    answer = await _call_logements(_without(*out), spies)

    spies["_charge_inventaire"].assert_awaited_once_with(
        None if "ban" in out else "44109_0001_00001"
    )
    spies["copropriete_par_parcelle"].assert_awaited_once()
    spies["dvf_appartements_par_parcelle"].assert_awaited_once()
    assert answer.donnees.id_parcelle == "44109000AB0001"
    hebergement = answer.meta.hebergement or {}
    # Unchanged meta: every served key named once one of them is out.
    served = {"dpe", "dvf", "copropriete", "rnb", "ban"}
    assert set(hebergement) == (served if out else set())
    assert [k for k, v in hebergement.items() if v.etat != "heberge"] == list(out)
