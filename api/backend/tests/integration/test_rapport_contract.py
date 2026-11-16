"""04.7-01: the address report on PostGIS, its cache and the KNN sorts of C10.

``GET /v1/adresse/{ban_id}/rapport`` composes the readers of the address page
and the dataset queries on a migrated disposable database: every SQL of the
themes runs for real (no theme in outage), the DPE of several buildings is
said, a point on two parcels attributes nothing, the facts of the commune are
counted apart, and no live source is called when it is not hosted.

The measures print a ``MESURE`` line (same addresses, same machine, same
volume): the report without and with its cache, and the two KNN sorts of
audit C10 (geometry ``<->`` without its dropped index, geography ``<->`` on
the index kept by migration 107) on a dense synthetic city.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_rapport_contract.py
"""

import json
import statistics
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import psycopg
import pytest
import respx
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.exceptions import register_exception_handlers
from app.config import settings
from app.database import get_db
from app.rapport import cache
from app.rapport.registre import THEMES
from app.rapport.router import rapport_router
from tests.integration.eligibilite_fixture import inserer_offre
from tests.integration.instance_scope_fixture import install_scope_at

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
HALF = 0.0002  # half side of a parcel square, about 15 m
METRES_PAR_DEGRE_LAT = 111_150
COMMUNE = "85191"
# Points 0.001 degree apart (about 76 m): no parcel overlaps another.
A = ("85191_0701_00002", (-1.4000, 46.6000), "85191000RA0001")
B = ("85191_0702_00001", (-1.4010, 46.6000), "85191000RB0001")
FRONTIERE = ("85191_0703_00001", (-1.4020, 46.6000), None)
HEBERGES = (
    "ban",
    "cadastre",
    "dpe",
    "rnb",
    "batiments",
    "copropriete",
    "connectivite",
    "transports",
    "gares",
    "equipements",
    "antennes",
    "loyers",
    "zonage_abc",
    "fiscalite",
    "revenus",
)
NON_HEBERGES = (
    "dvf",
    "commune",
    "georisques",
    "urbanisme",
    "errial",
    "bdnb",
    "altimetrie",
    "geologie",
    "axes_transport",
    "peb",
    "delinquance",
    "eau_potable",
    "annuaire_education",
    "qualite_air",
    "filosofi_carreaux",
    "iris",
    "zone_tendue",
    "encadrement_communes",
)


def _square(lon: float, lat: float, half: float = HALF) -> str:
    x0, x1, y0, y1 = lon - half, lon + half, lat - half, lat + half
    return f"MULTIPOLYGON((({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0})))"


