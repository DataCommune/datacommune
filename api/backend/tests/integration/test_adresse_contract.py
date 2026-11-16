"""LIEU-01 / D-105..D-107 / SEM-01: address search and identity on PostGIS.

``GET /v1/adresses/recherche`` reads the instance BAN only and serves typed
results without any coordinate; ``GET /v1/adresse/{ban_id}`` serves the
identity of one BAN key, its coverage and the parcel that contains the BAN
point (never the nearest one).

The scope is written by the production path (``replace_scope``) and read by
the real scope dependency of the API: only the session is overridden.
Every statement is captured so a test can prove a table was not read.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_adresse_contract.py
"""

import json
import traceback
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
from psycopg import sql as pgsql
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adresse.router import adresse_router
from app.adresse.schemas import ProblemeAdresse
from app.ban.queries import geocoder
from app.common.exceptions import UpstreamUnavailableError, register_exception_handlers
from app.commune import queries
from app.commune.dependencies import COMMUNE_INTERMEDIATES
from app.config import settings
from app.database import get_db
from tests.integration.instance_scope_fixture import install_scope_at

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]

# La Roche-sur-Yon (85): the tracer address sits inside one square parcel.
LILAS = (-1.4300, 46.6700)  # lon, lat
BAN_LILAS = "85191_0420_00012"
PARCELLE_LILAS = "85191000AB0012"

# (code, nom)
DEPARTEMENTS = (
    ("44", "Loire-Atlantique"),
    ("85", "Vendée"),
    ("35", "Ille-et-Vilaine"),
    ("79", "Deux-Sèvres"),
    ("49", "Maine-et-Loire"),
)
# (code, nom, departement, lon, lat)
COMMUNES = (
    ("85191", "La Roche-sur-Yon", "85", -1.43, 46.67),
    ("44109", "Nantes", "44", -1.55, 47.22),
    ("35238", "Rennes", "35", -1.68, 48.11),
    # Homonyms: one hosted (44), one outside the perimeter (79).
    ("44126", "Le Pin", "44", -1.15, 47.59),
    ("79211", "Le Pin", "79", -0.63, 46.86),
    # Homonyms, both outside the perimeter.
    ("35997", "Saint-Testin", "35", -1.70, 48.20),
    ("79997", "Saint-Testin", "79", -0.60, 46.80),
)
FAR = (-1.0, 46.0)  # a point no parcel contains

# Adversarial ranking (R02-05/2): the best global scores sit outside the
# requested or hosted departements (short commune names score higher in
# word_similarity), the admissible rows rank beyond the global LIMIT.
ACACIAS_44 = tuple(f"440{i:02d}" for i in range(1, 11))
TILLEULS_49 = tuple(f"490{i:02d}" for i in range(1, 11))
LONG_NAME = "La Commune Vendeenne Au Nom Particulierement Long"
# (ban_id, numero, nom_voie, code_postal, code_commune, nom_commune)
SEARCH_ROWS = (
    *(
        (f"{c}_0001_00008", "8", "Rue des Acacias", "44100", c, "Ax")
        for c in ACACIAS_44
    ),
    *(
        (f"{c}_0002_00006", "6", "Rue des Tilleuls", "49100", c, "By")
        for c in TILLEULS_49
    ),
    ("85001_0001_00008", "8", "Rue des Acacias", "85000", "85001", LONG_NAME),
    ("85002_0001_00008", "8", "Rue des Acacias", "85000", "85002", LONG_NAME),
    ("85001_0002_00006", "6", "Rue des Tilleuls", "85000", "85001", LONG_NAME),
    ("85002_0002_00006", "6", "Rue des Tilleuls", "85000", "85002", LONG_NAME),
    ("85191_0003_00001", "1", "Rue de la Paix", "85000", "85191", "La Roche-sur-Yon"),
    ("85191_0003_00003", "3", "Rue de la Paix", "85000", "85191", "La Roche-sur-Yon"),
    ("44109_0003_00002", "2", "Rue de la Paix", "44000", "44109", "Nantes"),
    ("44109_0004_00012", "12", "Rue de Rennes", "44000", "44109", "Nantes"),
    ("44126_0001_00003", "3", "Rue des Lilas", "44540", "44126", "Le Pin"),
    ("85003_0005_00004", "4", "Rue des Peupliers", "85000", "85003", "Cz"),
)

# Identity (task 3). Keys of every profile of block E, one row each.
PROFILE_KEYS = (
    "85191_ab12cd_00007",
    "85191_0700_00007_bis",
    "85191_0700_00007_a-1",
    "85191_0701_00007_123456789",
)
BAN_DOUBLON_COHERENT = "85191_0600_00002"
BAN_DOUBLON_CONTRADICTOIRE = "85191_0600_00004"
# A BAN point on the roadway, 0.5 m south of the nearest parcel.
CHAUSSEE = (-1.4400, 46.6800)
BAN_CHAUSSEE = "85191_0610_00001"
PARCELLE_VOISINE = "85191000AC0001"
# A BAN point exactly on the edge shared by two parcels.
FRONTIERE = (-1.4500, 46.6900)
BAN_FRONTIERE = "85191_0620_00001"
BAN_SANS_POSITION = "85191_0630_00001"
# (ban_id, numero, nom_voie, point)
IDENTITY_ROWS = (
    *((key, "7", "Rue des Profils", FAR) for key in PROFILE_KEYS),
    (BAN_DOUBLON_COHERENT, "2", "Rue du Double", FAR),
    (BAN_DOUBLON_COHERENT, "2", "Rue du Double", FAR),
    (BAN_DOUBLON_CONTRADICTOIRE, "4", "Rue du Double", FAR),
    (BAN_DOUBLON_CONTRADICTOIRE, "4", "Rue du Double", (-1.1, 46.1)),
    (BAN_CHAUSSEE, "1", "Rue de la Chaussee", CHAUSSEE),
    (BAN_FRONTIERE, "1", "Rue de la Frontiere", FRONTIERE),
    (BAN_SANS_POSITION, "1", "Rue sans Position", (None, None)),
)


# Declared DPE documents of the tracer address (04-03): one of the former
# method (before July 2021), two of the current one. (numero, established,
# energy class, GES class)
DPE_LILAS = (
    ("2185E0000001A", date(2020, 5, 4), "E", "E"),
    ("2285E0000002B", date(2022, 6, 1), "D", "C"),
    ("2485E0000003C", date(2024, 3, 1), "C", "B"),
)
# Task 2. Twenty-three documents: the most recent one has the highest
# number, so a number order would have put it on the last page.
BAN_VINGT_TROIS = "85191_0800_00023"
DPE_VINGT_TROIS = (
    *((f"2285E{index:07d}A", date(2022, 1, index), "D", "D") for index in range(1, 23)),
    ("2585E9999999Z", date(2025, 1, 15), "B", "A"),
)
# Twelve documents at the same latest date: the tie spans two pages.
BAN_EGALITE = "85191_0810_00012"
DPE_EGALITE = tuple(
    (f"2485E{index:07d}T", date(2024, 5, 1), "CDEFGAB"[index % 7], "C")
    for index in range(1, 13)
)
# Two documents at one key, one of them retired by the source (tombstone).
BAN_RETIRE = "85191_0820_00002"
DPE_PRESENT = ("2385E0000001P", date(2023, 9, 1), "E", "D")
DPE_RETIRE = "2285E0000001R"
# 04-19 Task 1. Declared addresses of « 12 Rue des Glycines » (P-ADR-01):
# (numero, established, raw address, full raw address, complement, expected
# declared address as served or None for a strictly concordant document).
GLYCINES = (-1.4000, 46.6400)
BAN_GLYCINES = "85191_0900_00012"
SENTINELLE = "SENTINELLE"
DPE_GLYCINES = (
    (
        "2485G0000001P",
        date(2024, 7, 1),
        "14 rue des Glycines",
        None,
        None,
        {"etat": "publiee", "texte": "14 rue des Glycines", "motif": None},
    ),
    (
        "2485G0000002B",
        date(2024, 6, 1),
        "12 bis rue des Glycines",
        None,
        None,
        {"etat": "publiee", "texte": "12 bis rue des Glycines", "motif": None},
    ),
    (
        "2485G0000003S",
        date(2024, 5, 1),
        f"12 rue des Glycines {SENTINELLE}-BRUT",
        None,
        None,
        {"etat": "masquee", "texte": None, "motif": "non_verifiable_sans_complement"},
    ),
    (
        "2485G0000004C",
        date(2024, 4, 1),
        "14 rue des Glycines",
        f"14 rue des Glycines {SENTINELLE}-COMPLETE 85000 La Roche-sur-Yon",
        f"porte {SENTINELLE}-COMPLEMENT",
        {"etat": "masquee", "texte": None, "motif": "complement_present"},
    ),
    ("2485G0000005K", date(2024, 3, 1), "12 rue des Glycines", None, None, None),
    (
        "2485G0000006R",
        date(2024, 2, 1),
        "12-14 rue des Glycines",
        None,
        None,
        {"etat": "masquee", "texte": None, "motif": "non_verifiable_sans_complement"},
    ),
    (
        "2485G0000007V",
        date(2024, 1, 1),
        "12 rue du Port",
        None,
        None,
        {"etat": "masquee", "texte": None, "motif": "non_verifiable_sans_complement"},
    ),
)
# Task 3. A unique parcel without any sale.
SANS_VENTE = (-1.4600, 46.7000)
BAN_SANS_VENTE = "85191_0430_00005"
PARCELLE_SANS_VENTE = "85191000AE0005"
PARCELLE_VOISINE_LILAS = "85191000AB0013"
# (id_mutation, date, class, value, built surface, parcels): three sales of
# the tracer parcel, one of them on two parcels, and the other sales of the
# commune feeding its medians (five houses in the last twelve months, two
# apartments: under the publication threshold).
MUTATIONS = (
    (
        "2021-M-LILAS-1",
        date(2021, 3, 1),
        "residentiel_maison",
        200_000,
        100,
        (PARCELLE_LILAS,),
    ),
    (
        "2025-M-LILAS-2",
        date(2025, 3, 10),
        "residentiel_maison",
        300_000,
        120,
        (PARCELLE_LILAS, PARCELLE_VOISINE_LILAS),
    ),
    (
        "2025-M-LILAS-3",
        date(2025, 11, 20),
        "residentiel_maison",
        250_000,
        100,
        (PARCELLE_LILAS,),
    ),
    (
        "2025-M-COM-1",
        date(2025, 1, 15),
        "residentiel_maison",
        200_000,
        100,
        ("85191000AZ0001",),
    ),
    (
        "2025-M-COM-2",
        date(2025, 5, 15),
        "residentiel_maison",
        300_000,
        100,
        ("85191000AZ0002",),
    ),
    (
        "2025-M-COM-3",
        date(2025, 9, 15),
        "residentiel_maison",
        280_000,
        100,
        ("85191000AZ0003",),
    ),
    (
        "2025-A-COM-1",
        date(2025, 6, 1),
        "residentiel_appartement",
        150_000,
        50,
        ("85191000AZ0004",),
    ),
    (
        "2025-A-COM-2",
        date(2025, 7, 1),
        "residentiel_appartement",
        160_000,
        50,
        ("85191000AZ0005",),
    ),
)


def _square(lon: float, lat: float, half: float) -> str:
    """WKT of an axis-aligned square multipolygon centred on (lon, lat)."""
    x0, x1, y0, y1 = lon - half, lon + half, lat - half, lat + half
    ring = f"{x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0}"
    return f"MULTIPOLYGON((({ring})))"


