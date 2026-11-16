"""LIEU-02 / D-115 / N-01 / N-02: sales of the parcel of the point on PostGIS.

``GET /v1/adresse/{ban_id}/ventes`` lists the distinct DVF mutations of the
parcel that contains the BAN point (never the nearest one), 10 per page,
latest first, under the DVF publication guard and a generation token. The
price of a recent isolated sale never leaves the API; the recency window is
anchored on the last imported DVF date, never on the system clock.

The scope is written by the production path and read by the real scope
dependency; only the session is overridden (the ``mounted`` fixture also
removes the test override of the publication guard).

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_adresse_ventes_contract.py
"""

import re
from collections.abc import AsyncIterator
from contextlib import contextmanager
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
from psycopg import sql as psql
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adresse.router import adresse_router
from app.common.exceptions import (
    UpstreamUnavailableError,
    register_exception_handlers,
)
from app.config import settings
from app.database import get_db
from app.etl_dvf.config import MARQUEUR_ADRESSE_SUFFIXE
from app.etl_dvf.qualification import NATURE_VEFA, NATURE_VENTE
from tests.integration.instance_scope_fixture import install_scope_at

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
TOUT = ("ban", "cadastre", "dvf", "dpe", "georisques", "demographie")
PERIODE = "2021-2025"
HALF = 0.0002  # half side of a parcel square, about 15 m


@dataclass(frozen=True)
class Adresse:
    ban_id: str
    point: tuple[float, float]
    parcelle: str | None


# One parcel per address, 0.001 degree apart (about 76 m): no overlap.
A = Adresse("85191_0801_00001", (-1.3000, 46.6000), "85191000BA0001")
B = Adresse("85191_0802_00001", (-1.3010, 46.6000), "85191000BB0001")
C = Adresse("85191_0803_00001", (-1.3020, 46.6000), "85191000BC0001")
Z = Adresse("85191_0804_00001", (-1.3030, 46.6000), "85191000BD0001")
G = Adresse("85191_0805_00001", (-1.3040, 46.6000), "85191000BE0001")
D = Adresse("85191_0806_00001", (-1.3050, 46.6000), "85191000BF0001")
E = Adresse("85191_0807_00001", (-1.3060, 46.6000), "85191000BG0001")
HORS = Adresse("85191_0808_00001", (-1.3200, 46.6000), None)
FRONTIERE = Adresse("85191_0809_00001", (-1.3300, 46.6000), None)
CONTRADICTOIRE = "85191_0810_00001"
G_ISOLEE = "85191000BE0002"  # second parcel of G's multi-parcel mutation

# Last imported DVF date: A's latest sale. The 24-month window starts on
# 2023-11-20 (inclusive), whatever the system date.
FIN_DVF = date(2025, 11, 20)

# Sentinel amounts: a withheld price must never be found in a response.
PRIX_B = 312_345
PRIX_D = 154_321
PRIX_G_MULTI = 198_765
NOMBRE_ISOLEES = 205


@dataclass(frozen=True)
class Mutation:
    id_mutation: str
    jour: date
    valeur: int | None
    surface: int | None
    parcelles: tuple[str, ...]
    maisons: int = 1
    appartements: int = 0
    dependances: int = 0
    commerciaux: int = 0
    nature: str = "Vente"


def _classe(m: Mutation) -> str:
    residentiels = m.maisons + m.appartements
    if residentiels and m.commerciaux:
        return "mixte"
    if m.commerciaux:
        return "commerciale"
    if residentiels == 1:
        return "residentiel_maison" if m.maisons else "residentiel_appartement"
    if residentiels > 1:
        return "ensemble_residentiel"
    return "terrain_seul"


A_VENTES = (
    Mutation("2019-A-1", date(2019, 3, 15), 210_000, 85, (A.parcelle,)),
    Mutation(
        "2021-A-2",
        date(2021, 6, 30),
        225_000,
        None,
        (A.parcelle,),
        maisons=0,
        appartements=2,
        dependances=1,
    ),
    Mutation("2025-A-3", FIN_DVF, 238_000, 90, (A.parcelle,), dependances=1),
)
C_VENTES = tuple(
    Mutation(f"2024-C-{i:02d}", date(2024, 5, 1), 150_000 + i, 40 + i, (C.parcelle,))
    for i in range(1, 24)
)
G_MULTI = Mutation(
    "2025-G-MULTI", date(2025, 3, 1), PRIX_G_MULTI, 70, (G.parcelle, G_ISOLEE)
)
G_DOUBLE = Mutation("2025-G-2", date(2025, 4, 1), 176_543, 72, (G.parcelle,))

# --- Task 2: similar sales around R (commune 85192), far from the 85191 sales.
R = Adresse("85192_0900_00001", (-1.5000, 46.5000), None)
METRES_PAR_DEGRE_LAT = 111_150


def au_nord(metres: float) -> tuple[float, float]:
    """A point *metres* north of R (geodesic distance within a metre)."""
    lon, lat = R.point
    return lon, lat + metres / METRES_PAR_DEGRE_LAT


def _p(n: int) -> str:
    return f"85192000CA{n:04d}"


PRIX_R8 = 287_654
# (mutation, metres north of R). Parcels are shared by two mutations, except
# R8's: a recent isolated sale, withheld.
COMPARABLES = (
    (Mutation("2025-R-01", date(2025, 2, 1), 300_000, 90, (_p(1),)), 120),
    (Mutation("2024-R-02", date(2024, 6, 1), 310_000, 100, (_p(1),)), 400),
    (Mutation("2023-R-03", date(2023, 1, 15), 250_000, 80, (_p(2),)), 900),
    (Mutation("2022-R-04", date(2022, 9, 1), 260_000, 90, (_p(3),)), 1500),
    (
        Mutation(
            "2024-R-05",
            date(2024, 2, 1),
            180_000,
            60,
            (_p(3),),
            maisons=0,
            appartements=1,
        ),
        200,
    ),
    (Mutation("2024-R-06", date(2024, 3, 1), 400_000, 120, (_p(4),)), 300),
    (Mutation("2020-R-07", date(2020, 6, 1), 230_000, 85, (_p(4),)), 600),
    (Mutation("2025-R-08", date(2025, 7, 1), PRIX_R8, 95, (_p(6),)), 700),
    (
        Mutation(
            "2024-R-09", date(2024, 9, 1), 240_000, 92, (_p(5),), nature="Echange"
        ),
        450,
    ),
    (Mutation("2022-R-10", date(2022, 3, 1), 270_000, 88, (_p(5),)), 800),
    (Mutation("2024-R-11", date(2024, 1, 1), 305_000, 90, (_p(2),)), 120),
    *(
        (Mutation(f"2022-R-X{i}", date(2022, 5, 1), 280_000, 100, (_p(7),)), 1100)
        for i in range(1, 6)
    ),
)


def _square(lon: float, lat: float, half: float = HALF) -> str:
    x0, x1, y0, y1 = lon - half, lon + half, lat - half, lat + half
    return f"MULTIPOLYGON((({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0})))"


