"""Phase 04.4 (D-01, D-02, D-04): the whole DVF source file is reconstructed.

End to end on a disposable PostGIS database: a geo-DVF file goes through the
real runner (``PipelineRunner`` with the validation and matview hooks), lands
in ``mutations``, is reconstructed into ``mutations_grain`` and is read back
by the served reader ``app.dvf.queries.dvf_par_id_parcelle``. The only
substitute is the HTTP download (``verified_download_gz`` as bound in the DVF
pipeline), which writes the fixture file instead.

Fixture shapes (D-31: ``SYN-`` identifiers, no real address or parcel):

- ``SYN-2021-000001`` copies the shape of source mutation ``2021-748011``
  (archive DVF 44, run ``48-20261019T171140Z``, read on the hypervisor): 4 lines on 4
  parcels, the same house of 131 m2 and 5 rooms on two of them, one of these
  two without coordinates, two land lines.
- ``SYN-2021-000002``: a house on a geocoded parcel and another, distinct,
  local on a parcel without coordinates.
- ``SYN-2021-000003``: no geocoded line at all.
- ``SYN-2021-000004`` (variant V of D-02, Ianis 2026-10-21): one house on one
  parcel repeated on its S and J culture lines, two land surfaces, no lot --
  the shape measured by plan 04.4-03 (D-02 alone: 7 of 15 kept mono-local).
- ``SYN-MC-*`` (D-04, lines inserted with explicit ids, then rebuilt): the
  address of a multi-commune mutation, ``SYN-MC-1`` copying the shape of
  source mutation ``2021-767870`` (same archive).

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_dvf_reconstruction_fidelite.py
"""

import csv
import re
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.dvf.queries import dvf_par_id_parcelle
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_dvf.config import COLUMNS_TO_KEEP
from app.etl_dvf.pipeline import DvfPipeline
from app.etl_dvf.reconstruction import reconstruit_mutations_grain
from app.scope import load_scope_sync, replace_scope

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
_URL = re.compile(r"/(?P<year>\d{4})/departements/(?P<dept>[0-9AB]+)\.csv\.gz$")

# Coordinates of the geocoded lines: the expected position of a mutation is
# the centroid of its geocoded lines only.
_POINTS = {
    "44SYN0000A0001": (-1.5000, 47.2000),
    "44SYN0000A0003": (-1.5020, 47.2030),
    "44SYN0000A0004": (-1.5040, 47.2060),
    "44SYN0000A0005": (-1.6000, 47.3000),
    "44SYN0000A0008": (-1.7000, 47.4000),
}


def _parcelle(n: int) -> str:
    """A 14-character parcel id whose prefix is the departement (partition)."""
    return f"44SYN0000A{n:04d}"


def _ligne(id_mutation: str, parcelle: str, **champs: str) -> dict[str, str]:
    row = dict.fromkeys(COLUMNS_TO_KEEP, "")
    row.update(
        id_mutation=id_mutation,
        date_mutation="2021-06-15",
        numero_disposition="000002",
        nature_mutation="Vente",
        valeur_fonciere="262000",
        adresse_numero="1",
        adresse_nom_voie="RUE SYNTHETIQUE",
        code_postal="44000",
        code_commune="44999",
        nom_commune="SYN-COMMUNE",
        id_parcelle=parcelle,
        nombre_lots="0",
    )
    if parcelle in _POINTS:
        lon, lat = _POINTS[parcelle]
        row.update(longitude=str(lon), latitude=str(lat))
    row.update(champs)
    return row


_MAISON = {
    "code_type_local": "1",
    "type_local": "Maison",
    "surface_reelle_bati": "131",
    "nombre_pieces_principales": "5",
}

