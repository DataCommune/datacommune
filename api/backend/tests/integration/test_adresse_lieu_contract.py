"""LIEU-01 / LIEU-02 / D-115 / N-01 / N-02: themes of the place on PostGIS.

``GET /v1/adresse/{ban_id}`` serves, after its landmarks, the themes of the
place in the order of D-115. « Bâtiment et parcelle » counts every building
of the parcel that contains the BAN point (never the nearest buildings of a
radius), serves the building of the point only when one geometry covers it,
and attributes nothing without a single parcel; an SQL outage of one source
stays in its value.

The scope is written by the production path and read by the real scope
dependency; only the session is overridden.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_adresse_lieu_contract.py
"""

from collections.abc import AsyncIterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adresse.router import adresse_router
from app.common.exceptions import register_exception_handlers
from app.config import settings
from app.database import get_db
from tests.integration.eligibilite_fixture import inserer_offre
from tests.integration.instance_scope_fixture import install_scope_at

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
LIEU = ("ban", "cadastre", "batiments", "copropriete", "rnb")
HALF = 0.0002  # half side of a parcel square, about 15 m


@dataclass(frozen=True)
class Adresse:
    ban_id: str
    point: tuple[float, float]
    parcelle: str | None
    voie: str = "Rue du Lieu"
    numero: str = "1"
    rep: str | None = None


# One parcel per address, 0.001 degree apart (about 76 m): no overlap.
A = Adresse(
    "85191_0601_00001", (-1.4000, 46.6000), "85191000CA0001", "Rue Victor Hugo", "12"
)
B = Adresse("85191_0602_00001", (-1.4010, 46.6000), "85191000CB0001")
C = Adresse("85191_0603_00001", (-1.4020, 46.6000), "85191000CC0001")
D = Adresse("85191_0604_00001", (-1.4030, 46.6000), "85191000CD0001")
E = Adresse("85191_0605_00001", (-1.4040, 46.6000), "85191000CE0001")
HORS = Adresse("85191_0606_00001", (-1.4200, 46.6000), None)
FRONTIERE = Adresse("85191_0607_00001", (-1.4300, 46.6000), None)
RNB_A = "RNBA00000001"
RNB_HORS = ("RNBH00000001", "RNBH00000002")
# Broadband (R06-09/4): « prolongée » shares the street prefix, « 16 bis » is
# not « 16 », and two buildings carry « 18 ».
PROLONGEE = Adresse(
    "85191_0608_00014", (-1.4400, 46.6000), None, "Rue Victor Hugo", "14"
)
SUFFIXE = Adresse("85191_0608_00016", (-1.4410, 46.6000), None, "Rue Victor Hugo", "16")
MULTIPLE = Adresse(
    "85191_0608_00018", (-1.4420, 46.6000), None, "Rue Victor Hugo", "18"
)
METRES_PAR_DEGRE_LAT = 111_150


def _square(lon: float, lat: float, half: float = HALF) -> str:
    x0, x1, y0, y1 = lon - half, lon + half, lat - half, lat + half
    return f"MULTIPOLYGON((({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0})))"