def _rect(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"MULTIPOLYGON((({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0})))"


def insert_adresse(conn, id_: int, ban_id: str, point, voie="Rue des Ventes"):
    lon, lat = point
    conn.execute(
        "INSERT INTO adresses (id, code_departement, ban_id, numero, nom_voie, "
        "code_postal, code_commune, nom_commune, position) VALUES (%s, %s, %s, "
        "%s, %s, '85000', %s, 'La Roche-sur-Yon', "
        "ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
        (id_, ban_id[:2], ban_id, ban_id[-2:].lstrip("0"), voie, ban_id[:5], lon, lat),
    )


def insert_parcelle(conn, id_: int, id_parcelle: str, wkt: str):
    conn.execute(
        "INSERT INTO parcelles (id, code_departement, id_parcelle, commune, "
        "geometrie) VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326))",
        (id_, id_parcelle[:2], id_parcelle, id_parcelle[:5], wkt),
    )


# Phase 04.4 (D-03): a mutation without position has no geocoded line and is
# served by no address, parcel or radius reader. The sales of this fixture are
# geocoded; those whose place does not matter share this point, more than
# 20 km from R, so no radius around R ever reaches them.
GEOCODEE = (-1.2000, 46.4000)


def insert_mutation(
    conn,
    id_: int,
    m: Mutation,
    *,
    point=GEOCODEE,
    commune="85191",
    adresse=(None, None),
):
    lon, lat = point
    numero, voie = adresse
    conn.execute(
        "INSERT INTO mutations_grain (id, code_departement, id_mutation, "
        "date_mutation, nature_mutation, valeur_fonciere, classe_mutation, "
        "nb_locaux_maison, nb_locaux_appartement, nb_locaux_commercial, "
        "nb_locaux_dependance, nb_lignes, nb_dispositions, nb_communes, "
        "nb_parcelles, surface_bati_totale, mono_local, est_vente, hors_stats_vefa, "
        "code_commune, adresse_numero, adresse_nom_voie, "
        "position) VALUES (%s, '85', %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 1, 1, "
        "%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s::float8 IS NULL THEN NULL ELSE "
        "ST_SetSRID(ST_MakePoint(%s::float8, %s::float8), 4326) END)",
        (
            id_,
            m.id_mutation,
            m.jour,
            m.nature,
            m.valeur,
            _classe(m),
            m.maisons,
            m.appartements,
            m.commerciaux,
            m.dependances,
            len(m.parcelles),
            m.surface,
            m.maisons + m.appartements + m.commerciaux == 1,
            m.nature == NATURE_VENTE,
            m.nature == NATURE_VEFA,
            commune,
            numero,
            voie,
            lon,
            lon,
            lat,
        ),
    )


def link_parcelles(conn, m: Mutation):
    for id_parcelle in m.parcelles:
        conn.execute(
            "INSERT INTO mutation_parcelles (id_mutation, id_parcelle, "
            "code_departement) VALUES (%s, %s, '85')",
            (m.id_mutation, id_parcelle),
        )


# --- C37: sales declared at the address (commune 85193, no parcel under the
# points: the parcel list says « point hors parcelle », the declared list does
# not need a parcel). (ban_id, numero, rep, BAN street).
DA = "85193_0901_00002"
DA_BIS = "85193_0901_00002_bis"
DA_EGLISE = "85193_0902_00007"
DA_ZERO = "85193_0903_00009"
ADRESSES_DECLAREES = (
    (DA, "2", None, "Avenue des Châtaigniers"),
    (DA_BIS, "2", "bis", "Avenue des Châtaigniers"),
    (DA_EGLISE, "7", None, "Rue de l\u2019Église-Saint-Jean"),
    (DA_ZERO, "9", None, "Impasse des Rosiers"),
)
PRIX_DA_RECENTE = 199_999
PRIX_DA_VEFA = 243_210


def _da(n: int) -> str:
    return f"85193000DA{n:04d}"


# (mutation, commune, (declared number, declared DVF street)).
VENTES_DECLAREES = (
    # Several parcels and several locals: shown as declared, whole amount.
    (
        Mutation(
            "2019-DA-1",
            date(2019, 5, 1),
            456_789,
            None,
            (_da(1), _da(2)),
            maisons=0,
            appartements=2,
            dependances=1,
        ),
        "85193",
        ("2", "AV DES CHATAIGNIERS"),
    ),
    # Written in full in DVF; recent and isolated: price withheld.
    (
        Mutation("2025-DA-2", date(2025, 1, 15), PRIX_DA_RECENTE, 80, (_da(3),)),
        "85193",
        ("2", "AVENUE DES CHATAIGNIERS"),
    ),
    # Not a sale (est_vente false): never listed.
    (
        Mutation(
            "2022-DA-3", date(2022, 2, 1), 150_000, 70, (_da(4),), nature="Echange"
        ),
        "85193",
        ("2", "AV DES CHATAIGNIERS"),
    ),
    # Another number of the same street.
    (
        Mutation("2021-DA-4", date(2021, 3, 1), 170_000, 75, (_da(5),)),
        "85193",
        ("4", "AV DES CHATAIGNIERS"),
    ),
    # The same street name in another commune.
    (
        Mutation("2021-DA-5", date(2021, 4, 1), 180_000, 76, (_da(6),)),
        "85194",
        ("2", "AV DES CHATAIGNIERS"),
    ),
    # A number written with a leading zero.
    (
        Mutation("2020-DA-6", date(2020, 9, 1), 260_000, 95, (_da(7),)),
        "85193",
        ("02", "AV DES CHATAIGNIERS"),
    ),
    # Accents, apostrophe and hyphens of the BAN street, as DVF writes them.
    (
        Mutation("2023-DA-7", date(2023, 7, 1), 210_000, 85, (_da(8),)),
        "85193",
        ("7", "RUE DE L EGLISE SAINT JEAN"),
    ),
    # Plan 04.1-15 (D-128): a VEFA declared at « 2 », listed on request only,
    # marked and out of every statistic (D-12, C56).
    (
        Mutation(
            "2022-DA-V",
            date(2022, 10, 1),
            PRIX_DA_VEFA,
            64,
            (_da(9),),
            nature=NATURE_VEFA,
        ),
        "85193",
        ("2", "AV DES CHATAIGNIERS"),
    ),
    # The only mutation declared at « 9 »: a VEFA (no sale attached by default).
    (
        Mutation(
            "2021-DA-V", date(2021, 5, 1), 187_000, 58, (_da(10),), nature=NATURE_VEFA
        ),
        "85193",
        ("9", "IMP DES ROSIERS"),
    ),
)


def insert_declarees(conn, ident) -> None:
    for index, (ban_id, numero, rep, voie) in enumerate(ADRESSES_DECLAREES):
        conn.execute(
            "INSERT INTO adresses (id, code_departement, ban_id, numero, rep, "
            "nom_voie, code_postal, code_commune, nom_commune, position) VALUES "
            "(%s, '85', %s, %s, %s, %s, '85000', '85193', 'Commune declaree', "
            "ST_SetSRID(ST_MakePoint(%s, 46.7), 4326))",
            (95_100_300 + index, ban_id, numero, rep, voie, -1.4 - index / 1000),
        )
    for m, commune, adresse in VENTES_DECLAREES:
        insert_mutation(conn, next(ident), m, commune=commune, adresse=adresse)
        link_parcelles(conn, m)


# --- Plan 04.1-14 (C44): sales declared at a number and a suffix, commune 85195.
# (ban_id, numero, rep). One BAN street, written « RUE DES SUFFIXES » in DVF.
DS2 = "85195_0911_00002"
DS2_BIS = "85195_0911_00002_bis"
DS2_C = "85195_0911_00002_c"
DS4_QUINQUIES = "85195_0911_00004_quinquies"
DS6_BIS = "85195_0911_00006_bis"
DS6_B = "85195_0911_00006_b"
DS8 = "85195_0911_00008"
ADRESSES_SUFFIXES = (
    (DS2, "2", None),
    (DS2_BIS, "2", "bis"),
    (DS2_C, "2", "c"),
    (DS4_QUINQUIES, "4", "quinquies"),
    # « 6 Bis » and « 6 b » both read DVF « 6 B »: ambiguous.
    (DS6_BIS, "6", "Bis"),
    (DS6_B, "6", "b"),
    (DS8, "8", None),
)


def _ds(n: int) -> str:
    return f"85195000DS{n:04d}"


# (mutation, declared number, declared DVF suffix of the first line).
VENTES_SUFFIXES = (
    (Mutation("2025-DS-1", date(2025, 3, 1), 301_000, 90, (_ds(1),)), "2", None),
    (Mutation("2024-DS-2", date(2024, 5, 1), 302_000, 91, (_ds(2),)), "2", "B"),
    (Mutation("2022-DS-3", date(2022, 6, 1), 303_000, 92, (_ds(3),)), "02", "B"),
    (Mutation("2023-DS-4", date(2023, 7, 1), 304_000, 93, (_ds(4),)), "2", "C"),
    # A suffix without any BAN address « 2 z »: attached to no address.
    (Mutation("2021-DS-5", date(2021, 8, 1), 305_000, 94, (_ds(5),)), "2", "Z"),
    (Mutation("2023-DS-7", date(2023, 9, 1), 307_000, 96, (_ds(7),)), "6", "B"),
    (Mutation("2023-DS-8", date(2023, 10, 1), 308_000, 97, (_ds(8),)), "4", "C"),
    # A VEFA declared at « 2 B » (plan 04.1-15): on « 2 bis » on request only.
    (
        Mutation(
            "2023-DS-V", date(2023, 2, 1), 310_000, 70, (_ds(10),), nature=NATURE_VEFA
        ),
        "2",
        "B",
    ),
    # Not a sale: never listed, whatever its suffix.
    (
        Mutation(
            "2022-DS-9", date(2022, 2, 1), 309_000, 98, (_ds(9),), nature="Echange"
        ),
        "2",
        "B",
    ),
)


# 04.5-08 (Codex review I1): BAN addresses that read the same DVF number,
# suffix and street once normalised as the attachment normalises them
# (leading zeros dropped, street compared in full and abbreviated), written
# differently in the BAN. (ban_id, numero, rep, BAN street), commune 85195.
DL6_BIS = "85195_0912_00006_bis"
DL6_B = "85195_0912_00006_b"
DL10_BIS = "85195_0912_00010_bis"
DL10_B = "85195_0913_00010_b"
DL12_BIS = "85195_0912_00012_bis"
DL12_TER = "85195_0913_00012_ter"
ADRESSES_LILAS = (
    # « 06 bis » and « 6 b »: both read DVF « 6 B ».
    (DL6_BIS, "06", "bis", "Avenue des Lilas"),
    (DL6_B, "6", "b", "Avenue des Lilas"),
    # « Avenue des Lilas » and « AV DES LILAS »: both read DVF « AV DES LILAS ».
    (DL10_BIS, "10", "bis", "Avenue des Lilas"),
    (DL10_B, "10", "b", "AV DES LILAS"),
    # Equivalent number and street, another suffix: not ambiguous.
    (DL12_BIS, "12", "bis", "Avenue des Lilas"),
    (DL12_TER, "012", "ter", "AV DES LILAS"),
)
# (mutation, declared number, declared DVF suffix); DVF writes « AV DES LILAS ».
VENTES_LILAS = (
    (Mutation("2023-DL-6", date(2023, 4, 1), 311_000, 81, (_ds(11),)), "6", "B"),
    (Mutation("2023-DL-10", date(2023, 5, 1), 312_000, 82, (_ds(12),)), "10", "B"),
    (Mutation("2023-DL-12", date(2023, 6, 1), 313_000, 83, (_ds(13),)), "12", "B"),
)


def insert_suffixes(conn, ident) -> None:
    for index, (ban_id, numero, rep, voie) in enumerate(ADRESSES_LILAS):
        conn.execute(
            "INSERT INTO adresses (id, code_departement, ban_id, numero, rep, "
            "nom_voie, code_postal, code_commune, nom_commune, position) VALUES "
            "(%s, '85', %s, %s, %s, %s, '85000', '85195', 'Commune suffixes', "
            "ST_SetSRID(ST_MakePoint(%s, 46.81), 4326))",
            (95_100_500 + index, ban_id, numero, rep, voie, -1.5 - index / 1000),
        )
    for m, numero, suffixe in VENTES_LILAS:
        insert_mutation(
            conn, next(ident), m, commune="85195", adresse=(numero, "AV DES LILAS")
        )
        link_parcelles(conn, m)
        conn.execute(
            "UPDATE mutations_grain SET adresse_suffixe = %s WHERE id_mutation = %s",
            (suffixe, m.id_mutation),
        )
    for index, (ban_id, numero, rep) in enumerate(ADRESSES_SUFFIXES):
        conn.execute(
            "INSERT INTO adresses (id, code_departement, ban_id, numero, rep, "
            "nom_voie, code_postal, code_commune, nom_commune, position) VALUES "
            "(%s, '85', %s, %s, %s, 'Rue des Suffixes', '85000', '85195', "
            "'Commune suffixes', ST_SetSRID(ST_MakePoint(%s, 46.8), 4326))",
            (95_100_400 + index, ban_id, numero, rep, -1.5 - index / 1000),
        )
    for m, numero, suffixe in VENTES_SUFFIXES:
        insert_mutation(
            conn, next(ident), m, commune="85195", adresse=(numero, "RUE DES SUFFIXES")
        )
        link_parcelles(conn, m)
        conn.execute(
            "UPDATE mutations_grain SET adresse_suffixe = %s WHERE id_mutation = %s",
            (suffixe, m.id_mutation),
        )


def marquer_suffixe(dsn: str, marqueur: str | None) -> None:
    """Comment of the column as the DVF reconstruction leaves it (or not)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            psql.SQL("COMMENT ON COLUMN mutations_grain.adresse_suffixe IS {}").format(
                psql.Literal(marqueur)
            )
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
            (_square(-1.3, 46.6, 0.1),),
        )
        for index, adresse in enumerate((A, B, C, Z, G, D, E, HORS, FRONTIERE)):
            insert_adresse(conn, 95_100_001 + index, adresse.ban_id, adresse.point)
            if adresse.parcelle:
                insert_parcelle(
                    conn, 96_100_001 + index, adresse.parcelle, _square(*adresse.point)
                )
        x, y = FRONTIERE.point
        insert_parcelle(
            conn, 96_100_100, "85191000BH0001", _rect(x - HALF, y - HALF, x, y + HALF)
        )
        insert_parcelle(
            conn, 96_100_101, "85191000BH0002", _rect(x, y - HALF, x + HALF, y + HALF)
        )
        insert_parcelle(conn, 96_100_102, G_ISOLEE, _square(-1.3040, 46.6100))
        insert_adresse(conn, 95_100_100, CONTRADICTOIRE, (-1.3070, 46.6000))
        insert_adresse(conn, 95_100_101, CONTRADICTOIRE, (-1.3080, 46.6000))

        ident = iter(range(98_100_001, 98_200_000))
        for m in (*A_VENTES, *C_VENTES):
            insert_mutation(conn, next(ident), m)
            link_parcelles(conn, m)
        # More than 200 parcels carrying one recent mutation each, inserted
        # before the target: a predicate bounded to 200 parcels would miss B.
        for i in range(NOMBRE_ISOLEES):
            m = Mutation(
                f"2025-ISO-{i:03d}",
                date(2025, 1, 2),
                100_000,
                60,
                (f"85191000ZZ{i:04d}",),
            )
            insert_mutation(conn, next(ident), m)
            link_parcelles(conn, m)
        for m in (
            Mutation(
                "2025-B-1", date(2025, 6, 1), PRIX_B, 95, (B.parcelle,), dependances=1
            ),
            G_MULTI,
            Mutation("2023-D-1", date(2023, 11, 20), PRIX_D, 88, (D.parcelle,)),
            Mutation("2023-E-1", date(2023, 11, 19), 143_210, 87, (E.parcelle,)),
        ):
            insert_mutation(conn, next(ident), m)
            link_parcelles(conn, m)
        # A duplicated DVF row of one mutation: one sale, counted once.
        insert_mutation(conn, next(ident), G_DOUBLE)
        insert_mutation(conn, next(ident), G_DOUBLE)
        link_parcelles(conn, G_DOUBLE)
        insert_declarees(conn, ident)
        insert_suffixes(conn, ident)
        insert_adresse(conn, 95_100_200, R.ban_id, R.point, voie="Rue Ressemblante")
        for m, metres in COMPARABLES:
            insert_mutation(
                conn, next(ident), m, point=au_nord(metres), commune="85192"
            )
            link_parcelles(conn, m)
        conn.execute(
            "INSERT INTO dataset_metadata (dataset_name, status, "
            "source_period_label) VALUES ('dvf', 'disponible', %s) "
            "ON CONFLICT (dataset_name) DO UPDATE SET source_period_label = "
            "EXCLUDED.source_period_label",
            (PERIODE,),
        )
    marquer_suffixe(isolated_pg_dsn, MARQUEUR_ADRESSE_SUFFIXE)
    return isolated_pg_dsn


@dataclass
class Api:
    client: httpx.AsyncClient
    statements: list[str]

    def reads(self, table: str) -> list[str]:
        return [sql for sql in self.statements if table in sql]


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


def scope(dsn, datasets=TOUT):
    install_scope_at(dsn, sorted(datasets), ("44", "85"))


async def ventes(api: Api, ban_id: str, status: int = 200, **params) -> dict:
    response = await api.client.get(f"/v1/adresse/{ban_id}/ventes", params=params)
    assert response.status_code == status, response.text
    # A page of one generation and a problem are never stored by a cache.
    assert response.headers["cache-control"] == "no-store"
    if status != 200:
        assert response.headers["content-type"].startswith("application/problem+json")
        assert ban_id not in response.text
        assert response.json()["instance"] == "/v1/adresse/{ban_id}/ventes"
        return response.json()
    return response.json()["donnees"]


def _prix(cartes: list[dict]) -> list[float | None]:
    return [carte["prix"] for carte in cartes]


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


def publish(dsn: str, status: str, table: str = "mutations_grain") -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO etl_publications (generation, dataset_name, table_name, "
            "status, relation_oids) VALUES (gen_random_uuid(), 'dvf', %s, %s, "
            "jsonb_build_object(%s::text, to_regclass(%s)::oid))",
            (table, status, table, table),
        )


# --- Task 1: tracer ---------------------------------------------------------------


async def test_tracer_parcel_sales_latest_first_and_a_withheld_isolated_price(
    api, migrated
):
    scope(migrated)

    a = await ventes(api, A.ban_id)
    assert (a["etat"], a["total"], a["page"], a["pages"], a["par_page"]) == (
        "value",
        3,
        1,
        1,
        10,
    )
    assert a["rattachement"] == "parcelle_du_point"
    assert a["periode"] == PERIODE
    assert a["source"]["cle"] == "dvf"
    assert len(a["generation"]) == 16
    assert [carte["date"] for carte in a["ventes"]] == [
        "2025-11-20",
        "2021-06-30",
        "2019-03-15",
    ]
    assert _prix(a["ventes"]) == [238_000, 225_000, 210_000]
    assert [carte["prix_affiche"] for carte in a["ventes"]] == [True, True, True]
    # Honest price per m2: a single-local residential sale with a surface only.
    assert [carte["prix_m2"] for carte in a["ventes"]] == [2644, None, 2471]
    assert a["ventes"][1]["composition"] == {
        "maisons": 0,
        "appartements": 2,
        "dependances": 1,
        "locaux_activite": 0,
    }
    assert a["ventes"][1]["surface_batie"] is None
    assert all(carte["distance_m"] is None for carte in a["ventes"])

    b = await ventes(api, B.ban_id)
    assert b["total"] == 1
    (carte,) = b["ventes"]
    assert (carte["prix"], carte["prix_m2"], carte["prix_affiche"]) == (
        None,
        None,
        False,
    )
    assert carte["minimisation"] == {"motif": "vente_isolee_recente"}
    assert carte["surface_batie"] == 95

    raw = (await api.client.get(f"/v1/adresse/{B.ban_id}/ventes")).text
    assert str(PRIX_B) not in raw and "312345" not in raw
    assert "2025-B-1" not in raw and "id_mutation" not in raw


async def test_the_list_total_is_the_landmark_count_on_three_stable_pages(
    api, migrated, monkeypatch
):
    scope(migrated)
    monkeypatch.setattr(
        "app.adresse.risques.query_rga", AsyncMock(return_value={"exposition": "x"})
    )
    # 04-06: the communal risks of the « Risques » theme, never a network call.
    monkeypatch.setattr(
        "app.adresse.risques.lire_risques_commune",
        AsyncMock(side_effect=UpstreamUnavailableError("Georisques commune")),
    )

    pages = [await ventes(api, C.ban_id, page=n) for n in (1, 2, 3)]
    fiche = (await api.client.get(f"/v1/adresse/{C.ban_id}")).json()["donnees"]

    surfaces = [carte["surface_batie"] for page in pages for carte in page["ventes"]]
    assert len(surfaces) == 23 and len(set(surfaces)) == 23
    # Same dates: the order falls back on the mutation, stable across pages.
    assert surfaces == list(range(41, 64))
    assert {page["total"] for page in pages} == {23}
    assert {page["pages"] for page in pages} == {3}
    assert len({page["generation"] for page in pages}) == 1
    assert fiche["reperes"]["ventes"]["nombre"]["valeur"] == 23
    problem = await ventes(api, C.ban_id, 404, page=4)
    assert problem["code"] == "page_hors_limites"


async def test_a_duplicated_row_and_a_multi_parcel_mutation(api, migrated):
    scope(migrated)

    g = await ventes(api, G.ban_id)

    # Two mutations, the duplicated DVF row counted once.
    assert g["total"] == 2 and len(g["ventes"]) == 2
    deux, multi = g["ventes"]
    assert deux["date"] == "2025-04-01" and deux["prix"] == 176_543
    # One of its parcels carries it alone: isolated, recent, withheld.
    assert multi["date"] == "2025-03-01"
    assert (multi["prix"], multi["minimisation"]) == (
        None,
        {"motif": "vente_isolee_recente"},
    )
    raw = (await api.client.get(f"/v1/adresse/{G.ban_id}/ventes")).text
    assert str(PRIX_G_MULTI) not in raw


async def test_the_window_is_anchored_on_the_dvf_date_and_inclusive(api, migrated):
    scope(migrated)

    d = (await ventes(api, D.ban_id))["ventes"][0]
    e = (await ventes(api, E.ban_id))["ventes"][0]

    # 2023-11-20 is exactly 24 months before the last DVF date: withheld,
    # although it is more than 24 months before the system date.
    assert (d["prix"], d["minimisation"]) == (None, {"motif": "vente_isolee_recente"})
    assert (e["prix"], e["minimisation"]) == (143_210, None)
    raw = (await api.client.get(f"/v1/adresse/{D.ban_id}/ventes")).text
    assert str(PRIX_D) not in raw


async def test_a_parcel_without_sale_is_a_measured_zero_with_its_period(api, migrated):
    scope(migrated)

    z = await ventes(api, Z.ban_id)

    assert (z["etat"], z["total"], z["ventes"], z["periode"]) == (
        "value",
        0,
        [],
        PERIODE,
    )
    assert (z["page"], z["pages"]) == (1, 1)


async def test_the_list_without_a_declared_label_carries_the_import_day(api, migrated):
    """C24 (b), WINDOWS n° 86: an empty DVF label falls back on C14."""
    scope(migrated)
    sans_libelle = (
        "UPDATE dataset_metadata SET source_period_label = NULL "
        "WHERE dataset_name = 'dvf'"
    )
    rendu = (
        f"UPDATE dataset_metadata SET source_period_label = '{PERIODE}' "
        "WHERE dataset_name = 'dvf'"
    )

    with injected(migrated, sans_libelle, restore=(rendu,)):
        sans_rien = await ventes(api, A.ban_id)
    with injected(
        migrated,
        sans_libelle,
        "INSERT INTO etl_runs (dataset_name, status, started_at, completed_at) "
        "VALUES ('dvf', 'success', TIMESTAMPTZ '2026-09-25 21:30:00+00', "
        "TIMESTAMPTZ '2026-09-25 22:30:00+00')",
        restore=(rendu, "DELETE FROM etl_runs WHERE dataset_name = 'dvf'"),
    ):
        importe = await ventes(api, A.ban_id)

    assert (sans_rien["etat"], sans_rien["total"], sans_rien["periode"]) == (
        "value",
        3,
        None,
    )
    assert (importe["etat"], importe["total"], importe["periode"]) == (
        "value",
        3,
        "2026-09-26",
    )


@pytest.mark.parametrize(
    ("ban_id", "motif", "nombre"),
    [
        (HORS.ban_id, "point_hors_parcelle", None),
        (FRONTIERE.ban_id, "parcelles_multiples", 2),
        (CONTRADICTOIRE, "parcelle_indeterminee", None),
    ],
)
async def test_no_sale_without_a_single_parcel_and_no_dvf_read(
    api, migrated, ban_id, motif, nombre
):
    scope(migrated)
    api.statements.clear()

    reponse = await ventes(api, ban_id)

    assert (reponse["etat"], reponse["motif"], reponse["nombre_parcelles"]) == (
        "not_available",
        motif,
        nombre,
    )
    assert reponse["ventes"] == [] and reponse["total"] is None
    assert api.reads("mutations_grain") == []


@pytest.mark.parametrize("absent", ["dvf", "cadastre", "ban"])
async def test_a_source_not_hosted_is_out_of_coverage_without_dvf_read(
    api, migrated, absent
):
    scope(migrated, tuple(d for d in TOUT if d != absent))
    api.statements.clear()

    reponse = await ventes(api, A.ban_id)

    assert reponse["etat"] == "out_of_coverage"
    assert reponse["motif"] == "dataset_non_heberge"
    assert reponse["ventes"] == []
    assert api.reads("mutations_grain") == []


async def test_a_cadastre_outage_is_an_outage_of_the_section(api, migrated):
    scope(migrated)
    with injected(
        migrated,
        "ALTER TABLE parcelles RENAME TO parcelles_panne",
        restore=("ALTER TABLE parcelles_panne RENAME TO parcelles",),
    ):
        reponse = await ventes(api, A.ban_id)

    assert (reponse["etat"], reponse["motif"]) == (
        "temporary_error",
        "lecture_indisponible",
    )
    assert reponse["ventes"] == []


@pytest.mark.parametrize(
    ("ban_id", "code"),
    [("85191_0801_00099", "adresse_inconnue"), ("85191_0801", "voie_sans_numero")],
)
async def test_unknown_key_and_street_are_404(api, migrated, ban_id, code):
    scope(migrated)
    assert (await ventes(api, ban_id, 404))["code"] == code


async def test_a_malformed_generation_is_refused_before_any_read(api, migrated):
    scope(migrated)
    response = await api.client.get(
        f"/v1/adresse/{A.ban_id}/ventes", params={"vg": "zz"}
    )
    assert response.status_code == 422


async def test_an_isolation_outage_publishes_no_exact_recent_price(
    api, migrated, monkeypatch
):
    """The predicate fails at the SQL level: recent sales are withheld."""
    from app.adresse import ventes as module

    scope(migrated)
    broken = module._ISOLEMENT_SQL.text.replace(
        "FROM mutation_parcelles c", "FROM mutation_parcelles_panne c"
    )
    assert broken != module._ISOLEMENT_SQL.text
    monkeypatch.setattr(module, "_ISOLEMENT_SQL", module.text(broken))

    a = await ventes(api, A.ban_id)
    b = await ventes(api, B.ban_id)

    assert a["total"] == 3
    assert _prix(a["ventes"]) == [None, 225_000, 210_000]
    assert a["ventes"][0]["minimisation"] == {"motif": "isolement_indetermine"}
    assert b["ventes"][0]["minimisation"] == {"motif": "isolement_indetermine"}
    assert b["ventes"][0]["prix"] is None and b["ventes"][0]["prix_m2"] is None


async def test_a_sql_outage_of_dvf_is_a_typed_503(api, migrated):
    scope(migrated)
    with injected(
        migrated,
        "ALTER TABLE mutations_grain RENAME TO mutations_grain_panne",
        restore=("ALTER TABLE mutations_grain_panne RENAME TO mutations_grain",),
    ):
        problem = await ventes(api, A.ban_id, 503)

    assert problem["code"] == "ventes_indisponibles"
    assert "ventes" not in problem


def _swap_mutations(dsn: str) -> None:
    """Replace ``mutations_grain`` by a copy, as an ETL swap does, then publish."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE TABLE mutations_grain_new (LIKE mutations_grain)")
        conn.execute("INSERT INTO mutations_grain_new SELECT * FROM mutations_grain")
        conn.execute("ALTER TABLE mutations_grain RENAME TO mutations_grain_retired")
        conn.execute("ALTER TABLE mutations_grain_new RENAME TO mutations_grain")
    publish(dsn, "published")