def _rect(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"MULTIPOLYGON((({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0})))"


def _au_nord(point: tuple[float, float], metres: float) -> tuple[float, float]:
    lon, lat = point
    return lon, lat + metres / METRES_PAR_DEGRE_LAT


def _point_sql() -> str:
    return "ST_SetSRID(ST_MakePoint(%s, %s), 4326)"


def migrate(dsn: str) -> None:
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", dsn)
        context.setattr(settings, "database_url", dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", dsn)
        command.upgrade(cfg, "head")


def _adresse(conn, id_, ban_id, point, numero, voie="Rue du Rapport"):
    conn.execute(
        "INSERT INTO adresses (id, code_departement, ban_id, numero, nom_voie, "
        "code_postal, code_commune, nom_commune, position) VALUES (%s, '85', %s, "
        f"%s, %s, '85000', '85191', 'La Roche-sur-Yon', {_point_sql()})",
        (id_, ban_id, numero, voie, *point),
    )


def _parcelle(conn, id_, id_parcelle, wkt):
    conn.execute(
        "INSERT INTO parcelles (id, code_departement, id_parcelle, commune, "
        "contenance, geometrie) VALUES (%s, '85', %s, '85191', 512, "
        "ST_GeomFromText(%s, 4326))",
        (id_, id_parcelle, wkt),
    )


def _dpe(conn, id_, numero, ban_id, classe, id_rnb):
    conn.execute(
        "INSERT INTO diagnostics_dpe (id, numero_dpe, identifiant_ban, "
        "code_departement, code_insee, type_batiment, etiquette_dpe, "
        "date_etablissement_dpe, date_fin_validite_dpe, id_rnb, lifecycle_facts) "
        "VALUES (%s, %s, %s, '85', '85191', 'appartement', %s, '2022-03-01', "
        "'2032-03-01', %s, %s::jsonb)",
        (
            id_,
            numero,
            ban_id,
            classe,
            id_rnb,
            '{"replacement_state": "no_successor_known", "graph_issues": [], '
            '"date_issues": []}',
        ),
    )


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    migrate(isolated_pg_dsn)
    with psycopg.connect(isolated_pg_dsn) as conn:
        for code, nom in (("44", "Loire-Atlantique"), ("85", "Vendée")):
            conn.execute(
                "INSERT INTO geo_departements (code, nom, geometry) VALUES "
                "(%s, %s, ST_GeomFromText(%s, 4326))",
                (code, nom, _square(0, 0, 0.1)),
            )
        conn.execute(
            "INSERT INTO geo_communes (code, nom, departement_code, geometry) "
            "VALUES ('85191', 'La Roche-sur-Yon', '85', ST_GeomFromText(%s, 4326))",
            (_square(-1.4, 46.6, 0.1),),
        )
        for index, (ban_id, point, parcelle) in enumerate((A, B, FRONTIERE)):
            _adresse(conn, 95_700_001 + index, ban_id, point, ban_id[-1])
            if parcelle:
                _parcelle(conn, 96_700_001 + index, parcelle, _square(*point))
        x, y = FRONTIERE[1]
        gauche = _rect(x - HALF, y - HALF, x, y + HALF)
        droite = _rect(x, y - HALF, x + HALF, y + HALF)
        _parcelle(conn, 96_700_100, "85191000RC0001", gauche)
        _parcelle(conn, 96_700_101, "85191000RC0002", droite)
        # A: one building covering the point, a registered condominium, the RNB
        # linking one building to the key, three DPE naming three buildings.
        conn.execute(
            "INSERT INTO batiments (id, code_departement, cleabs, usage_1, "
            "nombre_d_etages, hauteur, geometrie) VALUES (97700001, '85', "
            "'BATIMENT_A', 'Résidentiel', 4, 12.0, ST_GeomFromText(%s, 4326))",
            (_square(*A[1], 0.00005),),
        )
        conn.execute(
            "INSERT INTO copropriete (numero_immatriculation, nom_copropriete, "
            "id_parcelle_1, nb_lots_total, nb_lots_habitation_strict, type_syndic, "
            "nom_syndic, mandat_en_cours, date_fin_dernier_mandat) "
            "VALUES ('AA0000001', 'Residence du Rapport', %s, 24, 18, "
            "'professionnel', 'Cabinet du Rapport', 'mandat en cours', '2027-06-30')",
            (A[2],),
        )
        conn.execute(
            "INSERT INTO rnb_liens (id_rnb, cle_ban, code_departement, statut, "
            "parcelles) VALUES ('RNBA00000001', %s, '85', 'constructed', ARRAY[%s])",
            (A[0], A[2]),
        )
        for rang, (classe, rnb) in enumerate(
            (("F", "RNBA00000001"), ("F", "RNBA00000002"), ("D", "RNBA00000003"))
        ):
            _dpe(conn, 98_700_001 + rang, f"2285E000000{rang}A", A[0], classe, rnb)
        # B: one DPE of one building; nothing published for its broadband.
        _dpe(conn, 98_700_010, "2285E0000010B", B[0], "B", "RNBB00000001")
        inserer_offre(
            conn,
            technologie="FO",
            debit_max="THD1G",
            operateur="OPE",
            id_imb="IMB-A",
            code_insee=COMMUNE,
            voie="RUE DU RAPPORT",
            numero="2",
            complement=None,
            code_departement="85",
        )
        lon, lat = _au_nord(A[1], 180)
        conn.execute(
            "INSERT INTO arrets_transport (stop_id, stop_name, position) VALUES "
            f"('S1', 'Place du Rapport', {_point_sql()})",
            (lon, lat),
        )
        # The nearest station is read beyond the radius of the stops
        # (04.7-07): one at 3 km, one farther.
        for nom, metres in (("Gare du Rapport", 3000), ("Gare lointaine", 9000)):
            conn.execute(
                f"INSERT INTO gares (nom_gare, position) VALUES (%s, {_point_sql()})",
                (nom, *_au_nord(A[1], metres)),
            )
        for rang, (domaine, metres) in enumerate(
            (
                ("commerces", 100),
                ("commerces", 400),
                ("sante_social", 300),
                ("enseignement", 700),
                ("commerces", 2500),
                ("tourisme", 150),
            )
        ):
            lon, lat = _au_nord(A[1], metres)
            conn.execute(
                "INSERT INTO equipements (type_equipement, code_commune, "
                "code_departement, domaine, nb_equipements, annee, geom) VALUES "
                f"(%s, '85191', '85', %s, %s, 2024, {_point_sql()})",
                (f"T{rang}", domaine, 0 if domaine == "tourisme" else 1, lon, lat),
            )
        for support, generation, metres in (
            ("S1", "5G", 200),
            ("S1", "4G", 200),
            ("S2", "5G NR 3500", 800),
            ("S3", "4G", 600),
            ("S4", "5G", 3000),
        ):
            lon, lat = _au_nord(A[1], metres)
            conn.execute(
                "INSERT INTO antennes (operateur, generation, numero_support, "
                f"code_insee, geom) VALUES ('OPE', %s, %s, '85191', {_point_sql()})",
                (generation, support, lon, lat),
            )
        conn.execute(
            "INSERT INTO indicateurs_loyers (code_insee, nom_commune, type_habitat, "
            "loyer_m2, annee) VALUES ('85191', 'La Roche-sur-Yon', 'appartement', "
            "10.5, 2025), ('85191', 'La Roche-sur-Yon', 'maison', 9.8, 2025)"
        )
        conn.execute(
            "INSERT INTO zonage_abc (code_insee, nom_commune, code_departement, zone) "
            "VALUES ('85191', 'La Roche-sur-Yon', '85', 'B1')"
        )
        conn.execute(
            "INSERT INTO fiscalite_locale (code_insee, code_departement, taux_tfpb, "
            "annee, majoration_thrs, taux_majoration_thrs) VALUES "
            "('85191', '85', 45.2, 2024, true, 20), ('85047', '85', 35.0, 2024, false, "
            "NULL), ('85047', '85', 99.0, 2023, false, NULL)"
        )
        conn.execute(
            "INSERT INTO revenus (code_insee, revenu_median, annee) "
            "VALUES ('85191', 22500, 2021)"
        )
    install_scope_at(isolated_pg_dsn, sorted(HEBERGES), ("44", "85"))
    return isolated_pg_dsn


@pytest.fixture
async def client(migrated) -> AsyncIterator[httpx.AsyncClient]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(rapport_router)
    application.dependency_overrides[get_db] = db
    transport = httpx.ASGITransport(app=application)
    cache.vider()
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c
    finally:
        cache.vider()
        await engine.dispose()


async def rapport(client: httpx.AsyncClient, ban_id: str) -> dict:
    # No live source is hosted: respx refuses any call that would leave.
    with respx.mock(assert_all_called=False):
        response = await client.get(f"/v1/adresse/{ban_id}/rapport")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["donnees"]


def themes(donnees: dict) -> dict[str, dict]:
    return {theme["id"]: theme for theme in donnees["themes"]}


async def test_every_theme_sql_runs_and_no_theme_is_in_outage(client):
    donnees = await rapport(client, A[0])
    par_id = themes(donnees)
    assert len(par_id) == 43
    assert [t for t in par_id.values() if t["etat"] == "panne"] == []
    # A theme reading only sources out of scope calls nothing and says so.
    hors = [d.id for d in THEMES if set(d.sources) <= set(NON_HEBERGES)]
    assert len(hors) == 30
    for id_ in hors:
        assert par_id[id_]["etat"] == "hors_couverture", id_


async def test_the_head_carries_the_point_the_map_and_the_attachment(client):
    # 04.7-17: the BAN point, the map on the site origin and the sentence.
    donnees = await rapport(client, A[0])
    assert donnees["position"] == pytest.approx({"lon": A[1][0], "lat": A[1][1]})
    assert donnees["carte"]["images"] == {
        "plan": f"/carte/{A[0]}/plan.png",
        "parcelles": f"/carte/{A[0]}/parcelles.png",
    }
    assert donnees["rattachement_phrase"] == (
        "Le point de l\u2019adresse tombe dans la parcelle RA 1. "
        "Ces DPE peuvent venir de plusieurs bâtiments."
    )
    dpe = themes(donnees)["dpe"]
    assert dpe["accroche"] == "3 DPE déclarés"
    assert dpe["rattachement"]["alerte"] == (
        "Ces DPE peuvent venir de plusieurs bâtiments."
    )


async def test_dpe_of_several_buildings_and_the_building_of_the_point(client):
    par_id = themes(await rapport(client, A[0]))
    dpe = par_id["dpe"]
    assert dpe["verdict"].startswith("DPE de plusieurs bâtiments à cette adresse")
    assert (dpe["etat"], dpe["ton"]) == ("ok", "vigilance")
    assert dpe["rattachement"]["confiance"] == "incertaine"
    assert dpe["rattachement"]["candidats"] == 3
    assert {c["id"]: c["valeur"] for c in dpe["chiffres"]}["classe_dominante"] == "F"
    batiment = par_id["batiment"]
    assert batiment["rattachement"]["confiance"] == "sure"
    assert batiment["details"]["rnb"]["identifiant"] == "RNBA00000001"
    copro = par_id["copropriete"]
    assert copro["verdict"] == (
        "Copropriété immatriculée de 24 lots, dont 18 d'habitation."
    )
    assert par_id["parcelle"]["rattachement"]["cle"] == A[2]


async def test_one_building_one_document_is_probable(client):
    par_id = themes(await rapport(client, B[0]))
    dpe = par_id["dpe"]
    # 04.7-17: a favourable reading needs a sure link (UI-SPEC § 5, rule 4):
    # served as the page shows it, neutral.
    assert (dpe["etat"], dpe["ton"]) == ("ok", "neutre")
    assert dpe["rattachement"]["confiance"] == "probable"
    assert dpe["rattachement"]["alerte"] == "Rattachement probable."
    assert par_id["batiment"]["etat"] == "zero"  # no building on its parcel
    assert par_id["copropriete"]["etat"] == "zero"
    assert par_id["fibre"]["etat"] == "manque"


async def test_a_point_on_two_parcels_attributes_nothing(client):
    par_id = themes(await rapport(client, FRONTIERE[0]))
    for id_ in ("parcelle", "batiment", "copropriete"):
        theme = par_id[id_]
        assert (theme["etat"], theme["motif"]) == ("manque", "parcelles_multiples"), id_
        assert theme["rattachement"]["confiance"] == "incertaine"
        assert theme["rattachement"]["candidats"] == 2
    assert par_id["dpe"]["etat"] == "zero"


async def test_facts_of_the_commune_and_of_the_radius(client):
    donnees = await rapport(client, A[0])
    par_id = themes(donnees)
    for id_ in ("loyers", "zonage_abc", "fiscalite", "revenus"):
        assert par_id[id_]["granularite"]["niveau"] == "commune", id_
        assert par_id[id_]["etat"] == "ok", id_
    # 04.7-08: the plain mean of the departement, of the same year only.
    fiscalite = par_id["fiscalite"]["details"]
    assert fiscalite["moyenne_departement"]["moyenne_simple"] == 40.1
    assert fiscalite["moyenne_departement"]["communes"] == 2
    assert fiscalite["majoration_thrs"] == {"etat": "ok", "majoree": True, "taux": 20.0}
    syndic = par_id["copropriete"]["details"]["registres"][0]["syndic"]
    assert syndic == {
        "type": "professionnel",
        "nom": "Cabinet du Rapport",
        "mandat": {
            "etat": "ok",
            "statut": "en_cours",
            "fin_dernier_mandat": "2027-06-30",
        },
    }
    equipements = par_id["equipements"]
    assert {c["id"]: c["valeur"] for c in equipements["chiffres"]} == {
        "commerces": 2,
        "sante_social": 1,
        "enseignement": 1,
    }
    assert equipements["ton"] == "rassurant"
    assert equipements["details"]["par_domaine"]["tourisme"] == 0
    assert par_id["antennes_5g"]["details"]["supports_5g"] == 2
    assert par_id["antennes_5g"]["details"]["supports"] == 3
    transports = par_id["transports"]
    assert (transports["etat"], transports["ton"]) == ("ok", "rassurant")
    assert transports["chiffres"][0]["valeur"] in range(175, 186)
    gare = transports["details"]["gare"]
    assert (gare["etat"], gare["nom"], gare["plus_proche_connue"]) == (
        "ok",
        "Gare du Rapport",
        False,
    )
    assert gare["distance_m"] in range(2990, 3011)
    assert transports["pannes_partielles"] == []
    cles = {c["id"]: c for c in donnees["resume"]["chiffres_cles"]}
    assert cles["loyer_m2"]["etat"] == "ok"
    assert cles["dpe_dominant"]["chiffres"][0]["valeur"] == "F"
    adresse = {p["theme"] for p in donnees["resume"]["points_adresse"]}
    commune = {p["theme"] for p in donnees["resume"]["points_commune"]}
    assert "dpe" in adresse and not adresse & {"loyers", "fiscalite", "revenus"}
    assert all(par_id[t]["granularite"]["niveau"] == "commune" for t in commune)


@pytest.fixture
def bdnb_importe(migrated, monkeypatch, tmp_path):
    """``bdnb`` hosted, and its import loaded by the runner from the mini-export.

    A sync fixture on purpose: the pipeline downloads with ``asyncio.run``, as
    every ETL run from the CLI, which cannot run inside the event loop of an
    async test. The scope of the module is restored on teardown.
    """
    from app.etl.hooks import DependentMatviewHook, ValidationHook
    from app.etl.runner import PipelineRunner
    from app.etl_bdnb.pipeline import BdnbBatimentParcellePipeline
    from app.scope import load_scope_sync
    from tests.etl.bdnb_fixture import MILLESIME_URL, mini_exports
    from tests.integration.test_bdnb_pipeline import (
        serve_exports,
        without_producer_floors,
    )

    def scope_stocke(dsn):
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            return load_scope_sync(conn)

    without_producer_floors(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", migrated)
    monkeypatch.setattr(settings, "database_url", migrated)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    monkeypatch.setattr(settings, "source_archive_dir", "")
    exports = mini_exports(parcelle_a=A[2], parcelle_b=B[2])
    serve_exports(
        monkeypatch, tmp_path / "producteur", {d: exports[d] for d in ("44", "85")}
    )
    install_scope_at(migrated, sorted({*HEBERGES, "bdnb"}), ("44", "85"))
    try:
        pipeline = BdnbBatimentParcellePipeline()
        pipeline.pin_millesime(MILLESIME_URL)
        result = PipelineRunner(
            [ValidationHook(), DependentMatviewHook()], scope_loader=scope_stocke
        ).run(pipeline)
        assert result.success, result.error
        yield
    finally:
        install_scope_at(migrated, sorted(HEBERGES), ("44", "85"))
        cache.vider()


async def test_construction_reads_the_bdnb_import_and_never_the_api(
    client, bdnb_importe
):
    """04.7-19: with ``bdnb`` hosted, the year comes from
    ``bdnb_batiment_parcelle`` loaded by the runner from the mini-export, with
    the millesime of the export; no request leaves for api.bdnb.io."""
    cache.vider()
    with respx.mock(assert_all_called=False) as router:
        api = router.route(host="api.bdnb.io").respond(500)
        reponse_a = await client.get(f"/v1/adresse/{A[0]}/rapport")
        reponse_b = await client.get(f"/v1/adresse/{B[0]}/rapport")
    assert api.call_count == 0
    assert reponse_a.status_code == reponse_b.status_code == 200
    construction = themes(reponse_a.json()["donnees"])["construction"]
    assert (construction["etat"], construction["ton"]) == ("ok", "neutre")
    assert construction["verdict"] == "Construit en 1984 selon la BDNB."
    assert construction["rattachement"]["confiance"] == "probable"
    assert construction["rattachement"]["cle"] == "bdnb-bg-85A0-0000-0001"
    assert [(s["cle"], s["mode"], s["millesime"]) for s in construction["sources"]] == [
        ("bdnb", "import", "2026-02.b")
    ]
    assert construction["chiffres"][0]["millesime"] == "2026-02.b"
    plusieurs = themes(reponse_b.json()["donnees"])["construction"]
    assert (plusieurs["etat"], plusieurs["motif"]) == (
        "manque",
        "plusieurs_batiments_sur_la_parcelle",
    )
    assert plusieurs["rattachement"]["candidats"] == 2


# --- Measures (MESURE lines, read in the SUMMARY) -----------------------------

REPETITIONS = 30


def _quantiles(durees: list[float]) -> dict[str, float]:
    ordonnees = sorted(durees)
    p95 = ordonnees[round(0.95 * (len(ordonnees) - 1))]
    return {
        "p50_ms": round(statistics.median(ordonnees) * 1000, 2),
        "p95_ms": round(p95 * 1000, 2),
    }


async def test_the_sector_colleges_are_told_apart_beyond_the_rows_of_one(migrated):
    """04.7-06 (Codex I1): six rows of one college before the row of another
    never hide the second; the rows of one college are not counted."""
    from app.scolaire.queries import colleges_du_secteur

    with psycopg.connect(migrated, autocommit=True) as conn:
        for voie, code_rne, debut in [
            *(("RUE DE L'ÉCOLE", "0850001A", n) for n in range(6)),
            ("RUE DE L'ÉCOLE", "0850002B", 1),
            ("RUE DU COLLÈGE", "0850001A", 1),
            ("RUE DU COLLÈGE", "0850001A", 2),
        ]:
            conn.execute(
                "INSERT INTO carte_scolaire (code_insee, type_et_libelle, "
                "n_de_voie_debut, n_de_voie_fin, parite, code_rne) "
                "VALUES ('85194', %s, %s, 200, 'PI', %s)",
                (voie, debut, code_rne),
            )
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    try:
        async with async_sessionmaker(engine)() as session:
            plusieurs = await colleges_du_secteur(
                session, "85194", "Rue de l'École", 10
            )
            un = await colleges_du_secteur(session, "85194", "Rue du Collège", 10)
            hors = await colleges_du_secteur(session, "85194", "Rue du Collège", 500)
    finally:
        await engine.dispose()
    assert plusieurs == ["0850001A", "0850002B"]
    assert un == ["0850001A"]
    assert hors == []


async def test_measure_the_report_without_and_with_its_cache(client, capsys):
    adresses = (A[0], B[0], FRONTIERE[0])
    sans: list[float] = []
    avec: list[float] = []
    for _ in range(REPETITIONS):
        for ban_id in adresses:
            cache.vider()
            debut = time.perf_counter()
            await rapport(client, ban_id)
            sans.append(time.perf_counter() - debut)
    for ban_id in adresses:
        await rapport(client, ban_id)
    for _ in range(REPETITIONS):
        for ban_id in adresses:
            debut = time.perf_counter()
            await rapport(client, ban_id)
            avec.append(time.perf_counter() - debut)
    mesure = {
        "mesure": "rapport",
        "adresses": list(adresses),
        "repetitions": REPETITIONS,
        "sans_cache": _quantiles(sans),
        "avec_cache": _quantiles(avec),
    }
    with capsys.disabled():
        print(f"\nMESURE {json.dumps(mesure)}")
    assert statistics.median(avec) < statistics.median(sans)


# A dense synthetic city: 300 000 DPE and 300 000 mutations uniformly in a
# 0.05 degree square (about 21 km2, 14 000 per km2, near the density of
# Paris), then the KNN query of each table at its centre, 500 m, LIMIT 20.
VOLUME = 300_000
CENTRE = (-1.55, 47.21)

_DPE_KNN = """
    SELECT numero_dpe FROM diagnostics_dpe
    WHERE position IS NOT NULL AND NOT is_tombstone
      AND ST_DWithin(position::geography,
          ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, 500)
    ORDER BY {ordre}
    LIMIT 20
"""
_DVF_KNN = """
    SELECT id_mutation FROM mutations_grain mg
    WHERE ST_DWithin(mg.position::geography,
          ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, 500)
    ORDER BY {ordre}
    LIMIT 20
"""
ORDRES = {
    "geometrie": "{col} <-> ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)",
    "geographie": (
        "{col}::geography <-> ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)"
        "::geography"
    ),
}


def _plan(conn, sql: str) -> tuple[float, list[str], list[str]]:
    params = {"lon": CENTRE[0], "lat": CENTRE[1]}
    conn.execute(sql, params)  # warm the cache before the measured run
    (plan,) = conn.execute(f"EXPLAIN (ANALYZE, FORMAT JSON) {sql}", params).fetchone()
    noeuds: list[str] = []
    index: list[str] = []

    def parcourir(noeud):
        noeuds.append(noeud["Node Type"])
        if "Index Name" in noeud:
            index.append(noeud["Index Name"])
        for enfant in noeud.get("Plans", ()):
            parcourir(enfant)

    parcourir(plan[0]["Plan"])
    return plan[0]["Execution Time"], noeuds, index


@pytest.fixture(scope="module")
def ville_dense(migrated):
    with psycopg.connect(migrated, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO diagnostics_dpe (id, numero_dpe, code_departement, position) "
            "SELECT 990000000 + g, 'SYN' || g, '44', ST_SetSRID(ST_MakePoint("
            "%s - 0.025 + random() * 0.05, %s - 0.025 + random() * 0.05), 4326) "
            "FROM generate_series(1, %s) g",
            (CENTRE[0], CENTRE[1], VOLUME),
        )
        conn.execute(
            "INSERT INTO mutations_grain (code_departement, id_mutation, "
            "date_mutation, classe_mutation, nb_lignes, nb_dispositions, "
            "nb_communes, nb_parcelles, mono_local, est_vente, position) "
            "SELECT '44', 'SYN' || g, '2024-01-01', 'maison', 1, 1, 1, 1, true, "
            "true, ST_SetSRID(ST_MakePoint(%s - 0.025 + random() * 0.05, "
            "%s - 0.025 + random() * 0.05), 4326) FROM generate_series(1, %s) g",
            (CENTRE[0], CENTRE[1], VOLUME),
        )
        conn.execute("ANALYZE diagnostics_dpe")
        conn.execute("ANALYZE mutations_grain")
    return migrated


def test_measure_the_knn_sorts_of_c10(ville_dense, capsys):
    with psycopg.connect(ville_dense, autocommit=True) as conn:
        resultats = {}
        for table, gabarit, colonne in (
            ("diagnostics_dpe", _DPE_KNN, "position"),
            ("mutations_grain", _DVF_KNN, "mg.position"),
        ):
            for nom, ordre in ORDRES.items():
                sql = gabarit.format(ordre=ordre.format(col=colonne))
                temps = [_plan(conn, sql) for _ in range(5)]
                resultats[f"{table}.{nom}"] = {
                    "execution_ms_mediane": round(
                        statistics.median(t[0] for t in temps), 2
                    ),
                    "noeuds": temps[0][1],
                    "index": temps[0][2],
                }
        version = conn.execute("SHOW server_version").fetchone()[0]
        postgis = conn.execute("SELECT postgis_lib_version()").fetchone()[0]
    mesure = {
        "mesure": "knn_c10",
        "volume": VOLUME,
        "postgres": version,
        "postgis": postgis,
        "resultats": resultats,
    }
    with capsys.disabled():
        print(f"\nMESURE {json.dumps(mesure, ensure_ascii=False)}")
    # The geography sort reads the geography index of both tables; on
    # mutations_grain it is the only plan without a scan of every partition.
    for table in ("diagnostics_dpe", "mutations_grain"):
        geographie = resultats[f"{table}.geographie"]
        assert "Seq Scan" not in geographie["noeuds"], (table, geographie)
        assert any("geog" in i for i in geographie["index"]), table
    grain = resultats["mutations_grain.geographie"]
    assert "Sort" not in grain["noeuds"], grain


def _noeuds(plan: dict) -> tuple[list[str], list[str], bool]:
    """Node types, index names, and whether an index scan is ordered (KNN)."""
    noeuds: list[str] = []
    index: list[str] = []
    ordonne = False

    def parcourir(noeud):
        nonlocal ordonne
        noeuds.append(noeud["Node Type"])
        if "Index Name" in noeud:
            index.append(noeud["Index Name"])
            ordonne = ordonne or "Order By" in noeud
        for enfant in noeud.get("Plans", ()):
            parcourir(enfant)

    parcourir(plan["Plan"])
    return noeuds, index, ordonne


async def test_the_served_knn_functions_read_the_geography_index(ville_dense):
    """The real ``dpe_par_point`` and ``dvf_par_point``: geodesic order of
    their rows, and the plan of the very SQL they execute (C10)."""
    from app.dpe.queries import dpe_par_point
    from app.dvf.queries import dvf_par_point

    engine = create_async_engine(
        ville_dense.replace("postgresql://", "postgresql+asyncpg://")
    )
    executees: list[tuple[str, object]] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def noter(_conn, _cursor, statement, parameters, _context, _many):
        executees.append((statement, parameters))

    lon, lat = CENTRE
    try:
        async with async_sessionmaker(engine)() as session:
            dpe = await dpe_par_point(session, lat, lon, 500.0, limit=20)
            (sql_dpe, params_dpe) = executees[-1]
            dvf = await dvf_par_point(session, lat, lon, 500.0, limit=20)
            (sql_dvf, params_dvf) = executees[-1]
            plans = {}
            for nom, sql, params in (
                ("dpe", sql_dpe, params_dpe),
                ("dvf", sql_dvf, params_dvf),
            ):
                connexion = await session.connection()
                resultat = await connexion.exec_driver_sql(
                    f"EXPLAIN (FORMAT JSON) {sql}", params
                )
                plans[nom] = _noeuds(resultat.scalar_one()[0])
    finally:
        await engine.dispose()
    assert len(dpe) == len(dvf) == 20
    # Independent oracle, without the KNN operator: the twenty nearest rows
    # of the 500 m disc by sphere distance (the metric of geography <->).
    with psycopg.connect(ville_dense) as conn:
        for table, cle, filtre, lignes in (
            ("diagnostics_dpe", "numero_dpe", "AND NOT is_tombstone", dpe),
            ("mutations_grain", "id_mutation", "", dvf),
        ):
            attendus = [
                ligne[0]
                for ligne in conn.execute(
                    f"SELECT {cle} FROM {table} WHERE position IS NOT NULL "
                    f"{filtre} AND ST_DWithin(position::geography, "
                    "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 500) "
                    "ORDER BY ST_Distance(position::geography, "
                    "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, false) "
                    "LIMIT 20",
                    (lon, lat, lon, lat),
                ).fetchall()
            ]
            assert [ligne[cle] for ligne in lignes] == attendus, table
    for nom, (noeuds, index, _ordonne) in plans.items():
        assert "Seq Scan" not in noeuds, (nom, noeuds)
        assert any("geog" in i for i in index), (nom, index)
    # On the partitioned grain table the sort itself is the ordered index scan.
    assert plans["dvf"][2], plans["dvf"]