def _rect(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"MULTIPOLYGON((({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0})))"


def insert_adresse(conn, id_: int, adresse: Adresse) -> None:
    lon, lat = adresse.point
    conn.execute(
        "INSERT INTO adresses (id, code_departement, ban_id, numero, rep, nom_voie, "
        "code_postal, code_commune, nom_commune, position) VALUES (%s, '85', %s, "
        "%s, %s, %s, '85000', '85191', 'La Roche-sur-Yon', "
        "ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
        (id_, adresse.ban_id, adresse.numero, adresse.rep, adresse.voie, lon, lat),
    )


def insert_eligibilite(conn, imb, voie, numero, complement, technologie, debit):
    inserer_offre(
        conn,
        technologie=technologie,
        debit_max=debit,
        operateur="OPE",
        id_imb=imb,
        code_insee="85191",
        voie=voie,
        numero=numero,
        complement=complement,
        code_departement="85",
    )


def au_nord(adresse: Adresse, metres: float) -> tuple[float, float]:
    """A point *metres* north of *adresse* (geodesic distance within a metre)."""
    lon, lat = adresse.point
    return lon, lat + metres / METRES_PAR_DEGRE_LAT


def insert_parcelle(conn, id_: int, id_parcelle: str, wkt: str, contenance=None):
    conn.execute(
        "INSERT INTO parcelles (id, code_departement, id_parcelle, commune, "
        "contenance, geometrie) VALUES (%s, '85', %s, '85191', %s, "
        "ST_GeomFromText(%s, 4326))",
        (id_, id_parcelle, contenance, wkt),
    )


def insert_batiment(conn, id_: int, wkt: str, usage="Résidentiel", **attributs):
    conn.execute(
        "INSERT INTO batiments (id, code_departement, cleabs, usage_1, "
        "nombre_d_etages, hauteur, geometrie) VALUES (%s, '85', %s, %s, %s, %s, "
        "ST_GeomFromText(%s, 4326))",
        (
            id_,
            f"BATIMENT{id_}",
            usage,
            attributs.get("etages"),
            attributs.get("hauteur"),
            wkt,
        ),
    )


def migrate(dsn: str) -> None:
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", dsn)
        context.setattr(settings, "database_url", dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", dsn)
        command.upgrade(cfg, "head")


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    migrate(isolated_pg_dsn)
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute(
            "INSERT INTO geo_departements (code, nom, geometry) VALUES "
            "('85', 'Vendée', ST_GeomFromText(%s, 4326))",
            (_square(0, 0, 0.1),),
        )
        conn.execute(
            "INSERT INTO geo_communes (code, nom, departement_code, geometry) "
            "VALUES ('85191', 'La Roche-sur-Yon', '85', ST_GeomFromText(%s, 4326))",
            (_square(-1.4, 46.6, 0.1),),
        )
        for index, adresse in enumerate(
            (A, B, C, D, E, HORS, FRONTIERE, PROLONGEE, SUFFIXE, MULTIPLE)
        ):
            insert_adresse(conn, 95_200_001 + index, adresse)
            if adresse.parcelle:
                insert_parcelle(
                    conn,
                    96_200_001 + index,
                    adresse.parcelle,
                    _square(*adresse.point),
                    contenance=400 + index,
                )
        x, y = FRONTIERE.point
        insert_parcelle(
            conn, 96_200_100, "85191000CH0001", _rect(x - HALF, y - HALF, x, y + HALF)
        )
        insert_parcelle(
            conn, 96_200_101, "85191000CH0002", _rect(x, y - HALF, x + HALF, y + HALF)
        )
        ident = iter(range(97_200_001, 97_300_000))
        # A: two buildings on the parcel, none of them under the point.
        ax, ay = A.point
        for dx in (0.0001, -0.0001):
            insert_batiment(conn, next(ident), _square(ax + dx, ay + dx, 0.00003))
        # B: one building covering the point.
        insert_batiment(
            conn, next(ident), _square(*B.point, 0.00005), hauteur=7.5, etages=2
        )
        # C: six buildings (two declared annexes, one of unknown use), and a
        # neighbour that only overlaps the edge: its point on surface is out.
        cx, cy = C.point
        for rang, usage in enumerate(
            ("Résidentiel", "Résidentiel", None, "Résidentiel", "Annexe", "Annexe")
        ):
            decalage = -0.00015 + rang * 0.00006
            insert_batiment(
                conn, next(ident), _square(cx + decalage, cy + 0.0001, 0.00002), usage
            )
        insert_batiment(
            conn,
            next(ident),
            _rect(cx + HALF - 0.00002, cy - 0.00005, cx + HALF + 0.0002, cy + 0.00005),
        )
        # D and E, same commune: only D's parcel is a registered condominium.
        conn.execute(
            "INSERT INTO copropriete (numero_immatriculation, nom_copropriete, "
            "id_parcelle_1, nb_lots_total, nb_lots_habitation_strict) "
            "VALUES ('AB0000001', 'Residence du Lieu', %s, 14, 12)",
            (D.parcelle,),
        )
        # A registration on several parcels: C is its second, B its third.
        conn.execute(
            "INSERT INTO copropriete (numero_immatriculation, nom_copropriete, "
            "id_parcelle_1, id_parcelle_2, id_parcelle_3, nb_parcelles, "
            "nb_lots_total, nb_lots_habitation_strict) "
            "VALUES ('AB0000002', 'Cite des Trois Parcelles', '85191000ZZ0001', "
            "%s, %s, 4, 24, 24)",
            (C.parcelle, B.parcelle),
        )
        conn.execute(
            "INSERT INTO rnb_liens (id_rnb, cle_ban, code_departement, statut, "
            "parcelles) VALUES (%s, %s, '85', 'constructed', ARRAY[%s])",
            (RNB_A, A.ban_id, A.parcelle),
        )
        # HORS: the RNB links two buildings to its BAN key, whatever its parcel.
        for id_rnb in RNB_HORS:
            conn.execute(
                "INSERT INTO rnb_liens (id_rnb, cle_ban, code_departement, statut) "
                "VALUES (%s, %s, '85', 'constructed')",
                (id_rnb, HORS.ban_id),
            )
        # A: one building, fibre and copper, exactly « 12 Rue Victor Hugo ».
        insert_eligibilite(conn, "IMB-A", "RUE VICTOR HUGO", "12", None, "FO", "THD1G")
        insert_eligibilite(conn, "IMB-A", "RUE VICTOR HUGO", "12", None, "CU", "BHD")
        insert_eligibilite(
            conn, "IMB-P", "RUE VICTOR HUGO PROLONGEE", "14", None, "FO", "THD1G"
        )
        insert_eligibilite(conn, "IMB-S", "RUE VICTOR HUGO", "16", "BIS", "FO", "THD1G")
        insert_eligibilite(conn, "IMB-M1", "RUE VICTOR HUGO", "18", None, "FO", "THD1G")
        insert_eligibilite(conn, "IMB-M2", "RUE VICTOR HUGO", "18", None, "CU", "BHD")
        # Stops around A: 100 m, a station at 300 m, 600 m, and 1 500 m (out).
        for nom, metres in (("Place Napoléon", 100), ("Les Halles", 600)):
            lon, lat = au_nord(A, metres)
            conn.execute(
                "INSERT INTO arrets_transport (stop_id, stop_name, position) VALUES "
                "(%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                (nom, nom, lon, lat),
            )
        lon, lat = au_nord(A, 1500)
        conn.execute(
            "INSERT INTO arrets_transport (stop_id, stop_name, position) VALUES "
            "('LOIN', 'Trop loin', ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (lon, lat),
        )
        lon, lat = au_nord(A, 300)
        conn.execute(
            "INSERT INTO gares (nom_gare, code_insee, position) VALUES "
            "('Gare de La Roche-sur-Yon', '85191', "
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (lon, lat),
        )
    return isolated_pg_dsn


@dataclass
class Api:
    client: httpx.AsyncClient
    statements: list[str]

    def reads(self, fragment: str) -> list[str]:
        return [sql for sql in self.statements if fragment in sql]


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
    application.include_router(adresse_router)
    application.dependency_overrides[get_db] = db
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield Api(c, statements)
    finally:
        await engine.dispose()


def scope(dsn, datasets=LIEU):
    install_scope_at(dsn, sorted(datasets), ("44", "85"))


async def fiche(api: Api, ban_id: str) -> dict:
    response = await api.client.get(f"/v1/adresse/{ban_id}")
    assert response.status_code == 200, response.text
    return response.json()["donnees"]


def theme(donnees: dict, cle: str) -> dict:
    (trouve,) = [t for t in donnees["themes"] if t["cle"] == cle]
    return trouve


def champs(theme_servi: dict) -> dict[str, dict]:
    return {
        champ["field_id"]: champ
        for groupe in theme_servi["groupes"]
        for champ in groupe["champs"]
    }


@contextmanager
def injected(dsn: str, *statements: str, restore: tuple[str, ...]):
    """Commit a SQL-level outage, then restore it whatever the test outcome."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)
    try:
        yield
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            for statement in restore:
                conn.execute(statement)


# --- Task 1: tracer ---------------------------------------------------------------


async def test_tracer_two_buildings_on_the_parcel_and_no_dwelling(api, migrated):
    scope(migrated)

    donnees = await fiche(api, A.ban_id)

    batiment = theme(donnees, "batiment_parcelle")
    assert (batiment["titre"], batiment["etat"], batiment["applicable"]) == (
        "Bâtiment et parcelle",
        "value",
        True,
    )
    lus = champs(batiment)
    assert lus["adresse.batiments.nombre_parcelle"]["valeur"] == 2
    assert lus["adresse.batiments.nombre_parcelle"]["rattachement"] == (
        "parcelle_du_point"
    )
    assert lus["adresse.batiments.nombre_parcelle_hors_annexe"]["valeur"] == 2
    # Neither building contains the point: no characteristic, never « le bâtiment »;
    # the height says why, zero building and not « several » (D-15 API CR-01).
    assert "adresse.batiment.etages" not in lus
    hauteur = lus["adresse.batiment.hauteur"]
    assert (hauteur["etat"], hauteur["motif"], hauteur["valeur"]) == (
        "not_available",
        "aucun_batiment_au_point",
        None,
    )
    assert lus["adresse.parcelle.identifiant"]["valeur"] == A.parcelle
    assert lus["adresse.parcelle.contenance"]["valeur"] == 400
    assert lus["adresse.parcelle.contenance"]["unite"] == "m2"
    assert lus["adresse.copropriete.immatriculation"]["valeur"] == 0
    assert lus["adresse.batiment.rnb_id"]["valeur"] == RNB_A
    assert lus["adresse.batiment.rnb_id"]["rattachement"] == (
        "batiment_rnb_de_l_adresse"
    )
    assert lus["adresse.batiment.rnb_id"]["source"]["cle"] == "rnb"
    texte = str(batiment)
    assert "logement" not in texte and "lots" not in texte
    # No coordinate leaves the API.
    assert "46.6" not in str(donnees) and "-1.4" not in str(donnees)


async def test_one_building_covering_the_point_serves_its_characteristics(
    api, migrated
):
    scope(migrated)

    lus = champs(theme(await fiche(api, B.ban_id), "batiment_parcelle"))

    assert lus["adresse.batiments.nombre_parcelle"]["valeur"] == 1
    assert lus["adresse.batiment.hauteur"]["valeur"] == 7.5
    assert lus["adresse.batiment.hauteur"]["rattachement"] == "batiment_du_point"
    assert lus["adresse.batiment.etages"]["valeur"] == 2
    # The RNB links no building to this key: the field is served « not
    # reported », never omitted nor a zero (C52, B3).
    rnb = lus["adresse.batiment.rnb_id"]
    assert (rnb["etat"], rnb["valeur"], rnb["motif"]) == ("not_reported", None, None)


RNB_PLUSIEURS = ("RNBC00000001", "RNBC00000002", "RNBC00000003")


async def test_several_rnb_links_on_a_single_parcel_serve_no_identifier(api, migrated):
    """W2 (261011-ig1, D-128): the RNB links three buildings to the BAN key
    of an address whose point lies in one parcel; the SQL ``DISTINCT id_rnb
    … LIMIT 2`` reads two of them, the field is « not available »
    (``batiment_rnb_non_unique``) and no RNB identifier is served, while the
    buildings of the parcel are still served (C52, B3)."""
    scope(migrated)
    valeurs = ", ".join(
        f"('{id_rnb}', '{C.ban_id}', '85', 'constructed')" for id_rnb in RNB_PLUSIEURS
    )
    with injected(
        migrated,
        "INSERT INTO rnb_liens (id_rnb, cle_ban, code_departement, statut) "
        f"VALUES {valeurs}",
        restore=(f"DELETE FROM rnb_liens WHERE cle_ban = '{C.ban_id}'",),
    ):
        api.statements.clear()
        batiment = theme(await fiche(api, C.ban_id), "batiment_parcelle")

    assert batiment["etat"] == "value"
    lus = champs(batiment)
    rnb = lus["adresse.batiment.rnb_id"]
    assert (rnb["etat"], rnb["motif"], rnb["valeur"]) == (
        "not_available",
        "batiment_rnb_non_unique",
        None,
    )
    assert rnb["rattachement"] == "batiment_rnb_de_l_adresse"
    assert not any(id_rnb in str(batiment) for id_rnb in RNB_PLUSIEURS)
    # The parcel relation is unique: its buildings are still counted.
    assert lus["adresse.batiments.nombre_parcelle"]["etat"] == "value"
    assert api.reads("rnb_liens") != []


async def test_six_buildings_of_the_parcel_counted_without_the_neighbour(api, migrated):
    scope(migrated)

    lus = champs(theme(await fiche(api, C.ban_id), "batiment_parcelle"))

    assert lus["adresse.batiments.nombre_parcelle"]["valeur"] == 6
    # Two declared annexes out; the building of unknown use counts.
    assert lus["adresse.batiments.nombre_parcelle_hors_annexe"]["valeur"] == 4
    # None of the six covers the point: no height value, zero typed as such.
    assert "adresse.batiment.etages" not in lus
    hauteur = lus["adresse.batiment.hauteur"]
    assert (hauteur["etat"], hauteur["motif"], hauteur["valeur"]) == (
        "not_available",
        "aucun_batiment_au_point",
        None,
    )


@pytest.mark.parametrize(
    ("adresse", "motif", "rnb"),
    [
        (HORS, "point_hors_parcelle", ("not_available", "batiment_rnb_non_unique")),
        (FRONTIERE, "parcelles_multiples", ("not_reported", None)),
    ],
    ids=["point_hors_parcelle", "frontiere"],
)
async def test_no_single_parcel_attributes_no_building(
    api, migrated, adresse, motif, rnb
):
    scope(migrated)
    api.statements.clear()

    batiment = theme(await fiche(api, adresse.ban_id), "batiment_parcelle")

    # The theme keeps the reason of the parcel relation (N-02) and serves the
    # RNB field only, read by BAN key: never a building of the parcel (C55).
    assert (batiment["etat"], batiment["motif"]) == ("not_available", motif)
    lus = champs(batiment)
    assert list(lus) == ["adresse.batiment.rnb_id"]
    assert (
        lus["adresse.batiment.rnb_id"]["etat"],
        lus["adresse.batiment.rnb_id"]["motif"],
    ) == rnb
    assert lus["adresse.batiment.rnb_id"]["valeur"] is None
    assert not any(id_rnb in str(batiment) for id_rnb in RNB_HORS)
    assert api.reads("batiments") == []
    assert api.reads("FROM copropriete") == []
    assert api.reads("rnb_liens") != []


async def test_two_parcels_of_one_commune_only_one_registered(api, migrated):
    scope(migrated)

    d = champs(theme(await fiche(api, D.ban_id), "batiment_parcelle"))
    e = champs(theme(await fiche(api, E.ban_id), "batiment_parcelle"))

    assert d["adresse.copropriete.immatriculation"]["valeur"] == 1
    assert e["adresse.copropriete.immatriculation"]["valeur"] == 0
    assert "AB0000001" not in str(d)


async def test_a_condominium_is_found_from_its_second_and_third_parcel(api, migrated):
    scope(migrated)

    c = champs(theme(await fiche(api, C.ban_id), "batiment_parcelle"))
    b = champs(theme(await fiche(api, B.ban_id), "batiment_parcelle"))

    assert c["adresse.copropriete.immatriculation"]["valeur"] == 1
    assert b["adresse.copropriete.immatriculation"]["valeur"] == 1


async def test_a_sql_outage_of_the_buildings_stays_in_their_values(api, migrated):
    scope(migrated)
    with injected(
        migrated,
        "ALTER TABLE batiments RENAME TO batiments_panne",
        restore=("ALTER TABLE batiments_panne RENAME TO batiments",),
    ):
        donnees = await fiche(api, B.ban_id)

    assert donnees["identite"]["ban_id"] == B.ban_id
    batiment = theme(donnees, "batiment_parcelle")
    lus = champs(batiment)
    assert batiment["etat"] == "value"
    assert lus["adresse.batiments.nombre_parcelle"]["etat"] == "temporary_error"
    assert lus["adresse.parcelle.contenance"]["valeur"] == 401
    assert lus["adresse.copropriete.immatriculation"]["etat"] == "value"


async def test_buildings_not_hosted_are_out_of_coverage_without_read(api, migrated):
    scope(migrated, ("ban", "cadastre", "copropriete"))
    api.statements.clear()

    lus = champs(theme(await fiche(api, B.ban_id), "batiment_parcelle"))

    assert lus["adresse.batiments.nombre_parcelle"]["etat"] == "out_of_coverage"
    assert "adresse.batiment.hauteur" not in lus
    # The RNB is not hosted either: out of coverage with its reason, unread.
    rnb = lus["adresse.batiment.rnb_id"]
    assert (rnb["etat"], rnb["motif"]) == ("out_of_coverage", "dataset_non_heberge")
    assert api.reads("batiments") == [] and api.reads("rnb_liens") == []


async def test_an_address_out_of_coverage_reads_no_theme(api, migrated):
    scope(migrated, ("cadastre", "batiments"))
    api.statements.clear()

    donnees = await fiche(api, A.ban_id)

    assert donnees["couverture"]["etat"] == "hors_couverture"
    assert donnees["themes"] == []
    assert api.reads("batiments") == []


# --- Task 2: risks, planning, networks and transports -----------------------------

TOUT = (*LIEU, "georisques", "urbanisme", "connectivite", "transports", "gares")
GPU = {
    "zones_plu": [
        {"type": "Feature", "properties": {"libelle": "UB", "libelong": "Zone urbaine"}}
    ],
    "prescriptions": [],
    "sources": {
        "zones_plu": {"statut": "disponible", "complet": True},
        "prescriptions": {"statut": "vide", "complet": True},
    },
}
SANS_TRANSACTION = {"verrous": 0, "transactions": 0}


def _transactions_et_verrous(dsn: str) -> dict[str, int]:
    """Seen from another connection: open transactions and advisory locks."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        verrous = conn.execute(
            "SELECT count(*) FROM pg_locks AS l "
            "JOIN pg_database AS d ON d.oid = l.database "
            "WHERE l.locktype = 'advisory' AND d.datname = current_database()"
        ).fetchone()[0]
        transactions = conn.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "AND xact_start IS NOT NULL "
            # Autovacuum workers also show an open transaction on this
            # database: only the connections of a client are the app's.
            "AND backend_type = 'client backend'"
        ).fetchone()[0]
    return {"verrous": verrous, "transactions": transactions}