def _rect(x0: float, y0: float, x1: float, y1: float) -> str:
    """WKT of an axis-aligned rectangle multipolygon."""
    ring = f"{x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0}"
    return f"MULTIPOLYGON((({ring})))"


def _insert_adresse(conn, id_, ban_id, numero, rep, voie, cp, commune, nom, point):
    lon, lat = point
    conn.execute(
        "INSERT INTO adresses (id, code_departement, ban_id, numero, rep, "
        "nom_voie, code_postal, code_commune, nom_commune, position) VALUES "
        "(%s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
        (id_, commune[:2], ban_id, numero, rep, voie, cp, commune, nom, lon, lat),
    )


def _insert_parcelle(conn, id_, id_parcelle, wkt):
    conn.execute(
        "INSERT INTO parcelles (id, code_departement, id_parcelle, commune, "
        "geometrie) VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326))",
        (id_, id_parcelle[:2], id_parcelle, id_parcelle[:5], wkt),
    )


_NO_SUCCESSOR = (
    '{"replacement_state": "no_successor_known", "graph_issues": [], "date_issues": []}'
)


def _insert_dpe(
    conn,
    id_,
    numero,
    ban_id,
    etabli,
    classe,
    ges,
    *,
    fin=None,
    facts=_NO_SUCCESSOR,
    type_batiment="maison",
    adresse_brut=None,
    adresse_complete_brut=None,
    complement=None,
):
    """One present document of the exact source key (source state unknown)."""
    if fin is None and etabli is not None:
        fin = date(etabli.year + 10, etabli.month, etabli.day)
    conn.execute(
        "INSERT INTO diagnostics_dpe (id, numero_dpe, identifiant_ban, "
        "code_departement, code_insee, type_batiment, surface_habitable, "
        "etiquette_dpe, etiquette_ges, date_etablissement_dpe, "
        "date_fin_validite_dpe, lifecycle_facts, adresse_brut, "
        "adresse_complete_brut, complement_adresse_logement) VALUES "
        "(%s, %s, %s, %s, %s, %s, 82.5, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)",
        (
            id_,
            numero,
            ban_id,
            ban_id[:2],
            ban_id[:5],
            type_batiment,
            classe,
            ges,
            etabli,
            fin,
            facts,
            adresse_brut,
            adresse_complete_brut,
            complement,
        ),
    )


_RETIRED_OBSERVATIONS = (
    '[{"source_slug": "dpe03existant", "department": "85", '
    '"source_version": "' + "a" * 64 + '", '
    '"observed_at": "2026-09-25T20:30:00Z", '
    '"last_seen_at": "2026-09-20T20:30:00Z", '
    '"state": "absent_from_complete_scope", "import_id": "import-synthetique"}]'
)


def _insert_retired_dpe(conn, id_, numero, ban_id):
    """A tombstone: retired by a complete source scope, description purged."""
    conn.execute(
        "INSERT INTO diagnostics_dpe (id, numero_dpe, identifiant_ban, "
        "code_departement, source_state, source_snapshot_at, "
        "source_observations, is_tombstone, lifecycle_facts) VALUES "
        "(%s, %s, %s, %s, 'absent_from_complete_scope', "
        "'2026-09-25T20:30:00Z', %s::jsonb, true, %s::jsonb)",
        (id_, numero, ban_id, ban_id[:2], _RETIRED_OBSERVATIONS, _NO_SUCCESSOR),
    )


def _insert_mutation(conn, id_, id_mutation, jour, classe, valeur, surface, parcelles):
    # Geocoded (phase 04.4, D-03): a NULL position means no geocoded line, a
    # mutation no parcel reader serves. Its place is not read here.
    conn.execute(
        "INSERT INTO mutations_grain (id, code_departement, id_mutation, "
        "date_mutation, nature_mutation, valeur_fonciere, classe_mutation, "
        "nb_locaux_maison, nb_locaux_appartement, nb_lignes, nb_dispositions, "
        "nb_communes, nb_parcelles, surface_bati_totale, mono_local, est_vente, "
        "code_commune, position) VALUES (%s, '85', %s, %s, 'Vente', %s, %s, %s, "
        "%s, 1, 1, 1, %s, %s, true, true, '85191', "
        "ST_SetSRID(ST_MakePoint(-1.2, 46.4), 4326))",
        (
            id_,
            id_mutation,
            jour,
            valeur,
            classe,
            int(classe == "residentiel_maison"),
            int(classe == "residentiel_appartement"),
            len(parcelles),
            surface,
        ),
    )
    for id_parcelle in parcelles:
        conn.execute(
            "INSERT INTO mutation_parcelles (id_mutation, id_parcelle, "
            "code_departement) VALUES (%s, %s, '85')",
            (id_mutation, id_parcelle),
        )


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):  # noqa: C901
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    with psycopg.connect(isolated_pg_dsn) as conn:
        for code, nom in DEPARTEMENTS:
            conn.execute(
                "INSERT INTO geo_departements (code, nom, geometry) VALUES "
                "(%s, %s, ST_GeomFromText(%s, 4326))",
                (code, nom, _square(0, 0, 0.1)),
            )
        for code, nom, dept, lon, lat in COMMUNES:
            conn.execute(
                "INSERT INTO geo_communes (code, nom, departement_code, "
                "geometry) VALUES (%s, %s, %s, ST_GeomFromText(%s, 4326))",
                (code, nom, dept, _square(lon, lat, 0.05)),
            )
        _insert_adresse(
            conn,
            95_000_001,
            BAN_LILAS,
            "12",
            None,
            "Rue des Lilas",
            "85000",
            "85191",
            "La Roche-sur-Yon",
            LILAS,
        )
        _insert_parcelle(conn, 96_000_001, PARCELLE_LILAS, _square(*LILAS, 0.0002))
        for index, (ban_id, numero, voie, cp, code, nom) in enumerate(SEARCH_ROWS):
            _insert_adresse(
                conn, 95_000_100 + index, ban_id, numero, None, voie, cp, code, nom, FAR
            )
        for index, (ban_id, numero, voie, point) in enumerate(IDENTITY_ROWS):
            _insert_adresse(
                conn,
                95_000_500 + index,
                ban_id,
                numero,
                None,
                voie,
                "85000",
                "85191",
                "La Roche-sur-Yon",
                point,
            )
        x, y = CHAUSSEE
        # 4.5e-6 degree of latitude is about 0.5 m.
        _insert_parcelle(
            conn,
            96_000_002,
            PARCELLE_VOISINE,
            _rect(x - 2e-4, y + 4.5e-6, x + 2e-4, y + 2e-4),
        )
        x, y = FRONTIERE
        _insert_parcelle(
            conn, 96_000_003, "85191000AD0001", _rect(x - 2e-4, y - 2e-4, x, y + 2e-4)
        )
        _insert_parcelle(
            conn, 96_000_004, "85191000AD0002", _rect(x, y - 2e-4, x + 2e-4, y + 2e-4)
        )
        for index, (numero, etabli, classe, ges) in enumerate(DPE_LILAS):
            _insert_dpe(
                conn, 97_000_001 + index, numero, BAN_LILAS, etabli, classe, ges
            )
        for offset, (ban_id, rows) in enumerate(
            ((BAN_VINGT_TROIS, DPE_VINGT_TROIS), (BAN_EGALITE, DPE_EGALITE))
        ):
            _insert_adresse(
                conn,
                95_000_900 + offset,
                ban_id,
                ban_id[-2:].lstrip("0"),
                None,
                "Rue des Documents",
                "85000",
                "85191",
                "La Roche-sur-Yon",
                FAR,
            )
            for index, (numero, etabli, classe, ges) in enumerate(rows):
                _insert_dpe(
                    conn,
                    97_000_100 + offset * 100 + index,
                    numero,
                    ban_id,
                    etabli,
                    classe,
                    ges,
                )
        _insert_adresse(
            conn,
            95_000_910,
            BAN_RETIRE,
            "2",
            None,
            "Rue des Retraits",
            "85000",
            "85191",
            "La Roche-sur-Yon",
            FAR,
        )
        _insert_dpe(conn, 97_000_400, *DPE_PRESENT[:1], BAN_RETIRE, *DPE_PRESENT[1:])
        _insert_retired_dpe(conn, 97_000_401, DPE_RETIRE, BAN_RETIRE)
        _insert_adresse(
            conn,
            95_000_930,
            BAN_GLYCINES,
            "12",
            None,
            "Rue des Glycines",
            "85000",
            "85191",
            "La Roche-sur-Yon",
            GLYCINES,
        )
        _insert_parcelle(conn, 96_000_020, "85191000AG0012", _square(*GLYCINES, 2e-4))
        # 04-06: the area of the parcel is served by the « Bâtiment et parcelle »
        # theme; the fixture gives one, so that the fiche carries no state key
        # « not_reported » (which contains the « porte » sentinel below).
        conn.execute(
            "UPDATE parcelles SET contenance = 512 WHERE id_parcelle = '85191000AG0012'"
        )
        for index, (numero, etabli, brut, complete, complement, _) in enumerate(
            DPE_GLYCINES
        ):
            _insert_dpe(
                conn,
                97_000_500 + index,
                numero,
                BAN_GLYCINES,
                etabli,
                "D",
                "C",
                adresse_brut=brut,
                adresse_complete_brut=complete,
                complement=complement,
            )
        _insert_adresse(
            conn,
            95_000_920,
            BAN_SANS_VENTE,
            "5",
            None,
            "Rue sans Vente",
            "85000",
            "85191",
            "La Roche-sur-Yon",
            SANS_VENTE,
        )
        _insert_parcelle(
            conn, 96_000_010, PARCELLE_SANS_VENTE, _square(*SANS_VENTE, 0.0002)
        )
        for index, mutation in enumerate(MUTATIONS):
            _insert_mutation(conn, 98_000_001 + index, *mutation)
        conn.execute(
            "INSERT INTO dataset_metadata (dataset_name, status, "
            "source_period_label) VALUES ('dvf', 'disponible', '2021-2025') "
            "ON CONFLICT (dataset_name) DO UPDATE SET source_period_label = "
            "EXCLUDED.source_period_label"
        )
        conn.execute(
            "INSERT INTO demographie (code_insee, nom_commune, code_departement, "
            "population, annee) VALUES ('85191', 'La Roche-sur-Yon', '85', "
            "54699, 2022)"
        )
        for view in COMMUNE_INTERMEDIATES.values():
            conn.execute(f"REFRESH MATERIALIZED VIEW {view}")
        conn.execute("REFRESH MATERIALIZED VIEW v_commune_synthese")
        conn.execute(
            "INSERT INTO copropriete (numero_immatriculation, nom_copropriete, "
            "id_parcelle_1, nb_lots_total, nb_lots_habitation_strict) "
            "VALUES ('AA0000001', 'Residence privee des Lilas', %s, 14, 12)",
            (PARCELLE_LILAS,),
        )
        conn.execute("REFRESH MATERIALIZED VIEW communes_lookup")
    return isolated_pg_dsn


@dataclass
class Api:
    client: httpx.AsyncClient
    statements: list[str]

    def reads(self, table: str) -> list[str]:
        return [sql for sql in self.statements if f"FROM {table}" in sql]


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


def _scope(dsn, *, datasets=("ban", "cadastre"), departements=("44", "85")):
    install_scope_at(dsn, sorted(datasets), departements)


# --- Task 1: tracer -------------------------------------------------------