_LIGNES_2021_44 = [
    # Shape of 2021-748011: parcel 2 has no coordinates.
    _ligne("SYN-2021-000001", _parcelle(1), surface_terrain="675"),
    _ligne("SYN-2021-000001", _parcelle(2), surface_terrain="2721", **_MAISON),
    _ligne("SYN-2021-000001", _parcelle(3), surface_terrain="1714", **_MAISON),
    _ligne("SYN-2021-000001", _parcelle(4), surface_terrain="1009"),
    # A distinct local lost with the line without coordinates (parcel 6).
    _ligne(
        "SYN-2021-000002",
        _parcelle(5),
        valeur_fonciere="180000",
        code_type_local="1",
        type_local="Maison",
        surface_reelle_bati="90",
        nombre_pieces_principales="4",
    ),
    _ligne(
        "SYN-2021-000002",
        _parcelle(6),
        valeur_fonciere="180000",
        code_type_local="1",
        type_local="Maison",
        surface_reelle_bati="60",
        nombre_pieces_principales="3",
    ),
    # No geocoded line at all.
    _ligne(
        "SYN-2021-000003",
        _parcelle(7),
        valeur_fonciere="150000",
        code_type_local="2",
        type_local="Appartement",
        surface_reelle_bati="50",
        nombre_pieces_principales="2",
    ),
    # Variant V: the same house on the S and J culture lines of parcel 8.
    _ligne(
        "SYN-2021-000004",
        _parcelle(8),
        valeur_fonciere="192000",
        code_nature_culture="S",
        nature_culture="sols",
        surface_terrain="412",
        **_MAISON,
    ),
    _ligne(
        "SYN-2021-000004",
        _parcelle(8),
        valeur_fonciere="192000",
        code_nature_culture="J",
        nature_culture="jardins",
        surface_terrain="230",
        **_MAISON,
    ),
]


async def _fake_download(client, url, output_path):
    """Writes the fixture for 2021/44, an empty valid file for other years."""
    match = _URL.search(url)
    assert match, url
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS_TO_KEEP)
        writer.writeheader()
        if (match["year"], match["dept"]) == ("2021", "44"):
            writer.writerows(_LIGNES_2021_44)
    return True


def _scope_loader(dsn):
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        return load_scope_sync(conn)


@pytest.fixture(scope="module")
def importee(isolated_pg_dsn, tmp_path_factory):
    """Migrated base, DVF scope 44, fixture file imported through the runner."""
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        context.setattr(settings, "etl_temp_dir", str(tmp_path_factory.mktemp("etl")))
        command.upgrade(cfg, "head")
        with psycopg.connect(isolated_pg_dsn, connect_timeout=5) as conn:
            replace_scope(conn, ["dvf"], ["44"])
        # A handful of fixture lines: the scaled floor is covered elsewhere.
        context.setattr(DvfPipeline, "min_staging_rows", property(lambda _: 1))
        context.setattr("app.etl_dvf.pipeline.verified_download_gz", _fake_download)
        runner = PipelineRunner(
            [ValidationHook(), DependentMatviewHook()], scope_loader=_scope_loader
        )
        result = runner.run(DvfPipeline())
        assert result.success, result.error
        yield isolated_pg_dsn


