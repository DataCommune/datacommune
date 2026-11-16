"""Paris, Lyon, Marseille: a value published at the other mesh (R-09).

Each source publishes either at the commune or at the arrondissement
(internal note 2026-11-10-arrondissements). An arrondissement takes
from its commune what applies as such to every part of it (ABC zone, voted tax
rates, intercommunality), with the scale and the commune it comes from. A
commune never receives a value made from its arrondissements: its absence says
that the source publishes the value by arrondissement. An arrondissement whose
commune alone has a statistic (fibre) says so, without taking it.

No database: the rows are doubles. The SQL is proven against PostGIS by
``tests/integration/test_commune_autre_maille.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.commune import router as commune_router
from app.commune.autre_maille import (
    MOTIF_PUBLIEE_PAR_ARRONDISSEMENT,
    MOTIF_PUBLIEE_POUR_LA_COMMUNE,
    apply_autre_maille,
    codes_autre_maille,
)
from app.commune.schemas import (
    ResultatCommuneSynthese,
    apply_commune_coverage,
    commune_coverage,
)
from app.database import get_db
from app.main import app
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope

pytestmark = pytest.mark.unit

FISCAL = ("taux_tfpb", "taux_th_rs", "taux_teom", "nom_epci", "annee_fiscalite")


def _row(code: str, nom: str, **values) -> dict:
    """A raw synthesis row: every flat column null unless given."""
    row = {column: None for column in ResultatCommuneSynthese.model_fields}
    for column in (
        "couverture",
        "etats",
        "heritages",
        "type_zone",
        "commune_parent",
        "fenetre_mediane_dvf",
    ):
        row.pop(column)
    row.update(
        code_insee=code,
        nom_commune=nom,
        code_departement=code[:2],
        population=1000,
        annee_demographie=2022,
    )
    row.update(values)
    return row


PARIS = _row(
    "75056",
    "Paris",
    superficie=105.45,
    densite=20043.8,
    zone_abc="Abis",
    zone_abc_reclassement=False,
    taux_tfpb=21.21,
    taux_th_rs=21.0,
    taux_teom=6.21,
    nom_epci="Métropole du Grand Paris",
    annee_fiscalite=2025,
    fibre_pct=95.4,
)
PARIS_11 = _row(
    "75111",
    "Paris 11e Arrondissement",
    loyer_m2_appartement=31.72,
    annee_loyers=2025,
    taux_vacance=9.0,
    nb_vacants_plus_2ans=1761,
    annee_vacance=2026,
    nb_equipements=8610,
    annee_equipements=2025,
)
PARIS_12 = _row("75112", "Paris 12e Arrondissement")
LYON = _row("69123", "Lyon", zone_abc="A", annee_fiscalite=2025, taux_tfpb=32.82)
LYON_3 = _row("69383", "Lyon 3e Arrondissement", loyer_m2_appartement=16.4)
NANTES = _row("44109", "Nantes", zone_abc="A", taux_tfpb=53.37, annee_fiscalite=2025)


@pytest.fixture
def scope(full_scope_loader) -> InstanceScope:
    return InstanceScope(full_scope_loader("").datasets, ("44", "69", "75"))


def _served(scope, row):
    return apply_commune_coverage(dict(row), commune_coverage(scope, row["code_insee"]))


# ----------------------------------------------------------- which codes --


def test_an_arrondissement_reads_its_commune_and_a_commune_its_arrondissements():
    assert codes_autre_maille(["75111"]) == ["75056"]
    assert codes_autre_maille(["69123"]) == [f"6938{i}" for i in range(1, 10)]
    # Two parts of Paris read their commune once; a requested code is not reread.
    assert codes_autre_maille(["75111", "75112"]) == ["75056"]
    assert "75111" not in codes_autre_maille(["75056", "75111"])


def test_an_ordinary_commune_reads_nothing_more():
    assert codes_autre_maille(["44109"]) == []
    assert codes_autre_maille(["44109", "85047"]) == []


# ------------------------------------------- A: the arrondissement inherits --


def test_the_arrondissement_takes_the_abc_zone_and_taxes_of_its_commune(scope):
    served = apply_autre_maille(
        _served(scope, PARIS_11), {"75056": _served(scope, PARIS)}
    )

    assert served["zone_abc"] == "Abis"
    assert served["zone_abc_reclassement"] is False
    assert [served[column] for column in FISCAL] == [
        21.21,
        21.0,
        6.21,
        "Métropole du Grand Paris",
        2025,
    ]
    origine = {"echelle": "commune", "code_insee": "75056", "nom": "Paris"}
    for column in ("zone_abc", "zone_abc_reclassement", *FISCAL):
        assert served["heritages"][column] == origine, column
        assert column not in (served["etats"] or {}), column
    # Its own values stay its own: never replaced, never marked inherited.
    assert served["loyer_m2_appartement"] == 31.72
    assert "loyer_m2_appartement" not in served["heritages"]


def test_a_statistic_of_the_whole_commune_is_not_taken_but_pointed_to(scope):
    served = apply_autre_maille(
        _served(scope, PARIS_11), {"75056": _served(scope, PARIS)}
    )

    assert served["fibre_pct"] is None
    assert "fibre_pct" not in served["heritages"]
    assert served["etats"]["fibre_pct"] == {
        "etat": "not_reported",
        "motif": MOTIF_PUBLIEE_POUR_LA_COMMUNE,
        "publiee_a": "commune",
    }
    # Area and density: the census row exists without them, a real gap.
    assert served["superficie"] is None
    assert served["etats"]["superficie"]["etat"] == "not_available"
    assert "publiee_a" not in served["etats"]["superficie"]


def test_an_arrondissement_with_its_own_row_keeps_it(scope):
    own = _row(
        "75112", "Paris 12e Arrondissement", taux_tfpb=10.0, annee_fiscalite=2024
    )
    served = apply_autre_maille(_served(scope, own), {"75056": _served(scope, PARIS)})

    assert (served["taux_tfpb"], served["annee_fiscalite"]) == (10.0, 2024)
    # A row of the source exists for the part: its missing values are its own.
    assert served["taux_teom"] is None
    assert served["etats"]["taux_teom"]["etat"] == "not_available"
    assert not any(column in (served["heritages"] or {}) for column in FISCAL)


def test_a_commune_without_the_value_gives_none(scope):
    bare = _row("75056", "Paris")
    served = apply_autre_maille(
        _served(scope, PARIS_12), {"75056": _served(scope, bare)}
    )

    assert served["zone_abc"] is None
    assert served["heritages"] is None
    assert served["etats"]["zone_abc"]["etat"] == "not_reported"
    assert "publiee_a" not in served["etats"]["zone_abc"]


# ------------------------------------------- B: the commune points to them --


def test_the_commune_says_the_value_is_published_by_arrondissement(scope):
    autres = {
        code: _served(scope, row)
        for code, row in (("75111", PARIS_11), ("75112", PARIS_12))
    }
    served = apply_autre_maille(_served(scope, PARIS), autres)

    for column in (
        "loyer_m2_appartement",
        "annee_loyers",
        "taux_vacance",
        "nb_vacants_plus_2ans",
        "nb_equipements",
    ):
        assert served[column] is None, column  # never a sum of its parts
        assert served["etats"][column] == {
            "etat": "not_reported",
            "motif": MOTIF_PUBLIEE_PAR_ARRONDISSEMENT,
            "publiee_a": "arrondissement",
        }, column
    # No arrondissement has it either: a plain gap.
    assert served["etats"]["loyer_m2_maison"]["etat"] == "not_reported"
    assert "publiee_a" not in served["etats"]["loyer_m2_maison"]
    assert served["heritages"] is None


def test_an_ordinary_commune_is_unchanged(scope):
    before = _served(scope, NANTES)
    after = apply_autre_maille(dict(before), {})
    assert {k: v for k, v in after.items() if k != "heritages"} == before
    assert after["heritages"] is None


def test_the_served_model_carries_the_origin_and_the_mesh(scope):
    served = apply_autre_maille(
        _served(scope, PARIS_11), {"75056": _served(scope, PARIS)}
    )
    body = ResultatCommuneSynthese(**served).model_dump(mode="json")

    assert body["heritages"]["zone_abc"] == {
        "echelle": "commune",
        "code_insee": "75056",
        "nom": "Paris",
    }
    assert body["etats"]["fibre_pct"]["publiee_a"] == "commune"
    # An ordinary absence keeps its two keys only: the contract does not move.
    assert set(body["etats"]["superficie"]) == {"etat", "motif"}
    nantes = ResultatCommuneSynthese(**apply_autre_maille(_served(scope, NANTES), {}))
    assert "heritages" not in nantes.model_dump(mode="json")


# ------------------------------------------------------------- the routes --


@pytest.fixture
def api(client, scope, monkeypatch):
    """/v1/commune and the comparison on doubles; records the codes read."""
    rows = {
        r["code_insee"]: r for r in (PARIS, PARIS_11, PARIS_12, LYON, LYON_3, NANTES)
    }
    reads: dict[str, list[list[str]]] = {"synthese": [], "autre_maille": []}

    async def synthese(_db, codes, _sources):
        reads["synthese"].append(list(codes))
        return [dict(rows[code]) for code in codes if code in rows]

    async def autre_maille(_db, codes, _sources):
        reads["autre_maille"].append(list(codes))
        return [dict(rows[code]) for code in codes if code in rows]

    async def nothing(*_args):
        return None

    async def no_facts(_db, _names):
        return {}

    async def identities(_db, codes):
        return {
            code: {"nom": rows[code]["nom_commune"], "departement": code[:2]}
            for code in codes
            if code in rows
        }

    monkeypatch.setattr(commune_router, "commune_synthese_par_codes", synthese)
    monkeypatch.setattr(commune_router, "synthese_autre_maille", autre_maille)
    monkeypatch.setattr(commune_router, "fenetre_mediane_commune", nothing)
    # The yearly price series is another read of the route (migration 115).
    monkeypatch.setattr(
        commune_router, "tendances_prix_par_codes", AsyncMock(return_value={})
    )
    monkeypatch.setattr(commune_router, "get_provenance_facts", no_facts)
    monkeypatch.setattr(commune_router, "_commune_identities", identities)

    async def no_session():
        yield object()

    app.dependency_overrides[get_db] = no_session
    app.dependency_overrides[get_instance_scope] = lambda: scope
    yield reads
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_instance_scope, None)


async def test_the_route_serves_the_inherited_value_with_its_origin(client, api):
    response = await client.get("/v1/commune", params={"code_insee": "75111"})
    assert response.status_code == 200, response.text
    (row,) = response.json()["donnees"]
    assert row["zone_abc"] == "Abis"
    assert row["heritages"]["taux_tfpb"]["nom"] == "Paris"
    # Paris 11e is still read at its exact code; its commune in a second read.
    assert api["synthese"] == [["75111"]]
    assert api["autre_maille"] == [["75056"]]


async def test_the_route_of_an_ordinary_commune_reads_nothing_more(client, api):
    response = await client.get("/v1/commune", params={"code_insee": "44109"})
    assert response.status_code == 200, response.text
    (row,) = response.json()["donnees"]
    assert "heritages" not in row
    assert api["autre_maille"] == []


async def test_two_arrondissements_compare_their_inherited_values(client, api):
    response = await client.get(
        "/v1/communes/comparaison", params={"codes": "75111,69383"}
    )
    assert response.status_code == 200, response.text
    body = response.json()["donnees"]
    zone = next(c for c in body["criteres"] if c["cle"] == "zone_abc")
    assert [(cell["etat"], cell["valeur"]) for cell in zone["cellules"]] == [
        ("value", "Abis"),
        ("value", "A"),
    ]
    syntheses = {c["code_insee"]: c["synthese"] for c in body["communes"]}
    assert syntheses["69383"]["heritages"]["zone_abc"]["code_insee"] == "69123"
    assert sorted(api["autre_maille"][0]) == ["69123", "75056"]