async def test_tracer_search_then_identity(api, migrated):
    _scope(migrated)

    response = await api.client.get(
        "/v1/adresses/recherche", params={"q": "12 rue des Lilas"}
    )
    assert response.status_code == 200, response.text
    body = response.json()["donnees"]
    assert body["ordre_types"] == ["adresse", "commune"]
    adresses = [r for r in body["resultats"] if r["type_resultat"] == "adresse"]
    assert adresses, body
    first = adresses[0]
    assert first["ban_id"] == BAN_LILAS
    assert (first["numero"], first["nom_voie"], first["code_postal"]) == (
        "12",
        "Rue des Lilas",
        "85000",
    )
    assert first["departement"] == {"code": "85", "nom": "Vendée"}
    assert "46.67" not in response.text and "-1.43" not in response.text

    response = await api.client.get(f"/v1/adresse/{first['ban_id']}")
    assert response.status_code == 200, response.text
    fiche = response.json()["donnees"]
    identite = fiche["identite"]
    assert identite["ban_id"] == BAN_LILAS
    assert identite["libelle"] == "12 Rue des Lilas, 85000 La Roche-sur-Yon"
    assert identite["commune"] == {"code_insee": "85191", "nom": "La Roche-sur-Yon"}
    assert identite["departement"] == {"code": "85", "nom": "Vendée"}
    assert fiche["couverture"] == {"etat": "heberge", "motif": None}
    assert identite["parcelle"] == {
        "etat": "unique",
        "id_parcelle": PARCELLE_LILAS,
        "nombre_candidats": 1,
        "rattachement": "parcelle_du_point",
        "motif": None,
    }
    assert "46.67" not in response.text and "-1.43" not in response.text


# --- Task 2: complete search ----------------------------------------------


async def _recherche(api: Api, status: int = 200, **params) -> dict:
    response = await api.client.get("/v1/adresses/recherche", params=params)
    assert response.status_code == status, response.text
    # Never stored by a cache, a refusal included (final review API IN-03).
    assert response.headers["cache-control"] == "no-store"
    return response.json()["donnees"] if status == 200 else response.json()


async def test_street_without_number_is_served_as_voie(api, migrated):
    _scope(migrated)

    body = await _recherche(api, q="rue de la Paix")

    assert body["ordre_types"] == ["adresse", "commune"]
    voies = [
        (r["nom_voie"], r["code_postal"], r["code_commune"]) for r in body["resultats"]
    ]
    assert sorted(voies) == [
        ("Rue de la Paix", "44000", "44109"),
        ("Rue de la Paix", "85000", "85191"),
    ]
    for result in body["resultats"]:
        assert result["type_resultat"] == "voie"
        assert result["numero"] is None and result["ban_id"] is None


async def test_departement_filter_is_applied_before_the_limit(api, migrated):
    _scope(migrated)

    body = await _recherche(api, q="8 rue des Acacias", departement="85")

    assert {r["ban_id"] for r in body["resultats"]} == {
        "85001_0001_00008",
        "85002_0001_00008",
    }
    assert all(r["departement"]["code"] == "85" for r in body["resultats"])
    # Control: without the filter the ten 44 rows fill the whole LIMIT.
    body = await _recherche(api, q="8 rue des Acacias")
    assert {r["code_commune"][:2] for r in body["resultats"]} == {"44"}
    assert len(body["resultats"]) == 8


async def test_hosted_departements_are_filtered_before_the_limit(api, migrated):
    _scope(migrated)  # BAN hosted on 44 and 85; the best rows sit in 49

    body = await _recherche(api, q="6 rue des Tilleuls")

    assert {r["ban_id"] for r in body["resultats"]} == {
        "85001_0002_00006",
        "85002_0002_00006",
    }
    assert body["hebergement"]["ban"]["etat"] == "heberge"


async def test_unknown_departement_filter_is_a_422(api, migrated):
    _scope(migrated)

    probleme = await _recherche(api, 422, q="8 rue des Acacias", departement="99")

    assert probleme["code"] == "departement_invalide"
    assert "99" not in probleme["title"]


async def test_unknown_departement_problem_is_in_the_contract(api, migrated):
    _scope(migrated)

    probleme = await _recherche(api, 422, q="8 rue des Acacias", departement="99")

    # A client generated from the contract reads the served code: it is one of
    # the enumeration of ProblemeAdresse (review of 261007-crf, WR-06).
    assert ProblemeAdresse.model_validate(probleme).code == "departement_invalide"


@pytest.mark.parametrize(
    "params",
    [
        {"q": "a"},
        {"q": "8 rue des Acacias", "departement": "9999"},
        {"q": "8 rue des Acacias", "limite": "9"},
    ],
    ids=["q-court", "departement-long", "limite-hors-bornes"],
)
async def test_search_validation_errors_are_never_stored(api, migrated, params):
    _scope(migrated)

    # ``_recherche`` asserts no-store on every answer, a validation error too.
    probleme = await _recherche(api, 422, **params)

    assert "code" not in probleme


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/v1/adresse/" + "1" * 41, {}),
        (f"/v1/adresse/{BAN_LILAS}/dpe", {"page": "0"}),
        (f"/v1/adresse/{BAN_LILAS}/ventes", {"page": "0"}),
        (f"/v1/adresse/{BAN_LILAS}/ventes-declarees", {"page": "0"}),
        (
            f"/v1/adresse/{BAN_LILAS}/ventes-ressemblantes",
            {"type": "maison", "surface": "1"},
        ),
    ],
    ids=["fiche", "documents", "ventes", "ventes-declarees", "ventes-ressemblantes"],
)
async def test_address_validation_errors_are_never_stored(api, migrated, path, params):
    _scope(migrated)

    response = await api.client.get(path, params=params)

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"


async def test_departement_filter_out_of_coverage_reads_no_ban(api, migrated):
    _scope(migrated)

    body = await _recherche(api, q="8 rue des Acacias", departement="35")

    assert body["resultats"] == []
    ban = body["hebergement"]["ban"]
    assert (ban["etat"], ban["motif"]) == (
        "hors_couverture",
        "territoire_hors_perimetre",
    )
    assert ban["territoire"]["departement"] == {"code": "35", "nom": "Ille-et-Vilaine"}
    assert api.reads("adresses") == []


@contextmanager
def _injected(dsn: str, *statements: str, restore: tuple[str, ...]):
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


def _assert_typed_outage(response: httpx.Response) -> None:
    assert response.status_code == 503, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "geocodage_indisponible"
    # Never an empty list, never the input reflected.
    assert "resultats" not in response.text and "Acacia" not in response.text


async def test_fts_search_outage_is_a_typed_503(api, migrated):
    _scope(migrated)
    with _injected(
        migrated,
        "ALTER TABLE adresses RENAME TO adresses_panne",
        restore=("ALTER TABLE adresses_panne RENAME TO adresses",),
    ):
        response = await api.client.get(
            "/v1/adresses/recherche",
            params={"q": "8 rue des Acacias", "departement": "85"},
        )
        _assert_typed_outage(response)
        # /v1/geocode keeps its historical degradation in the default mode.
        engine = create_async_engine(
            migrated.replace("postgresql://", "postgresql+asyncpg://")
        )
        try:
            async with async_sessionmaker(engine)() as session:
                assert await geocoder(session, "8 rue des Acacias", 5) == []
        finally:
            await engine.dispose()
    assert any("websearch_to_tsquery" in sql for sql in api.statements)


MISSPELLED = "4 rue des Peuplierz"  # FTS misses it, the trigram fallback finds it


async def test_trigram_fallback_finds_a_misspelling(api, migrated):
    _scope(migrated)

    body = await _recherche(api, q=MISSPELLED, departement="85")

    assert [r["ban_id"] for r in body["resultats"]] == ["85003_0005_00004"]
    assert any("websearch_to_tsquery" in sql for sql in api.statements)
    assert any("search_text % unaccent" in sql for sql in api.statements)


async def test_trigram_fallback_outage_is_a_typed_503(api, migrated):
    # Fresh engine (fixture): no statement of this test was prepared before
    # the outage, so the % operator is resolved after the injection.
    _scope(migrated)
    with psycopg.connect(migrated) as conn:
        user = conn.execute("SELECT current_user").fetchone()[0]
    schema = pgsql.Identifier(user)
    # Outage: the trigram operator text % text is shadowed, in the "$user"
    # schema searched before public, by an operator whose function fails.
    # FTS uses no % operator: it keeps working and returns zero rows.
    # The failure is a cancelled statement (57014): only expected database
    # failures are a geocoding outage (C55, fix 4); any other error is a 500.
    with _injected(
        migrated,
        pgsql.SQL("CREATE SCHEMA {} AUTHORIZATION CURRENT_USER").format(schema),
        pgsql.SQL(
            "CREATE FUNCTION {}.panne_trigram(text, text) RETURNS boolean "
            "LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'injected' "
            "USING ERRCODE = 'query_canceled'; END$$"
        ).format(schema),
        pgsql.SQL(
            "CREATE OPERATOR {}.% (LEFTARG = text, RIGHTARG = text, "
            "FUNCTION = {}.panne_trigram)"
        ).format(schema, schema),
        restore=(pgsql.SQL("DROP SCHEMA {} CASCADE").format(schema),),
    ):
        response = await api.client.get(
            "/v1/adresses/recherche", params={"q": MISSPELLED, "departement": "85"}
        )
    _assert_typed_outage(response)
    assert any("websearch_to_tsquery" in sql for sql in api.statements)
    assert any("search_text % unaccent" in sql for sql in api.statements)


@contextmanager
def _trigram_raising(dsn: str, errcode: str):
    """The trigram operator raises *errcode* (same shadowing as above)."""
    with psycopg.connect(dsn) as conn:
        user = conn.execute("SELECT current_user").fetchone()[0]
    schema = pgsql.Identifier(user)
    with _injected(
        dsn,
        pgsql.SQL("CREATE SCHEMA {} AUTHORIZATION CURRENT_USER").format(schema),
        pgsql.SQL(
            "CREATE FUNCTION {}.panne_trigram(text, text) RETURNS boolean "
            "LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'injected' "
            "USING ERRCODE = {}; END$$"
        ).format(schema, pgsql.Literal(errcode)),
        pgsql.SQL(
            "CREATE OPERATOR {}.% (LEFTARG = text, RIGHTARG = text, "
            "FUNCTION = {}.panne_trigram)"
        ).format(schema, schema),
        restore=(pgsql.SQL("DROP SCHEMA {} CASCADE").format(schema),),
    ):
        yield


@pytest.mark.parametrize("errcode", ["out_of_memory", "cannot_connect_now"])
async def test_trigram_fallback_resource_outage_is_a_typed_503(api, migrated, errcode):
    # C58 (WR-01): 53xxx and 57P03 reach SQLAlchemy as a plain DBAPIError
    # through asyncpg, never as OperationalError; they are an outage.
    _scope(migrated)
    with _trigram_raising(migrated, errcode):
        response = await api.client.get(
            "/v1/adresses/recherche", params={"q": MISSPELLED, "departement": "85"}
        )
    _assert_typed_outage(response)
    assert any("search_text % unaccent" in sql for sql in api.statements)


