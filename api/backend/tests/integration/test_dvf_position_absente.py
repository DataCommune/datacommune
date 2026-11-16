"""Phase 04.4 (D-03): a sale without any geocoded line is a statistic only.

Since plan 04.4-01 every geo-DVF line is loaded and reconstructed; a mutation
none of whose lines carries coordinates gets ``position IS NULL``. Ianis
decided on 2026-10-19 (D-03) that such a mutation counts in the statistics
of its commune and is absent from every read by address, parcel, position or
radius. One fixture, judged by each served reader:

- ``SYN-M1``: a house sale declared at the BAN address, on the parcel under
  the BAN point, with one geocoded line;
- ``SYN-M0``: another house sale declared at the same address, on the same
  parcel, in the same 12 months, without any geocoded line;
- ``SYN-ZERO``: a geocoded house sale at 0 euro on another parcel (A5a-01);
- ``SYN-A1`` and ``SYN-A0``: two appartement sales of one building parcel,
  the first geocoded, the second without any geocoded line.

Raw lines are inserted into ``mutations``, then rebuilt by
``reconstruit_mutations_grain``; the reconstruction renews the relations, so
the DVF publication is re-anchored afterwards and the commune chain is
recreated, as in ``test_dvf_numeric_chain.py``. D-31: ``SYN-`` identifiers,
no real address, parcel or mutation.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_dvf_position_absente.py
"""

from datetime import date

import psycopg
import pytest
from psycopg import sql as psql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.commune.matview_ddl import recreate_commune_synthese
from app.data_quality.base import AuditResult
from app.data_quality.checkers.dvf import DvfAuditor
from app.dvf.queries import (
    dvf_appartements_par_parcelle,
    dvf_compte_par_id_parcelle,
    dvf_par_adresse,
    dvf_par_id_parcelle,
    dvf_par_parcelle,
    dvf_par_point,
)
from app.etl_dvf.config import MARQUEUR_ADRESSE_SUFFIXE
from app.etl_dvf.reconstruction import reconstruit_mutations_grain
from app.zone.queries import zone_resolve_dvf, zone_stats_dvf
from tests.integration.test_adresse_ventes_contract import (
    api,  # noqa: F401 - fixture, resolved against this module's ``migrated``
    declarees,
    insert_adresse,
    insert_parcelle,
    migrate,
    publish,
    ressemblantes,
    scope,
    ventes,
)

pytestmark = pytest.mark.destructive

COMMUNE = "85196"
CLE = "85196_0920_00003"
POINT = (-1.6000, 46.9000)
HALF = 0.0002
PARCELLE = "85196000PA0001"
PARCELLE_ZERO = "85196000PA0002"
IMMEUBLE = "85196000PA0003"
PRIX_M1 = 250_000
# M1's line is geocoded a few metres north of the BAN point.
POINT_M1 = (-1.6000, 46.90005)

# Surfaces tell the served cards apart: a card never carries its mutation id.
SURFACE_M1 = 80
SURFACE_M0 = 85
SURFACE_A1 = 60
APPARTEMENT = ("2", "Appartement")


def _square(lon: float, lat: float, half: float = HALF) -> str:
    x0, x1, y0, y1 = lon - half, lon + half, lat - half, lat + half
    return f"MULTIPOLYGON((({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0})))"


