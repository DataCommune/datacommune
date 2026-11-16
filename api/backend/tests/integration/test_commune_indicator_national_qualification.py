"""National qualification of the commune indicators on disposable PostgreSQL 17.

Plan 04.1-10 (decision of plan 09, option a). A received DVF lot of the
hosted departements {44, 75, 85} is qualified under the contract
``dvf-delivery-qualification.v3``: its COG reference is read per departement
(no frozen 207/253), the SQL checks take the departements of the lot, the build
follows the qualified set (461 communes, not a fixed 460/920). It is built,
activated and read by ``/v1/commune`` like the 44/85 build. The 44/85 build
qualified under ``v2`` is still served byte for byte as before the change
(public blocks compared with the snapshot taken on the parent commit 165a2a5).
Trust pins and the independently reviewed decision stay required: nothing is
qualified automatically.
"""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.commune import indicator_activation, indicator_binding
from app.commune import indicator_context as ic
from app.commune.indicator_manifest import ManifestRefused
from app.commune.indicator_policy import RuntimeIndicatorPolicy
from app.commune.indicator_projector import ProjectionRequest
from app.commune.indicator_reader import read_active_preproduction_build
from app.commune.indicator_sql import IndicatorSqlRefused
from tests.integration import test_commune_indicator_storage as registry
from tests.integration.instance_scope_fixture import (
    COMMUNE_SOURCES,
    install_instance_scope,
)
from tests.integration.test_commune_indicator_http import build_preproduction
from tests.integration.test_commune_indicator_projector import NOW, register
from tests.integration.test_commune_indicator_sql import dpe, insert, mutation

pytest_plugins = ("tests.integration.test_commune_indicator_results",)

FIXTURES = Path(__file__).parents[1] / "fixtures/commune_indicators"
V2_SNAPSHOT = FIXTURES / "v2-build-public-165a2a5.json"
PARIS = "75056"
V3 = {
    "version_qualification": "dvf-delivery-qualification.v3",
    "departements": ["44", "75", "85"],
}


def _canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def national_trust(directory, *, extra=({"commune": PARIS, "department": "75"},)):
    """The pinned 44/85 COG identities plus *extra* rows, in the producer order.

    Same form as the measured reference (``geo_communes`` rows, sorted keys,
    ordered by departement then code), so its digest is a byte pin.
    """
    directory.mkdir(exist_ok=True)
    rows = json.loads((FIXTURES / "cog-2026-identities.json").read_bytes())
    rows = sorted([*rows, *extra], key=lambda r: (r["department"], r["commune"]))
    raw = _canonical(rows)
    path = directory / "cog.json"
    path.write_bytes(raw)
    return ic.IndicatorTrustInputs(
        cog=ic.CogInput(
            path=path,
            root=directory,
            sha256=hashlib.sha256(raw).hexdigest(),
            version="insee-cog-2026-01-01-identities",
        ),
        evidence_root=directory,
        scope="preproduction",
        checked_at="2026-09-09T14:00:00Z",
    )


def prepare_national(conn, tmp_path, *, anchor=V3, trust=None):
    """Sales and DPE in 44 and 75, both decisions registered and pinned."""
    insert(conn, "mutations_grain", [mutation(f"n{i}") for i in range(5)])
    insert(
        conn,
        "mutations_grain",
        [
            mutation(f"p{i}", code_departement="75", code_commune=PARIS)
            for i in range(5)
        ],
    )
    insert(conn, "diagnostics_dpe", [dpe(f"n{i}", "A") for i in range(2)])
    insert(
        conn,
        "diagnostics_dpe",
        [dpe(f"p{i}", label, code_insee=PARIS) for i, label in enumerate("CCF")],
    )
    registry.publish(conn)
    registry.publish(conn, "dvf", registry.G2)
    registry.lock(conn)
    inputs = trust or national_trust(tmp_path)
    dpe_pins = register(conn, tmp_path, inputs, "dpe")
    dvf_pins = register(conn, tmp_path, inputs, "dvf", anchor=anchor)
    return inputs.model_copy(update={"dpe": dpe_pins, "dvf": dvf_pins})