def _restore_mutations(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP TABLE mutations_grain")
        conn.execute("ALTER TABLE mutations_grain_retired RENAME TO mutations_grain")
    publish(dsn, "published")


async def test_a_page_link_of_a_replaced_generation_is_refused(api, migrated):
    scope(migrated)
    first = await ventes(api, C.ban_id, page=1)

    _swap_mutations(migrated)
    try:
        problem = await ventes(api, C.ban_id, 409, page=2, vg=first["generation"])
        fresh = await ventes(api, C.ban_id, page=1)
    finally:
        _restore_mutations(migrated)

    assert problem["code"] == "generation_changee"
    assert first["generation"] not in str(problem)
    assert fresh["generation"] != first["generation"] and fresh["total"] == 23


@pytest.fixture
async def mounted(migrated, client, monkeypatch):
    """The real application: authentication bypassed, publication guard real."""
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
    monkeypatch.setattr(
        "app.adresse.risques.query_rga", AsyncMock(return_value={"exposition": "x"})
    )
    # 04-06: the communal risks of the « Risques » theme, never a network call.
    monkeypatch.setattr(
        "app.adresse.risques.lire_risques_commune",
        AsyncMock(side_effect=UpstreamUnavailableError("Georisques commune")),
    )
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def test_refused_dvf_publication_breaks_the_sales_only(mounted, migrated):
    scope(migrated)
    publish(migrated, "failed")
    try:
        identite = await mounted.get(f"/v1/adresse/{A.ban_id}")
        documents = await mounted.get(f"/v1/adresse/{A.ban_id}/dpe")
        liste = await mounted.get(f"/v1/adresse/{A.ban_id}/ventes")
    finally:
        publish(migrated, "published")

    assert identite.status_code == 200, identite.text
    fiche = identite.json()["donnees"]
    assert fiche["identite"]["ban_id"] == A.ban_id
    assert fiche["reperes"]["ventes"]["nombre"]["etat"] == "temporary_error"
    assert fiche["reperes"]["dpe"]["etat"] == "value"
    assert documents.status_code == 200, documents.text
    assert liste.status_code == 503, liste.text
    assert liste.headers["content-type"].startswith("application/problem+json")
    assert liste.json()["code"] == "ventes_indisponibles"
    assert liste.headers["cache-control"] == "no-store"
    assert "238000" not in liste.text
    assert "Configuration de publication indisponible" not in liste.text


# --- Task 2: similar sales (D-114) ---------------------------------------------------


async def ressemblantes(api: Api, ban_id: str, status: int = 200, **params) -> dict:
    response = await api.client.get(
        f"/v1/adresse/{ban_id}/ventes-ressemblantes", params=params
    )
    assert response.status_code == status, response.text
    if status != 422:  # the generic validation handler of the API (IN-01, out of scope)
        assert response.headers["cache-control"] == "no-store"
    if status not in (200, 422):
        assert response.headers["content-type"].startswith("application/problem+json")
        assert ban_id not in response.text
        assert (
            response.json()["instance"] == "/v1/adresse/{ban_id}/ventes-ressemblantes"
        )
    return response.json() if status != 200 else response.json()["donnees"]


def _surfaces(reponse: dict) -> list[int]:
    return [carte["surface_batie"] for carte in reponse["ventes"]]


async def test_recipe_task_4_houses_of_90_m2_within_1_km(api, migrated):
    """« Liste les ventes ressemblantes pour une maison de 90 m² dans un rayon de
    1 km ; dis combien il y en a et selon quels critères. »"""
    scope(migrated)

    r = await ressemblantes(api, R.ban_id, type="maison", surface=90, rayon="1000")

    assert r["etat"] == "value" and r["rattachement"] == "rayon_du_point"
    assert r["criteres_appliques"] == {
        "type": "maison",
        "surface": 90,
        "tolerance": 20,
        "surface_min": 72,
        "surface_max": 108,
        "perimetre": "rayon",
        "rayon_m": 1000,
        "periode": {"annees": 5, "debut": 2021, "fin": 2025},
    }
    # R1 and R11 at 120 m (latest first), R2, R8, R10, R3; never R6 (120 m2),
    # R7 (2020), R9 (exchange), R4 (1.5 km) nor R5 (appartement).
    assert r["effectif"] == 6 and (r["page"], r["pages"]) == (1, 1)
    assert [c["date"] for c in r["ventes"]] == [
        "2025-02-01",
        "2024-01-01",
        "2024-06-01",
        "2025-07-01",
        "2022-03-01",
        "2023-01-15",
    ]
    distances = [c["distance_m"] for c in r["ventes"]]
    assert distances == sorted(distances)
    for servie, attendue in zip(distances, (120, 120, 400, 700, 800, 900), strict=True):
        assert abs(servie - attendue) <= 2
    assert len(r["generation"]) == 16


async def test_the_withheld_price_rule_applies_to_similar_sales(api, migrated):
    scope(migrated)

    r = await ressemblantes(api, R.ban_id, type="maison", surface=90, rayon="1000")
    response = await api.client.get(
        f"/v1/adresse/{R.ban_id}/ventes-ressemblantes",
        params={"type": "maison", "surface": 90, "rayon": "1000"},
    )

    (r8,) = [c for c in r["ventes"] if c["date"] == "2025-07-01"]
    assert (r8["prix"], r8["prix_m2"], r8["minimisation"]) == (
        None,
        None,
        {"motif": "vente_isolee_recente"},
    )
    assert str(PRIX_R8) not in response.text
    assert r["ventes"][0]["prix"] == 300_000


@pytest.mark.parametrize(
    ("params", "effectif", "surfaces"),
    [
        # Without surface: no surface bound, the 120 m2 house is in.
        ({"rayon": "500"}, 4, [90, 90, 120, 100]),
        ({"rayon": "250"}, 2, [90, 90]),
        ({"surface": 90, "rayon": "1000", "periode": "3"}, 5, [90, 90, 100, 95, 80]),
        ({"surface": 90, "rayon": "1000", "periode": "1"}, 2, [90, 95]),
        ({"surface": 90, "rayon": "1000", "tolerance": "10"}, 4, [90, 90, 95, 88]),
    ],
)
async def test_radius_period_and_tolerance_are_applied_as_served(
    api, migrated, params, effectif, surfaces
):
    scope(migrated)

    r = await ressemblantes(api, R.ban_id, type="maison", **params)

    assert r["effectif"] == effectif
    assert _surfaces(r) == surfaces


async def test_the_whole_commune_counted_before_two_stable_pages(api, migrated):
    scope(migrated)

    pages = [
        await ressemblantes(api, R.ban_id, type="maison", rayon="commune", page=n)
        for n in (1, 2)
    ]

    assert {p["effectif"] for p in pages} == {13}
    assert {p["pages"] for p in pages} == {2}
    assert pages[0]["rattachement"] == "commune_de_l_adresse"
    assert pages[0]["criteres_appliques"]["perimetre"] == "commune"
    assert pages[0]["criteres_appliques"]["rayon_m"] is None
    assert len(pages[0]["ventes"]) == 10
    # Distance, then date, then mutation: the same order across pages.
    assert _surfaces(pages[1]) == [100, 100, 90]
    distances = [c["distance_m"] for p in pages for c in p["ventes"]]
    assert distances == sorted(distances)
    problem = await ressemblantes(
        api, R.ban_id, 404, type="maison", rayon="commune", page=3
    )
    assert problem["code"] == "page_hors_limites"


async def test_one_type_never_mixes_with_the_other(api, migrated):
    scope(migrated)

    r = await ressemblantes(api, R.ban_id, type="appartement", rayon="500")

    assert r["effectif"] == 1
    assert r["ventes"][0]["composition"]["appartements"] == 1
    assert r["criteres_appliques"]["type"] == "appartement"


async def test_zero_similar_sale_still_serves_the_applied_criteria(api, migrated):
    scope(migrated)

    r = await ressemblantes(
        api, R.ban_id, type="appartement", surface=200, rayon="250", periode="1"
    )

    assert (r["etat"], r["effectif"], r["ventes"]) == ("value", 0, [])
    assert r["criteres_appliques"]["surface_min"] == 160
    assert r["criteres_appliques"]["periode"] == {
        "annees": 1,
        "debut": 2025,
        "fin": 2025,
    }


@pytest.mark.parametrize(
    "params",
    [
        {"type": "maison", "rayon": "5000"},
        {"type": "maison", "tolerance": "50"},
        {"type": "maison", "periode": "10"},
        {"type": "bureau"},
        {"type": "maison", "surface": 5},
        {"type": "maison", "surface": 1001},
        {"rayon": "500"},
    ],
)
async def test_a_criterion_outside_the_closed_enumerations_is_a_422(
    api, migrated, params
):
    scope(migrated)
    api.statements.clear()

    await ressemblantes(api, R.ban_id, 422, **params)

    assert api.reads("mutations_grain") == []


async def test_no_single_point_no_distance(api, migrated):
    scope(migrated)

    r = await ressemblantes(api, CONTRADICTOIRE, type="maison")

    assert (r["etat"], r["motif"]) == ("not_available", "position_indeterminee")
    assert r["effectif"] is None and r["ventes"] == []


async def test_similar_sales_dvf_not_hosted_is_out_of_coverage(api, migrated):
    scope(migrated, tuple(d for d in TOUT if d != "dvf"))
    api.statements.clear()

    r = await ressemblantes(api, R.ban_id, type="maison")

    assert (r["etat"], r["motif"]) == ("out_of_coverage", "dataset_non_heberge")
    assert api.reads("mutations_grain") == []


async def test_similar_sales_unknown_key_is_a_404(api, migrated):
    scope(migrated)
    problem = await ressemblantes(api, "85192_0900_00099", 404, type="maison")
    assert problem["code"] == "adresse_inconnue"


async def test_a_similar_sales_link_of_a_replaced_generation_is_refused(api, migrated):
    scope(migrated)
    first = await ressemblantes(api, R.ban_id, type="maison", rayon="commune")

    _swap_mutations(migrated)
    try:
        problem = await ressemblantes(
            api,
            R.ban_id,
            409,
            type="maison",
            rayon="commune",
            page=2,
            vg=first["generation"],
        )
    finally:
        _restore_mutations(migrated)

    assert problem["code"] == "generation_changee"


async def test_refused_dvf_publication_breaks_the_similar_sales(mounted, migrated):
    scope(migrated)
    publish(migrated, "failed")
    try:
        response = await mounted.get(
            f"/v1/adresse/{R.ban_id}/ventes-ressemblantes",
            params={"type": "maison", "surface": 90, "rayon": "1000"},
        )
    finally:
        publish(migrated, "published")

    assert response.status_code == 503, response.text
    assert response.json()["code"] == "ventes_indisponibles"
    assert response.headers["cache-control"] == "no-store"
    assert "300000" not in response.text


# --- C16 (final review API WR-02): a radius that leaves the hosted departements ----


@contextmanager
def departement_voisin(dsn: str):
    """A departement not hosted (49), about 600 m east of R."""
    x0 = R.point[0] + 600 / 76_500
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO geo_departements (code, nom, geometry) VALUES "
            "('49', 'Maine-et-Loire', ST_GeomFromText(%s, 4326))",
            (_rect(x0, 46.4, x0 + 0.1, 46.6),),
        )
    try:
        yield
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM geo_departements WHERE code = '49'")