@pytest.fixture
def amont(monkeypatch, migrated):
    """Upstreams answered here, never called; each call records what the
    database holds at that moment (API CR-01)."""
    from app.adresse import risques
    from app.common.proxy_coverage import ProxySource

    observe: list[tuple[str, dict[str, int]]] = []

    def complet(total):
        return ProxySource(
            statut="disponible", nombre_retournes=total, total=total, complet=True
        )

    async def rga(lat, lon):
        observe.append(("rga", _transactions_et_verrous(migrated)))
        return {"codeExposition": "2", "exposition": "Exposition moyenne"}

    async def commune(code_insee):
        observe.append(("commune", _transactions_et_verrous(migrated)))
        return risques.RisquesCommune(
            lignes={
                "gaspar": [{"risques_detail": [{"libelle_risque_long": "Inondation"}]}],
                "radon": [{"classe_potentiel": "1"}],
                "icpe": [],
            },
            couverture={"gaspar": complet(1), "radon": complet(1), "icpe": complet(0)},
            total={"gaspar": 1, "radon": 1, "icpe": 0},
        )

    async def urbanisme(lat, lon):
        observe.append(("urbanisme", _transactions_et_verrous(migrated)))
        return GPU

    monkeypatch.setattr("app.adresse.risques.query_rga", rga)
    monkeypatch.setattr("app.adresse.risques.lire_risques_commune", commune)
    monkeypatch.setattr("app.adresse.lieu.query_urbanisme", urbanisme)
    return observe