def build_national(conn, tmp_path):
    inputs = prepare_national(conn, tmp_path)
    receipt = indicator_binding.rebuild_and_activate_indicator_results(
        conn,
        trust=inputs,
        request=ProjectionRequest(NOW + timedelta(minutes=1), "preproduction"),
    )
    conn.commit()
    policy = RuntimeIndicatorPolicy(
        scope="preproduction",
        cog=inputs.cog,
        evidence_root=inputs.evidence_root,
        checked_at=inputs.checked_at,
        dpe=inputs.dpe,
        dvf=inputs.dvf,
    )
    return receipt, policy


@pytest.mark.asyncio
async def test_v3_lot_with_75_is_qualified_built_activated_and_served(
    store, tmp_path, client, monkeypatch
):
    from app.common.publication_readiness import require_publication
    from app.commune import queries
    from app.database import get_db
    from app.main import app

    conn, dsn = store
    receipt, policy = build_national(conn, tmp_path)
    # The build follows the qualified set: 460 COG identities of 44/85 + 75056.
    header = conn.execute(
        "SELECT header FROM commune_indicator_result_builds WHERE id=%s",
        (receipt.build_id,),
    ).fetchone()[0]
    assert (header["commune_count"], header["cell_count"]) == (461, 922)
    assert len(receipt.public_rows) == 461

    conn.execute("SELECT pg_advisory_unlock_all()")
    conn.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE")
    conn.execute(
        """CREATE MATERIALIZED VIEW v_commune_synthese AS
        SELECT c.code_insee, c.nom_commune
        FROM (VALUES ('44001', 'Commune 44001'), ('75056', 'Paris'))
            AS c(code_insee, nom_commune)
        CROSS JOIN (SELECT 1 FROM diagnostics_dpe LIMIT 1) d
        CROSS JOIN (SELECT 1 FROM mutations_grain LIMIT 1) m"""
    )
    conn.execute(
        "CREATE TABLE matview_metadata("
        "matview_name text PRIMARY KEY,last_refresh_status text)"
    )
    conn.execute("INSERT INTO matview_metadata VALUES ('v_commune_synthese','success')")
    conn.commit()
    install_instance_scope(conn, COMMUNE_SOURCES, ("44", "75", "85"))
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[require_publication] = lambda: None
    monkeypatch.setattr(
        queries, "COMMUNE_SOURCES", {"dpe": "diagnostics_dpe", "dvf": "mutations_grain"}
    )
    monkeypatch.setattr(
        queries,
        "COMMUNE_EDGES",
        (
            ("v_commune_synthese", "diagnostics_dpe"),
            ("v_commune_synthese", "mutations_grain"),
        ),
    )
    monkeypatch.setattr(queries.settings, "commune_indicators_enabled", True)
    monkeypatch.setattr(queries.settings, "app_environment", "preproduction")
    monkeypatch.setattr(
        queries.settings, "commune_indicator_policy", policy.model_dump_json()
    )
    try:
        response = await client.get("/v1/commune", params={"code_insee": PARIS})
        assert response.status_code == 200, response.text
        (paris,) = response.json()["donnees"]
        dvf = paris["indicateurs_dvf"]
        assert dvf["ancrage_temporel"]["version_qualification"] == (
            "dvf-delivery-qualification.v3"
        )
        assert dvf["ancrage_temporel"]["departements"] == ["44", "75", "85"]
        current = dvf["residentiel_maison"]["courante"]
        assert current["effectif"] == 5
        assert current["mediane"] == {"etat": "value", "valeur": 2000.0}
        dpe_block = paris["indicateurs_dpe"]["resultat"]
        assert dpe_block["population"]["total"] == 3
        assert dpe_block["modes"] == ["C"]
        # A 44 commune of the same lot is served by the same process.
        nantes = await client.get("/v1/commune", params={"code_insee": "44001"})
        assert nantes.status_code == 200, nantes.text
        (row,) = nantes.json()["donnees"]
        assert row["indicateurs_dvf"]["residentiel_maison"]["courante"]["effectif"] == 5
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_publication, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_v2_build_is_served_exactly_as_before_the_v3_contract(store, tmp_path):
    conn, dsn = store
    _, policy = build_preproduction(conn, tmp_path, trend=True)
    snapshot = json.loads(V2_SNAPSHOT.read_text(encoding="utf-8"))
    registry.lock(conn)
    public = indicator_activation.read_active_build(
        conn, trust=policy.trust_inputs()
    ).public_dump()
    header = conn.execute(
        "SELECT header FROM commune_indicator_result_builds b "
        "JOIN commune_indicator_build_activations a ON a.build_id=b.id "
        "ORDER BY a.id DESC LIMIT 1"
    ).fetchone()[0]
    conn.commit()
    # Same public blocks, same header shape and format as on 165a2a5.
    assert hashlib.sha256(_canonical(public)).hexdigest() == snapshot["public_sha256"]
    assert len(public) == snapshot["communes"] == 460
    assert sorted(header) == snapshot["header_keys"]
    assert [header["commune_count"], header["cell_count"]] == snapshot["header_counts"]
    assert header["format_version"] == snapshot["format_version"]
    anchor = public[0]["indicateurs_dvf"]["ancrage_temporel"]
    assert anchor["version_qualification"] == "dvf-delivery-qualification.v2"
    assert anchor["departements"] == ["44", "85"]
    # And the API reader serves these same blocks.
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with AsyncSession(engine) as session:
            active = await read_active_preproduction_build(
                session,
                policy=policy,
                environment="preproduction",
                codes=list(snapshot["sample"]),
            )
    finally:
        await engine.dispose()
    assert active.blocks == snapshot["sample"]
    assert active.hors_build == frozenset()