async def test_a_radius_reaching_a_departement_not_hosted_is_a_partial_count(
    api, migrated
):
    """Sales beyond the hosted departements are not in the base: the count is
    the hosted side only, never served as complete, and never a zero."""
    scope(migrated)
    with departement_voisin(migrated):
        partiel = await ressemblantes(
            api, R.ban_id, type="maison", surface=90, rayon="1000"
        )
        zero = await ressemblantes(
            api, R.ban_id, type="appartement", surface=200, rayon="1000", periode="1"
        )
        dans = await ressemblantes(
            api, R.ban_id, type="maison", surface=90, rayon="500"
        )
        commune = await ressemblantes(api, R.ban_id, type="maison", rayon="commune")
        install_scope_at(migrated, sorted(TOUT), ("44", "49", "85"))
        heberge = await ressemblantes(
            api, R.ban_id, type="maison", surface=90, rayon="1000"
        )

    assert (partiel["etat"], partiel["effectif"], partiel["liste_incomplete"]) == (
        "value",
        6,
        True,
    )
    assert partiel["motif"] == "territoire_hors_perimetre"
    assert (zero["etat"], zero["effectif"], zero["motif"]) == (
        "out_of_coverage",
        None,
        "territoire_hors_perimetre",
    )
    assert zero["liste_incomplete"] is True and zero["ventes"] == []
    assert zero["criteres_appliques"]["rayon_m"] == 1000
    assert (dans["liste_incomplete"], dans["motif"]) == (False, None)
    assert (commune["liste_incomplete"], commune["motif"]) == (False, None)
    assert (heberge["effectif"], heberge["liste_incomplete"]) == (6, False)


