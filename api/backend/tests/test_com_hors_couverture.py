"""Overseas collectivities: out of coverage with their reason (D-123, 04.1-06).

975 (Saint-Pierre-et-Miquelon), 977, 978, 984, 986, 987, 988 and 989 are never
hosted: an INSEE code of one of them is « hors couverture », reason
``territoire_hors_perimetre``, on the routes that receive a commune code --
never unknown, never covered, never zero. 975 stays in ``DEPARTEMENTS`` (its
partitions exist) but not in ``DEPARTEMENTS_HEBERGEABLES``, and a scope naming
it is refused. A code naming no territory (99999) stays unknown; a hosted DROM
stays covered. No database: scope doubles and a fake session.
"""

from __future__ import annotations

import pytest

from app.common.schemas import OutOfCoverageReason
from app.commune.schemas import (
    COMMUNE_SOURCE_KEYS,
    apply_commune_coverage,
    commune_coverage,
)
from app.database import get_db
from app.etl.base import DEPARTEMENTS
from app.main import app
from app.scope.__main__ import main as scope_main
from app.scope.coverage import hosting_for, hosting_for_key
from app.scope.dependencies import commune_departement
from app.scope.model import (
    COLLECTIVITES_OUTRE_MER,
    DEPARTEMENTS_HEBERGEABLES,
    InstanceScope,
    collectivite_outre_mer,
    parse_departements,
    territoire_couverture,
)

pytestmark = pytest.mark.unit

PREFIXES = ("975", "977", "978", "984", "986", "987", "988", "989")
OUTSIDE = OutOfCoverageReason.TERRITOIRE_HORS_PERIMETRE
DROM = ("971", "972", "973", "974", "976")


@pytest.fixture
def full_scope(full_scope_loader) -> InstanceScope:
    # The test scope names the 102 codes of DEPARTEMENTS, 975 included: the
    # rule must hold even for a scope written by code rather than the CLI.
    scope = full_scope_loader("")
    assert "975" in scope.departements
    return scope


def test_eight_collectivities_and_101_hostable_departements():
    assert set(PREFIXES) == COLLECTIVITES_OUTRE_MER
    assert len(DEPARTEMENTS_HEBERGEABLES) == 101
    assert "975" in DEPARTEMENTS  # partitions kept, no migration
    assert "975" not in DEPARTEMENTS_HEBERGEABLES
    assert set(DROM) <= set(DEPARTEMENTS_HEBERGEABLES)
    assert list(DEPARTEMENTS_HEBERGEABLES) == [d for d in DEPARTEMENTS if d != "975"]


@pytest.mark.parametrize("prefix", PREFIXES)
def test_each_collectivity_is_out_of_coverage_with_its_reason(prefix, full_scope):
    code = f"{prefix}01"
    assert collectivite_outre_mer(code) == prefix
    assert territoire_couverture(code) == prefix
    assert commune_departement(code) == prefix

    couverture = commune_coverage(full_scope, code)
    assert set(couverture) == set(COMMUNE_SOURCE_KEYS)
    for hosting in couverture.values():
        assert hosting.etat == "hors_couverture"
        assert hosting.motif == OUTSIDE
    commune = hosting_for_key("commune", full_scope, commune_departement(code))
    assert (commune.etat, commune.motif) == ("hors_couverture", OUTSIDE)


@pytest.mark.parametrize("prefix", PREFIXES)
def test_a_collectivity_is_never_served_as_zero(prefix, full_scope):
    row = {"code_insee": f"{prefix}01", "population": 0, "nb_logements": 0}
    # The structural source is out: no row is served at all, no zero.
    couverture = commune_coverage(full_scope, row["code_insee"])
    assert apply_commune_coverage(row, couverture) is None


def test_975_is_out_even_though_it_names_a_partition(full_scope):
    hosting = hosting_for("ban", full_scope, "975")
    assert (hosting.etat, hosting.motif) == ("hors_couverture", OUTSIDE)


@pytest.mark.parametrize("code", ["99999", "98001", "97999", "00000", ""])
def test_a_code_naming_no_territory_stays_unknown(code):
    assert territoire_couverture(code) is None
    assert collectivite_outre_mer(code) is None
    assert commune_departement(code) is None


@pytest.mark.parametrize("code", ["97105", "97209", "97302", "97411", "97611"])
def test_a_hosted_drom_stays_covered(code):
    departement = code[:3]
    scope = InstanceScope(frozenset({"ban", "demographie"}), ("44", departement))
    assert territoire_couverture(code) == departement
    assert hosting_for("ban", scope, departement).etat == "heberge"
    demographie = commune_coverage(scope, code)["demographie"]
    if departement == "976":
        # A DROM, not a collectivity: out of coverage only because the census
        # files publish no commune of Mayotte (source_catalog, 2026-10-30).
        assert (demographie.etat, demographie.motif) == (
            "hors_couverture",
            OutOfCoverageReason.SOURCE_SANS_COUVERTURE,
        )
    else:
        assert demographie.etat == "heberge"


# ------------------------------------------------------------------- scope --


def test_parse_departements_refuses_a_collectivity():
    with pytest.raises(ValueError, match=r"collectivité d'outre-mer.*D-123.*975"):
        parse_departements("44,975")
    assert parse_departements("44,971,976") == ["44", "971", "976"]


def test_scope_set_refuses_a_collectivity_before_connecting(monkeypatch, capsys):
    def no_database(*args, **kwargs):
        raise AssertionError("refused input must not open a connection")

    monkeypatch.setattr("app.scope.__main__.psycopg.connect", no_database)
    argv = ["set", "--datasets", "ban", "--departements", "44,975"]

    assert scope_main(argv) == 2
    err = capsys.readouterr().err
    assert "collectivité d'outre-mer hors couverture (D-123)" in err
    assert "975" in err


def test_a_scope_of_the_101_hostable_departements_is_national():
    scope = InstanceScope(frozenset({"ban"}), DEPARTEMENTS_HEBERGEABLES)
    assert scope.covers_all_departements
    assert not InstanceScope(frozenset({"ban"}), ("44", "85")).covers_all_departements


# ------------------------------------------------------------------ routes --


async def test_commune_route_serves_a_collectivity_out_of_coverage(client):
    response = await client.get("/v1/commune", params={"code_insee": "97701"})

    assert response.status_code == 200
    body = response.json()
    assert body["donnees"] == []
    hosting = body["meta"]["hebergement"]["commune"]
    assert hosting["etat"] == "hors_couverture"
    assert hosting["motif"] == "territoire_hors_perimetre"


class _NoIdentity:
    """Session double: the referential knows none of the requested codes."""

    async def execute(self, *args, **kwargs):
        return []


@pytest.fixture
def empty_referential():
    async def session():
        yield _NoIdentity()

    app.dependency_overrides[get_db] = session
    yield
    app.dependency_overrides.pop(get_db, None)


async def test_comparison_names_collectivities_apart_from_unknown_codes(
    client, empty_referential
):
    response = await client.get(
        "/v1/communes/comparaison", params={"codes": "97501,99999,98735"}
    )

    assert response.status_code == 200
    donnees = response.json()["donnees"]
    assert donnees["codes_inconnus"] == ["99999"]
    assert [
        (c["code_insee"], c["hebergement"]["etat"], c["hebergement"]["motif"])
        for c in donnees["codes_hors_couverture"]
    ] == [
        ("97501", "hors_couverture", "territoire_hors_perimetre"),
        ("98735", "hors_couverture", "territoire_hors_perimetre"),
    ]
    assert donnees["communes"] == []