@pytest.mark.parametrize(
    "anchor",
    [
        # A departement outside the hostable list.
        {**V3, "departements": ["44", "75", "85", "99"]},
        # An overseas collectivity is never hosted (D-123).
        {**V3, "departements": ["44", "75", "85", "975"]},
        # Repeated or out of canonical order: the lot scope is one exact list.
        {**V3, "departements": ["44", "75", "75", "85"]},
        {**V3, "departements": ["75", "44", "85"]},
        # The v2 reading rule stays exactly 44 and 85.
        {"departements": ["44", "75", "85"]},
    ],
)
def test_invalid_lot_scope_is_refused_before_any_qualification(store, tmp_path, anchor):
    conn, _ = store
    with pytest.raises(ManifestRefused):
        prepare_national(conn, tmp_path, anchor=anchor)


def test_lot_not_covering_the_cog_reference_never_serves_a_false_zero(store, tmp_path):
    """A v3 lot of 44/85 against a COG reference holding 75: no 75 zero sale."""
    conn, _ = store
    inputs = prepare_national(
        conn,
        tmp_path,
        anchor={**V3, "departements": ["44", "85"]},
    )
    with pytest.raises(ManifestRefused, match="dvf_lot_scope_mismatch"):
        indicator_binding.rebuild_indicator_results(
            conn,
            trust=inputs,
            request=ProjectionRequest(NOW + timedelta(minutes=1), "preproduction"),
        )


def test_commune_of_75_filed_under_44_is_refused(store, tmp_path):
    conn, _ = store
    # In the COG reference: an identity whose departement is not its own.
    misfiled = national_trust(
        tmp_path / "cog", extra=({"commune": PARIS, "department": "44"},)
    )
    registry.lock(conn)
    with pytest.raises(ManifestRefused, match="invalid_cog_reference"):
        ic.build_indicator_context(conn, trust=misfiled)
    conn.rollback()
    # In the received stock: a 75 commune sold under departement 44.
    inputs = prepare_national(conn, tmp_path)
    insert(
        conn,
        "mutations_grain",
        [mutation("misfiled", code_departement="44", code_commune="75101")],
    )
    with pytest.raises(IndicatorSqlRefused, match="invalid_canonical_source"):
        indicator_binding.rebuild_indicator_results(
            conn,
            trust=inputs,
            request=ProjectionRequest(NOW + timedelta(minutes=1), "preproduction"),
        )