async def test_both_sales_lists_share_one_generation_token(api, migrated):
    """Final review FRONT IN-05: the page relays one ``vg`` to both lists; the API
    dates the sales of the parcel and the similar sales by the same DVF generation.
    A list with its own generation must break this test and get its own parameter."""
    scope(migrated)

    parcelle = await ventes(api, A.ban_id)
    ressemblantes_ = await ressemblantes(api, R.ban_id, type="maison", rayon="1000")

    assert len(parcelle["generation"]) == 16
    assert parcelle["generation"] == ressemblantes_["generation"]


# --- C37: sales declared at the address ---------------------------------------------


async def declarees(api: Api, ban_id: str, status: int = 200, **params) -> dict:
    response = await api.client.get(
        f"/v1/adresse/{ban_id}/ventes-declarees", params=params
    )
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    if status != 200:
        assert response.headers["content-type"].startswith("application/problem+json")
        assert ban_id not in response.text
        assert response.json()["instance"] == "/v1/adresse/{ban_id}/ventes-declarees"
        return response.json()
    return response.json()["donnees"]


async def test_declared_sales_at_an_address_outside_every_parcel(api, migrated):
    """C37: the sales declared at « 2 Avenue des Châtaigniers » are listed for
    the address whose point lies in no parcel; the parcel list keeps its own
    state, the two relations are never merged."""
    scope(migrated)
    api.statements.clear()

    reponse = await declarees(api, DA)

    # The cadastre is never read (``mutation_parcelles`` is, for the isolation).
    assert not [sql for sql in api.statements if re.search(r"\bparcelles\b", sql)]
    assert (reponse["etat"], reponse["motif"], reponse["total"]) == ("value", None, 3)
    assert reponse["rattachement"] == "adresse_declaree_vente"
    assert (reponse["page"], reponse["pages"], reponse["par_page"]) == (1, 1, 10)
    assert reponse["periode"] == PERIODE and reponse["source"]["cle"] == "dvf"
    # Abbreviated and full street forms, a leading zero; not the exchange, not
    # number 4, not the same street of another commune.
    assert [carte["date"] for carte in reponse["ventes"]] == [
        "2025-01-15",
        "2020-09-01",
        "2019-05-01",
    ]
    recente, zero_initial, plusieurs = reponse["ventes"]
    assert (recente["prix"], recente["prix_affiche"]) == (None, False)
    assert recente["minimisation"] == {"motif": "vente_isolee_recente"}
    assert zero_initial["prix"] == 260_000
    # Several parcels and locals: shown as declared, with its whole amount.
    assert plusieurs["prix"] == 456_789 and plusieurs["prix_m2"] is None
    assert plusieurs["composition"] == {
        "maisons": 0,
        "appartements": 2,
        "dependances": 1,
        "locaux_activite": 0,
    }
    raw = (await api.client.get(f"/v1/adresse/{DA}/ventes-declarees")).text
    assert str(PRIX_DA_RECENTE) not in raw
    assert "2025-DA-2" not in raw and "id_mutation" not in raw

    parcelle = await ventes(api, DA)
    assert (parcelle["etat"], parcelle["motif"]) == (
        "not_available",
        "point_hors_parcelle",
    )


