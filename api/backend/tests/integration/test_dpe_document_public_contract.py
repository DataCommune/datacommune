"""LIEU-03 / D-102 / SEM-01: a public DPE document read by its number, on PostGIS.

``GET /v1/dpe/{numero}`` reads ``diagnostics_dpe`` by the ADEME number on
every hosted departement (block E: a number does not name its partition),
serves its source identity (contract V3) and its declared landmarks, and
answers a constant ``dpe_inconnu`` problem naming the served departements.

The scope is written by the production path (``replace_scope``) and read by
the real scope dependency of the API: only the session is overridden.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_dpe_document_public_contract.py
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.exceptions import register_exception_handlers
from app.config import settings
from app.database import get_db
from app.dpe.router import router as dpe_router
from tests.integration.instance_scope_fixture import install_scope_at

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]

LILAS = (-1.4300, 46.6700)  # lon, lat
BAN_LILAS = "85191_0420_00012"
# A document of the 85 partition, observed present in the existing dataset.
NUMERO = "2385E0000001A"
# Block E: the digits of this number say « 44 », its partition is 85.
NUMERO_CONTRADICTOIRE = "2344E0000009Z"
# A document of the 44 partition: the read is not bounded to one partition.
NUMERO_44 = "2444E0000004N"
# A document retired by a complete source scope (tombstone).
NUMERO_RETIRE = "2285E0000001R"
NUMERO_INCONNU = "2385E9999999X"

_NO_SUCCESSOR = (
    '{"replacement_state": "no_successor_known", "graph_issues": [], "date_issues": []}'
)


def _observations(state: str, *slugs: str) -> str:
    entries = [
        {
            "source_slug": slug,
            "department": "85",
            "source_version": "a" * 64,
            "observed_at": "2026-09-25T20:30:00Z",
            "last_seen_at": "2026-09-20T20:30:00Z",
            "state": state,
            "import_id": "import-synthetique",
        }
        for slug in sorted(slugs)
    ]
    return json.dumps(entries)


def _insert_dpe(
    conn,
    id_,
    numero,
    departement,
    *,
    ban_id=None,
    etabli=date(2024, 3, 1),
    classe="D",
    conso=17830.4,
    conso_m2=231,
    ges_m2=47.5,
    observations=None,
):
    fin = None if etabli is None else date(etabli.year + 10, etabli.month, 1)
    conn.execute(
        "INSERT INTO diagnostics_dpe (id, numero_dpe, identifiant_ban, "
        "code_departement, code_insee, type_batiment, surface_habitable, "
        "etiquette_dpe, etiquette_ges, date_etablissement_dpe, "
        "date_fin_validite_dpe, conso_5_usages_ep, emission_ges_5_usages, "
        "cout_total_5_usages, periode_construction, lifecycle_facts, "
        "source_state, source_snapshot_at, source_observations, "
        "conso_5_usages_par_m2_ep, emission_ges_5_usages_par_m2) VALUES "
        "(%s, %s, %s, %s, %s, 'maison', 79.0, %s, 'C', %s, %s, %s, 3433.1, "
        "1834.2, '1948-1974', %s::jsonb, %s, %s, %s::jsonb, %s, %s)",
        (
            id_,
            numero,
            ban_id,
            departement,
            f"{departement}191",
            classe,
            etabli,
            fin,
            conso,
            _NO_SUCCESSOR,
            "present" if observations else "unknown",
            "2026-09-26T00:00:00Z" if observations else None,
            observations or "[]",
            conso_m2,
            ges_m2,
        ),
    )


def _insert_retired_dpe(conn, id_, numero):
    """A tombstone (the table refuses one that keeps its description)."""
    conn.execute(
        "INSERT INTO diagnostics_dpe (id, numero_dpe, code_departement, "
        "source_state, source_snapshot_at, source_observations, is_tombstone, "
        "lifecycle_facts) VALUES (%s, %s, '85', 'absent_from_complete_scope', "
        "'2026-09-26T00:00:00Z', %s::jsonb, true, %s::jsonb)",
        (
            id_,
            numero,
            _observations("absent_from_complete_scope", "dpe03existant"),
            _NO_SUCCESSOR,
        ),
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
        lon, lat = LILAS
        conn.execute(
            "INSERT INTO adresses (id, code_departement, ban_id, numero, rep, "
            "nom_voie, code_postal, code_commune, nom_commune, position) VALUES "
            "(95000001, '85', %s, '12', NULL, 'Rue des Lilas', '85000', '85191', "
            "'La Roche-sur-Yon', ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (BAN_LILAS, lon, lat),
        )
        _insert_dpe(
            conn,
            97000001,
            NUMERO,
            "85",
            ban_id=BAN_LILAS,
            observations=_observations("present", "dpe03existant"),
        )
        # Totals and surface present, no per-square-metre value (C51).
        _insert_dpe(
            conn,
            97000002,
            NUMERO_CONTRADICTOIRE,
            "85",
            classe="B",
            conso_m2=None,
            ges_m2=None,
        )
        _insert_dpe(conn, 97000003, NUMERO_44, "44", classe="E", conso=None)
        _insert_retired_dpe(conn, 97000004, NUMERO_RETIRE)
    return isolated_pg_dsn


@dataclass
class Api:
    client: httpx.AsyncClient
    statements: list[str]

    def reads(self, table: str) -> list[str]:
        return [sql for sql in self.statements if f"FROM public.{table}" in sql]


@pytest.fixture
async def api(migrated) -> AsyncIterator[Api]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(dpe_router)
    application.dependency_overrides[get_db] = db
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield Api(c, statements)
    finally:
        await engine.dispose()


def _scope(dsn, *, datasets=("ban", "dpe"), departements=("44", "85")):
    install_scope_at(dsn, sorted(datasets), departements)


async def _document(api: Api, numero: str) -> dict:
    response = await api.client.get(f"/v1/dpe/{numero}")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["donnees"]


# --- Task 1: tracer ------------------------------------------------------------


async def test_tracer_document_read_by_its_source_number(api, migrated):
    _scope(migrated)

    document = await _document(api, NUMERO)

    assert document["numero"] == NUMERO
    assert document["source_identity"] == {
        "reference": NUMERO,
        "sources": [
            {
                "dataset": "dpe03existant",
                "url": "https://data.ademe.fr/datasets/dpe03existant",
            }
        ],
    }
    assert document["disponibilite"] == "disponible"
    assert document["methode"] == "actuelle"
    assert document["dates"] == {
        "etablissement": "2024-03-01",
        "fin_validite": "2034-03-01",
    }
    energie = document["reperes"]["etiquette_energie"]
    assert (energie["classe"]["etat"], energie["classe"]["valeur"]) == ("value", "D")
    assert energie["mesure"]["valeur"] == 17830.4
    assert energie["mesure"]["unite"] == "kwh_ep_per_year"
    # Exactly one read of the documents table, by number on the served departements.
    [lecture] = api.reads("diagnostics_dpe")
    assert "d.numero_dpe = $1" in " ".join(lecture.split())


async def test_a_number_whose_digits_contradict_its_partition_is_found(api, migrated):
    _scope(migrated)

    document = await _document(api, NUMERO_CONTRADICTOIRE)

    assert document["numero"] == NUMERO_CONTRADICTOIRE
    assert document["reperes"]["etiquette_energie"]["classe"]["valeur"] == "B"
    # No established origin: the reference is kept, no dataset is claimed.
    assert document["source_identity"]["sources"] == []


async def test_a_document_of_another_served_departement_is_found(api, migrated):
    _scope(migrated)

    document = await _document(api, NUMERO_44)

    mesure = document["reperes"]["etiquette_energie"]["mesure"]
    assert (mesure["etat"], mesure["valeur"]) == ("not_reported", None)


async def test_per_square_metre_landmarks_are_served_next_to_the_totals(api, migrated):
    _scope(migrated)

    document = await _document(api, NUMERO)

    energie = document["reperes"]["etiquette_energie"]
    climat = document["reperes"]["etiquette_climat"]
    assert (
        energie["mesure_m2"]["field_id"],
        energie["mesure_m2"]["etat"],
        energie["mesure_m2"]["valeur"],
        energie["mesure_m2"]["unite"],
    ) == ("dpe.document.conso_ep_m2", "value", 231, "kwh_ep_per_m2_per_year")
    assert (
        climat["mesure_m2"]["field_id"],
        climat["mesure_m2"]["etat"],
        climat["mesure_m2"]["valeur"],
        climat["mesure_m2"]["unite"],
    ) == ("dpe.document.emissions_ges_m2", "value", 47.5, "kg_co2_eq_per_m2_per_year")
    # The totals keep their identifier and unit (page and comparators).
    assert (energie["mesure"]["field_id"], energie["mesure"]["unite"]) == (
        "dpe.document.conso_ep",
        "kwh_ep_per_year",
    )
    assert (climat["mesure"]["valeur"], climat["mesure"]["unite"]) == (
        3433.1,
        "kg_co2_eq_per_year",
    )
    # No characteristic added: the totals move there with the redesign only.
    assert [c["field_id"] for c in document["caracteristiques"]] == [
        "dpe.document.type_batiment",
        "dpe.document.surface_habitable",
        "dpe.document.periode_construction",
        "dpe.document.energie_chauffage",
        "dpe.document.installation_chauffage",
        "dpe.document.isolation",
    ]


async def test_a_document_without_per_square_metre_values_is_not_reported(
    api, migrated
):
    _scope(migrated)

    document = await _document(api, NUMERO_CONTRADICTOIRE)

    for repere in ("etiquette_energie", "etiquette_climat"):
        valeur = document["reperes"][repere]["mesure_m2"]
        # Total and surface are served: nothing is divided, nothing is zero.
        assert (valeur["etat"], valeur["valeur"], valeur["unite"]) == (
            "not_reported",
            None,
            None,
        )
        assert document["reperes"][repere]["mesure"]["etat"] == "value"
    assert document["reperes"]["logement_decrit"]["surface"]["valeur"] == 79.0


async def test_a_retired_document_keeps_its_number_without_description(api, migrated):
    _scope(migrated)

    document = await _document(api, NUMERO_RETIRE)

    assert document["disponibilite"] == "retire"
    assert document["dates"] is None and document["reperes"] is None
    assert [s["dataset"] for s in document["source_identity"]["sources"]] == [
        "dpe03existant"
    ]
    assert document["methode"] == "inconnue"


async def test_unknown_number_names_the_served_departements(api, migrated):
    _scope(migrated)

    response = await api.client.get(f"/v1/dpe/{NUMERO_INCONNU}")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["code"] == "dpe_inconnu"
    assert body["couverture"] == {"departements": ["44", "85"]}
    assert NUMERO_INCONNU not in response.text


async def test_a_departement_outside_the_scope_is_not_read(api, migrated):
    _scope(migrated, departements=("85",))

    response = await api.client.get(f"/v1/dpe/{NUMERO_44}")

    assert response.status_code == 404
    assert response.json()["couverture"] == {"departements": ["85"]}


async def test_dpe_not_hosted_is_unknown_with_an_empty_coverage(api, migrated):
    _scope(migrated, datasets=("ban",))

    response = await api.client.get(f"/v1/dpe/{NUMERO}")

    assert response.status_code == 404
    assert response.json()["couverture"] == {"departements": []}
    assert api.reads("diagnostics_dpe") == []


async def test_malformed_number_reads_nothing(api, migrated):
    _scope(migrated)

    response = await api.client.get("/v1/dpe/2385e0000001a")

    assert response.status_code == 422
    assert response.json()["code"] == "numero_mal_forme"
    assert "2385e0000001a" not in response.text
    assert api.reads("diagnostics_dpe") == []


# --- Task 2: declared and BAN addresses, status, characteristics, guard ----------

# Documents of « 12 Rue des Lilas » (BAN_LILAS), each with its raw address
# (numero, raw address, full raw address, complement).
SENTINELLE = "SENTINELLE"
NUMERO_DIVERGENT = "2485E0000011D"
NUMERO_CONCORDANT = "2485E0000012K"
NUMERO_BRUT_SENTINELLE = "2485E0000013S"
NUMERO_COMPLEMENT = "2485E0000014C"
NUMERO_COMPLETE_SENTINELLE = "2485E0000015P"
# A range: the concordance is not evaluable (API WR-05).
NUMERO_NON_EVALUABLE = "2485E0000020N"
ADRESSES_LILAS = (
    (NUMERO_DIVERGENT, "14 rue des Lilas", None, None),
    (NUMERO_CONCORDANT, "12 rue des Lilas", None, None),
    (NUMERO_BRUT_SENTINELLE, f"12 rue des Lilas {SENTINELLE}-BRUT", None, None),
    (
        NUMERO_COMPLEMENT,
        "12 rue des Lilas",
        None,
        f"porte {SENTINELLE}-COMPLEMENT",
    ),
    (
        NUMERO_COMPLETE_SENTINELLE,
        "14 rue des Lilas",
        f"14 rue des Lilas {SENTINELLE}-COMPLETE 85000 La Roche-sur-Yon",
        None,
    ),
    (NUMERO_NON_EVALUABLE, "12-14 rue des Lilas", None, None),
)
# A document whose BAN key is absent from the hosted BAN.
NUMERO_CLE_INCONNUE = "2485E0000016U"
# A verified replacement: the ADEME declares that the new one replaces the old.
NUMERO_REMPLACE = "2185E0000017A"
NUMERO_REMPLACANT = "2485E0000018B"
# Replaced according to the lifecycle facts, but no successor row is served.
NUMERO_REMPLACANT_ABSENT = "2185E0000019Z"
_REPLACED = '{"replacement_state": "replaced", "graph_issues": [], "date_issues": []}'


def _insert_adresses_brutes(conn):
    for index, (numero, brut, complete, complement) in enumerate(ADRESSES_LILAS):
        conn.execute(
            "INSERT INTO diagnostics_dpe (id, numero_dpe, identifiant_ban, "
            "code_departement, type_batiment, etiquette_dpe, "
            "date_etablissement_dpe, date_fin_validite_dpe, lifecycle_facts, "
            "adresse_brut, adresse_complete_brut, complement_adresse_logement) "
            "VALUES (%s, %s, %s, '85', 'maison', 'D', '2024-03-01', "
            "'2034-02-28', %s::jsonb, %s, %s, %s)",
            (
                97000100 + index,
                numero,
                BAN_LILAS,
                _NO_SUCCESSOR,
                brut,
                complete,
                complement,
            ),
        )
    conn.execute(
        "INSERT INTO diagnostics_dpe (id, numero_dpe, identifiant_ban, "
        "code_departement, etiquette_dpe, lifecycle_facts, adresse_brut) VALUES "
        "(97000200, %s, '85191_9999_00001', '85', 'C', %s::jsonb, "
        "'1 rue Inconnue')",
        (NUMERO_CLE_INCONNUE, _NO_SUCCESSOR),
    )
    conn.execute(
        "INSERT INTO diagnostics_dpe (id, numero_dpe, code_departement, "
        "etiquette_dpe, date_etablissement_dpe, date_fin_validite_dpe, "
        "lifecycle_facts, numero_dpe_remplace) VALUES "
        "(97000300, %s, '85', 'E', '2021-09-01', '2031-08-31', %s::jsonb, NULL), "
        "(97000301, %s, '85', 'C', '2024-09-01', '2034-08-31', %s::jsonb, %s), "
        "(97000302, %s, '85', 'E', '2021-10-01', '2031-09-30', %s::jsonb, NULL)",
        (
            NUMERO_REMPLACE,
            _REPLACED,
            NUMERO_REMPLACANT,
            _NO_SUCCESSOR,
            NUMERO_REMPLACE,
            NUMERO_REMPLACANT_ABSENT,
            _REPLACED,
        ),
    )


@pytest.fixture(scope="module")
def adresses_brutes(migrated):
    with psycopg.connect(migrated) as conn:
        _insert_adresses_brutes(conn)
    return migrated


async def test_divergent_declared_address_published_as_typed(api, adresses_brutes):
    _scope(adresses_brutes)

    document = await _document(api, NUMERO_DIVERGENT)

    assert document["adresse_declaree"] == {
        "etat": "publiee",
        "texte": "14 rue des Lilas",
        "motif": None,
    }
    assert document["adresse_divergente"] is True
    assert document["adresse_ban"] == {
        "etat": "retenue",
        "ban_id": BAN_LILAS,
        "libelle": "12 Rue des Lilas, 85000 La Roche-sur-Yon",
        "numero": "12",
        "rep": None,
        "nom_voie": "Rue des Lilas",
        "commune": {"code_insee": "85191", "nom": "La Roche-sur-Yon"},
        "motif": None,
    }
    assert document["association"] == "source_ban_equality"
    assert document["address_check"] == "unverified"


async def test_concordant_declared_address_is_not_divergent(api, adresses_brutes):
    _scope(adresses_brutes)

    document = await _document(api, NUMERO_CONCORDANT)

    assert document["adresse_declaree"]["texte"] == "12 rue des Lilas"
    assert document["adresse_divergente"] is False


async def test_a_concordance_not_evaluable_is_unknown_not_concordant(
    api, adresses_brutes
):
    """API WR-05: ``non_evaluable`` is served ``None``, never ``False``."""
    _scope(adresses_brutes)

    document = await _document(api, NUMERO_NON_EVALUABLE)

    assert document["adresse_divergente"] is None


@pytest.mark.parametrize(
    ("numero", "attendu"),
    [
        (
            NUMERO_BRUT_SENTINELLE,
            {
                "etat": "masquee",
                "texte": None,
                "motif": "non_verifiable_sans_complement",
            },
        ),
        (
            NUMERO_COMPLEMENT,
            {"etat": "masquee", "texte": None, "motif": "complement_present"},
        ),
        (
            NUMERO_COMPLETE_SENTINELLE,
            {"etat": "publiee", "texte": "14 rue des Lilas", "motif": None},
        ),
    ],
    ids=["adresse_brut", "complement_adresse_logement", "adresse_complete_brut"],
)
async def test_no_raw_address_sentinel_leaves_the_api(
    api, adresses_brutes, numero, attendu, caplog
):
    _scope(adresses_brutes)

    response = await api.client.get(f"/v1/dpe/{numero}")

    assert response.status_code == 200, response.text
    assert response.json()["donnees"]["adresse_declaree"] == attendu
    assert SENTINELLE not in response.text
    assert all(SENTINELLE not in record.getMessage() for record in caplog.records)
    assert all(numero not in record.getMessage() for record in caplog.records)


async def test_a_key_absent_from_the_ban_is_not_a_retained_address(
    api, adresses_brutes
):
    _scope(adresses_brutes)

    document = await _document(api, NUMERO_CLE_INCONNUE)

    assert document["adresse_ban"]["etat"] == "cle_inconnue"
    assert document["adresse_ban"]["ban_id"] is None
    assert document["adresse_declaree"]["etat"] == "masquee"


async def test_ban_not_hosted_still_serves_the_document(api, adresses_brutes):
    _scope(adresses_brutes, datasets=("dpe",))

    document = await _document(api, NUMERO_DIVERGENT)

    assert document["adresse_ban"]["etat"] == "hors_couverture"
    assert document["adresse_declaree"]["etat"] == "masquee"
    assert document["reperes"]["etiquette_energie"]["classe"]["valeur"] == "D"
    assert api.reads("adresses") == []


async def test_verified_replacement_names_both_documents(api, adresses_brutes):
    _scope(adresses_brutes)

    ancien = await _document(api, NUMERO_REMPLACE)
    nouveau = await _document(api, NUMERO_REMPLACANT)

    assert ancien["statut"]["code"] == "remplace"
    assert ancien["statut"]["remplace_par"] == NUMERO_REMPLACANT
    assert nouveau["statut"]["code"] == "en_vigueur"
    assert nouveau["statut"]["remplace"] == NUMERO_REMPLACE


async def test_replacement_without_a_served_successor_is_still_replaced(
    api, adresses_brutes
):
    """Decision n° 24 (API WR-01): « Remplacé selon l'ADEME », no number."""
    _scope(adresses_brutes)

    document = await _document(api, NUMERO_REMPLACANT_ABSENT)

    assert document["statut"]["code"] == "remplace"
    assert document["statut"]["remplace_par"] is None


