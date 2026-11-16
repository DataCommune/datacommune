"""Tracer DATA-03 / D-24: scope table -> /v1/datasets and /v1/resolve.

A dataset the persisted scope does not host is declared « hors couverture —
dataset non hébergé », never absent and never queried; a point outside the
hosted departements is « territoire hors périmètre ». The scope is written by
the production write path (``instance_scope`` -> ``replace_scope``) into a
disposable database and read by the real scope dependency of the API:
no override of it reaches this module.
"""

import dataclasses
import re
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.exceptions import register_exception_handlers
from app.common.semantic_audit import audit_commune_payload, audit_resolve_payload
from app.common.source_catalog import DATASET_CATALOG, SourceCoverage
from app.commune.dependencies import COMMUNE_SOURCES
from app.commune.matview_ddl import recreate_commune_synthese
from app.commune.router import router as commune_router
from app.config import settings
from app.database import get_db
from app.datasets.router import router as datasets_router
from app.resolve import multi
from app.resolve import router as resolve_module
from app.scope import SCOPE_EXCLUDED_KEYS

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
NOT_HOSTED = {"antennes", "georisques"}
HOSTED = sorted(set(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS - NOT_HOSTED)

# Synthetic BAN rows: one inside the hosted departements, one outside.
NANTES = (47.2184, -1.5536)
PARIS = (48.8606, 2.3376)
_ADRESSES = (
    (91_000_001, "44", "44109_0001_00001", "44000", "44109", "Nantes", NANTES),
    (91_000_002, "75", "75101_0001_00001", "75001", "75101", "Paris", PARIS),
)


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    with psycopg.connect(isolated_pg_dsn) as conn:
        for id_, dept, ban_id, cp, insee, commune, (lat, lon) in _ADRESSES:
            conn.execute(
                "INSERT INTO adresses (id, code_departement, ban_id, numero, "
                "nom_voie, code_postal, code_commune, nom_commune, position) "
                "VALUES (%s, %s, %s, '1', 'rue du test', %s, %s, %s, "
                "ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                (id_, dept, ban_id, cp, insee, commune, lon, lat),
            )
        # A catalogue key without a metadata row must still be listed.
        conn.execute("DELETE FROM dataset_metadata WHERE dataset_name = 'antennes'")
    return isolated_pg_dsn


@pytest.fixture
def scoped(migrated, instance_scope):
    instance_scope(migrated, HOSTED, ["44", "85"])
    return migrated


@pytest.fixture
def upstream_calls(monkeypatch):
    calls = []

    async def fake_http(name, lat, lon, ban_id=None):
        calls.append(name)
        return None

    monkeypatch.setattr(multi, "_dispatch_http", fake_http)
    return calls


@pytest.fixture
def statements():
    """SQL statements the API runs during a test (captured on its engine)."""
    return []


@pytest.fixture
async def api(scoped, monkeypatch, statements):
    engine = create_async_engine(
        scoped.replace("postgresql://", "postgresql+asyncpg://")
    )

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(datasets_router)
    app.include_router(resolve_module.router)
    app.include_router(commune_router)
    app.dependency_overrides[get_db] = db
    # Dataset tasks open their own sessions: point them at this database too.
    monkeypatch.setattr(resolve_module, "async_session", sessions)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c
    finally:
        await engine.dispose()


def _by_name(body):
    return {entry["nom"]: entry for entry in body["donnees"]}


async def test_datasets_lists_a_non_hosted_key_as_out_of_coverage(api):
    response = await api.get("/v1/datasets")
    assert response.status_code == 200, response.text
    entries = _by_name(response.json())

    assert set(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS <= set(entries)
    antennes = entries["antennes"]
    assert antennes["statut"] == "hors_couverture"
    assert antennes["hebergement"] == {
        "etat": "hors_couverture",
        "motif": "dataset_non_heberge",
        "departements": [],
    }
    assert entries["georisques"]["hebergement"]["motif"] == "dataset_non_heberge"
    assert entries["dvf"]["hebergement"] == {
        "etat": "heberge",
        "motif": None,
        "departements": ["44", "85"],
    }


async def test_datasets_follows_the_scope_written_in_the_database(
    api, migrated, instance_scope
):
    instance_scope(migrated, [*HOSTED, "antennes"], ["44", "85"])
    entries = _by_name((await api.get("/v1/datasets")).json())
    assert entries["antennes"]["hebergement"]["etat"] == "heberge"
    assert entries["antennes"]["statut"] == "non_importe"


async def test_resolve_declares_non_hosted_datasets_without_querying(
    api, upstream_calls
):
    lat, lon = NANTES
    response = await api.get(
        "/v1/resolve",
        params={
            "q": f"{lat},{lon}",
            "type": "gps",
            "datasets": "antennes,risques,entreprises",
        },
    )
    assert response.status_code == 200, response.text
    states = response.json()["donnees"][0]["dataset_states"]

    for name in ("antennes", "risques"):
        assert states[name]["absence_reason"] == "out_of_coverage"
        assert states[name]["motif"] == "dataset_non_heberge"
        assert states[name]["raison"] == "Point hors du perimetre couvert."
    # risques is backed by the non-hosted Georisques proxy: never called.
    # entreprises (Sirene, hosted) is the positive control of the counter.
    assert upstream_calls == ["entreprises"]
    assert "motif" not in states["entreprises"]
    assert "antennes" not in (response.json()["donnees"][0]["datasets"] or {})


@pytest.mark.parametrize(
    "address", ["1 rue du test 75001 Paris", "1 rue du test 33000 Bordeaux"]
)
async def test_resolve_address_outside_the_departements(api, upstream_calls, address):
    response = await api.get(
        "/v1/resolve",
        params={"q": address, "datasets": "risques"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert body["meta"]["resolution"]["issue"] == "out_of_coverage"
    assert body["meta"]["resolution"]["motif"] == "territoire_hors_perimetre"
    assert upstream_calls == []


async def test_resolve_point_outside_the_departements(api, upstream_calls):
    lat, lon = PARIS
    response = await api.get(
        "/v1/resolve",
        params={"q": f"{lat},{lon}", "type": "gps", "datasets": "entreprises,dvf"},
    )
    assert response.status_code == 200, response.text
    states = response.json()["donnees"][0]["dataset_states"]
    for name in ("entreprises", "dvf"):
        assert states[name]["absence_reason"] == "out_of_coverage"
        assert states[name]["motif"] == "territoire_hors_perimetre"
    assert upstream_calls == []


_PERMIS_TABLE = re.compile(r"\b(FROM|JOIN)\s+permis\b", re.IGNORECASE)


async def test_datasets_lists_permis_as_source_without_coverage(api):
    # permis is hosted on 44/85 but its source only covers 93 (catalogue).
    entries = _by_name((await api.get("/v1/datasets")).json())
    assert "permis" in entries
    assert entries["permis"]["hebergement"] == {
        "etat": "hors_couverture",
        "motif": "source_sans_couverture",
        "departements": [],
    }


async def test_resolve_declares_permis_without_source_coverage(
    api, statements, monkeypatch
):
    lat, lon = NANTES
    params = {"q": f"{lat},{lon}", "type": "gps", "datasets": "permis"}
    response = await api.get("/v1/resolve", params=params)
    assert response.status_code == 200, response.text
    state = response.json()["donnees"][0]["dataset_states"]["permis"]
    assert state["absence_reason"] == "out_of_coverage"
    assert state["motif"] == "source_sans_couverture"
    # Not a proof on its own: the permis dispatch is quarantined and never
    # reads its table (IN-09). The discriminating evidence is the state above
    # and the witness below.
    assert not [sql for sql in statements if _PERMIS_TABLE.search(sql)]

    # Witness: were the source national, permis on 44 would reach its
    # (quarantined) dispatch instead of being declared out of coverage.
    national = dataclasses.replace(
        DATASET_CATALOG["permis"], source_coverage=SourceCoverage()
    )
    monkeypatch.setitem(DATASET_CATALOG, "permis", national)
    response = await api.get("/v1/resolve", params=params)
    state = response.json()["donnees"][0]["dataset_states"]["permis"]
    assert state["absence_reason"] != "out_of_coverage"
    assert "motif" not in state


# -- SEM-01: the four states in one response, the commune synthesis ----------

# Tables of the commune synthesis, emptied so no inherited row hides a
# dependency; the publication journal is emptied too.
_COMMUNE_TABLES = (
    "demographie",
    "revenus",
    "zonage_abc",
    "vacance",
    "fiscalite_locale",
    "indicateurs_loyers",
    "mutations_grain",
    "diagnostics_dpe",
    "equipements",
    "carte_scolaire",
    "monuments_historiques",
    "foncier_pm",
    "eligibilite_fixe",
)


def _commune_fixture(dsn):
    """Nantes (44109): population, revenus, 0 equipements, 2 monuments."""
    with psycopg.connect(dsn) as conn:
        conn.execute(f"TRUNCATE {', '.join(_COMMUNE_TABLES)}")
        conn.execute("DELETE FROM etl_publications")
        conn.execute(
            "INSERT INTO demographie (code_insee, nom_commune, code_departement, "
            "population, annee) VALUES ('44109', 'Nantes', '44', 320732, 2021)"
        )
        conn.execute(
            "INSERT INTO revenus (code_insee, revenu_median, annee) "
            "VALUES ('44109', 22480, 2021)"
        )
        conn.execute(
            "INSERT INTO equipements (type_equipement, code_commune, "
            "code_departement, nb_equipements, annee) "
            "VALUES ('A101', '44109', '44', 0, 2024)"
        )
        conn.execute(
            "INSERT INTO monuments_historiques (reference, code_insee) "
            "VALUES ('PA00108700', '44109'), ('PA00108701', '44109')"
        )
        # The application DDL, as every commune-source publication installs it.
        recreate_commune_synthese(conn)


def _publish(dsn, statuses):
    """Latest publication of each commune source, with its status."""
    with psycopg.connect(dsn) as conn:
        for dataset, status in statuses.items():
            conn.execute(
                "INSERT INTO etl_publications "
                "(generation, dataset_name, table_name, status) "
                "VALUES (gen_random_uuid(), %s, %s, %s)",
                (dataset, COMMUNE_SOURCES[dataset], status),
            )


async def test_one_resolve_response_shows_the_four_states(api, migrated, monkeypatch):
    _commune_fixture(migrated)
    calls = []

    async def failing_upstream(name, lat, lon, ban_id=None):
        calls.append(name)
        raise httpx.ConnectError("upstream down")

    monkeypatch.setattr(multi, "_dispatch_http", failing_upstream)
    lat, lon = NANTES
    response = await api.get(
        "/v1/resolve",
        params={
            "q": f"{lat},{lon}",
            "type": "gps",
            "datasets": "commune,loyers,antennes,entreprises",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    result = body["donnees"][0]
    states = result["dataset_states"]
    # (a) a measured zero of a hosted source
    assert states["commune"]["presence"] == "value"
    commune = result["datasets"]["commune"]
    assert commune["nb_equipements"] == 0
    assert commune["couverture"]["equipements"]["etat"] == "heberge"
    # (b) hosted, nothing reported for this commune
    assert states["loyers"]["absence_reason"] == "not_reported"
    assert "motif" not in states["loyers"]
    # (c) not hosted: out of coverage with its reason
    assert states["antennes"]["absence_reason"] == "out_of_coverage"
    assert states["antennes"]["motif"] == "dataset_non_heberge"
    # (d) upstream outage; the local datasets of the response stay present
    assert calls == ["entreprises"]
    assert states["entreprises"]["absence_reason"] == "temporary_error"
    assert commune["population"] == 320732
    assert audit_resolve_payload(body) == []


async def test_commune_response_passes_the_semantic_audit(api, migrated):
    _commune_fixture(migrated)
    response = await api.get("/v1/commune", params={"code_insee": "44109"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["code_insee"] for row in body["donnees"]] == ["44109"]
    assert body["donnees"][0]["nb_equipements"] == 0
    assert audit_commune_payload(body) == []
    periods = {
        entry["dataset"]: entry["periode"]["statut"] for entry in body["meta"]["source"]
    }
    assert set(periods) == set(COMMUNE_SOURCES)
    assert set(periods.values()) <= {"connue", "glissante", "inconnue"}


async def test_commune_without_demographie_is_out_of_coverage(
    api, migrated, instance_scope, statements
):
    _commune_fixture(migrated)
    with psycopg.connect(migrated) as conn:
        named = conn.execute(
            "SELECT revenu_median FROM revenus WHERE code_insee = '44109'"
        ).fetchone()
    assert named == (22480,)
    without = [key for key in HOSTED if key != "demographie"]
    instance_scope(migrated, without, ["44", "85"])
    statements.clear()
    commune = await api.get("/v1/commune", params={"code_insee": "44109"})
    assert commune.status_code == 200, commune.text
    assert commune.json()["donnees"] == []
    assert commune.json()["meta"]["hebergement"] == {
        "commune": {
            "etat": "hors_couverture",
            "motif": "dataset_non_heberge",
            "departements": [],
        }
    }
    lat, lon = NANTES
    resolved = await api.get(
        "/v1/resolve",
        params={"q": f"{lat},{lon}", "type": "gps", "datasets": "commune"},
    )
    assert resolved.status_code == 200, resolved.text
    result = resolved.json()["donnees"][0]
    assert result["dataset_states"]["commune"]["absence_reason"] == "out_of_coverage"
    assert result["dataset_states"]["commune"]["motif"] == "dataset_non_heberge"
    assert "commune" not in (result["datasets"] or {})
    assert not [sql for sql in statements if "v_commune_synthese" in sql]


async def test_only_hosted_publications_gate_the_synthesis(
    api, migrated, instance_scope
):
    _commune_fixture(migrated)
    _publish(migrated, {key: "published" for key in COMMUNE_SOURCES})
    _publish(migrated, {"monuments": "failed"})
    without_monuments = [key for key in HOSTED if key != "monuments"]
    instance_scope(migrated, without_monuments, ["44", "85"])
    served = await api.get("/v1/commune", params={"code_insee": "44109"})
    assert served.status_code == 200, served.text
    row = served.json()["donnees"][0]
    assert row["nb_monuments"] is None
    assert row["couverture"]["monuments"]["motif"] == "dataset_non_heberge"
    assert (row["population"], row["revenu_median"], row["nb_equipements"]) == (
        320732,
        22480.0,
        0,
    )
    # The same failed publication on a hosted source still blocks the read.
    instance_scope(migrated, HOSTED, ["44", "85"])
    blocked = await api.get("/v1/commune", params={"code_insee": "44109"})
    assert blocked.status_code == 503, blocked.text
    # Witness: once monuments is published again, its two rows are served.
    _publish(migrated, {"monuments": "published"})
    healthy = await api.get("/v1/commune", params={"code_insee": "44109"})
    assert healthy.status_code == 200, healthy.text
    assert healthy.json()["donnees"][0]["nb_monuments"] == 2


# -- partially hosted aggregates (02-09 task 4, case 4.b) --------------------


def _transport_fixture(dsn):
    """Three identified stops around Nantes, both tables populated."""
    lat, lon = NANTES
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE arrets_transport, gares")
        conn.execute(
            "INSERT INTO arrets_transport (stop_id, stop_name, position) VALUES "
            "('A', 'ARRET-A', ST_SetSRID(ST_MakePoint(%s, %s), 4326)), "
            "('B', 'ARRET-B', ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (lon, lat, lon + 0.001, lat),
        )
        conn.execute(
            "INSERT INTO gares (nom_gare, code_insee, position) VALUES "
            "('GARE-A', '44109', ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (lon, lat + 0.001),
        )


def _without(*keys):
    return [key for key in HOSTED if key not in keys]


@pytest.mark.parametrize(
    ("hosted", "expected"),
    [
        (_without("gares"), {"ARRET-A", "ARRET-B"}),
        (_without("transports"), {"GARE-A"}),
        (HOSTED, {"ARRET-A", "ARRET-B", "GARE-A"}),
    ],
    ids=["transports-only", "gares-only", "both"],
)
async def test_resolve_transports_reads_only_the_hosted_branches(
    api, migrated, instance_scope, hosted, expected
):
    _transport_fixture(migrated)
    instance_scope(migrated, hosted, ["44", "85"])
    lat, lon = NANTES
    response = await api.get(
        "/v1/resolve",
        params={"q": f"{lat},{lon}", "type": "gps", "datasets": "transports"},
    )
    assert response.status_code == 200, response.text
    rows = response.json()["donnees"][0]["datasets"]["transports"]
    stop_names = [row["stop_name"] for row in rows]
    assert set(stop_names) == expected
    assert len(stop_names) == len(expected)


@pytest.fixture
def synthesis_calls(monkeypatch):
    calls = []
    original = multi.commune_synthese_par_codes

    async def counting(*args, **kwargs):
        calls.append(args[1])
        return await original(*args, **kwargs)

    monkeypatch.setattr(multi, "commune_synthese_par_codes", counting)
    return calls


async def test_resolve_commune_needs_its_structural_source(
    api, migrated, instance_scope, synthesis_calls
):
    _commune_fixture(migrated)
    lat, lon = NANTES
    params = {"q": f"{lat},{lon}", "type": "gps", "datasets": "commune"}
    instance_scope(migrated, _without("demographie"), ["44", "85"])
    response = await api.get("/v1/resolve", params=params)
    assert response.status_code == 200, response.text
    state = response.json()["donnees"][0]["dataset_states"]["commune"]
    assert state["absence_reason"] == "out_of_coverage"
    assert state["motif"] == "dataset_non_heberge"
    assert synthesis_calls == []
    # Witness: with demographie hosted the same commune is served.
    instance_scope(migrated, HOSTED, ["44", "85"])
    response = await api.get("/v1/resolve", params=params)
    assert response.status_code == 200, response.text
    served = response.json()["donnees"][0]["datasets"]["commune"]
    assert served["code_insee"] == "44109"
    assert synthesis_calls == [["44109"]]


# -- identity sources of /v1/resolve (WR-03 of 02-08, decision 2026-09-22) --


def _nantes_referential(dsn):
    """Nantes in geo_communes, one parcel under the point, one vacance row.

    The cadastre stays hosted: the parcel gives the GPS identity, the BAN
    being off only removes the address context.
    """
    lat, lon = NANTES
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM geo_communes WHERE code = '44109'")
        conn.execute(
            "INSERT INTO geo_communes (code, nom, departement_code, population, "
            "geometry) VALUES ('44109', 'Nantes', '44', 320732, "
            "ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 0.05)))",
            (lon, lat),
        )
        conn.execute("DELETE FROM parcelles WHERE id_parcelle = '44109000HN0129'")
        conn.execute(
            "INSERT INTO parcelles (code_departement, id_parcelle, commune, "
            "section, numero, geometrie) VALUES ('44', '44109000HN0129', "
            "'44109', 'HN', '0129', "
            "ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 0.0005)))",
            (lon, lat),
        )
        conn.execute("DELETE FROM vacance WHERE code_commune = '44109'")
        conn.execute(
            "INSERT INTO vacance (code_commune, nom_commune, code_departement, "
            "nb_logements_prives, nb_logements_vacants, taux_vacance, annee) "
            "VALUES ('44109', 'Nantes', '44', 1000, 70, 7.0, 2024)"
        )


def _reads(statements, table):
    return [sql for sql in statements if re.search(rf"\b{table}\b", sql)]


@pytest.mark.parametrize("ban_hosted", [True, False], ids=["ban", "no-ban"])
async def test_resolve_address_needs_the_ban(
    api, migrated, instance_scope, statements, ban_hosted
):
    instance_scope(migrated, HOSTED if ban_hosted else _without("ban"), ["44", "85"])
    statements.clear()
    response = await api.get("/v1/resolve", params={"q": "1 rue du test 44000 Nantes"})
    assert response.status_code == 200, response.text
    body = response.json()
    if ban_hosted:
        assert _reads(statements, "adresses") != []
    else:
        assert body["donnees"] == []
        assert body["meta"]["resolution"]["issue"] == "out_of_coverage"
        assert body["meta"]["resolution"]["motif"] == "dataset_non_heberge"
        assert _reads(statements, "adresses") == []


async def test_resolve_cadastral_reference_needs_the_cadastre(
    api, migrated, instance_scope, statements
):
    instance_scope(migrated, _without("cadastre"), ["44", "85"])
    statements.clear()
    response = await api.get(
        "/v1/resolve", params={"q": "44109000HN0129", "type": "cadastre"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["resolution"]["issue"] == "out_of_coverage"
    assert body["meta"]["resolution"]["motif"] == "dataset_non_heberge"
    assert _reads(statements, "parcelles") == []


@pytest.mark.parametrize("ban_hosted", [True, False], ids=["ban", "no-ban"])
async def test_resolve_gps_without_ban_takes_the_commune_from_the_referential(
    api, migrated, instance_scope, statements, ban_hosted
):
    _nantes_referential(migrated)
    instance_scope(migrated, HOSTED if ban_hosted else _without("ban"), ["44", "85"])
    statements.clear()
    lat, lon = NANTES
    response = await api.get(
        "/v1/resolve",
        params={"q": f"{lat},{lon}", "type": "gps", "datasets": "vacance"},
    )
    assert response.status_code == 200, response.text
    result = response.json()["donnees"][0]
    assert result["dataset_states"]["vacance"]["presence"] == "value"
    assert result["datasets"]["vacance"]["code_commune"] == "44109"
    if ban_hosted:
        assert _reads(statements, "adresses") != []
    else:
        assert _reads(statements, "adresses") == []
        assert _reads(statements, "geo_communes") != []
        assert result["adresse"] is None


# -- identity sources named in meta.hebergement (IN-12, IN-15) ---------------

_NO_BAN = {
    "etat": "hors_couverture",
    "motif": "dataset_non_heberge",
    "departements": [],
}


async def test_gps_without_ban_names_the_unconsulted_ban(
    api, migrated, instance_scope, statements
):
    # Case 1: cadastre hosted, no parcel at the point, BAN not hosted.
    instance_scope(migrated, _without("ban"), ["44", "85"])
    statements.clear()
    response = await api.get("/v1/resolve", params={"q": "47.30,-1.40", "type": "gps"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert body["meta"]["hebergement"] == {"ban": _NO_BAN}
    assert _reads(statements, "adresses") == []


async def test_gps_without_any_identity_source_names_both(
    api, migrated, instance_scope, statements
):
    # Case 3: neither the BAN nor the cadastre is hosted.
    instance_scope(migrated, _without("ban", "cadastre"), ["44", "85"])
    statements.clear()
    lat, lon = NANTES
    response = await api.get("/v1/resolve", params={"q": f"{lat},{lon}", "type": "gps"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["resolution"]["motif"] == "dataset_non_heberge"
    assert body["meta"]["hebergement"] == {"ban": _NO_BAN, "cadastre": _NO_BAN}
    assert _reads(statements, "adresses") == []
    assert _reads(statements, "parcelles") == []


async def test_ban_keyed_dataset_is_out_of_coverage_without_the_ban(
    api, migrated, instance_scope, upstream_calls
):
    # Case 2: bdnb is answered only through the BAN key of the address.
    _nantes_referential(migrated)
    instance_scope(migrated, _without("ban"), ["44", "85"])
    lat, lon = NANTES
    response = await api.get(
        "/v1/resolve",
        params={"q": f"{lat},{lon}", "type": "gps", "datasets": "bdnb,vacance"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    states = body["donnees"][0]["dataset_states"]
    assert states["bdnb"]["absence_reason"] == "out_of_coverage"
    assert states["bdnb"]["motif"] == "dataset_non_heberge"
    assert "bdnb" not in upstream_calls
    assert states["vacance"]["presence"] == "value"
    assert body["meta"]["hebergement"] == {"ban": _NO_BAN}
    # Witness: with the BAN hosted, bdnb is dispatched (upstream substituted).
    instance_scope(migrated, HOSTED, ["44", "85"])
    response = await api.get(
        "/v1/resolve",
        params={"q": f"{lat},{lon}", "type": "gps", "datasets": "bdnb"},
    )
    assert response.status_code == 200, response.text
    assert "hebergement" not in (response.json()["meta"] or {})
    assert "bdnb" in upstream_calls