def _ligne(
    conn,
    id_: int,
    id_mutation: str,
    jour: date,
    valeur: int,
    parcelle: str,
    point: tuple[float, float] | None,
    *,
    surface: int,
    numero: str = "3",
    local: tuple[str, str] = ("1", "Maison"),
) -> None:
    """One raw ``mutations`` line; an explicit id keeps the rebuild stable."""
    lon, lat = point if point else (None, None)
    code_type_local, type_local = local
    conn.execute(
        "INSERT INTO mutations (id, code_departement, id_mutation, date_mutation, "
        "nature_mutation, valeur_fonciere, numero_disposition, code_commune, "
        "nom_commune, code_postal, adresse_numero, adresse_nom_voie, id_parcelle, "
        "code_type_local, type_local, surface_reelle_bati, "
        "nombre_pieces_principales, longitude, latitude, position) VALUES "
        "(%s, '85', %s, %s, 'Vente', %s, '000001', %s, 'SYN-COMMUNE', '85000', "
        "%s, 'RUE DES POSITIONS', %s, %s, %s, %s, 4, %s, %s, CASE WHEN "
        "%s::float8 IS NULL THEN NULL ELSE "
        "ST_SetSRID(ST_MakePoint(%s::float8, %s::float8), 4326) END)",
        (
            id_,
            id_mutation,
            jour,
            valeur,
            COMMUNE,
            numero,
            parcelle,
            code_type_local,
            type_local,
            surface,
            lon,
            lat,
            lon,
            lon,
            lat,
        ),
    )


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    """Raw lines, rebuilt grain, re-anchored publication, commune chain."""
    migrate(isolated_pg_dsn)
    with psycopg.connect(isolated_pg_dsn) as conn:
        insert_adresse(conn, 97_200_001, CLE, POINT, voie="Rue des Positions")
        insert_parcelle(conn, 97_300_001, PARCELLE, _square(*POINT))
        _ligne(
            conn,
            9_720_001,
            "SYN-M1",
            date(2025, 6, 1),
            PRIX_M1,
            PARCELLE,
            POINT_M1,
            surface=SURFACE_M1,
        )
        _ligne(
            conn,
            9_720_002,
            "SYN-M0",
            date(2025, 5, 1),
            260_000,
            PARCELLE,
            None,
            surface=SURFACE_M0,
        )
        # Geocoded, 0 euro, another parcel and number: never in a statistic.
        _ligne(
            conn,
            9_720_003,
            "SYN-ZERO",
            date(2025, 3, 1),
            0,
            PARCELLE_ZERO,
            (-1.6000, 46.9020),
            surface=200,
            numero="5",
        )
        # One building parcel, out of the 12 months of the commune view.
        _ligne(
            conn,
            9_720_004,
            "SYN-A1",
            date(2019, 4, 1),
            150_000,
            IMMEUBLE,
            (-1.6000, 46.9040),
            surface=SURFACE_A1,
            numero="7",
            local=APPARTEMENT,
        )
        _ligne(
            conn,
            9_720_005,
            "SYN-A0",
            date(2019, 5, 1),
            140_000,
            IMMEUBLE,
            None,
            surface=55,
            numero="7",
            local=APPARTEMENT,
        )
        # The import of a suffix-carrying source marks the column (C44).
        conn.execute(
            psql.SQL("COMMENT ON COLUMN mutations.adresse_suffixe IS {}").format(
                psql.Literal(MARQUEUR_ADRESSE_SUFFIXE)
            )
        )
    reconstruit_mutations_grain(isolated_pg_dsn, ("85",))
    publish(isolated_pg_dsn, "published")
    scope(isolated_pg_dsn)
    with psycopg.connect(isolated_pg_dsn) as conn:
        recreate_commune_synthese(conn)
    return isolated_pg_dsn


async def _lire(dsn: str, lecteur, *args, **kwargs):
    """One served query function on its own session."""
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with async_sessionmaker(engine)() as session:
            return await lecteur(session, *args, **kwargs)
    finally:
        await engine.dispose()


def _surfaces(page: dict) -> list[int]:
    return [carte["surface_batie"] for carte in page["ventes"]]


def test_the_fixture_has_one_positioned_and_one_unpositioned_mutation(migrated):
    with psycopg.connect(migrated) as conn:
        rows = conn.execute(
            "SELECT id_mutation, position IS NULL FROM mutations_grain "
            "ORDER BY id_mutation"
        ).fetchall()
    assert rows == [
        ("SYN-A0", True),
        ("SYN-A1", False),
        ("SYN-M0", True),
        ("SYN-M1", False),
        ("SYN-ZERO", False),
    ]


def test_the_sale_without_position_counts_in_the_commune_statistics(migrated):
    with psycopg.connect(migrated) as conn:
        row = conn.execute(
            "SELECT nb_mutations_12m, nb_mutations_12m_maison FROM v_commune_dvf "
            "WHERE code_insee = %s",
            (COMMUNE,),
        ).fetchone()
    assert row == (2, 2)


@pytest.mark.parametrize("regle", ["numero", "numero_et_suffixe"])
@pytest.mark.parametrize("vefa", ["exclues", "incluses"])
async def test_the_declared_sales_list_the_positioned_sale_only(
    api,  # noqa: F811
    migrated,
    regle,
    vefa,
):
    page = await declarees(api, CLE, regle=regle, vefa=vefa)

    assert (page["etat"], page["total"]) == ("value", 1)
    assert _surfaces(page) == [SURFACE_M1]


