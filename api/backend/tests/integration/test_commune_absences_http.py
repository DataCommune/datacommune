"""D-47 on a migrated database: typed absences served by real HTTP.

The view is the one ``alembic upgrade head`` installs (098 included); rows go
through ``/v1/commune`` exactly as a client reads them.
"""

import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.common.semantic_audit import audit_commune_payload
from app.commune.router import router
from app.database import get_db
from app.dvf.couverture import SEUIL_ND_COMMUNE
from tests.integration.instance_scope_fixture import (
    COMMUNE_SOURCES,
    install_instance_scope,
)

# ``migrated`` (and the ``migrated_dsn`` it needs) are fixtures: imported for
# pytest, not called.
from tests.integration.test_commune_coverage_contract import (  # noqa: F401
    _engine,
    documents,
    migrated,
    migrated_dsn,
)

# Baseline communes of the ``migrated`` fixture: 44991, 44992, 85991.
AT_THRESHOLD, UNDER_THRESHOLD = "44991", "44992"


def _apartment_sales(conn, code: str, count: int) -> None:
    for index in range(count):
        conn.execute(
            """INSERT INTO mutations_grain (
                code_departement, id_mutation, date_mutation, classe_mutation,
                nb_lignes, nb_dispositions, nb_communes, nb_parcelles,
                mono_local, est_vente, valeur_fonciere, surface_bati_totale,
                code_commune)
            VALUES (%s, %s, '2025-06-01', 'residentiel_appartement',
                1, 1, 1, 1, true, true, %s, 50, %s)""",
            (code[:2], f"{code}-{index}", 150000 + 1000 * index, code),
        )


async def _serve(conn, dsn, codes) -> dict[str, dict]:
    """``/v1/commune`` bodies of *codes*, on the view refreshed from *conn*."""
    documents(conn, "migration")
    engine = _engine(dsn, None)
    sessions = async_sessionmaker(engine)
    app = FastAPI()
    app.include_router(router)

    async def database():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = database
    payloads = {}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://fixture"
        ) as client:
            for code in codes:
                response = await client.get("/v1/commune", params={"code_insee": code})
                assert response.status_code == 200, response.text
                payloads[code] = response.json()
    finally:
        await engine.dispose()
    return payloads


@pytest.mark.asyncio
async def test_dvf_median_threshold_edge_is_served_as_a_typed_absence(
    migrated,  # noqa: F811
):
    """COM-02 edge: at the threshold the median is served, one under it is not."""
    conn, dsn, _ = migrated
    _apartment_sales(conn, AT_THRESHOLD, SEUIL_ND_COMMUNE)
    _apartment_sales(conn, UNDER_THRESHOLD, SEUIL_ND_COMMUNE - 1)
    payloads = await _serve(conn, dsn, (AT_THRESHOLD, UNDER_THRESHOLD))
    at = payloads[AT_THRESHOLD]["donnees"][0]
    under = payloads[UNDER_THRESHOLD]["donnees"][0]

    assert at["nb_mutations_12m_appartement"] == SEUIL_ND_COMMUNE
    assert at["prix_m2_median_appartement"] is not None
    assert "prix_m2_median_appartement" not in (at.get("etats") or {})

    assert under["nb_mutations_12m_appartement"] == SEUIL_ND_COMMUNE - 1
    assert under["prix_m2_median_appartement"] is None
    state = under["etats"]["prix_m2_median_appartement"]
    assert state["etat"] == "insufficient_sample"
    assert f"{SEUIL_ND_COMMUNE - 1} mutations d'appartements" in state["motif"]
    # Same rule for houses: no house sale, a real count of 0 under the threshold.
    assert under["nb_mutations_12m_maison"] == 0
    assert under["etats"]["prix_m2_median_maison"]["etat"] == "insufficient_sample"
    for payload in payloads.values():
        assert audit_commune_payload(payload) == []

    # Frozen harness response for the frontend reading layer (etats-colonnes).
    if target := os.environ.get("ETATS_API_FIXTURE_OUTPUT"):
        Path(target).write_text(
            json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8"
        )


@pytest.mark.asyncio
async def test_every_null_column_is_served_with_its_declared_state(
    migrated,  # noqa: F811
):
    """Revenus row without Gini, commune without revenus, source not hosted."""
    conn, dsn, _ = migrated
    conn.execute("""INSERT INTO revenus (code_insee, revenu_median, indice_gini, annee)
        VALUES ('44991', 21000, NULL, 2021)""")
    served = {
        code: payload["donnees"][0]
        for code, payload in (await _serve(conn, dsn, ("44991", "44992"))).items()
    }
    with_row, without_row = served["44991"], served["44992"]
    assert with_row["revenu_median"] == 21000
    assert with_row["indice_gini"] is None
    assert with_row["etats"]["indice_gini"]["etat"] == "not_available"
    assert without_row["indice_gini"] is None
    assert without_row["etats"]["indice_gini"]["etat"] == "not_reported"
    assert without_row["etats"]["annee_revenus"]["etat"] == "not_reported"

    install_instance_scope(conn, set(COMMUNE_SOURCES) - {"equipements"}, ("44", "85"))
    payloads = await _serve(conn, dsn, ("44991",))
    unhosted = payloads["44991"]["donnees"][0]
    for column in ("nb_equipements", "annee_equipements"):
        assert unhosted[column] is None
        assert unhosted["etats"][column] == {
            "etat": "out_of_coverage",
            "motif": "dataset_non_heberge",
        }
    # Every column served null carries a state, each allowed by the contract.
    for payload in payloads.values():
        assert audit_commune_payload(payload) == []
    # Identifier and object columns aside, no null escapes etats.
    objects = {"indicateurs_dvf", "indicateurs_dpe", "loyers_appartement"}
    contextual = {"fenetre_mediane_dvf"}
    assert unhosted["fenetre_mediane_dvf"] is None
    assert "fenetre_mediane_dvf" not in unhosted["etats"]
    # commune_parent is an identifier, null for a commune (04.7-09).
    skipped = {"code_region", "commune_parent", "loyers_maison", *objects, *contextual}
    nulls = {c for c, v in unhosted.items() if v is None and c not in skipped}
    assert nulls and nulls <= set(unhosted["etats"])
