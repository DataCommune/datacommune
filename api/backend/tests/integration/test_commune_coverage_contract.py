"""COR-14: adversarial populations through canonical SQL and real HTTP."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from psycopg import sql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.batiments.queries import batiments_par_point
from app.commune.dependencies import COMMUNE_INTERMEDIATES
from app.commune.matview_ddl import (
    COMMUNE_VIEW_NAMES,
    commune_view_drift,
    recreate_commune_synthese,
)
from app.commune.router import router
from app.config import settings
from app.database import get_db
from scripts.check_commune_views import main as check_commune_views
from tests.integration.eligibilite_fixture import inserer_offre
from tests.integration.instance_scope_fixture import (
    COMMUNE_SOURCES,
    install_instance_scope,
)
from tests.integration.publication_fixture import install_publication_journal
from tests.integration.test_dpe_consumer_population import _TABLES


@pytest.fixture
def corpus(isolated_pg_dsn):
    schema = "cor14_" + uuid4().hex
    with psycopg.connect(isolated_pg_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    conn = psycopg.connect(
        isolated_pg_dsn,
        autocommit=True,
        options=f"-c search_path={schema},public -c statement_timeout=15000",
    )
    tables = {
        **_TABLES,
        "equipements": "code_commune text, nb_equipements int, annee int",
        "monuments_historiques": "code_insee text, reference text",
        "vacance": """code_commune text, nom_departement text, nom_region text,
            taux_vacance numeric, nb_vacants_plus_2ans int, annee int,
            nb_logements_prives int, nb_logements_vacants int""",
        "geo_arrondissements": "code text, departement_code text",
    }
    try:
        for name, columns in tables.items():
            conn.execute(f"CREATE TABLE {name} ({columns})")
        install_publication_journal(conn)
        # /v1/commune reads the scope first: host its thirteen sources.
        install_instance_scope(conn, COMMUNE_SOURCES, ("44", "85"))
        conn.execute("""INSERT INTO demographie
            (code_insee, nom_commune, code_departement, population, annee)
            VALUES ('44991', 'Ancienne', '44', 100, 2022),
                   ('44992', 'Courante', '44', 200, 2025),
                   ('85991', 'Couverture inconnue', '85', 300, 2024)""")
        yield conn, isolated_pg_dsn, schema
    finally:
        conn.close()
        with psycopg.connect(isolated_pg_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


BACKEND = Path(__file__).resolve().parents[2]
# The communal counters of KI-D4 and their sibling indicators.
KI_D4_COUNTERS = (
    "nb_equipements",
    "nb_etablissements_scolaires",
    "nb_monuments",
    "nb_proprietaires_pm",
    "fibre_pct",
    "taux_vacance",
    "nb_mutations_12m",
    "nb_mutations_12m_maison",
    "nb_mutations_12m_appartement",
)
# Tables the migrated synthesis reads; emptied between migration cases.
_MIGRATED_TABLES = (
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
    "eligibilite_offres",
)
_BASELINE_DEMOGRAPHIE = """INSERT INTO demographie
    (code_insee, nom_commune, code_departement, population, annee)
    VALUES ('44991', 'Ancienne', '44', 100, 2022),
           ('44992', 'Courante', '44', 200, 2025),
           ('85991', 'Couverture inconnue', '85', 300, 2024)"""


def _migrate(dsn, action, revision):
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", dsn)
        patch.setattr(settings, "database_url", dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", dsn)
        action(cfg, revision)


@pytest.fixture(scope="module")
def migrated_dsn(pg_dsn):
    """A child database built by the migration chain only (``upgrade head``).

    Its commune views keep the definitions the migrations installed (098
    included); they are refreshed, never rebuilt by
    ``recreate_commune_synthese``.
    """
    database = "commune_migrated_" + uuid4().hex
    child = urlunsplit(urlsplit(pg_dsn)._replace(path="/" + database))
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    try:
        with psycopg.connect(child, connect_timeout=5) as conn:
            for extension in ("postgis", "pg_trgm", "unaccent"):
                conn.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
        _migrate(child, command.upgrade, "head")
        yield child
    finally:
        with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )


@pytest.fixture
def migrated(migrated_dsn):
    conn = psycopg.connect(migrated_dsn, autocommit=True)
    try:
        conn.execute(f"TRUNCATE {', '.join(_MIGRATED_TABLES)}")
        conn.execute(_BASELINE_DEMOGRAPHIE)
        install_instance_scope(conn, COMMUNE_SOURCES, ("44", "85"))
        yield conn, migrated_dsn, None
    finally:
        conn.close()


@pytest.fixture(params=["reconstruction", "migration"])
def built(request):
    """Both ways the commune view is created, with the same three communes.

    ``reconstruction``: the application DDL (``recreate_commune_synthese``),
    run by every commune-source publication. ``migration``: the definitions
    left by ``alembic upgrade head`` (migration 088 for the synthesis).
    """
    if request.param == "reconstruction":
        conn, dsn, schema = request.getfixturevalue("corpus")
    else:
        conn, dsn, schema = request.getfixturevalue("migrated")
    return conn, dsn, schema, request.param


# Required columns of the migrated tables that the bounded corpus omits.
_MIGRATED_DEFAULTS = {
    "equipements": {"type_equipement": "A101"},
    "eligibilite_fixe": {},
}


def _insert(conn, construction, table, columns, rows):
    """Insert *rows* in either schema; the migrated one needs more columns."""
    if table == "eligibilite_fixe":
        # Source offer rows into the per-building shape (plan 04.1-16).
        for code_insee, id_imb, technologie in rows:
            dep = (
                {"code_departement": code_insee[:2]}
                if construction == "migration"
                else {}
            )
            inserer_offre(
                conn,
                technologie=technologie,
                code_insee=code_insee,
                id_imb=id_imb,
                **dep,
            )
        return
    extra = _MIGRATED_DEFAULTS.get(table) if construction == "migration" else None
    names = list(columns)
    if extra is not None:
        names += [*extra, "code_departement"]
    for row in rows:
        values = list(row)
        if extra is not None:
            values += [*extra.values(), row[0][:2]]
        conn.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(", ").join(map(sql.Identifier, names)),
                sql.SQL(", ").join(sql.Placeholder() * len(values)),
            ),
            values,
        )


def documents(conn, construction="reconstruction"):
    if construction == "reconstruction":
        recreate_commune_synthese(conn)
    else:
        for view in COMMUNE_INTERMEDIATES.values():
            conn.execute(f"REFRESH MATERIALIZED VIEW {view}")
        conn.execute("REFRESH MATERIALIZED VIEW v_commune_synthese")
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT code_insee, to_jsonb(v) FROM v_commune_synthese v"
        )
    }


def test_latest_year_is_local_and_rental_types_keep_their_year(corpus):
    conn, _, _ = corpus
    conn.execute("""INSERT INTO revenus (code_insee, revenu_median, annee)
        VALUES ('44991', 12345, 2021), ('44991', 11111, 2020),
               ('44992', 22222, 2024)""")
    conn.execute("""INSERT INTO fiscalite_locale (code_insee, taux_tfpb, annee)
        VALUES ('44991', 12.5, 2022), ('44992', 19, 2025)""")
    conn.execute("""INSERT INTO vacance
        (code_commune, taux_vacance, annee, nb_logements_prives, nb_logements_vacants)
        VALUES ('44991', 10, 2020, 100, 10), ('44992', 5, 2025, 100, 5)""")
    conn.execute("""INSERT INTO indicateurs_loyers
        (code_insee, type_habitat, loyer_m2, annee) VALUES
        ('44991', 'appartement', 9, 2020), ('44991', 'appartement', 10, 2022),
        ('44991', 'maison', 12, 2021), ('44992', 'maison', 14, 2025)""")
    data = documents(conn)
    assert set(data) == {"44991", "44992", "85991"}
    old = data["44991"]
    assert (old["population"], old["annee_demographie"]) == (100, 2022)
    assert (old["revenu_median"], old["annee_revenus"]) == (12345, 2021)
    assert (old["taux_tfpb"], old["annee_fiscalite"]) == (12.5, 2022)
    assert (old["taux_vacance"], old["annee_vacance"]) == (10, 2020)
    assert (old["loyer_m2_appartement"], old["loyer_m2_maison"]) == (10, 12)
    assert old["loyers_appartement"]["annee"] == 2022
    assert old["loyers_maison"]["annee"] == 2021
    assert old["annee_loyers"] is None
    assert data["44992"]["annee_loyers"] == 2025
    # Rebuild after reversed insertion order: no physical-order selection.
    conn.execute("CREATE TEMP TABLE saved_rents AS SELECT * FROM indicateurs_loyers")
    conn.execute("TRUNCATE indicateurs_loyers")
    conn.execute(
        "INSERT INTO indicateurs_loyers SELECT * FROM saved_rents ORDER BY annee DESC"
    )
    assert documents(conn) == data


def _zero_and_unknown_scenario(conn, construction):
    """44991: attested zeros; 44992: unknown values; 85991: no source row."""
    _insert(
        conn,
        construction,
        "equipements",
        ("code_commune", "nb_equipements", "annee"),
        [("44991", 25, 2021), ("44991", 0, 2022), ("44992", None, 2025)],
    )
    _insert(
        conn,
        construction,
        "eligibilite_fixe",
        ("code_insee", "id_imb", "technologie"),
        [
            ("44991", "building-one", "Cuivre"),
            ("44991", "building-one", "Cuivre"),
            ("44992", "", "FO"),
        ],
    )
    conn.execute("""INSERT INTO vacance
        (code_commune, taux_vacance, annee, nb_logements_prives, nb_logements_vacants)
        VALUES ('44991', 0, 2022, 100, 0), ('44992', 0, 2025, NULL, 0)""")
    return documents(conn, construction)


def test_unknown_collection_is_null_and_attested_source_zero_survives(built):
    conn, _, _, construction = built
    data = _zero_and_unknown_scenario(conn, construction)
    assert "44991" in data and "85991" in data
    assert data["44991"]["nb_equipements"] == 0
    assert data["44991"]["annee_equipements"] == 2022
    assert data["44991"]["fibre_pct"] == 0
    assert data["44991"]["taux_vacance"] == 0
    assert data["44992"]["taux_vacance"] is None
    assert data["44992"]["nb_equipements"] is None
    assert data["44992"]["fibre_pct"] is None
    for field in (
        "nb_equipements",
        "nb_etablissements_scolaires",
        "nb_monuments",
        "nb_proprietaires_pm",
        "fibre_pct",
    ):
        assert data["85991"][field] is None


def test_migration_098_installs_and_restores_the_view_chain(migrated_dsn, capsys):
    """098 applies the application DDL; downgrade restores the saved chain.

    The operator command of the restore runbook
    (``python -m scripts.check_commune_views``) runs on both states.
    """
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        assert commune_view_drift(conn) == []
    assert check_commune_views(migrated_dsn) == 0
    assert capsys.readouterr().out == "COMMUNE VIEWS OK\n"
    _migrate(migrated_dsn, command.downgrade, "097_instance_scope")
    try:
        with psycopg.connect(migrated_dsn, autocommit=True) as conn:
            drift = commune_view_drift(conn)
            backup = conn.execute(
                "SELECT to_regclass('commune_matview_definitions_098')"
            ).fetchone()
        # Counter-proof of the check: the 067/068/088 chain is back, and every
        # view drifts from the application DDL -- v_commune_dvf included since
        # migration 111 reads a PLM commune over its arrondissements (04.7-09).
        assert drift == list(COMMUNE_VIEW_NAMES)
        assert backup == (None,)
        assert check_commune_views(migrated_dsn) == 1
        assert "COMMUNE VIEW DRIFT v_commune_synthese" in capsys.readouterr().out
    finally:
        _migrate(migrated_dsn, command.upgrade, "head")
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        assert commune_view_drift(conn) == []


def test_migration_098_waits_for_the_publication_lock(migrated_dsn):
    """WR-06: 098 never rebuilds the chain while a publication holds the lock.

    The lock_timeout travels in the migration URL (``options``, long form
    without ``%`` or space: migrations/env.py copies the URL into Alembic's
    interpolating config, where a ``%`` would be refused).
    """
    lock_id = 0x434F523230
    separator = "&" if "?" in migrated_dsn else "?"
    impatient = f"{migrated_dsn}{separator}options=--lock_timeout=1000"
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        head = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    holder = psycopg.connect(migrated_dsn, autocommit=True)
    try:
        holder.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
        with pytest.raises(Exception, match="lock timeout"):
            _migrate(impatient, command.downgrade, "097_instance_scope")
    finally:
        holder.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
        holder.close()
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        # Unchanged: the refused downgrade rolled back as a whole.
        assert version == head
        assert commune_view_drift(conn) == []


def test_ki_d4_counters_on_both_constructions(built):
    """KI-D4 counters of one scenario: the same values on both constructions."""
    conn, _, _, construction = built
    data = _zero_and_unknown_scenario(conn, construction)
    measured = {
        code: {counter: data[code].get(counter, "absent") for counter in KI_D4_COUNTERS}
        for code in sorted(data)
    }
    assert measured == EXPECTED_KI_D4


# Measured 2026-09-22 (plan 02-09): an unknown count stays null, an
# attested zero stays 0. Before migration 098 the migrated chain (088) served
# 0 for every unknown counter and summed equipements over every vintage (25);
# since 098 both constructions serve the same values.
EXPECTED_KI_D4: dict = {
    "44991": {
        "nb_equipements": 0,
        "nb_etablissements_scolaires": None,
        "nb_monuments": None,
        "nb_proprietaires_pm": None,
        "fibre_pct": 0.0,
        "taux_vacance": 0,
        "nb_mutations_12m": None,
        "nb_mutations_12m_maison": None,
        "nb_mutations_12m_appartement": None,
    },
    "44992": {
        "nb_equipements": None,
        "nb_etablissements_scolaires": None,
        "nb_monuments": None,
        "nb_proprietaires_pm": None,
        "fibre_pct": None,
        "taux_vacance": None,
        "nb_mutations_12m": None,
        "nb_mutations_12m_maison": None,
        "nb_mutations_12m_appartement": None,
    },
    "85991": {
        "nb_equipements": None,
        "nb_etablissements_scolaires": None,
        "nb_monuments": None,
        "nb_proprietaires_pm": None,
        "fibre_pct": None,
        "taux_vacance": None,
        "nb_mutations_12m": None,
        "nb_mutations_12m_maison": None,
        "nb_mutations_12m_appartement": None,
    },
}


def test_one_to_many_branches_count_their_documented_grain(corpus):
    conn, _, _ = corpus
    conn.execute("INSERT INTO demographie (code_insee, annee) VALUES ('44991', 2025)")
    conn.execute("""INSERT INTO indicateurs_loyers VALUES
        ('44991', 'appartement', 10, 2025), ('44991', 'maison', 12, 2025),
        ('44991', '1-2_pieces', 15, 2025), ('44991', '3+_pieces', 9, 2025)""")
    conn.execute(
        "INSERT INTO equipements VALUES ('44991', 2, 2025), ('44991', 3, 2025)"
    )
    conn.execute(
        "INSERT INTO carte_scolaire VALUES "
        "('44991', 'RNE-A'), ('44991', 'RNE-A'), ('44991', 'RNE-B')"
    )
    conn.execute(
        "INSERT INTO monuments_historiques VALUES "
        "('44991', 'PA-A'), ('44991', 'PA-A'), ('44991', 'PA-B')"
    )
    conn.execute("""INSERT INTO foncier_pm VALUES
        ('44991', 'SIREN-A', 'PARCELLE-A', 10000),
        ('44991', 'SIREN-B', 'PARCELLE-A', 10000),
        ('44991', 'SIREN-A', 'PARCELLE-A', 10000)""")
    for id_imb, technologie in (("A", "FO"), ("A", "FO"), ("B", "Cuivre")):
        inserer_offre(conn, technologie=technologie, code_insee="44991", id_imb=id_imb)
    row = documents(conn)["44991"]
    assert row["nb_equipements"] == 5
    assert row["nb_etablissements_scolaires"] == 2
    assert row["nb_monuments"] == 2
    assert row["nb_proprietaires_pm"] == 2
    assert row["surface_pm_ha"] == 1
    assert row["fibre_pct"] == 50


def test_partial_source_populations_do_not_produce_totals_or_zero(corpus):
    conn, _, _ = corpus
    conn.execute("""INSERT INTO equipements VALUES
        ('44991', 0, 2022), ('44991', NULL, 2022)""")
    conn.execute("""INSERT INTO carte_scolaire VALUES
        ('44991', 'RNE-A'), ('44991', '   ')""")
    conn.execute("""INSERT INTO monuments_historiques VALUES
        ('44991', 'PA-A'), ('44991', NULL)""")
    conn.execute("""INSERT INTO foncier_pm VALUES
        ('44991', 'SIREN-A', 'PARCELLE-A', 10000),
        ('44991', NULL, 'PARCELLE-A', NULL)""")
    for id_imb, technologie in (("A", "FO"), ("B", None)):
        inserer_offre(conn, technologie=technologie, code_insee="44991", id_imb=id_imb)
    data = documents(conn)["44991"]
    for field in (
        "nb_equipements",
        "nb_etablissements_scolaires",
        "nb_monuments",
        "nb_proprietaires_pm",
        "surface_pm_ha",
        "fibre_pct",
    ):
        assert data[field] is None


def test_latest_missing_value_is_not_backfilled_from_an_older_vintage(corpus):
    conn, _, _ = corpus
    conn.execute("""INSERT INTO revenus (code_insee, revenu_median, annee)
        VALUES ('44991', 12345, 2021), ('44991', NULL, 2023)""")
    row = documents(conn)["44991"]
    assert row["revenu_median"] is None
    assert row["annee_revenus"] == 2023


def _engine(dsn, schema):
    connect_args = (
        {"server_settings": {"search_path": f"{schema},public"}} if schema else {}
    )
    return create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args=connect_args,
    )


@pytest.mark.asyncio
async def test_real_http_keeps_null_and_source_zero(built, tmp_path):
    conn, dsn, schema, construction = built
    _insert(
        conn,
        construction,
        "equipements",
        ("code_commune", "nb_equipements", "annee"),
        [("44991", 0, 2022)],
    )
    conn.execute("""INSERT INTO indicateurs_loyers
        (code_insee, type_habitat, loyer_m2, annee) VALUES
        ('44991', 'appartement', 10, 2022), ('44991', 'maison', 12, 2021)""")
    documents(conn, construction)
    engine = _engine(dsn, schema)
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
            for code in ("44991", "85991"):
                response = await client.get("/v1/commune", params={"code_insee": code})
                assert response.status_code == 200, response.text
                payloads[code] = response.json()
                assert len(response.json()["donnees"]) == 1
        assert payloads["44991"]["donnees"][0]["nb_equipements"] == 0
        assert payloads["44991"]["donnees"][0]["annee_equipements"] == 2022
        assert payloads["44991"]["donnees"][0]["annee_loyers"] is None
        assert payloads["44991"]["donnees"][0]["loyers_appartement"]["annee"] == 2022
        assert payloads["44991"]["donnees"][0]["loyers_maison"]["annee"] == 2021
        assert payloads["85991"]["donnees"][0]["nb_equipements"] is None
        if target := os.environ.get("COR14_API_FIXTURE_OUTPUT"):
            from pathlib import Path

            Path(target).write_text(
                json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_non_hosted_source_serves_null_over_a_stored_zero(corpus):
    """SEM-01 on real HTTP: the table holds 0, the instance does not host it."""
    conn, dsn, schema = corpus
    conn.execute("INSERT INTO equipements VALUES ('44991', 0, 2022)")
    documents(conn)
    stored = conn.execute(
        "SELECT nb_equipements FROM v_commune_synthese WHERE code_insee = '44991'"
    ).fetchone()
    assert stored == (0,)
    engine = _engine(dsn, schema)
    sessions = async_sessionmaker(engine)
    app = FastAPI()
    app.include_router(router)

    async def database():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = database
    served = {}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://fixture"
        ) as client:
            for label, sources in (
                ("without", set(COMMUNE_SOURCES) - {"equipements"}),
                ("with", set(COMMUNE_SOURCES)),
            ):
                install_instance_scope(conn, sources, ("44", "85"))
                response = await client.get(
                    "/v1/commune", params={"code_insee": "44991"}
                )
                assert response.status_code == 200, response.text
                served[label] = response.json()["donnees"][0]
    finally:
        await engine.dispose()
    assert served["without"]["nb_equipements"] is None
    assert served["without"]["annee_equipements"] is None
    assert served["without"]["couverture"]["equipements"] == {
        "etat": "hors_couverture",
        "motif": "dataset_non_heberge",
        "departements": [],
    }
    assert served["with"]["nb_equipements"] == 0
    assert served["with"]["couverture"]["equipements"]["etat"] == "heberge"
    # Other sources are unchanged by the equipements scope.
    assert served["without"]["population"] == served["with"]["population"] == 100


@pytest.mark.asyncio
async def test_bdtopo_border_occurrences_do_not_inflate_pagination(corpus):
    conn, dsn, schema = corpus
    conn.execute("""CREATE TABLE batiments (
        cleabs text, nature text, usage_1 text, nombre_d_etages int,
        hauteur numeric, altitude_minimale_sol numeric, altitude_maximale_toit numeric,
        geometrie geometry(Polygon,4326), code_departement text)""")
    for key, department, x in (("A", "44", 0), ("A", "85", 0), ("B", "85", 0.001)):
        conn.execute(
            """INSERT INTO batiments (cleabs, hauteur, geometrie, code_departement)
            VALUES (%s, 10, ST_MakeEnvelope(%s, 0, %s, 0.0001, 4326), %s)""",
            (key, x, x + 0.0001, department),
        )
    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    try:
        async with async_sessionmaker(engine)() as session:
            all_rows = await batiments_par_point(session, 0, 0, rayon_m=200)
            pages = [
                await batiments_par_point(session, 0, 0, rayon_m=200, limit=1, offset=i)
                for i in range(3)
            ]
        assert [row["cleabs"] for row in all_rows] == ["A", "B"]
        assert [len(page) for page in pages] == [1, 1, 0]
        assert pages[0][0]["cleabs"] != pages[1][0]["cleabs"]
    finally:
        await engine.dispose()