async def test_the_parcel_sales_and_the_landmark_list_the_positioned_sale_only(
    api,  # noqa: F811
    migrated,
):
    page = await ventes(api, CLE)

    assert (page["etat"], page["total"]) == ("value", 1)
    assert _surfaces(page) == [SURFACE_M1]
    # Landmark ② of the address page (app/adresse/service.py).
    compte = await _lire(migrated, dvf_compte_par_id_parcelle, PARCELLE)
    assert compte["nombre"] == 1
    assert compte["derniere"] == date(2025, 6, 1)


@pytest.mark.parametrize("rayon", ["commune", "250"])
async def test_the_similar_sales_list_the_positioned_sale_only(
    api,  # noqa: F811
    migrated,
    rayon,
):
    page = await ressemblantes(
        api, CLE, type="maison", surface=str(SURFACE_M1), tolerance="10", rayon=rayon
    )

    assert (page["etat"], page["effectif"]) == ("value", 1)
    assert _surfaces(page) == [SURFACE_M1]


# --- Task 2: every other reader ---------------------------------------------------


def _ids(rows: list[dict]) -> list[str]:
    return [row["id_mutation"] for row in rows]


async def test_the_parcel_readers_of_the_fiche_serve_the_positioned_sale_only(
    migrated,
):
    canonique = await _lire(migrated, dvf_par_id_parcelle, PARCELLE)
    par_point = await _lire(migrated, dvf_par_parcelle, POINT[1], POINT[0])

    assert _ids(canonique) == ["SYN-M1"]
    assert _ids(par_point) == ["SYN-M1"]


async def test_the_building_sales_serve_the_positioned_appartement_only(migrated):
    rows = await _lire(migrated, dvf_appartements_par_parcelle, IMMEUBLE)

    assert [row["surface_reelle_bati"] for row in rows] == [SURFACE_A1]


async def test_the_radius_readers_never_serve_a_sale_without_position(migrated):
    """Code unchanged: ``ST_DWithin`` on a NULL position is never true."""
    point = await _lire(migrated, dvf_par_point, POINT[1], POINT[0], rayon_m=1000)
    adresse = await _lire(migrated, dvf_par_adresse, POINT[1], POINT[0], rayon_m=50)

    assert set(_ids(point)) == {"SYN-M1", "SYN-ZERO", "SYN-A1"}
    assert _ids(adresse) == ["SYN-M1"]


async def test_a_recent_sale_alone_among_the_positioned_sales_of_its_parcel_is_withheld(
    api,  # noqa: F811
    migrated,
):
    """R*112 A-3 LPF: counting M0 would make M1 « not isolated » and publish
    its price. The isolation counts the positioned mutations only."""
    page = await ventes(api, CLE)

    (carte,) = page["ventes"]
    assert (carte["prix"], carte["prix_m2"], carte["prix_affiche"]) == (
        None,
        None,
        False,
    )
    assert carte["minimisation"] == {"motif": "vente_isolee_recente"}
    raw = (await api.client.get(f"/v1/adresse/{CLE}/ventes")).text
    assert str(PRIX_M1) not in raw


@pytest.mark.parametrize(
    ("zone_type", "code"), [("commune", COMMUNE), ("departement", "85")]
)
async def test_the_zone_list_serves_the_positioned_sales_only(
    migrated, zone_type, code
):
    rows, total = await _lire(
        migrated, zone_resolve_dvf, zone_type=zone_type, code=code
    )

    assert set(_ids(rows)) == {"SYN-M1", "SYN-ZERO", "SYN-A1"}
    assert total == 3


async def test_the_zone_stats_count_every_sale_but_never_a_price_of_zero(migrated):
    """D-03: M0 and A0 are counted. A5a-01: the 0 euro sale is not."""
    stats = await _lire(migrated, zone_stats_dvf, "commune", COMMUNE)

    assert stats["nb_transactions"] == 4
    assert stats["nb_transactions_maison"] == 2
    assert stats["nb_transactions_appartement"] == 2


def test_the_dvf_audit_takes_a_line_without_position_as_legitimate(migrated):
    with psycopg.connect(migrated) as conn:
        (total,) = conn.execute("SELECT count(*) FROM mutations").fetchone()
        result = AuditResult(dataset="dvf", table="mutations", row_count=total)
        auditor = DvfAuditor()
        auditor._check_required_nulls(conn, result, total)
        auditor._check_expected_nulls(conn, result, total)

    assert not [f for f in result.findings if "position" in f.title]
    # Two of the five lines carry no coordinates.
    assert result.stats["null_pct_position"] == "40.0%"