async def test_accents_apostrophe_and_hyphens_match_the_dvf_street(api, migrated):
    scope(migrated)

    reponse = await declarees(api, DA_EGLISE)

    assert reponse["total"] == 1
    assert [carte["prix"] for carte in reponse["ventes"]] == [210_000]


async def test_a_bis_address_does_not_show_the_sales_of_its_number(api, migrated):
    """C37, « Au 2 seulement »: the imported DVF keeps no suffix (C44, until 4.1)."""
    scope(migrated)
    api.statements.clear()

    reponse = await declarees(api, DA_BIS)

    assert (reponse["etat"], reponse["motif"]) == (
        "not_available",
        "suffixe_non_distingue",
    )
    assert reponse["total"] is None and reponse["ventes"] == []
    assert api.reads("mutations_grain") == []


async def test_no_sale_attached_to_the_address_is_a_value_0(api, migrated):
    """The list serves its 0 found with its period; it says no sale attached,
    not that none took place (C44, CR-02)."""
    scope(migrated)

    reponse = await declarees(api, DA_ZERO)

    assert (reponse["etat"], reponse["total"], reponse["pages"]) == ("value", 0, 1)
    assert reponse["ventes"] == [] and reponse["periode"] == PERIODE
    assert (await declarees(api, DA_ZERO, 404, page=2))["code"] == "page_hors_limites"


@pytest.mark.parametrize("absent", ["dvf", "ban"])
async def test_declared_sales_not_hosted_are_out_of_coverage(api, migrated, absent):
    scope(migrated, tuple(d for d in TOUT if d != absent))
    api.statements.clear()

    reponse = await declarees(api, DA)

    assert (reponse["etat"], reponse["motif"]) == (
        "out_of_coverage",
        "dataset_non_heberge",
    )
    assert reponse["ventes"] == [] and reponse["total"] is None
    assert api.reads("mutations_grain") == []


@pytest.mark.parametrize(
    ("ban_id", "code"),
    [("85193_0901_00099", "adresse_inconnue"), ("85193_0901", "voie_sans_numero")],
)
async def test_declared_sales_unknown_key_and_street_are_404(
    api, migrated, ban_id, code
):
    scope(migrated)
    assert (await declarees(api, ban_id, 404))["code"] == code


async def test_a_sql_outage_of_the_declared_sales_is_a_typed_503(api, migrated):
    scope(migrated)
    with injected(
        migrated,
        "ALTER TABLE mutations_grain RENAME TO mutations_grain_panne",
        restore=("ALTER TABLE mutations_grain_panne RENAME TO mutations_grain",),
    ):
        problem = await declarees(api, DA, 503)

    assert problem["code"] == "ventes_indisponibles"


async def test_refused_dvf_publication_breaks_the_declared_sales(mounted, migrated):
    scope(migrated)
    publish(migrated, "failed")
    try:
        liste = await mounted.get(f"/v1/adresse/{DA}/ventes-declarees")
    finally:
        publish(migrated, "published")

    assert liste.status_code == 503, liste.text
    assert liste.json()["code"] == "ventes_indisponibles"
    assert "456789" not in liste.text


async def test_the_declared_sales_share_the_dvf_generation_token(api, migrated):
    """The page relays one ``vg`` to every sales list (FRONT IN-05)."""
    scope(migrated)

    parcelle = await ventes(api, A.ban_id)
    adresse = await declarees(api, DA)

    assert parcelle["generation"] == adresse["generation"]
    page = await declarees(api, DA, vg=adresse["generation"])
    assert page["total"] == 3


# --- Plan 04.1-14 (C44): rule « numéro et suffixe », served on request ------------

SUFFIXE = {"regle": "numero_et_suffixe"}


def _dates(reponse: dict) -> list[str]:
    return [carte["date"] for carte in reponse["ventes"]]


async def test_the_sale_of_2_b_is_on_2_bis_and_no_longer_on_2(api, migrated):
    """Tracer (plan 04.1-14): « 2 » keeps the sales declared without suffix,
    « 2 bis » gets those declared « 2 B » (leading zero included), « 2 c »
    those declared « 2 C »; the order and the price rules are unchanged."""
    scope(migrated)

    nu = await declarees(api, DS2, **SUFFIXE)
    bis = await declarees(api, DS2_BIS, **SUFFIXE)
    lettre = await declarees(api, DS2_C, **SUFFIXE)

    assert (nu["etat"], nu["motif"], nu["total"]) == ("value", None, 1)
    assert _dates(nu) == ["2025-03-01"]
    assert (bis["etat"], bis["motif"], bis["total"]) == ("value", None, 2)
    assert _dates(bis) == ["2024-05-01", "2022-06-01"]
    assert (lettre["total"], _dates(lettre)) == (1, ["2023-07-01"])
    for reponse in (nu, bis, lettre):
        assert reponse["rattachement"] == "adresse_declaree_vente"
        assert reponse["periode"] == PERIODE and reponse["pages"] == 1
    # The recent isolated sale keeps its price withheld under either rule.
    assert (nu["ventes"][0]["prix"], nu["ventes"][0]["prix_affiche"]) == (None, False)
    assert bis["ventes"][1]["prix"] == 303_000
    # The exchange and the « 2 Z » sale (no BAN « 2 z ») are on no page.
    tous = {*_dates(nu), *_dates(bis), *_dates(lettre)}
    assert not tous & {"2022-02-01", "2021-08-01"}