async def test_the_four_themes_in_order_upstreams_outside_any_transaction(
    api, migrated, amont
):
    scope(migrated, TOUT)

    donnees = await fiche(api, A.ban_id)

    assert [t["cle"] for t in donnees["themes"]] == [
        "batiment_parcelle",
        "risques",
        "urbanisme",
        "reseaux_transports",
    ]
    # No publication lock nor open transaction while an upstream answers.
    assert sorted(nom for nom, _ in amont) == ["commune", "rga", "urbanisme"]
    assert all(etat == SANS_TRANSACTION for _, etat in amont), amont
    au_point, commune = theme(donnees, "risques")["groupes"]
    assert au_point["titre"] == "Au point de l'adresse"
    assert [c["field_id"] for c in au_point["champs"]] == [
        "adresse.risques.argiles_au_point"
    ]
    # The value of landmark ③, not a second reading.
    assert au_point["champs"][0] == {
        **donnees["reperes"]["argiles"],
        "elements": [],
        "liste_incomplete": False,
        "rayon_m": None,
    }
    assert commune["titre"] == "Sur la commune (référence)"
    assert {c["rattachement"] for c in commune["champs"]} == {"commune_reference"}
    icpe = champs(theme(donnees, "risques"))["adresse.risques.icpe_commune"]
    assert (icpe["etat"], icpe["valeur"]) == ("value", 0)
    zone = champs(theme(donnees, "urbanisme"))["adresse.urbanisme.zone_plu"]
    assert [e["libelle"] for e in zone["elements"]] == ["UB · Zone urbaine"]