async def test_trigram_fallback_defect_is_a_bounded_500(api, migrated):
    # C58 (CR-01): an unexpected SQL error leaves the geocoder as a bounded
    # error without context, whose text never quotes the input (the app of
    # this module has no 500 middleware: the error reaches the client).
    from app.ban.queries import GeocodageDefautError

    _scope(migrated)
    with (
        _trigram_raising(migrated, "undefined_function"),
        pytest.raises(GeocodageDefautError) as excinfo,
    ):
        await api.client.get(
            "/v1/adresses/recherche", params={"q": MISSPELLED, "departement": "85"}
        )
    assert str(excinfo.value) == "ProgrammingError 42883"
    assert excinfo.value.__context__ is None
    texte = "".join(traceback.format_exception(excinfo.value))
    assert "peuplierz" not in texte.lower()


async def test_city_detection_outage_is_a_typed_503(api, migrated):
    _scope(migrated)
    with _injected(
        migrated,
        "ALTER MATERIALIZED VIEW communes_lookup RENAME TO communes_lookup_panne",
        restore=(
            "ALTER MATERIALIZED VIEW communes_lookup_panne RENAME TO communes_lookup",
        ),
    ):
        response = await api.client.get(
            "/v1/adresses/recherche", params={"q": "8 rue des Acacias"}
        )
    _assert_typed_outage(response)
    assert any("communes_lookup" in sql for sql in api.statements)
    assert not any("websearch_to_tsquery" in sql for sql in api.statements)


async def test_out_of_coverage_commune_reads_no_ban(api, migrated):
    _scope(migrated)

    body = await _recherche(api, q="1 rue de la Gare Rennes")

    assert body["resultats"] == []
    assert body["hebergement"]["ban"] == {
        "etat": "hors_couverture",
        "motif": "territoire_hors_perimetre",
        "departements": [],
        "territoire": {
            "code_insee": "35238",
            "nom": "Rennes",
            "departement": {"code": "35", "nom": "Ille-et-Vilaine"},
        },
        "homonymes": [],
    }
    assert api.reads("adresses") == []
    assert not any("communes_lookup" in sql for sql in api.statements)
    assert api.reads("geo_communes")


async def test_out_of_coverage_postal_code_reads_no_ban(api, migrated):
    _scope(migrated)

    body = await _recherche(api, q="35000")

    assert body["ordre_types"] == ["commune", "adresse"]
    assert body["resultats"] == []
    ban = body["hebergement"]["ban"]
    assert (ban["etat"], ban["motif"]) == (
        "hors_couverture",
        "territoire_hors_perimetre",
    )
    assert ban["territoire"] == {
        "code_insee": None,
        "nom": None,
        "departement": {"code": "35", "nom": "Ille-et-Vilaine"},
    }
    assert api.reads("adresses") == []


async def test_homonym_with_a_hosted_commune_has_no_out_of_coverage(api, migrated):
    _scope(migrated)

    body = await _recherche(api, q="3 rue des Lilas Le Pin")

    assert body["hebergement"]["ban"]["etat"] == "heberge"
    assert "44126_0001_00003" in {r["ban_id"] for r in body["resultats"]}
    assert api.reads("adresses")


async def test_homonyms_all_out_of_coverage_are_all_named(api, migrated):
    _scope(migrated)

    body = await _recherche(api, q="2 rue Haute Saint-Testin")

    ban = body["hebergement"]["ban"]
    assert ban["etat"] == "hors_couverture"
    assert ban["territoire"] is None
    assert [h["code_insee"] for h in ban["homonymes"]] == ["35997", "79997"]
    assert api.reads("adresses") == []


async def test_street_named_after_a_commune_is_not_a_territory(api, migrated):
    _scope(migrated)

    body = await _recherche(api, q="12 rue de Rennes")

    assert body["hebergement"]["ban"]["etat"] == "heberge"
    assert "44109_0004_00012" in {r["ban_id"] for r in body["resultats"]}


# --- Task 3: identity outcomes and the point-in-parcel relation -----------


async def _fiche(api: Api, ban_id: str, status: int = 200) -> dict:
    response = await api.client.get(f"/v1/adresse/{ban_id}")
    assert response.status_code == status, response.text
    if status != 200:
        assert response.headers["content-type"].startswith("application/problem+json")
        # Constant problem: the key typed is never reflected in the body.
        assert ban_id not in response.json().get("title", "")
        assert "detail" not in response.json()
        if status != 422:
            # Problems of the route: template as instance (API IN-01), never
            # stored by a cache (API WR-06).
            assert ban_id not in response.text
            assert response.json()["instance"] == "/v1/adresse/{ban_id}"
            assert response.headers["cache-control"] == "no-store"
        return response.json()
    # A page may carry blocks in outage: never stored (final review API IN-03).
    assert response.headers["cache-control"] == "no-store"
    return response.json()["donnees"]


async def test_out_of_coverage_key_is_a_200_without_reading(api, migrated):
    _scope(migrated)  # BAN and cadastre hosted on 44/85, Rennes is in 35

    fiche = await _fiche(api, "35238_1234_00001")

    assert fiche["couverture"] == {
        "etat": "hors_couverture",
        "motif": "territoire_hors_perimetre",
    }
    identite = fiche["identite"]
    assert identite["ban_id"] == "35238_1234_00001"
    assert identite["commune"] == {"code_insee": "35238", "nom": "Rennes"}
    assert identite["departement"] == {"code": "35", "nom": "Ille-et-Vilaine"}
    assert identite["libelle"] is None and identite["parcelle"] is None
    assert api.reads("adresses") == [] and api.reads("parcelles") == []


@pytest.mark.parametrize(
    "ban_id",
    [
        "85191_9999_00099",  # hosted departement, absent from the BAN
        "85999_0420_00001",  # INSEE prefix unknown to the referential
        "99999_0420_00001",  # prefix naming no departement
    ],
)
async def test_unknown_key_is_a_404(api, migrated, ban_id):
    _scope(migrated)

    problem = await _fiche(api, ban_id, 404)

    assert problem["code"] == "adresse_inconnue"


async def test_unknown_insee_prefix_reads_no_ban(api, migrated):
    _scope(migrated)

    await _fiche(api, "85999_0420_00001", 404)

    assert api.reads("adresses") == []


@pytest.mark.parametrize("ban_id", ["85191_0420", "85191_ab12cd"])
async def test_street_form_is_a_404_voie_sans_numero(api, migrated, ban_id):
    _scope(migrated)

    problem = await _fiche(api, ban_id, 404)

    assert problem["code"] == "voie_sans_numero"
    assert api.reads("adresses") == [] and api.reads("geo_communes") == []


@pytest.mark.parametrize("ban_id", ["48.85,2.35", "..%2F..%2Fetc"])
async def test_malformed_key_is_refused_before_any_read(api, migrated, ban_id):
    _scope(migrated)

    response = await api.client.get(f"/v1/adresse/{ban_id}")

    assert response.status_code in {404, 422}, response.text
    assert api.reads("adresses") == [] and api.reads("geo_communes") == []


@pytest.mark.parametrize("ban_id", PROFILE_KEYS)
async def test_every_key_profile_round_trips_exactly(api, migrated, ban_id):
    _scope(migrated)

    fiche = await _fiche(api, ban_id)

    assert fiche["identite"]["ban_id"] == ban_id
    assert fiche["identite"]["etat"] == "etablie"
    assert fiche["identite"]["libelle"].startswith("7")


async def test_coherent_duplicate_rows_are_one_identity(api, migrated):
    _scope(migrated)

    fiche = await _fiche(api, BAN_DOUBLON_COHERENT)

    identite = fiche["identite"]
    assert identite["etat"] == "etablie"
    assert identite["libelle"] == "2 Rue du Double, 85000 La Roche-sur-Yon"
    assert identite["parcelle"]["etat"] == "point_hors_parcelle"


async def test_contradictory_positions_are_indeterminate(api, migrated):
    _scope(migrated)

    fiche = await _fiche(api, BAN_DOUBLON_CONTRADICTOIRE)

    identite = fiche["identite"]
    assert identite["etat"] == "indeterminee"
    # Labels shared by every row are kept; no row and no point is chosen.
    assert identite["libelle"] == "4 Rue du Double, 85000 La Roche-sur-Yon"
    assert identite["parcelle"] == {
        "etat": "indeterminee",
        "id_parcelle": None,
        "nombre_candidats": None,
        "rattachement": None,
        "motif": "positions_contradictoires",
    }
    assert api.reads("parcelles") == []


async def test_point_on_the_roadway_is_outside_every_parcel(api, migrated):
    _scope(migrated)

    fiche = await _fiche(api, BAN_CHAUSSEE)

    # A parcel lies 0.5 m away: the nearest-parcel rule would have served it.
    assert fiche["identite"]["parcelle"] == {
        "etat": "point_hors_parcelle",
        "id_parcelle": None,
        "nombre_candidats": None,
        "rattachement": None,
        "motif": "point_hors_parcelle",
    }
    assert not any("ST_DWithin" in sql for sql in api.reads("parcelles"))


async def test_nearby_parcel_really_is_half_a_metre_away(migrated):
    with psycopg.connect(migrated) as conn:
        distance = conn.execute(
            "SELECT ST_Distance(geometrie::geography, "
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) "
            "FROM parcelles WHERE id_parcelle = %s",
            (*CHAUSSEE, PARCELLE_VOISINE),
        ).fetchone()[0]
    assert 0.3 < distance < 0.7


async def test_point_on_a_shared_boundary_is_ambiguous(api, migrated):
    _scope(migrated)

    fiche = await _fiche(api, BAN_FRONTIERE)

    assert fiche["identite"]["parcelle"] == {
        "etat": "ambigue",
        "id_parcelle": None,
        "nombre_candidats": 2,
        "rattachement": None,
        "motif": "parcelles_multiples",
    }


async def test_cadastre_not_hosted_is_out_of_coverage_without_reading(api, migrated):
    _scope(migrated, datasets=("ban",))

    fiche = await _fiche(api, BAN_LILAS)

    assert fiche["couverture"]["etat"] == "heberge"
    assert fiche["identite"]["parcelle"] == {
        "etat": "hors_couverture",
        "id_parcelle": None,
        "nombre_candidats": None,
        "rattachement": None,
        "motif": "dataset_non_heberge",
    }
    assert api.reads("parcelles") == []


async def test_cadastre_outage_keeps_the_identity(api, migrated):
    _scope(migrated)
    with _injected(
        migrated,
        "ALTER TABLE parcelles RENAME TO parcelles_panne",
        restore=("ALTER TABLE parcelles_panne RENAME TO parcelles",),
    ):
        fiche = await _fiche(api, BAN_LILAS)

    assert fiche["identite"]["libelle"] == "12 Rue des Lilas, 85000 La Roche-sur-Yon"
    assert fiche["identite"]["parcelle"]["etat"] == "indisponible"
    assert fiche["identite"]["parcelle"]["id_parcelle"] is None


async def test_ban_outage_is_a_typed_503(api, migrated):
    _scope(migrated)
    with _injected(
        migrated,
        "ALTER TABLE adresses RENAME TO adresses_panne",
        restore=("ALTER TABLE adresses_panne RENAME TO adresses",),
    ):
        problem = await _fiche(api, BAN_LILAS, 503)

    assert problem["code"] == "adresse_indisponible"


async def test_ban_row_without_position_is_a_gap_not_an_outage(api, migrated):
    _scope(migrated)

    fiche = await _fiche(api, BAN_SANS_POSITION)

    assert fiche["identite"]["etat"] == "indeterminee"
    assert fiche["identite"]["parcelle"]["etat"] == "indeterminee"
    assert fiche["identite"]["parcelle"]["motif"] == "position_absente"
    assert api.reads("parcelles") == []


# --- 04-03 Task 1: declared DPE count and first document page ---------------


