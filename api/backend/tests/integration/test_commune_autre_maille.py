"""Paris against PostGIS: a value published at the other mesh (R-09).

The fixture follows production (internal note 2026-11-10-arrondissements):
the ABC zone and the local taxes are published for 75056 only, the rents for the
12e only; Nantes has all of them at its own code. Through the real view and the
real routes:

- the 12e serves the zone and the taxes of Paris, named with their origin in
  ``heritages``, and keeps its own rent;
- Paris serves no rent (never one made of its arrondissements), its absence
  saying that the source publishes it by arrondissement;
- Nantes, an ordinary commune, is served as before: nothing inherited, nothing
  pointed to.

    pwsh -NoProfile -File scripts/test-postgis.ps1 -Paths
        tests/integration/test_commune_autre_maille.py
"""

from __future__ import annotations

import pytest

from tests.integration.instance_scope_fixture import (
    HOSTABLE_KEYS,
    install_instance_scope,
)

# ``migrated`` (and the ``migrated_dsn`` it needs) are fixtures: imported for
# pytest, not called.
from tests.integration.test_commune_coverage_contract import (  # noqa: F401
    documents,
    migrated,
    migrated_dsn,
)
from tests.integration.test_plm_arrondissements import _api, _geo

pytestmark = pytest.mark.destructive

PARIS = {"code_insee": "75056", "nom": "Paris", "echelle": "commune"}


def _sources(conn) -> None:
    """Each source at the mesh production shows for Paris; Nantes at its own code."""
    conn.execute(
        "TRUNCATE demographie, zonage_abc, fiscalite_locale, indicateurs_loyers, "
        "mutations, mutations_grain, diagnostics_dpe"
    )
    conn.execute(
        """INSERT INTO demographie
            (code_insee, nom_commune, code_departement, population, annee)
        VALUES ('75056', 'Paris', '75', 2100000, 2022),
               ('75112', 'Paris 12e Arrondissement', '75', 140000, 2022),
               ('44109', 'Nantes', '44', 320000, 2022)"""
    )
    conn.execute(
        """INSERT INTO zonage_abc (code_insee, nom_commune, code_departement, zone)
        VALUES ('75056', 'Paris', '75', 'Abis'), ('44109', 'Nantes', '44', 'A')"""
    )
    conn.execute(
        """INSERT INTO fiscalite_locale (code_insee, taux_tfpb, nom_epci, annee)
        VALUES ('75056', 21.21, 'Métropole du Grand Paris', 2025),
               ('44109', 53.37, 'Nantes Métropole', 2025)"""
    )
    conn.execute(
        """INSERT INTO indicateurs_loyers (code_insee, type_habitat, loyer_m2, annee)
        VALUES ('75112', 'appartement', 27.5, 2025),
               ('44109', 'appartement', 14.5, 2025)"""
    )


@pytest.fixture
def paris(migrated):  # noqa: F811
    conn, dsn, _ = migrated
    install_instance_scope(conn, sorted(HOSTABLE_KEYS), ("44", "75"))
    _geo(conn)
    _sources(conn)
    documents(conn, "reconstruction")
    return dsn


async def _row(api, code: str) -> dict:
    response = await api.get("/v1/commune", params={"code_insee": code})
    assert response.status_code == 200, response.text
    (row,) = response.json()["donnees"]
    return row


async def test_the_arrondissement_inherits_what_its_commune_alone_publishes(paris):
    async with _api(paris) as api:
        row = await _row(api, "75112")
    assert (row["zone_abc"], row["taux_tfpb"], row["annee_fiscalite"]) == (
        "Abis",
        21.21,
        2025,
    )
    assert row["nom_epci"] == "Métropole du Grand Paris"
    for column in ("zone_abc", "taux_tfpb", "nom_epci", "annee_fiscalite"):
        assert row["heritages"][column] == PARIS, column
        assert column not in (row.get("etats") or {}), column
    # Its own rent stays its own, never marked inherited.
    assert row["loyer_m2_appartement"] == 27.5
    assert "loyer_m2_appartement" not in row["heritages"]
    # Paris has no TEOM in the fixture: inherited as an absence of Paris's row.
    assert row["taux_teom"] is None
    assert row["etats"]["taux_teom"]["etat"] == "not_available"


async def test_the_commune_points_to_its_arrondissements_without_a_value(paris):
    async with _api(paris) as api:
        row = await _row(api, "75056")
    assert row["loyer_m2_appartement"] is None
    assert row["etats"]["loyer_m2_appartement"]["etat"] == "not_reported"
    assert row["etats"]["loyer_m2_appartement"]["publiee_a"] == "arrondissement"
    # Its own values are untouched, and nothing is inherited by a commune.
    assert row["zone_abc"] == "Abis"
    assert "heritages" not in row
    # No arrondissement has a house rent: a plain gap.
    assert "publiee_a" not in row["etats"]["loyer_m2_maison"]


async def test_an_ordinary_commune_inherits_and_points_to_nothing(paris):
    async with _api(paris) as api:
        row = await _row(api, "44109")
    assert (row["zone_abc"], row["taux_tfpb"]) == ("A", 53.37)
    assert "heritages" not in row
    assert not any("publiee_a" in state for state in (row.get("etats") or {}).values())


async def test_the_comparison_reads_the_inherited_values(paris):
    async with _api(paris) as api:
        response = await api.get(
            "/v1/communes/comparaison", params={"codes": "75112,44109"}
        )
    assert response.status_code == 200, response.text
    body = response.json()["donnees"]
    zone = next(c for c in body["criteres"] if c["cle"] == "zone_abc")
    assert [(cell["etat"], cell["valeur"]) for cell in zone["cellules"]] == [
        ("value", "Abis"),
        ("value", "A"),
    ]
    douze = next(c for c in body["communes"] if c["code_insee"] == "75112")
    assert douze["synthese"]["heritages"]["zone_abc"] == PARIS