async def test_the_default_rule_is_the_number_only_rule_unchanged(api, migrated):
    """The current pages send no rule: C37 (« Au 2 seulement ») is served as
    before and never reads the suffix column."""
    scope(migrated)
    api.statements.clear()

    defaut = await declarees(api, DS2)
    bis = await declarees(api, DS2_BIS)

    assert not [sql for sql in api.statements if "adresse_suffixe" in sql]
    assert (defaut["total"], _dates(defaut)) == (
        5,
        ["2025-03-01", "2024-05-01", "2023-07-01", "2022-06-01", "2021-08-01"],
    )
    assert (bis["etat"], bis["motif"]) == ("not_available", "suffixe_non_distingue")
    explicite = await api.client.get(
        f"/v1/adresse/{DS2}/ventes-declarees", params={"regle": "numero"}
    )
    implicite = await api.client.get(f"/v1/adresse/{DS2}/ventes-declarees")
    assert explicite.json()["donnees"] == implicite.json()["donnees"]


@pytest.mark.parametrize(
    "ban_id", [DS4_QUINQUIES, DS6_BIS, DS6_B, DL6_BIS, DL6_B, DL10_BIS, DL10_B]
)
async def test_a_suffix_without_single_correspondence_is_a_typed_state(
    api, migrated, ban_id
):
    """« quinquies » has no measured DVF code; « 6 Bis » and « 6 b » both read
    « 6 B »: a typed state, never the sales of the bare number, no DVF read.
    So do « 06 bis » and « 6 b », and « 10 bis Avenue des Lilas » and
    « 10 b AV DES LILAS »: the competing addresses are searched with the
    normalisation of the attachment (Codex review I1), never as written."""
    scope(migrated)
    api.statements.clear()

    reponse = await declarees(api, ban_id, **SUFFIXE)

    assert (reponse["etat"], reponse["motif"]) == (
        "not_available",
        "suffixe_sans_correspondance",
    )
    assert reponse["total"] is None and reponse["ventes"] == []
    assert api.reads("mutations_grain") == []


async def test_an_equivalent_number_and_street_with_another_suffix_is_kept(
    api, migrated
):
    """« 12 bis Avenue des Lilas » next to « 012 ter AV DES LILAS »: same number
    and street once normalised, another DVF code; « 12 bis » gets its « 12 B »
    sale, « 012 ter » none (the check does not flag every equivalent address)."""
    scope(migrated)

    bis = await declarees(api, DL12_BIS, **SUFFIXE)
    ter = await declarees(api, DL12_TER, **SUFFIXE)

    assert (bis["etat"], bis["motif"], bis["total"]) == ("value", None, 1)
    assert _dates(bis) == ["2023-06-01"]
    assert (ter["etat"], ter["motif"], ter["total"]) == ("value", None, 0)


async def test_no_sale_at_a_bare_number_is_a_value_0_under_the_suffix_rule(
    api, migrated
):
    scope(migrated)

    reponse = await declarees(api, DS8, **SUFFIXE)

    assert (reponse["etat"], reponse["total"], reponse["ventes"]) == ("value", 0, [])
    assert reponse["periode"] == PERIODE


async def test_a_generation_imported_without_the_suffix_is_a_typed_absence(
    api, migrated
):
    """Migrated but not yet reimported (column without its marker): the
    suffix rule serves a typed absence, never every sale as « no suffix »;
    the default rule is unaffected."""
    scope(migrated)
    marquer_suffixe(migrated, None)
    try:
        nu = await declarees(api, DS2, **SUFFIXE)
        bis = await declarees(api, DS2_BIS, **SUFFIXE)
        defaut = await declarees(api, DS2)
    finally:
        marquer_suffixe(migrated, MARQUEUR_ADRESSE_SUFFIXE)

    for reponse in (nu, bis):
        assert (reponse["etat"], reponse["motif"]) == (
            "not_available",
            "suffixe_non_importe",
        )
        assert reponse["total"] is None and reponse["ventes"] == []
    assert defaut["total"] == 5


async def test_the_suffix_rule_outages_and_closed_values(api, migrated):
    scope(migrated)
    refus = await api.client.get(
        f"/v1/adresse/{DS2}/ventes-declarees", params={"regle": "numero_seul"}
    )
    assert refus.status_code == 422
    with injected(
        migrated,
        "ALTER TABLE mutations_grain RENAME TO mutations_grain_panne",
        restore=("ALTER TABLE mutations_grain_panne RENAME TO mutations_grain",),
    ):
        problem = await declarees(api, DS2_BIS, 503, **SUFFIXE)
    assert problem["code"] == "ventes_indisponibles"

    page = await declarees(api, DS2_BIS, **SUFFIXE)
    relue = await declarees(api, DS2_BIS, vg=page["generation"], **SUFFIXE)
    assert relue["total"] == 2


# --- Plan 04.1-15 (D-128): VEFA listed and marked, out of every statistic ----------

VEFA = {"vefa": "incluses"}


async def test_a_vefa_declared_at_the_address_is_listed_marked_on_request(
    api, migrated
):
    """Tracer (plan 04.1-15): under ``vefa=incluses`` the VEFA declared at « 2 »
    is listed in date order, marked ``hors_stats_vefa``, its price rule
    unchanged; ``nombre_vefa`` says how many of ``total`` are VEFA, and the
    count of the sales (the comparator row) never takes them (D-12, C56)."""
    commune_declaree(migrated)
    scope(migrated, SANS_GEORISQUES)

    reponse = await declarees(api, DA, **VEFA)
    seule = await declarees(api, DA_ZERO, **VEFA)

    assert (reponse["etat"], reponse["total"], reponse["nombre_vefa"]) == (
        "value",
        4,
        1,
    )
    assert _dates(reponse) == ["2025-01-15", "2022-10-01", "2020-09-01", "2019-05-01"]
    marques = [carte["hors_stats_vefa"] for carte in reponse["ventes"]]
    assert marques == [False, True, False, False]
    vefa = reponse["ventes"][1]
    assert vefa["nature"] == NATURE_VEFA
    # An old VEFA keeps its price; the recent isolated sale stays withheld.
    assert (vefa["prix"], vefa["prix_affiche"]) == (PRIX_DA_VEFA, True)
    assert reponse["ventes"][0]["prix"] is None
    # An address whose only declared mutation is a VEFA: listed on request.
    assert (seule["etat"], seule["total"], seule["nombre_vefa"]) == ("value", 1, 1)
    assert [carte["hors_stats_vefa"] for carte in seule["ventes"]] == [True]

    # Out of every statistic: the comparator counts the sales only.
    cellules = (await comparer(api.client, DA, DA_ZERO))["ventes_declarees"]["cellules"]
    assert (cellules[0]["etat"], cellules[0]["valeur"]) == ("value", 3)
    assert reponse["total"] - reponse["nombre_vefa"] == cellules[0]["valeur"]
    assert (cellules[1]["etat"], cellules[1]["motif"]) == (
        "not_available",
        "aucune_vente_rattachee",
    )


async def test_the_default_still_lists_the_sales_only(api, migrated):
    """The current pages send no ``vefa``: the sales only, as before 04.1-15;
    no VEFA count is served for a list that did not search them."""
    scope(migrated)

    defaut = await declarees(api, DA)
    zero = await declarees(api, DA_ZERO)

    assert (defaut["total"], _dates(defaut)) == (
        3,
        ["2025-01-15", "2020-09-01", "2019-05-01"],
    )
    assert defaut["nombre_vefa"] is None
    assert [carte["hors_stats_vefa"] for carte in defaut["ventes"]] == [False] * 3
    assert (zero["total"], zero["ventes"], zero["nombre_vefa"]) == (0, [], None)
    explicite = await api.client.get(
        f"/v1/adresse/{DA}/ventes-declarees", params={"vefa": "exclues"}
    )
    implicite = await api.client.get(f"/v1/adresse/{DA}/ventes-declarees")
    assert explicite.json()["donnees"] == implicite.json()["donnees"]


async def test_the_vefa_rule_composes_with_the_suffix_rule(api, migrated):
    """« 2 B » VEFA: on « 2 bis » under both rules, never on « 2 »."""
    scope(migrated)

    bis = await declarees(api, DS2_BIS, **SUFFIXE, **VEFA)
    nu = await declarees(api, DS2, **SUFFIXE, **VEFA)
    numero = await declarees(api, DS2, **VEFA)

    assert (bis["total"], bis["nombre_vefa"]) == (3, 1)
    assert _dates(bis) == ["2024-05-01", "2023-02-01", "2022-06-01"]
    assert [c["hors_stats_vefa"] for c in bis["ventes"]] == [False, True, False]
    assert (nu["total"], nu["nombre_vefa"], _dates(nu)) == (1, 0, ["2025-03-01"])
    # Number-only rule: the « 2 B » VEFA is on « 2 », as its sales (C37).
    assert (numero["total"], numero["nombre_vefa"]) == (6, 1)


async def test_the_vefa_parameter_is_a_closed_value(api, migrated):
    scope(migrated)
    refus = await api.client.get(
        f"/v1/adresse/{DA}/ventes-declarees", params={"vefa": "toutes"}
    )
    assert refus.status_code == 422
    with injected(
        migrated,
        "ALTER TABLE mutations_grain RENAME TO mutations_grain_panne",
        restore=("ALTER TABLE mutations_grain_panne RENAME TO mutations_grain",),
    ):
        problem = await declarees(api, DA, 503, **VEFA)
    assert problem["code"] == "ventes_indisponibles"


# --- C40: the declared sales in the address comparator ----------------------------

SANS_GEORISQUES = tuple(d for d in TOUT if d != "georisques")