async def test_landmarks_characteristics_and_provenance_on_the_fixture(
    api, adresses_brutes
):
    _scope(adresses_brutes)

    document = await _document(api, NUMERO)

    reperes = document["reperes"]
    assert reperes["etiquette_climat"]["mesure"]["valeur"] == 3433.1
    assert reperes["cout_annuel"]["valeur"] == 1834.2
    assert reperes["logement_decrit"]["surface"]["valeur"] == 79.0
    caracteristiques = {
        c["field_id"]: (c["etat"], c["valeur"]) for c in document["caracteristiques"]
    }
    assert caracteristiques["dpe.document.periode_construction"] == (
        "value",
        "1948-1974",
    )
    assert caracteristiques["dpe.document.isolation"] == ("not_reported", None)
    assert document["statut"]["code"] == "en_vigueur"
    assert document["provenance"]["source"] == "ADEME"


async def test_refused_publication_is_a_no_store_503_on_the_mounted_app(
    adresses_brutes, client, monkeypatch
):
    """The real router of ``app.main``: ``require_publication`` not overridden."""
    from app.main import app
    from app.middleware import RequestLoggingMiddleware

    _scope(adresses_brutes)
    engine = create_async_engine(
        adresses_brutes.replace("postgresql://", "postgresql+asyncpg://")
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(RequestLoggingMiddleware, "_log_to_db", AsyncMock())
    try:
        servi = await client.get(f"/v1/dpe/{NUMERO}")
        assert servi.status_code == 200, servi.text
        with psycopg.connect(adresses_brutes) as conn:
            conn.execute(
                "INSERT INTO etl_publications(generation, dataset_name, "
                "table_name, status) VALUES (gen_random_uuid(), 'dpe', "
                "'diagnostics_dpe', 'pending')"
            )
        refuse = await client.get(f"/v1/dpe/{NUMERO}")
        assert refuse.status_code == 503
        assert refuse.headers["cache-control"] == "no-store"
        assert "donnees" not in refuse.json()
        assert NUMERO not in refuse.text
    finally:
        with psycopg.connect(adresses_brutes) as conn:
            conn.execute("DELETE FROM etl_publications WHERE dataset_name = 'dpe'")
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