async def test_fibre_published_for_this_address_and_the_nearest_stops(
    api, migrated, amont
):
    scope(migrated, TOUT)

    lus = champs(theme(await fiche(api, A.ban_id), "reseaux_transports"))

    fibre = lus["adresse.connectivite.eligibilite_fibre"]
    assert (fibre["etat"], fibre["valeur"], fibre["rattachement"]) == (
        "value",
        True,
        "eligibilite_adresse",
    )
    assert lus["adresse.connectivite.debit"]["valeur"] == "THD1G"
    arrets = lus["adresse.transports.arret_proche"]
    assert (arrets["valeur"], arrets["rayon_m"]) == (3, 1000)
    # Exactly three stops within the radius: the list is complete (API IN-01).
    assert (arrets["liste_incomplete"], arrets["motif"]) == (False, None)
    noms = [(e["libelle"], e["nature"]) for e in arrets["elements"]]
    assert noms == [
        ("Place Napoléon", "arret"),
        ("Gare de La Roche-sur-Yon", "gare"),
        ("Les Halles", "arret"),
    ]
    for element, attendu in zip(arrets["elements"], (100, 300, 600), strict=True):
        assert abs(element["distance_m"] - attendu) <= 2
    assert "latitude" not in str(arrets) and "46.60" not in str(arrets)