async def test_tracer_declared_dpe_count_then_first_page(api, migrated):
    _scope(migrated, datasets=("ban", "cadastre", "dpe"))

    fiche = await _fiche(api, BAN_LILAS)

    dpe = fiche["reperes"]["dpe"]
    assert dpe["field_id"] == "adresse.dpe.nombre_declares"
    assert (dpe["etat"], dpe["valeur"]) == ("value", 3)
    assert dpe["rattachement"] == "adresse_saisie_diagnostiqueur"
    assert dpe["source"]["cle"] == "dpe"
    selection = dpe["selection"]
    assert selection["etat"] == "unique"
    assert selection["document"]["numero"] == "2485E0000003C"
    assert selection["document"]["etiquette_dpe"] == "C"
    assert selection["date"] == "2024-03-01"

    response = await api.client.get(f"/v1/adresse/{BAN_LILAS}/dpe", params={"page": 1})
    assert response.status_code == 200, response.text
    page = response.json()["donnees"]
    assert (page["total"], page["page"], page["pages"], page["par_page"]) == (
        3,
        1,
        1,
        10,
    )
    assert page["generation"]
    assert [carte["numero"] for carte in page["documents"]] == [
        "2485E0000003C",
        "2285E0000002B",
        "2185E0000001A",
    ]
    assert page["selection"] == selection
    assert "ancienne_methode" in page["documents"][2]["badges"]
    assert "46.67" not in response.text and "-1.43" not in response.text


async def test_dpe_not_hosted_is_out_of_coverage_without_reading(api, migrated):
    _scope(migrated)  # BAN and cadastre only

    fiche = await _fiche(api, BAN_LILAS)
    response = await api.client.get(f"/v1/adresse/{BAN_LILAS}/dpe")

    assert fiche["reperes"]["dpe"]["etat"] == "out_of_coverage"
    assert fiche["reperes"]["dpe"]["motif"] == "dataset_non_heberge"
    assert response.status_code == 200, response.text
    assert response.json()["donnees"]["etat"] == "out_of_coverage"
    assert response.json()["donnees"]["documents"] == []
    assert api.reads("diagnostics_dpe") == []
    assert api.reads("public.diagnostics_dpe") == []


def _publish(dsn: str, dataset: str, table: str, status: str) -> None:
    """Journal one publication of *dataset* for the current relation of *table*."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO etl_publications (generation, dataset_name, table_name, "
            "status, relation_oids) VALUES (gen_random_uuid(), %s, %s, %s, "
            "jsonb_build_object(%s::text, to_regclass(%s)::oid))",
            (dataset, table, status, table, table),
        )


@pytest.fixture
async def mounted(migrated, client, monkeypatch):
    """The real application: authentication bypassed, publication guard real.

    Same pattern as ``published`` in ``test_cor20_publication.py``: the
    journal ``etl_publications`` is the migrated one and the test override of
    ``require_publication`` is removed.
    """
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


async def test_refused_dpe_publication_breaks_only_its_block(mounted, migrated):
    _scope(migrated, datasets=("ban", "cadastre", "dpe"))
    _publish(migrated, "dpe", "diagnostics_dpe", "failed")
    try:
        identity = await mounted.get(f"/v1/adresse/{BAN_LILAS}")
        listing = await mounted.get(f"/v1/adresse/{BAN_LILAS}/dpe")
    finally:
        _publish(migrated, "dpe", "diagnostics_dpe", "published")

    assert identity.status_code == 200, identity.text
    fiche = identity.json()["donnees"]
    assert fiche["identite"]["libelle"] == "12 Rue des Lilas, 85000 La Roche-sur-Yon"
    assert fiche["reperes"]["dpe"]["etat"] == "temporary_error"
    assert fiche["reperes"]["dpe"]["valeur"] is None
    assert listing.status_code == 503, listing.text
    assert listing.headers["content-type"].startswith("application/problem+json")
    assert listing.json()["code"] == "documents_dpe_indisponibles"
    assert listing.headers["cache-control"] == "no-store"
    for response in (identity, listing):
        assert "Configuration de publication indisponible" not in response.text
        assert "2485E0000003C" not in response.text


# --- 04-03 Task 2: global selection, retired documents, pages, signal --------


async def _page(api: Api, ban_id: str, status: int = 200, **params) -> dict:
    response = await api.client.get(f"/v1/adresse/{ban_id}/dpe", params=params)
    assert response.status_code == status, response.text
    if status != 200:
        assert response.headers["content-type"].startswith("application/problem+json")
        # Constant problem: neither the key, the page nor the token reflected
        # (``instance`` is the path of the route, as for the identity).
        assert ban_id not in response.json()["title"]
        assert "detail" not in response.json()
        assert str(params.get("g")) not in response.text
        if status != 422:
            assert ban_id not in response.text
            assert response.json()["instance"] == "/v1/adresse/{ban_id}/dpe"
            assert response.headers["cache-control"] == "no-store"
        return response.json()
    # A page of one generation is never stored by a cache (API WR-06).
    assert response.headers["cache-control"] == "no-store"
    return response.json()["donnees"]


async def test_twenty_three_documents_on_three_stable_pages(api, migrated):
    _scope(migrated, datasets=("ban", "cadastre", "dpe"))

    first = await _page(api, BAN_VINGT_TROIS, page=1)
    pages = [first]
    for number in (2, 3):
        pages.append(  # noqa: PERF401
            await _page(api, BAN_VINGT_TROIS, page=number, g=first["generation"])
        )

    numeros = [carte["numero"] for page in pages for carte in page["documents"]]
    assert [len(page["documents"]) for page in pages] == [10, 10, 3]
    assert len(numeros) == 23 and len(set(numeros)) == 23
    assert {page["total"] for page in pages} == {23}
    assert {page["pages"] for page in pages} == {3}
    # The most recent document, last by number, is the global selection.
    assert numeros[0] == "2585E9999999Z"
    for page in pages:
        assert page["selection"] == first["selection"]
        assert page["selection"]["etat"] == "unique"
        assert page["selection"]["document"]["numero"] == "2585E9999999Z"
    assert pages[0]["documents"][0]["badges"] == ["plus_recent"]
    assert sum(first["distribution"].values()) == 23
    assert first["distribution"]["D"] == 22 and first["distribution"]["B"] == 1

    problem = await _page(api, BAN_VINGT_TROIS, 404, page=4, g=first["generation"])
    assert problem["code"] == "page_hors_limites"


async def test_a_tie_spread_over_two_pages_names_no_document(api, migrated):
    _scope(migrated, datasets=("ban", "cadastre", "dpe"))

    first = await _page(api, BAN_EGALITE, page=1)
    second = await _page(api, BAN_EGALITE, page=2, g=first["generation"])

    for page in (first, second):
        selection = page["selection"]
        assert selection["etat"] == "egalite"
        assert selection["document"] is None
        assert selection["date"] == "2024-05-01"
        assert len(selection["documents_ex_aequo"]) == 12
        assert selection == first["selection"]
        for carte in page["documents"]:
            assert "plus_recent" not in carte["badges"]
            assert "meme_date_plus_recente" in carte["badges"]
    fiche = await _fiche(api, BAN_EGALITE)
    assert fiche["reperes"]["dpe"]["selection"] == first["selection"]


async def test_a_retired_document_is_not_a_declared_document(api, migrated):
    _scope(migrated, datasets=("ban", "cadastre", "dpe"))

    fiche = await _fiche(api, BAN_RETIRE)
    page = await _page(api, BAN_RETIRE, page=1)

    assert fiche["reperes"]["dpe"]["valeur"] == 1
    assert page["total"] == 1
    assert [carte["numero"] for carte in page["documents"]] == [DPE_PRESENT[0]]
    assert sum(page["distribution"].values()) == 1
    assert page["distribution"]["E"] == 1
    assert page["selection"]["document"]["numero"] == DPE_PRESENT[0]
    assert DPE_RETIRE not in str(page) and DPE_RETIRE not in str(fiche)
    # It still counts as a document of the key for D-110 (04-19): the key
    # carries two documents, so « only document of the key » stays false.
    with psycopg.connect(migrated) as conn:
        documents_de_la_cle = conn.execute(
            "SELECT count(*) FROM diagnostics_dpe WHERE identifiant_ban = %s",
            (BAN_RETIRE,),
        ).fetchone()[0]
    assert documents_de_la_cle == 2


# --- 04.1-13: documents attached by their exact raw address (option B) -----

# Keys served by the list for the raw-address group, outside the ADEME key.
CLES_ADRESSE_BRUTE = {
    "etat_adresse_brute",
    "total_adresse_brute",
    "documents_adresse_brute",
}
MARQUEUR_ADRESSE_BRUTE = "dc:rattachement-adresse-brute:v1"


def _cle_ademe(page: dict) -> dict:
    """The answer without the raw-address group: the ADEME key part."""
    return {
        cle: valeur for cle, valeur in page.items() if cle not in CLES_ADRESSE_BRUTE
    }


@contextmanager
def _adresse_brute(dsn: str, documents=(), *, marquer: bool = True):
    """Documents of an orphan commune key attached to a BAN key by raw address.

    *documents*: (id, numero, ban_id attached, established, class, raw address,
    complement, tombstone). The column comment says the generation carries the
    attachment, as the DPE import writes it. Everything is undone on exit.
    """
    with psycopg.connect(dsn, autocommit=True) as conn:
        for id_, numero, cible, etabli, classe, brut, complement, retire in documents:
            _insert_dpe(
                conn,
                id_,
                numero,
                "85191",
                etabli,
                classe,
                "C",
                adresse_brut=brut,
                complement=complement,
            )
            conn.execute(
                "UPDATE diagnostics_dpe SET ban_id_adresse_brute = %s WHERE id = %s",
                (cible, id_),
            )
            if retire:
                conn.execute(
                    "UPDATE diagnostics_dpe SET is_tombstone = true, "
                    "source_state = 'absent_from_complete_scope', "
                    "source_snapshot_at = '2026-09-25T20:30:00Z', "
                    "source_observations = %s::jsonb, etiquette_dpe = NULL, "
                    "adresse_brut = NULL, type_batiment = NULL, "
                    "surface_habitable = NULL, etiquette_ges = NULL, "
                    "date_etablissement_dpe = NULL, date_fin_validite_dpe = NULL "
                    "WHERE id = %s",
                    (_RETIRED_OBSERVATIONS, id_),
                )
        if marquer:
            conn.execute(
                "COMMENT ON COLUMN diagnostics_dpe.ban_id_adresse_brute "
                f"IS '{MARQUEUR_ADRESSE_BRUTE}'"
            )
    try:
        yield
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM diagnostics_dpe WHERE id = ANY(%s)",
                ([document[0] for document in documents],),
            )
            conn.execute(
                "COMMENT ON COLUMN diagnostics_dpe.ban_id_adresse_brute IS NULL"
            )


def test_the_marker_is_the_one_the_import_writes():
    from app.etl_dpe.adresse_brute import MARQUEUR_CALCUL

    assert MARQUEUR_CALCUL == MARQUEUR_ADRESSE_BRUTE


async def test_raw_address_group_is_not_available_before_its_import(api, migrated):
    """Migrated, never imported with the attachment: a gap, never a zero."""
    _scope(migrated, datasets=("ban", "cadastre", "dpe"))

    page = await _page(api, BAN_LILAS, page=1)

    assert page["etat_adresse_brute"] == "not_available"
    assert page["total_adresse_brute"] is None
    assert page["documents_adresse_brute"] == []
    assert page["total"] == 3


async def test_raw_address_group_is_served_apart_from_the_ademe_key(api, migrated):
    _scope(migrated, datasets=("ban", "cadastre", "dpe"))
    with _adresse_brute(migrated):
        zero = await _page(api, BAN_LILAS, page=1)
        fiche_avant = await _fiche(api, BAN_LILAS)
    # Computed, none at this address: a measured zero.
    assert zero["etat_adresse_brute"] == "value"
    assert zero["total_adresse_brute"] == 0
    assert zero["documents_adresse_brute"] == []

    documents = (
        # More recent than every document of the key, class A: counted with the
        # key it would change the selection and the distribution.
        (97_013_001, "2585B0000001A", BAN_LILAS, date(2025, 6, 1), "A",
         "12 rue des Lilas", None, False),
        (97_013_002, "2385B0000002B", BAN_LILAS, date(2023, 2, 1), "B",
         "12 rue des Lilas", "porte SENTINELLE", False),
        # Retired: never served.
        (97_013_003, "2485B0000003R", BAN_LILAS, date(2024, 1, 1), "C",
         None, None, True),
        # Attached to another address.
        (97_013_004, "2485B0000004X", BAN_GLYCINES, date(2024, 1, 1), "D",
         "12 rue des Glycines", None, False),
    )  # fmt: skip
    with _adresse_brute(migrated, documents):
        page = await _page(api, BAN_LILAS, page=1)
        fiche = await _fiche(api, BAN_LILAS)

    # The ADEME key part is the same answer, byte for byte.
    assert json.dumps(_cle_ademe(page), sort_keys=True) == json.dumps(
        _cle_ademe(zero), sort_keys=True
    )
    assert fiche["reperes"]["dpe"] == fiche_avant["reperes"]["dpe"]
    assert (page["total"], page["selection"]["document"]["numero"]) == (
        3,
        "2485E0000003C",
    )
    assert page["constat_d110"] is None
    # The group, apart, most recent first, each card with its relation.
    assert page["etat_adresse_brute"] == "value"
    assert page["total_adresse_brute"] == 2
    cartes = page["documents_adresse_brute"]
    assert [carte["numero"] for carte in cartes] == ["2585B0000001A", "2385B0000002B"]
    assert {carte["rattachement"] for carte in cartes} == {"adresse_brute_exacte"}
    assert all("plus_recent" not in carte["badges"] for carte in cartes)
    # The declared address of the card follows P-ADR-01: published without
    # complement, masked with one (the complement never served).
    assert cartes[0]["adresse_declaree"] == {
        "etat": "publiee",
        "texte": "12 rue des Lilas",
        "motif": None,
    }
    assert cartes[1]["adresse_declaree"]["etat"] == "masquee"
    assert "SENTINELLE" not in json.dumps(page)
    assert "2485B0000003R" not in json.dumps(page)
    assert "2485B0000004X" not in json.dumps(page)


async def test_raw_address_group_is_capped_and_the_same_on_every_page(api, migrated):
    _scope(migrated, datasets=("ban", "cadastre", "dpe"))
    with _adresse_brute(migrated):
        avant = [await _page(api, BAN_VINGT_TROIS, page=1)]
        avant.append(
            await _page(api, BAN_VINGT_TROIS, page=2, g=avant[0]["generation"])
        )
    documents = tuple(
        (97_013_100 + index, f"2485B{index:07d}K", BAN_VINGT_TROIS,
         date(2024, 1, index), "D", f"23 rue des Documents {index}", None, False)
        for index in range(1, 13)
    )  # fmt: skip
    with _adresse_brute(migrated, documents):
        pages = [await _page(api, BAN_VINGT_TROIS, page=1)]
        pages.append(
            await _page(api, BAN_VINGT_TROIS, page=2, g=pages[0]["generation"])
        )

    for page, page_avant in zip(pages, avant, strict=True):
        assert _cle_ademe(page) == _cle_ademe(page_avant)
        assert page["total_adresse_brute"] == 12
        assert [carte["numero"] for carte in page["documents_adresse_brute"]] == [
            f"2485B{index:07d}K" for index in range(12, 2, -1)
        ]
    assert pages[0]["documents_adresse_brute"] == pages[1]["documents_adresse_brute"]


async def test_raw_address_group_outage_is_the_list_outage_detached(
    api, migrated, monkeypatch
):
    """C59 B1: a failed read of the group is the outage of the list, raised
    outside the ``except``, carrying nothing of the database error."""
    from datetime import date as jour
    from unittest.mock import MagicMock

    from sqlalchemy.exc import OperationalError

    from app.adresse import documents as module
    from app.dpe import queries as dpe_queries

    async def panne(*_args, **_kwargs):
        raise OperationalError("SELECT SENTINELLE", {"cle": BAN_LILAS}, Exception())

    _scope(migrated, datasets=("ban", "cadastre", "dpe"))
    monkeypatch.setattr(dpe_queries, "dpe_documents_adresse_brute", panne)
    with _adresse_brute(migrated):
        probleme = await _page(api, BAN_LILAS, 503, page=1)
        assert probleme["code"] == "documents_dpe_indisponibles"
        assert "SENTINELLE" not in json.dumps(probleme)

        engine = create_async_engine(
            migrated.replace("postgresql://", "postgresql+asyncpg://")
        )
        hosting = MagicMock()
        hosting.hostings = {
            cle: MagicMock(etat="heberge")
            for cle in ("ban", "dpe", "cadastre", "copropriete", "batiments")
        }
        try:
            async with async_sessionmaker(engine)() as session:
                with pytest.raises(module.DocumentsIndisponiblesError) as raised:
                    await module.lire_documents_adresse(
                        session, BAN_LILAS, 1, None, hosting, jour(2026, 10, 19)
                    )
        finally:
            await engine.dispose()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def _swap_diagnostics(dsn: str) -> None:
    """Replace ``diagnostics_dpe`` by a copy, as the ETL swap does, then publish."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE diagnostics_dpe_new (LIKE diagnostics_dpe INCLUDING ALL)"
        )
        conn.execute("INSERT INTO diagnostics_dpe_new SELECT * FROM diagnostics_dpe")
        # Dependent views keep the retired relation; it is not dropped here.
        conn.execute("ALTER TABLE diagnostics_dpe RENAME TO diagnostics_dpe_retired")
        conn.execute("ALTER TABLE diagnostics_dpe_new RENAME TO diagnostics_dpe")
    _publish(dsn, "dpe", "diagnostics_dpe", "published")