def commune_declaree(dsn: str) -> None:
    """The comparator names its columns by their commune (the route does not)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO geo_communes (code, nom, departement_code, geometry) "
            "VALUES ('85193', 'Commune declaree', '85', ST_GeomFromText(%s, 4326)) "
            "ON CONFLICT DO NOTHING",
            (_square(-1.4, 46.7, 0.05),),
        )


async def comparer(client, *ids: str) -> dict:
    response = await client.get(
        "/v1/adresses/comparaison", params={"ids": ",".join(ids)}
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    groupes = response.json()["donnees"]["groupes"]
    ventes = next(groupe for groupe in groupes if groupe["cle"] == "ventes")
    return {ligne["cle"]: ligne for ligne in ventes["lignes"]}


async def test_the_comparator_counts_the_declared_sales_as_the_route(api, migrated):
    """C40: one row after « Dernière vente », the count of the route under the
    rule of the page (``regle=numero_et_suffixe`` since 04.5-08, R8) with its
    period and source; no sale attached (C44) stays a state, never a 0."""
    commune_declaree(migrated)
    scope(migrated, SANS_GEORISQUES)

    lignes = await comparer(api.client, DA, DA_ZERO, DA_BIS)

    assert list(lignes)[-2:] == ["derniere_vente", "ventes_declarees"]
    ligne = lignes["ventes_declarees"]
    assert ligne["field_id"] == "adresse.dvf.ventes_adresse.nombre"
    trois, zero, bis = ligne["cellules"]
    for cellule, ban_id in zip(ligne["cellules"], (DA, DA_ZERO, DA_BIS), strict=True):
        route = await declarees(api, ban_id, **SUFFIXE)
        if route["total"] == 0:
            # C44 (CR-02): the list serves its 0 found; the row says no sale
            # attached, never the figure 0 as a fact.
            attendu = ("not_available", "aucune_vente_rattachee", None)
        else:
            attendu = (route["etat"], route["motif"], route["total"])
        assert (cellule["etat"], cellule["motif"], cellule["valeur"]) == attendu
        assert cellule["periode"] == route["periode"]
        assert cellule["rattachement"] == "adresse_declaree_vente"
        assert cellule["source"]["cle"] == "dvf"
    assert (trois["valeur"], trois["periode"]) == (3, PERIODE)
    assert (zero["etat"], zero["motif"]) == ("not_available", "aucune_vente_rattachee")
    assert (zero["periode"], zero["ecart"]) == (PERIODE, None)
    assert zero["motif_non_calcul"] == "donnee_absente"
    assert zero["identique_a_la_reference"] is not True
    # C3 (04.5-08): « 2 bis » is searched by its suffix, no longer
    # « suffixe_non_distingue »; no sale is declared at « 2 B » of this street.
    assert (bis["etat"], bis["motif"], bis["valeur"]) == (
        "not_available",
        "aucune_vente_rattachee",
        None,
    )
    assert (bis["periode"], bis["motif_non_calcul"]) == (PERIODE, "donnee_absente")
    # The parcel relation keeps its own state: never merged (C37).
    parcelle = lignes["ventes_parcelle"]["cellules"][0]
    assert (parcelle["etat"], parcelle["motif"]) == (
        "not_available",
        "point_hors_parcelle",
    )


def commune_des_suffixes(dsn: str) -> None:
    """The commune of the suffix addresses, named by the comparator."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO geo_communes (code, nom, departement_code, geometry) "
            "VALUES ('85195', 'Commune suffixes', '85', ST_GeomFromText(%s, 4326)) "
            "ON CONFLICT DO NOTHING",
            (_square(-1.5, 46.8, 0.05),),
        )


async def test_the_comparator_follows_the_number_and_suffix_rule(api, migrated):
    """04.5-08 (C3, R8 « comparateur aligné »): the line C40 counts the sales
    declared at the number and the suffix, as the page reads them, on the
    mutations of the route tests: « 2 » its sale without suffix (never the
    « 2 B », « 2 C » or « 2 Z » ones), « 2 bis » its two « 2 B » sales (never
    its « 2 B » VEFA, D-12), « 2 c » its « 2 C » sale, « quinquies » the typed
    state. The figures are written here, not read from the route: a count
    without the suffix filter (5 at « 2 ») fails."""
    commune_des_suffixes(migrated)
    scope(migrated, SANS_GEORISQUES)

    lignes = await comparer(api.client, DS2, DS2_BIS, DS2_C, DS4_QUINQUIES)

    nu, bis, lettre, quinquies = lignes["ventes_declarees"]["cellules"]
    assert [(c["etat"], c["motif"], c["valeur"]) for c in (nu, bis, lettre)] == [
        ("value", None, 1),
        ("value", None, 2),
        ("value", None, 1),
    ]
    for cellule in (nu, bis, lettre):
        assert cellule["periode"] == PERIODE
        assert cellule["rattachement"] == "adresse_declaree_vente"
    assert (quinquies["etat"], quinquies["motif"], quinquies["valeur"]) == (
        "not_available",
        "suffixe_sans_correspondance",
        None,
    )
    assert quinquies["motif_non_calcul"] == "donnee_absente"
    # The list the page reads (suffix rule, VEFA listed): the same sales, its
    # VEFA marked and never counted by the line.
    page_bis = await declarees(api, DS2_BIS, **SUFFIXE, vefa="incluses")
    assert (page_bis["total"], page_bis["nombre_vefa"]) == (3, 1)
    for cellule, ban_id in zip((nu, bis, lettre), (DS2, DS2_BIS, DS2_C), strict=True):
        page = await declarees(api, ban_id, **SUFFIXE, vefa="incluses")
        assert cellule["valeur"] == page["total"] - page["nombre_vefa"]
    # The number-only rule (C37, the API default) would count 5 at « 2 ».
    assert (await declarees(api, DS2))["total"] == 5


async def test_the_comparator_suffix_rule_ambiguity_zero_marker_and_outage(
    api, migrated
):
    """04.5-08 (C3): under the suffix rule the line keeps the states of the
    route: an ambiguous suffix (« 6 Bis » and « 6 b ») is typed, a bare number
    without sale is « no sale attached » with its period, a generation imported
    without the suffix is ``suffixe_non_importe`` (never every sale of the
    number), and a refused read is an outage, never a 0."""
    commune_des_suffixes(migrated)
    scope(migrated, SANS_GEORISQUES)

    ambigu_bis, ambigu_b, zero = (await comparer(api.client, DS6_BIS, DS6_B, DS8))[
        "ventes_declarees"
    ]["cellules"]
    for cellule in (ambigu_bis, ambigu_b):
        assert (cellule["etat"], cellule["motif"], cellule["valeur"]) == (
            "not_available",
            "suffixe_sans_correspondance",
            None,
        )
    # Codex review I1: « 06 bis » / « 6 b » and « Avenue des Lilas » /
    # « AV DES LILAS » read the same DVF sale: typed on both, never counted
    # twice; « 12 bis » next to « 012 ter » keeps its one sale.
    equivalentes = (await comparer(api.client, DL6_BIS, DL6_B, DL10_BIS, DL10_B))[
        "ventes_declarees"
    ]["cellules"]
    assert [(c["etat"], c["motif"], c["valeur"]) for c in equivalentes] == [
        ("not_available", "suffixe_sans_correspondance", None)
    ] * 4
    temoin = (await comparer(api.client, DL12_BIS, DL12_TER))["ventes_declarees"][
        "cellules"
    ]
    assert [(c["etat"], c["motif"], c["valeur"]) for c in temoin] == [
        ("value", None, 1),
        ("not_available", "aucune_vente_rattachee", None),
    ]
    assert (zero["etat"], zero["motif"], zero["valeur"], zero["periode"]) == (
        "not_available",
        "aucune_vente_rattachee",
        None,
        PERIODE,
    )

    marquer_suffixe(migrated, None)
    try:
        sans_marqueur = (await comparer(api.client, DS2, DS2_BIS))["ventes_declarees"][
            "cellules"
        ]
    finally:
        marquer_suffixe(migrated, MARQUEUR_ADRESSE_SUFFIXE)
    assert [(c["etat"], c["motif"], c["valeur"]) for c in sans_marqueur] == [
        ("not_available", "suffixe_non_importe", None)
    ] * 2

    with injected(
        migrated,
        "ALTER TABLE mutations_grain RENAME TO mutations_grain_panne",
        restore=("ALTER TABLE mutations_grain_panne RENAME TO mutations_grain",),
    ):
        en_panne = (await comparer(api.client, DS2, DS2_BIS))["ventes_declarees"][
            "cellules"
        ]
    assert [(c["etat"], c["valeur"]) for c in en_panne] == [
        ("temporary_error", None)
    ] * 2


async def test_the_comparator_declared_sales_not_hosted_and_in_outage(api, migrated):
    commune_declaree(migrated)
    scope(migrated, tuple(d for d in SANS_GEORISQUES if d != "dvf"))
    cellules = (await comparer(api.client, DA, DA_ZERO))["ventes_declarees"]["cellules"]
    assert {(c["etat"], c["motif"]) for c in cellules} == {
        ("out_of_coverage", "dataset_non_heberge")
    }

    scope(migrated, SANS_GEORISQUES)
    with injected(
        migrated,
        "ALTER TABLE mutations_grain RENAME TO mutations_grain_panne",
        restore=("ALTER TABLE mutations_grain_panne RENAME TO mutations_grain",),
    ):
        cellules = (await comparer(api.client, DA, DA_ZERO))["ventes_declarees"][
            "cellules"
        ]
    # A failed read is an outage of the row, never a zero.
    assert [c["etat"] for c in cellules] == ["temporary_error"] * 2
    assert [c["valeur"] for c in cellules] == [None, None]


async def test_refused_dvf_publication_breaks_the_declared_row_only(mounted, migrated):
    commune_declaree(migrated)
    scope(migrated, SANS_GEORISQUES)
    publish(migrated, "failed")
    try:
        lignes = await comparer(mounted, DA, DA_ZERO)
    finally:
        publish(migrated, "published")

    cellules = lignes["ventes_declarees"]["cellules"]
    assert [c["etat"] for c in cellules] == ["temporary_error"] * 2
    assert "456789" not in str(lignes)