# --- Periods of the place (C14, SEM-02) ----------------------------------------------

# What the imports recorded: a millésime declared by the RNB links import, and
# only the date of a successful run for the others (22:30 UTC: the 26th in
# Paris). Removed afterwards whatever the test outcome.
IMPORTS = {
    "batiments": "2026-09-25 22:30:00+00",
    "cadastre": "2026-09-25 21:54:00+00",
    "copropriete": "2026-09-24 21:29:00+00",
    "transports": "2026-09-24 21:32:00+00",
}


async def test_values_of_the_place_carry_the_millesime_or_import_of_their_table(
    api, migrated
):
    scope(migrated, (*LIEU, "transports", "gares"))
    jeux = ", ".join(f"'{nom}'" for nom in (*IMPORTS, "rnb_liens"))
    with injected(
        migrated,
        "INSERT INTO dataset_metadata (dataset_name, status) "
        f"SELECT nom, 'disponible' FROM unnest(ARRAY[{jeux}]) AS nom "
        "ON CONFLICT (dataset_name) DO NOTHING",
        "UPDATE dataset_metadata SET source_period_label = 'Export RNB 2026-09' "
        "WHERE dataset_name = 'rnb_liens'",
        *(
            "INSERT INTO etl_runs (dataset_name, status, started_at, completed_at) "
            f"VALUES ('{nom}', 'success', TIMESTAMPTZ '{fin}' - INTERVAL '1 hour', "
            f"TIMESTAMPTZ '{fin}')"
            for nom, fin in IMPORTS.items()
        ),
        restore=(
            "UPDATE dataset_metadata SET source_period_label = NULL "
            "WHERE dataset_name = 'rnb_liens'",
            f"DELETE FROM etl_runs WHERE dataset_name IN ({jeux})",
        ),
    ):
        donnees = await fiche(api, A.ban_id)

    lus = champs(theme(donnees, "batiment_parcelle"))
    assert {cle: champ["periode"] for cle, champ in lus.items()} == {
        "adresse.batiments.nombre_parcelle": "2026-09-26",
        "adresse.batiments.nombre_parcelle_hors_annexe": "2026-09-26",
        "adresse.batiment.hauteur": "2026-09-26",
        "adresse.batiment.rnb_id": "Export RNB 2026-09",
        "adresse.parcelle.identifiant": "2026-09-25",
        "adresse.parcelle.contenance": "2026-09-25",
        "adresse.copropriete.immatriculation": "2026-09-24",
    }
    reseaux = champs(theme(donnees, "reseaux_transports"))
    arrets = reseaux["adresse.transports.arret_proche"]
    assert (arrets["valeur"], arrets["periode"]) == (3, "2026-09-24")