def _grain(dsn: str, id_mutation: str) -> dict | None:
    with psycopg.connect(dsn) as conn:
        cur = conn.execute(
            "SELECT classe_mutation, nb_lignes, nb_parcelles, nb_locaux_maison, "
            "surface_bati_totale, nombre_pieces_totales, mono_local, "
            "ST_X(position) AS lon, ST_Y(position) AS lat "
            "FROM mutations_grain WHERE id_mutation = %s",
            (id_mutation,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip([c.name for c in cur.description], row, strict=True))


async def _servi(dsn: str, id_parcelle: str) -> list[dict]:
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with async_sessionmaker(engine)() as session:
            return await dvf_par_id_parcelle(session, id_parcelle)
    finally:
        await engine.dispose()


def test_every_source_line_is_loaded_lines_without_coordinates_without_position(
    importee,
):
    with psycopg.connect(importee) as conn:
        rows = conn.execute(
            "SELECT id_mutation, count(*), count(position) FROM mutations "
            "GROUP BY id_mutation ORDER BY id_mutation"
        ).fetchall()
    assert rows == [
        ("SYN-2021-000001", 4, 3),
        ("SYN-2021-000002", 2, 1),
        ("SYN-2021-000003", 1, 0),
        ("SYN-2021-000004", 2, 2),
    ]


async def test_house_on_two_parcels_one_without_coordinates_stays_one_house(
    importee,
):
    """Shape of 2021-748011: 4 lines, one house, priced on its own surface."""
    grain = _grain(importee, "SYN-2021-000001")

    assert grain is not None
    assert {k: grain[k] for k in grain if k not in ("lon", "lat")} == {
        "classe_mutation": "residentiel_maison",
        "nb_lignes": 4,
        "nb_parcelles": 4,
        "nb_locaux_maison": 1,
        "surface_bati_totale": 131,
        "nombre_pieces_totales": 5,
        "mono_local": True,
    }
    # Centroid of the three geocoded lines; the line without coordinates is
    # ignored by ST_Collect, never placed at (0, 0).
    geocodees = [_POINTS[_parcelle(n)] for n in (1, 3, 4)]
    assert grain["lon"] == pytest.approx(sum(p[0] for p in geocodees) / 3)
    assert grain["lat"] == pytest.approx(sum(p[1] for p in geocodees) / 3)

    (servie,) = await _servi(importee, _parcelle(3))
    assert servie["classe_mutation"] == "residentiel_maison"
    assert servie["nb_locaux_maison"] == 1
    assert servie["prix_m2"] == round(262000 / 131)
    assert servie["prix_m2_raison"] is None
    # The parcel without coordinates serves the same mutation.
    assert [v["id_mutation"] for v in await _servi(importee, _parcelle(2))] == [
        "SYN-2021-000001"
    ]


async def test_distinct_local_on_the_parcel_without_coordinates_is_counted(
    importee,
):
    grain = _grain(importee, "SYN-2021-000002")

    assert grain is not None
    assert grain["classe_mutation"] == "ensemble_residentiel"
    assert (grain["nb_lignes"], grain["nb_locaux_maison"]) == (2, 2)
    assert grain["surface_bati_totale"] == 150
    (servie,) = await _servi(importee, _parcelle(5))
    assert servie["prix_m2"] is None
    assert servie["prix_m2_raison"] == "mutation_multi_locaux"


async def test_house_repeated_on_its_culture_lines_stays_one_house(importee):
    """Variant V of D-02: ``locaux_culture`` is aligned with the other local
    arrays, so the house repeated on the S and J lines of its parcel is one
    house, priced on its own surface."""
    grain = _grain(importee, "SYN-2021-000004")

    assert grain is not None
    assert {
        k: grain[k]
        for k in (
            "classe_mutation",
            "nb_lignes",
            "nb_parcelles",
            "nb_locaux_maison",
            "surface_bati_totale",
            "nombre_pieces_totales",
            "mono_local",
        )
    } == {
        "classe_mutation": "residentiel_maison",
        "nb_lignes": 2,
        "nb_parcelles": 1,
        "nb_locaux_maison": 1,
        "surface_bati_totale": 131,
        "nombre_pieces_totales": 5,
        "mono_local": True,
    }
    (servie,) = await _servi(importee, _parcelle(8))
    assert servie["classe_mutation"] == "residentiel_maison"
    assert servie["prix_m2"] == round(192000 / 131)
    assert servie["prix_m2_raison"] is None


def test_mutation_without_any_geocoded_line_is_reconstructed_without_position(
    importee,
):
    grain = _grain(importee, "SYN-2021-000003")

    assert grain is not None
    assert grain["classe_mutation"] == "residentiel_appartement"
    assert grain["nb_lignes"] == 1
    assert (grain["lon"], grain["lat"]) == (None, None)


# -- D-04: the address of a multi-commune mutation is that of its commune ------

# Two synthetic communes: C1 is the retained one, min(code_commune).
_C1, _C2 = "44991", "44998"

_ADRESSE_SQL = (
    "SELECT code_commune, nom_commune, adresse_numero, adresse_suffixe, "
    "adresse_nom_voie, code_postal, ST_X(position), ST_Y(position) "
    "FROM mutations_grain WHERE id_mutation = %s"
)


def _ligne_id(conn, id_, id_mutation, commune, numero, suffixe, point, **champs):
    """One ``mutations`` line with an explicit ``id``: the order of a COPY is
    not deterministic, an explicit id makes the address pick (and the red)
    reproducible."""
    valeurs = {
        "id": id_,
        "code_departement": "44",
        "id_mutation": id_mutation,
        "date_mutation": "2021-09-01",
        "nature_mutation": "Vente",
        "valeur_fonciere": 300000,
        "numero_disposition": "000001",
        "code_commune": commune,
        "nom_commune": None if commune is None else f"SYN-COMMUNE-{commune}",
        "code_postal": None if commune is None else f"SYN{commune[-2:]}",
        "adresse_numero": numero,
        "adresse_suffixe": suffixe,
        "adresse_nom_voie": f"SYN-VOIE-{numero}",
        "id_parcelle": _parcelle(id_ % 10_000),
        **champs,
    }
    colonnes = ", ".join(valeurs)
    marques = ", ".join(["%s"] * len(valeurs))
    position = (
        "NULL"
        if point is None
        else f"ST_SetSRID(ST_MakePoint({point[0]}, {point[1]}), 4326)"
    )
    conn.execute(
        f"INSERT INTO mutations ({colonnes}, position) VALUES ({marques}, {position})",
        list(valeurs.values()),
    )


@pytest.fixture(scope="module")
def multi_communes(importee):
    """Shapes read on the archive (run 48), D-31 identifiers:

    - ``SYN-MC-1`` copies source mutation ``2021-767870``: its line of lowest
      id (a house) lies in the commune of HIGHER code, the three others (two
      dependances, an appartement) in the retained commune.
    - ``SYN-MC-2``: the first line of the retained commune has no
      coordinates; it still carries the address, never the position.
    - ``SYN-MC-3``: no line carries a commune code.
    """
    maison = {"code_type_local": "1", "surface_reelle_bati": 76}
    dep = {"code_type_local": "3", "nombre_pieces_principales": 0}
    appart = {"code_type_local": "2", "surface_reelle_bati": 51}
    with psycopg.connect(importee) as conn:
        _ligne_id(conn, 1_000_010, "SYN-MC-1", _C2, "9", None, (-1.55, 47.25), **maison)
        _ligne_id(conn, 1_000_011, "SYN-MC-1", _C1, "3", "B", (-1.56, 47.24), **dep)
        _ligne_id(conn, 1_000_012, "SYN-MC-1", _C1, "5", None, (-1.56, 47.24), **dep)
        _ligne_id(conn, 1_000_013, "SYN-MC-1", _C1, "7", None, (-1.56, 47.24), **appart)
        _ligne_id(conn, 1_000_020, "SYN-MC-2", _C2, "2", None, (-1.50, 47.20), **maison)
        _ligne_id(conn, 1_000_021, "SYN-MC-2", _C1, "4", "T", None, **appart)
        _ligne_id(conn, 1_000_022, "SYN-MC-2", _C1, "6", None, (-1.52, 47.22), **dep)
        _ligne_id(
            conn, 1_000_031, "SYN-MC-3", None, "8", None, (-1.40, 47.10), **maison
        )
        _ligne_id(conn, 1_000_030, "SYN-MC-3", None, "1", None, (-1.40, 47.10), **dep)
    reconstruit_mutations_grain(importee, ("44",))
    return importee


def _adresse(dsn, id_mutation):
    with psycopg.connect(dsn) as conn:
        return conn.execute(_ADRESSE_SQL, (id_mutation,)).fetchone()


def test_multi_commune_address_is_the_first_line_of_the_retained_commune(
    multi_communes,
):
    """Shape of 2021-767870 (D-04): commune, name, number, suffix, street and
    postal code all come from the first line (id) of min(code_commune)."""
    row = _adresse(multi_communes, "SYN-MC-1")

    assert row[:6] == (_C1, f"SYN-COMMUNE-{_C1}", "3", "B", "SYN-VOIE-3", "SYN91")


def test_a_line_without_coordinates_may_carry_the_address_not_the_position(
    multi_communes,
):
    row = _adresse(multi_communes, "SYN-MC-2")

    assert row[:6] == (_C1, f"SYN-COMMUNE-{_C1}", "4", "T", "SYN-VOIE-4", "SYN91")
    # Centroid of the two geocoded lines, both communes (D-01).
    assert row[6] == pytest.approx(-1.51)
    assert row[7] == pytest.approx(47.21)


def test_without_any_commune_code_the_address_is_the_lowest_id_line(
    multi_communes,
):
    row = _adresse(multi_communes, "SYN-MC-3")

    assert row[:6] == (None, None, "1", None, "SYN-VOIE-1", None)