def _restore_diagnostics(dsn: str) -> None:
    """Put the original relation back (its dependent views follow it)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP TABLE diagnostics_dpe")
        conn.execute("ALTER TABLE diagnostics_dpe_retired RENAME TO diagnostics_dpe")
    _publish(dsn, "dpe", "diagnostics_dpe", "published")


async def test_a_page_link_of_a_replaced_generation_is_refused(api, migrated):
    _scope(migrated, datasets=("ban", "cadastre", "dpe"))
    first = await _page(api, BAN_VINGT_TROIS, page=1)

    _swap_diagnostics(migrated)

    try:
        problem = await _page(api, BAN_VINGT_TROIS, 409, page=2, g=first["generation"])
        fresh = await _page(api, BAN_VINGT_TROIS, page=1)
    finally:
        _restore_diagnostics(migrated)

    assert problem["code"] == "generation_changee"
    assert first["generation"] not in str(problem)
    assert fresh["generation"] != first["generation"]
    assert fresh["total"] == 23


async def test_a_registered_condominium_on_the_unique_parcel_is_signalled(
    api, migrated
):
    _scope(migrated, datasets=("ban", "cadastre", "dpe", "copropriete"))

    page = await _page(api, BAN_LILAS, page=1)

    signal = page["signal_plusieurs_logements"]
    assert signal["etat"] == "value"
    assert signal["motif"] == "copropriete_immatriculee"
    assert signal["rattachement"] == "parcelle_du_point"
    assert signal["nombre_lots_habitation"] == 12
    assert signal["source"]["cle"] == "copropriete"
    # A parcel signal designates no dwelling and no registry identity.
    assert "Residence privee" not in str(page) and "AA0000001" not in str(page)


@pytest.mark.parametrize(
    ("ban_id", "datasets"),
    [
        (BAN_LILAS, ("ban", "cadastre", "dpe")),  # condominium not hosted
        (BAN_CHAUSSEE, ("ban", "cadastre", "dpe", "copropriete")),
        (BAN_FRONTIERE, ("ban", "cadastre", "dpe", "copropriete")),
    ],
)
async def test_no_signal_without_a_unique_parcel_and_a_hosted_registry(
    api, migrated, ban_id, datasets
):
    _scope(migrated, datasets=datasets)

    page = await _page(api, ban_id, page=1)

    assert page["signal_plusieurs_logements"] is None


# --- 04-03 Task 3: landmarks ② ③ ④ and isolated SQL outages ----------------

# Every source of the four landmarks, the condominium registry and the
# structural source of the commune synthesis.
TOUT = ("ban", "cadastre", "dpe", "dvf", "georisques", "demographie", "copropriete")
MOYENNE = "Exposition moyenne"


@pytest.fixture
def georisques(monkeypatch):
    """Géorisques is an upstream: never called from a test, answered here.

    Landmark ③ calls ``/rga`` only (API CR-01): GASPAR, radon and ICPE are
    never read by landmark ③. The « Risques » theme (04-06) reads them by
    INSEE code: answered here as an upstream outage, never a network call.
    """
    fake = AsyncMock(return_value={"codeExposition": "2", "exposition": MOYENNE})
    monkeypatch.setattr("app.adresse.risques.query_rga", fake)
    monkeypatch.setattr(
        "app.adresse.risques.lire_risques_commune",
        AsyncMock(side_effect=UpstreamUnavailableError("Georisques commune")),
    )
    return fake


async def test_four_landmarks_in_their_order_with_their_meaning(
    api, migrated, georisques
):
    _scope(migrated, datasets=TOUT)

    fiche = await _fiche(api, BAN_LILAS)

    reperes = fiche["reperes"]
    assert list(reperes) == ["dpe", "ventes", "argiles", "reference_commune"]
    assert reperes["dpe"]["valeur"] == 3
    nombre, derniere = reperes["ventes"]["nombre"], reperes["ventes"]["derniere"]
    assert nombre["field_id"] == "adresse.dvf.ventes_parcelle.nombre"
    # Three distinct mutations, one of them on two parcels: counted once.
    assert (nombre["etat"], nombre["valeur"]) == ("value", 3)
    assert (derniere["etat"], derniere["valeur"]) == ("value", "2025-11-20")
    for valeur in (nombre, derniere):
        assert valeur["rattachement"] == "parcelle_du_point"
        assert valeur["source"]["cle"] == "dvf"
        assert valeur["periode"] == "2021-2025"
    argiles = reperes["argiles"]
    assert (argiles["etat"], argiles["valeur"]) == ("value", MOYENNE)
    assert argiles["rattachement"] == "au_point"
    assert argiles["source"]["cle"] == "georisques"
    georisques.assert_awaited_once()
    maisons = reperes["reference_commune"]["maisons"]
    appartements = reperes["reference_commune"]["appartements"]
    assert maisons["field_id"] == "adresse.reference_commune.prix_median_maison"
    assert (maisons["etat"], maisons["valeur"]) == ("value", 2500)
    assert maisons["unite"] == "euro_per_m2"
    for valeur in (maisons, appartements):
        assert valeur["rattachement"] == "commune_reference"
        reference = valeur["reference"]
        assert reference["territoire"] == {
            "code_insee": "85191",
            "nom": "La Roche-sur-Yon",
        }
        assert reference["url"].startswith("https://")
        assert reference["source"]["cle"] == "dvf"
    # Two apartment sales: under the publication threshold, a served sub-state.
    assert appartements["etat"] == "insufficient_sample"
    assert appartements["valeur"] is None
    assert "46.67" not in str(fiche) and "-1.43" not in str(fiche)


async def test_a_unique_parcel_without_sale_is_a_measured_zero(
    api, migrated, georisques
):
    _scope(migrated, datasets=TOUT)

    ventes = (await _fiche(api, BAN_SANS_VENTE))["reperes"]["ventes"]

    assert (ventes["nombre"]["etat"], ventes["nombre"]["valeur"]) == ("value", 0)
    assert ventes["nombre"]["periode"] == "2021-2025"
    assert ventes["nombre"]["source"]["cle"] == "dvf"
    assert (ventes["derniere"]["etat"], ventes["derniere"]["motif"]) == (
        "not_available",
        "aucune_vente",
    )


# What the work base held on 2026-10-08 (WINDOWS n° 86): no label declared by
# the DVF import, only a successful run (22:30 UTC: the 26th in Paris).
DVF_SANS_LIBELLE = (
    "UPDATE dataset_metadata SET source_period_label = NULL WHERE dataset_name = 'dvf'"
)
DVF_LIBELLE_RENDU = (
    "UPDATE dataset_metadata SET source_period_label = '2021-2025' "
    "WHERE dataset_name = 'dvf'"
)


def _periodes_dvf(fiche: dict) -> dict[str, object]:
    ventes = fiche["reperes"]["ventes"]
    return {
        "ventes.nombre": ventes["nombre"]["periode"],
        "ventes.derniere": ventes["derniere"]["periode"],
    }


def _periodes_reference(fiche: dict) -> dict[str, object]:
    reference = fiche["reperes"]["reference_commune"]
    return {
        "reference.maisons": reference["maisons"]["periode"],
        "reference.maisons.millesime": reference["maisons"]["reference"]["millesime"],
        "reference.appartements": reference["appartements"]["periode"],
        "reference.appartements.millesime": reference["appartements"]["reference"][
            "millesime"
        ],
    }


# C29 (Ianis, 2026-10-08, WINDOWS n° 90): the commune medians of ④ are computed
# by v_commune_dvf on the sales from max(date_mutation) - 12 months to
# max(date_mutation), both included; the last sale of the fixture is on
# 2025-11-20. That window is their period, never the whole source window.
FENETRE_MEDIANE = "2024-11-20 → 2025-11-20"


async def test_dvf_values_without_a_declared_label_carry_the_import_day(
    api, migrated, georisques
):
    """C24 (b): an empty label never yields a DVF value without period (C14)."""
    _scope(migrated, datasets=TOUT)

    with _injected(
        migrated,
        DVF_SANS_LIBELLE,
        "INSERT INTO etl_runs (dataset_name, status, started_at, completed_at) "
        "VALUES ('dvf', 'success', TIMESTAMPTZ '2026-09-25 21:30:00+00', "
        "TIMESTAMPTZ '2026-09-25 22:30:00+00')",
        restore=(DVF_LIBELLE_RENDU, "DELETE FROM etl_runs WHERE dataset_name = 'dvf'"),
    ):
        fiche = await _fiche(api, BAN_LILAS)

    assert _periodes_dvf(fiche) == dict.fromkeys(_periodes_dvf(fiche), "2026-09-26")
    assert fiche["reperes"]["ventes"]["nombre"]["valeur"] == 3
    # ④ is dated by the months it is computed on, label or not (C29).
    assert set(_periodes_reference(fiche).values()) == {FENETRE_MEDIANE}


async def test_dvf_values_without_label_nor_import_serve_no_period(
    api, migrated, georisques
):
    """Nothing known, nothing invented (C14): the value stays without period."""
    _scope(migrated, datasets=TOUT)

    with _injected(migrated, DVF_SANS_LIBELLE, restore=(DVF_LIBELLE_RENDU,)):
        fiche = await _fiche(api, BAN_LILAS)

    assert set(_periodes_dvf(fiche).values()) == {None}
    # The window of ④ is read on the sales its medians are computed on (C29).
    assert set(_periodes_reference(fiche).values()) == {FENETRE_MEDIANE}


async def test_the_commune_reference_is_dated_by_the_twelve_months_it_is_computed_on(
    api, migrated, georisques
):
    """C29: the declared DVF label (« 2021-2025 ») is not the period of ④."""
    _scope(migrated, datasets=TOUT)

    fiche = await _fiche(api, BAN_LILAS)

    assert _periodes_reference(fiche) == dict.fromkeys(
        _periodes_reference(fiche), FENETRE_MEDIANE
    )
    assert _periodes_dvf(fiche) == dict.fromkeys(_periodes_dvf(fiche), "2021-2025")
    maisons = fiche["reperes"]["reference_commune"]["maisons"]
    assert (maisons["etat"], maisons["valeur"]) == ("value", 2500)


@pytest.mark.parametrize(
    ("ban_id", "motif"),
    [(BAN_CHAUSSEE, "point_hors_parcelle"), (BAN_FRONTIERE, "parcelles_multiples")],
)
async def test_no_sale_is_attributed_without_a_unique_parcel(
    api, migrated, georisques, ban_id, motif
):
    _scope(migrated, datasets=TOUT)

    ventes = (await _fiche(api, ban_id))["reperes"]["ventes"]

    for valeur in (ventes["nombre"], ventes["derniere"]):
        # A gap with its reason: never a zero, never « cadastre absent ».
        assert (valeur["etat"], valeur["motif"], valeur["valeur"]) == (
            "not_available",
            motif,
            None,
        )
    # No sale is read: the one read of the sales table is the window of the
    # commune medians of ④ (C29), an aggregate of the whole table that names
    # no parcel.
    assert api.reads("mutations_grain") == [queries._FENETRE_MEDIANE_SQL.text]


async def test_dvf_not_hosted_is_out_of_coverage_without_reading(
    api, migrated, georisques
):
    _scope(migrated, datasets=tuple(key for key in TOUT if key != "dvf"))

    ventes = (await _fiche(api, BAN_LILAS))["reperes"]["ventes"]

    assert (ventes["nombre"]["etat"], ventes["nombre"]["motif"]) == (
        "out_of_coverage",
        "dataset_non_heberge",
    )
    assert api.reads("mutations_grain") == []


async def test_an_rga_answer_without_exposure_is_never_a_zero(
    api, migrated, georisques
):
    georisques.return_value = {"exposition": None, "code": None}
    _scope(migrated, datasets=TOUT)

    argiles = (await _fiche(api, BAN_LILAS))["reperes"]["argiles"]

    assert (argiles["etat"], argiles["valeur"]) == ("not_reported", None)
    assert " 0" not in str(argiles)


@pytest.mark.parametrize(
    "panne",
    [
        {"side_effect": UpstreamUnavailableError("Georisques RGA")},
        {"return_value": {"message": "inattendu"}},
    ],
    ids=["rga_down", "rga_without_its_keys"],
)
async def test_clay_sub_source_down_is_an_outage(api, migrated, georisques, panne):
    for attribut, valeur in panne.items():
        setattr(georisques, attribut, valeur)
    _scope(migrated, datasets=TOUT)

    fiche = await _fiche(api, BAN_LILAS)

    assert fiche["reperes"]["argiles"]["etat"] == "temporary_error"
    assert fiche["reperes"]["dpe"]["etat"] == "value"


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


async def test_georisques_is_called_without_a_transaction_nor_the_lock(
    api, migrated, georisques
):
    """API CR-01 (D-15): no shared publication lock and no pool connection in a
    transaction while the upstream answers (up to its timeout).
    """
    _scope(migrated, datasets=TOUT)
    observe: list[dict[str, int]] = []
    reponse = georisques.return_value

    async def amont(*_args, **_kwargs):
        observe.append(_transactions_et_verrous(migrated))
        return reponse

    georisques.side_effect = amont

    fiche = await _fiche(api, BAN_LILAS)

    assert observe == [{"verrous": 0, "transactions": 0}]
    reperes = fiche["reperes"]
    # The blocks read after the call are still guarded and served.
    assert reperes["argiles"]["etat"] == "value"
    assert reperes["dpe"]["etat"] == "value"
    assert reperes["ventes"]["nombre"]["etat"] == "value"


async def test_the_commune_reference_is_the_value_of_v1_commune(
    mounted, migrated, georisques
):
    _scope(migrated, datasets=TOUT)

    fiche = (await mounted.get(f"/v1/adresse/{BAN_LILAS}")).json()["donnees"]
    response = await mounted.get("/v1/commune", params={"code_insee": "85191"})

    assert response.status_code == 200, response.text
    commune = response.json()["donnees"][0]
    reference = fiche["reperes"]["reference_commune"]
    assert reference["maisons"]["valeur"] == commune["prix_m2_median_maison"]
    assert commune["prix_m2_median_appartement"] is None
    assert (
        reference["appartements"]["etat"]
        == commune["etats"]["prix_m2_median_appartement"]["etat"]
    )


async def test_refused_dvf_publication_breaks_the_sales_landmark_only(
    mounted, migrated, georisques
):
    _scope(migrated, datasets=TOUT)
    _publish(migrated, "dvf", "mutations_grain", "failed")
    try:
        response = await mounted.get(f"/v1/adresse/{BAN_LILAS}")
    finally:
        _publish(migrated, "dvf", "mutations_grain", "published")

    assert response.status_code == 200, response.text
    reperes = response.json()["donnees"]["reperes"]
    assert reperes["ventes"]["nombre"]["etat"] == "temporary_error"
    assert reperes["ventes"]["derniere"]["etat"] == "temporary_error"
    assert reperes["dpe"]["etat"] == "value"
    assert reperes["argiles"]["etat"] == "value"
    # The commune median is built from the same DVF publication: the commune
    # barrier refuses it too (see SUMMARY, contradiction plan / code).
    assert reperes["reference_commune"]["maisons"]["etat"] == "temporary_error"
    assert "Configuration de publication indisponible" not in response.text


@pytest.mark.parametrize(
    ("panne", "restaure", "en_panne"),
    [
        (
            "ALTER TABLE mutations_grain RENAME TO mutations_grain_panne",
            "ALTER TABLE mutations_grain_panne RENAME TO mutations_grain",
            {"ventes", "reference_commune"},
        ),
        (
            "ALTER TABLE diagnostics_dpe RENAME TO diagnostics_dpe_panne",
            "ALTER TABLE diagnostics_dpe_panne RENAME TO diagnostics_dpe",
            {"dpe", "reference_commune"},
        ),
        (
            "ALTER MATERIALIZED VIEW v_commune_synthese "
            "RENAME TO v_commune_synthese_panne",
            "ALTER MATERIALIZED VIEW v_commune_synthese_panne "
            "RENAME TO v_commune_synthese",
            {"reference_commune"},
        ),
    ],
)
async def test_a_sql_outage_of_one_source_leaves_the_other_landmarks(
    api, migrated, georisques, panne, restaure, en_panne
):
    _scope(migrated, datasets=TOUT)
    with _injected(migrated, panne, restore=(restaure,)):
        fiche = await _fiche(api, BAN_LILAS)

    assert fiche["identite"]["libelle"] == "12 Rue des Lilas, 85000 La Roche-sur-Yon"
    reperes = fiche["reperes"]
    etats = {
        "dpe": reperes["dpe"]["etat"],
        "ventes": reperes["ventes"]["nombre"]["etat"],
        "argiles": reperes["argiles"]["etat"],
        "reference_commune": reperes["reference_commune"]["maisons"]["etat"],
    }
    assert {nom for nom, etat in etats.items() if etat == "temporary_error"} == en_panne
    assert {etats[nom] for nom in etats.keys() - en_panne} == {"value"}


# --- 04-19 Task 1: declared address published or masked (P-ADR-01, N-12) -----


async def test_tracer_declared_address_published_or_masked_never_leaked(
    api, migrated, caplog
):
    _scope(migrated, datasets=("ban", "cadastre", "dpe"))

    response = await api.client.get(f"/v1/adresse/{BAN_GLYCINES}/dpe")
    fiche = await api.client.get(f"/v1/adresse/{BAN_GLYCINES}")

    assert response.status_code == 200, response.text
    assert fiche.status_code == 200, fiche.text
    # The sentinel of the raw address, the full raw address and the
    # complement appears in no answer and no log line (T-04-19-01).
    for texte in (response.text, fiche.text, caplog.text):
        assert SENTINELLE not in texte
        assert "porte" not in texte
    page = response.json()["donnees"]
    servies = {c["numero"]: c["adresse_declaree"] for c in page["documents"]}
    assert servies == {numero: attendu for numero, *_, attendu in DPE_GLYCINES}
    for carte in page["documents"]:
        assert not {
            "adresse_brut",
            "adresse_complete_brut",
            "complement_adresse_logement",
        } & set(carte)


async def test_every_stored_complement_column_is_read_by_the_projection(migrated):
    from app.dpe.public_projection import COLONNES_COMPLEMENT

    with psycopg.connect(migrated) as conn:
        colonnes = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'diagnostics_dpe' "
                "AND column_name LIKE 'complement%%'"
            ).fetchall()
        }
    assert colonnes == set(COLONNES_COMPLEMENT)


# --- 04-19 Task 2: D-110 finding served only when every criterion holds -------

D110_DATASETS = ("ban", "cadastre", "dpe", "batiments", "copropriete")
D110_BASE = (-1.3900, 46.6300)
# A neighbour building straddling the parcel edge: it intersects the parcel
# but its point on surface lies outside, so it is not on the parcel.
D110_VOISIN = (1.9e-4, -1e-5, 6e-4, 1e-5)


@dataclass(frozen=True)
class ScenarioD110:
    """One address of the D-110 matrix (04-19 ``<behavior>``)."""

    nom: str
    attendu: bool
    usages: tuple[str | None, ...] = ("Résidentiel", "Annexe")
    parcelle: str = "unique"  # unique | frontiere | voisine
    documents: tuple[tuple[str, str | None], ...] = (("maison", None),)
    retire: bool = False
    doublon_contradictoire: bool = False
    # Two BAN rows at the same point that disagree on the number (API WR-04).
    doublon_meme_point: bool = False
    voisin: bool = False
    id_rnb: str | None = None


SCENARIOS_D110 = (
    ScenarioD110("positif", True),
    ScenarioD110("deux_principaux", False, usages=("Résidentiel", "Résidentiel")),
    ScenarioD110("usage_inconnu", False, usages=("Résidentiel", None)),
    ScenarioD110("frontiere", False, parcelle="frontiere"),
    # The nearest parcel holds one building and the RNB link is set: neither
    # is a proof (no parcel contains the point; ``cad_parcelles`` is not
    # stored by the BAN import).
    ScenarioD110("hors_parcelle", False, parcelle="voisine", id_rnb="RNB-ADVERSE"),
    ScenarioD110("brut_divergent", False, documents=(("maison", "{n2} {voie}"),)),
    ScenarioD110("non_evaluable", False, documents=(("maison", "{n}-{n2} {voie}"),)),
    ScenarioD110("doublon_incoherent", False, doublon_contradictoire=True),
    ScenarioD110("deux_documents", False, documents=(("maison", None),) * 2),
    ScenarioD110("document_retire", False, retire=True),
    ScenarioD110("appartement", False, documents=(("appartement", None),)),
    ScenarioD110(
        "six_dont_cinq_annexes",
        True,
        usages=("Résidentiel", *("Annexe",) * 5),
        voisin=True,
    ),
    # Undetermined identity around a single point (decision n° 23): the
    # concordance would read the first row only, an arbitrary choice.
    ScenarioD110("doublon_meme_point", False, doublon_meme_point=True),
)


def _d110_cle(index: int) -> str:
    return f"85191_0910_{index + 1:05d}"


def _d110_point(index: int) -> tuple[float, float]:
    return (D110_BASE[0] + index * 0.003, D110_BASE[1])


def _insert_batiment(conn, id_, cleabs, usage, wkt):
    conn.execute(
        "INSERT INTO batiments (id, code_departement, cleabs, usage_1, geometrie) "
        "VALUES (%s, '85', %s, %s, ST_GeomFromText(%s, 4326))",
        (id_, cleabs, usage, wkt),
    )


def _insert_parcelles_d110(conn, base, index, mode, x, y):
    parcelle = f"85191000AH{index + 1:04d}"
    if mode == "unique":
        _insert_parcelle(conn, base, parcelle, _square(x, y, 2e-4))
    elif mode == "frontiere":
        _insert_parcelle(conn, base, parcelle, _rect(x - 2e-4, y - 2e-4, x, y + 2e-4))
        _insert_parcelle(
            conn,
            base + 1,
            f"85191000AJ{index + 1:04d}",
            _rect(x, y - 2e-4, x + 2e-4, y + 2e-4),
        )
    else:  # the nearest parcel, 0.5 m north of the point
        _insert_parcelle(
            conn, base, parcelle, _rect(x - 2e-4, y + 4.5e-6, x + 2e-4, y + 4e-4)
        )


def _insert_scenario_d110(conn, index: int, scenario: ScenarioD110) -> None:
    cle, (x, y) = _d110_cle(index), _d110_point(index)
    numero, voie = str(index + 1), "Chemin des Essais"
    base = 99_000_000 + index * 100
    commune = ("85000", "85191", "La Roche-sur-Yon")
    _insert_adresse(conn, base, cle, numero, None, voie, *commune, (x, y))
    if scenario.doublon_contradictoire:
        _insert_adresse(
            conn, base + 1, cle, numero, None, voie, *commune, (x + 1e-3, y)
        )
    if scenario.doublon_meme_point:
        _insert_adresse(conn, base + 1, cle, f"{numero}0", None, voie, *commune, (x, y))
    _insert_parcelles_d110(conn, base, index, scenario.parcelle, x, y)
    centre_y = y + 2e-4 if scenario.parcelle == "voisine" else y
    for rang, usage in enumerate(scenario.usages):
        # Every building inside the (western) parcel, apart from each other.
        cx = x - 1e-4 if scenario.parcelle == "frontiere" else x - 1e-4 + rang * 4e-5
        wkt = _square(cx, centre_y, 1e-5)
        _insert_batiment(conn, base + rang, f"BAT{base + rang}", usage, wkt)
    if scenario.voisin:
        x0, y0, x1, y1 = D110_VOISIN
        wkt = _rect(x + x0, y + y0, x + x1, y + y1)
        _insert_batiment(conn, base + 50, f"BAT{base + 50}", "Résidentiel", wkt)
    for rang, (type_batiment, gabarit) in enumerate(scenario.documents):
        brut = (gabarit or "{n} {voie}").format(
            n=numero, n2=str(index + 2), voie=voie.lower()
        )
        _insert_dpe(
            conn,
            base + rang,
            f"2585H{index:04d}{rang:03d}D",
            cle,
            date(2025, 1, 1 + rang),
            "D",
            "C",
            type_batiment=type_batiment,
            adresse_brut=brut,
        )
        if scenario.id_rnb:
            conn.execute(
                "UPDATE diagnostics_dpe SET id_rnb = %s WHERE id = %s",
                (scenario.id_rnb, base + rang),
            )
    if scenario.retire:
        _insert_retired_dpe(conn, base + 90, f"2285H{index:04d}RET", cle)


@pytest.fixture(scope="module")
def d110(migrated):
    with psycopg.connect(migrated) as conn:
        for index, scenario in enumerate(SCENARIOS_D110):
            _insert_scenario_d110(conn, index, scenario)
        # A registered condominium on the positive parcel: the signal is
        # another block of the list and must survive a buildings outage.
        conn.execute(
            "INSERT INTO copropriete (numero_immatriculation, nom_copropriete, "
            "id_parcelle_1, nb_lots_total, nb_lots_habitation_strict) "
            "VALUES ('AA0000419', 'Copropriete du constat', '85191000AH0001', 2, 2)"
        )
    return migrated


CRITERES_D110 = [
    "maison",
    "document_unique_cle",
    "concordance_stricte",
    "batiment_unique_parcelle_du_point",
    "annexes_exclues_usage_inconnu_compte",
]


@pytest.mark.parametrize(
    ("index", "scenario"),
    list(enumerate(SCENARIOS_D110)),
    ids=[scenario.nom for scenario in SCENARIOS_D110],
)
async def test_the_d110_finding_is_served_only_on_every_criterion(
    api, d110, index, scenario
):
    _scope(d110, datasets=D110_DATASETS)

    page = await _page(api, _d110_cle(index), page=1)

    constat = page["constat_d110"]
    if not scenario.attendu:
        assert constat is None
        return
    assert constat["criteres"] == CRITERES_D110
    assert [source["cle"] for source in constat["sources"]] == [
        "dpe",
        "ban",
        "cadastre",
        "batiments",
    ]
    assert set(constat) == {"criteres", "sources"}
    assert "RNB" not in str(constat)


async def test_buildings_not_hosted_serve_no_finding(api, d110):
    _scope(d110, datasets=("ban", "cadastre", "dpe", "copropriete"))

    page = await _page(api, _d110_cle(0), page=1)

    assert page["constat_d110"] is None
    assert api.reads("batiments") == []


async def test_a_buildings_outage_serves_no_finding_and_keeps_the_other_blocks(
    api, d110
):
    _scope(d110, datasets=D110_DATASETS)
    with _injected(
        d110,
        "ALTER TABLE batiments RENAME TO batiments_panne",
        restore=("ALTER TABLE batiments_panne RENAME TO batiments",),
    ):
        page = await _page(api, _d110_cle(0), page=1)

    assert page["constat_d110"] is None
    assert page["total"] == 1 and len(page["documents"]) == 1
    assert page["selection"]["etat"] == "unique"
    assert page["signal_plusieurs_logements"]["etat"] == "value"


async def test_buildings_on_the_parcel_are_counted_exactly_without_limit(d110):
    from app.adresse.relations import BatimentsParcelle, batiments_sur_parcelle

    engine = create_async_engine(d110.replace("postgresql://", "postgresql+asyncpg://"))
    index = [s.nom for s in SCENARIOS_D110].index("six_dont_cinq_annexes")
    try:
        async with async_sessionmaker(engine)() as session:
            compte = await batiments_sur_parcelle(session, f"85191000AH{index + 1:04d}")
            inconnue = await batiments_sur_parcelle(session, "85191000ZZ9999")
    finally:
        await engine.dispose()

    # Six on the parcel, five of them declared annexes; the straddling
    # neighbour is not counted.
    assert compte == BatimentsParcelle(total=6, hors_annexe=1)
    assert inconnue is None