async def test_broadband_without_a_declared_label_carries_the_import_day(
    api, migrated, amont
):
    """C24 (b), WINDOWS n° 86: an empty connectivite label falls back on C14."""
    scope(migrated, TOUT)
    sans_libelle = (
        "INSERT INTO dataset_metadata (dataset_name, status) "
        "VALUES ('connectivite', 'disponible') "
        "ON CONFLICT (dataset_name) DO UPDATE SET source_period_label = NULL"
    )
    sans_import = ("DELETE FROM etl_runs WHERE dataset_name = 'connectivite'",)

    with injected(migrated, sans_libelle, restore=sans_import):
        sans_rien = champs(theme(await fiche(api, A.ban_id), "reseaux_transports"))
    with injected(
        migrated,
        sans_libelle,
        "INSERT INTO etl_runs (dataset_name, status, started_at, completed_at) "
        "VALUES ('connectivite', 'success', TIMESTAMPTZ '2026-09-26 10:41:00+00', "
        "TIMESTAMPTZ '2026-09-26 22:43:00+00')",
        restore=sans_import,
    ):
        importe = champs(theme(await fiche(api, A.ban_id), "reseaux_transports"))

    cles = ("adresse.connectivite.eligibilite_fibre", "adresse.connectivite.debit")
    assert [(sans_rien[c]["etat"], sans_rien[c]["periode"]) for c in cles] == [
        ("value", None),
        ("value", None),
    ]
    assert [(importe[c]["etat"], importe[c]["periode"]) for c in cles] == [
        ("value", "2026-09-27"),
        ("value", "2026-09-27"),
    ]


async def test_a_table_without_millesime_nor_import_serves_no_period(api, migrated):
    scope(migrated)

    lus = champs(theme(await fiche(api, A.ban_id), "batiment_parcelle"))

    assert {champ["periode"] for champ in lus.values()} == {None}
    assert lus["adresse.batiments.nombre_parcelle"]["valeur"] == 2


@pytest.mark.parametrize(
    "adresse",
    [PROLONGEE, SUFFIXE, MULTIPLE],
    ids=["prolongee", "bis", "deux_immeubles"],
)
async def test_an_unverified_broadband_identity_serves_no_eligibility(
    api, migrated, amont, adresse
):
    scope(migrated, TOUT)

    lus = champs(theme(await fiche(api, adresse.ban_id), "reseaux_transports"))

    fibre = lus["adresse.connectivite.eligibilite_fibre"]
    assert (fibre["etat"], fibre["motif"], fibre["valeur"]) == (
        "not_available",
        "adresse_non_verifiable",
        None,
    )
    assert "adresse.connectivite.debit" not in lus


async def test_no_broadband_row_for_the_address_is_not_a_value(api, migrated, amont):
    scope(migrated, TOUT)

    lus = champs(theme(await fiche(api, B.ban_id), "reseaux_transports"))

    assert lus["adresse.connectivite.eligibilite_fibre"]["etat"] == "not_reported"


async def test_a_sql_outage_of_broadband_reaches_its_theme_only(api, migrated, amont):
    scope(migrated, TOUT)
    with injected(
        migrated,
        "ALTER TABLE eligibilite_fixe RENAME TO eligibilite_fixe_panne",
        restore=("ALTER TABLE eligibilite_fixe_panne RENAME TO eligibilite_fixe",),
    ):
        donnees = await fiche(api, A.ban_id)

    reseaux = champs(theme(donnees, "reseaux_transports"))
    assert reseaux["adresse.connectivite.eligibilite_fibre"]["etat"] == (
        "temporary_error"
    )
    assert reseaux["adresse.transports.arret_proche"]["etat"] == "value"
    for cle in ("batiment_parcelle", "risques", "urbanisme"):
        assert theme(donnees, cle)["etat"] == "value", cle
    assert donnees["reperes"]["argiles"]["etat"] == "value"


async def test_sources_not_hosted_are_out_of_coverage_without_any_read(
    api, migrated, amont
):
    scope(migrated, LIEU)
    api.statements.clear()

    donnees = await fiche(api, A.ban_id)

    assert theme(donnees, "risques")["etat"] == "out_of_coverage"
    assert theme(donnees, "urbanisme")["etat"] == "out_of_coverage"
    reseaux = champs(theme(donnees, "reseaux_transports"))
    assert {c["etat"] for c in reseaux.values()} == {"out_of_coverage"}
    assert amont == []
    assert api.reads("eligibilite_fixe") == []
    assert api.reads("arrets_transport") == []


def publish(dsn: str, status: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO etl_publications (generation, dataset_name, table_name, "
            "status, relation_oids) VALUES (gen_random_uuid(), 'connectivite', "
            "'eligibilite_fixe', %s, jsonb_build_object('eligibilite_fixe', "
            "to_regclass('eligibilite_fixe')::oid))",
            (status,),
        )


@pytest.fixture
async def mounted(migrated, client, monkeypatch):
    """The real application: authentication bypassed, publication guard real."""
    from unittest.mock import AsyncMock

    from app.common.publication_readiness import require_publication
    from app.main import app
    from app.middleware import RequestLoggingMiddleware

    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    app.dependency_overrides.pop(require_publication, None)
    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(RequestLoggingMiddleware, "_log_to_db", AsyncMock())
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def test_a_refused_broadband_publication_breaks_its_theme_only(
    mounted, migrated, amont
):
    scope(migrated, TOUT)
    publish(migrated, "failed")
    try:
        reponse = await mounted.get(f"/v1/adresse/{A.ban_id}")
    finally:
        publish(migrated, "published")

    assert reponse.status_code == 200, reponse.text
    donnees = reponse.json()["donnees"]
    reseaux = champs(theme(donnees, "reseaux_transports"))
    fibre = reseaux["adresse.connectivite.eligibilite_fibre"]
    assert (fibre["etat"], fibre["motif"]) == (
        "temporary_error",
        "publication_non_validee",
    )
    assert reseaux["adresse.transports.arret_proche"]["etat"] == "value"
    assert theme(donnees, "batiment_parcelle")["etat"] == "value"
    assert theme(donnees, "risques")["etat"] == "value"
    assert "Configuration de publication indisponible" not in reponse.text


# --- C16 (final review API WR-02): a disc that leaves the hosted departements -----


@contextmanager
def departement_voisin(dsn: str):
    """A departement not hosted (49), 700 m south of A and of HORS."""
    y1 = A.point[1] - 700 / METRES_PAR_DEGRE_LAT
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO geo_departements (code, nom, geometry) VALUES "
            "('49', 'Maine-et-Loire', ST_GeomFromText(%s, 4326))",
            (_rect(-1.43, y1 - 0.1, -1.39, y1),),
        )
    try:
        yield
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM geo_departements WHERE code = '49'")


async def test_stops_of_a_disc_reaching_a_departement_not_hosted(api, migrated, amont):
    """The stops of the other departement are not in the base: a partial list,
    and « aucun arrêt » only when the whole disc is hosted."""
    scope(migrated, TOUT)
    with departement_voisin(migrated):
        a = champs(theme(await fiche(api, A.ban_id), "reseaux_transports"))
        sans_arret = theme(await fiche(api, HORS.ban_id), "reseaux_transports")
        install_scope_at(migrated, sorted(TOUT), ("44", "49", "85"))
        heberge = champs(theme(await fiche(api, HORS.ban_id), "reseaux_transports"))

    arrets = a["adresse.transports.arret_proche"]
    assert (arrets["etat"], arrets["valeur"], arrets["liste_incomplete"]) == (
        "value",
        3,
        True,
    )
    assert arrets["motif"] == "territoire_hors_perimetre"
    hors = champs(sans_arret)["adresse.transports.arret_proche"]
    assert (hors["etat"], hors["valeur"], hors["motif"]) == (
        "out_of_coverage",
        None,
        "territoire_hors_perimetre",
    )
    assert hors["liste_incomplete"] is True and sans_arret["liste_incomplete"] is True
    zero = heberge["adresse.transports.arret_proche"]
    assert (zero["etat"], zero["valeur"], zero["liste_incomplete"]) == (
        "value",
        0,
        False,
    )
