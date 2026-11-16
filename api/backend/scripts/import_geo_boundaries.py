#!/usr/bin/env python3
"""Import the geographic referentials into PostGIS.

Tables (migration 024 and 099):
  - geo_departements: departement polygons, metropolitan + DROM by union of
    the communes (see below);
  - geo_communes: communes of every departement of France, as listed by
    their producer (geo.api.gouv.fr, ``DEPARTEMENTS_LIST_URL``);
  - geo_sections: cadastral sections of the scope departements
    (cadastre.data.gouv.fr, ``latest``);
  - geo_arrondissements: municipal arrondissements of Paris, Lyon and
    Marseille, for the scope departements that have them (75, 69, 13).

Communes, France entière (R1, route A, decided by Ianis on 2026-09-26, plan
03-14; amends 02-11 / DATA-01): a commune outside the hosted scope stays
*known* -- search proposes it, ``/v1/communes?code_insee=`` names it,
``/v1/commune`` and the comparison serve it out of coverage -- and
"unknown" is kept for codes that do not exist. Coverage never comes from
membership of geo_communes: every reader decides it from the scope in base
(``app.scope``). The national list is the producer's own list of its
departements (101 today: 01-95 with 2A/2B, 971-974 and 976); a collectivity
code missing from that list (975, 977, 978) stays outside the referential.
This is the second exception to the scope bound, after the departement
polygons.

Scope (plan 02-11, DATA-01): sections and arrondissements are fetched for
the departements of the persistent instance scope only
(``app.scope.store.load_scope_sync``). ``--departements`` narrows a run to
the named codes, validated against the producer's list: communes of those
codes, sections and arrondissements of their intersection with the scope;
only their rows are replaced. Departement polygons are national: the 96
metropolitan polygons of ``DEPARTEMENTS_URL``, plus each DROM of the
producer's list (971-974, 976; that file has none) built as
``ST_Multi(ST_Union(...))`` of its communes, in the same transaction as the
communes (D-122, plan 04.1-06). The transports import attributes stops and
stations by polygon (``TransportPipeline.check_scope_prerequisites``, 02-05
m3), the BD TOPO bounds gate and the departement of a point read them: they
must cover every scope departement, DROM included.

Fail closed: an empty list, a list without a scope departement, or a
download that fails or comes back empty (communes of any listed
departement, sections or arrondissements of a scope one) stops the script
(exit 1, departement and kind named) before any insertion; after
insertion, ``SELECT departement_code, count(*)`` must name every expected
departement with a positive count -- geo_communes every listed (or
requested) code, geo_sections every scope one, geo_arrondissements every
scope PLM, geo_departements a non-empty polygon for every DROM whose
communes were replaced -- else the transaction is rolled back (exit 1). Communes,
sections and arrondissements are replaced as a whole in one transaction.

Source archive (OPS-03 amended, plan 02-14): with ``SOURCE_ARCHIVE_DIR`` and
``SOURCE_ARCHIVE_RUN_ID`` set, the raw responses (bytes as served, the list
as ``departements-liste.json``, sections still gzip-compressed) are archived
under dataset ``geo_boundaries`` before the first insertion; a refused
archive exits 1 without inserting. Each source file is printed as
``SOURCE_FILE <name> <bytes> <sha256>`` for the import receipt. Without these
variables nothing is archived.

Usage (from api/backend, DATABASE_URL set, asyncpg or psycopg form)::

    python -m scripts.import_geo_boundaries [--departements 44,85] [--skip-sections]

Exit codes: 0 imported; 1 download, archive or final count refused; 2 usage
(empty scope, departement unknown to the producer's list, DATABASE_URL
missing).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import psycopg

DEPARTEMENTS_URL = (
    "https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/"
    "departements.geojson"
)
# The producer of the communes lists its own departements: this list, not
# app.etl.base.DEPARTEMENTS, is the national extent of geo_communes (R1).
DEPARTEMENTS_LIST_URL = "https://geo.api.gouv.fr/departements?fields=code,nom"
DEPARTEMENTS_LIST_NAME = "departements-liste.json"
COMMUNES_URL = (
    "https://geo.api.gouv.fr/departements/{dept}/communes"
    "?fields=nom,code,population,codeDepartement&format=geojson&geometry=contour"
)
ARRONDISSEMENTS_URL = (
    "https://geo.api.gouv.fr/communes?type=arrondissement-municipal"
    "&codeDepartement={dept}&fields=nom,code,population,codeDepartement,codeParent"
    "&format=geojson&geometry=contour"
)
# `latest` redirects (302) to the dated folder: redirects must be followed.
SECTIONS_URL = (
    "https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/geojson/"
    "departements/{dept}/cadastre-{dept}-sections.json.gz"
)
# Departements carrying municipal arrondissements -> their parent commune.
PLM_DEPARTEMENTS = {"13": "13055", "69": "69123", "75": "75056"}
# Overseas departements (DROM, D-122): absent from DEPARTEMENTS_URL, their
# polygon is the union of their communes. 975 is a collectivity (D-123).
DROM_DEPARTEMENTS = ("971", "972", "973", "974", "976")

# Archive dataset names are [a-z][a-z0-9_]* (app.etl.source_archive).
ARCHIVE_DATASET = "geo_boundaries"

EXIT_FAILED = 1
EXIT_USAGE = 2


class BoundaryImportError(RuntimeError):
    """A refusal of the import, with its exit code (1 failed, 2 usage)."""

    def __init__(self, message: str, exit_code: int = EXIT_FAILED) -> None:
        self.exit_code = exit_code
        super().__init__(message)


@dataclass(frozen=True)
class Response:
    """One raw source response: its archive name, bytes as served, features."""

    name: str
    content: bytes
    features: list[dict[str, Any]]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_multi(geom: dict) -> dict:
    """Convert Polygon to MultiPolygon if needed."""
    if geom["type"] == "Polygon":
        return {"type": "MultiPolygon", "coordinates": [geom["coordinates"]]}
    return geom


# ── Downloads ─────────────────────────────────────────────────────────


def fetch(client: Any, url: str, name: str, *, gz: bool = False) -> Response:
    """GET *url*; raise on any non-success status (a 404 is a failure).

    A FeatureCollection gives its features; a JSON array (the departement
    list) gives one feature per entry, the entry as its properties.
    """
    resp = client.get(url, timeout=120)
    resp.raise_for_status()
    content = resp.content
    data = json.loads(gzip.decompress(content) if gz else content)
    if isinstance(data, list):
        features = [
            {"type": "Feature", "properties": entry, "geometry": None} for entry in data
        ]
    else:
        features = list(data.get("features") or [])
    return Response(name, content, features)


@dataclass(frozen=True)
class Downloads:
    """Every source response of a run, and the departements they cover.

    ``communes``: codes whose communes are replaced (the national list, or
    the requested codes); ``scoped``: scope departements whose sections and
    arrondissements are replaced.
    """

    listing: Response
    polygons: Response
    responses: list[Response]
    communes: list[str]
    scoped: list[str]


def download_departements_list(
    client: Any, scope: list[str]
) -> tuple[Response, list[str]]:
    """The producer's departement list; refused empty or without a scope one."""
    log(f"Downloading the producer's departement list from {DEPARTEMENTS_LIST_URL}")
    try:
        listing = fetch(client, DEPARTEMENTS_LIST_URL, DEPARTEMENTS_LIST_NAME)
    except Exception as exc:
        raise BoundaryImportError(
            f"liste des départements: téléchargement en échec "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    codes = [str(f["properties"].get("code") or "") for f in listing.features]
    codes = [code for code in codes if code]
    if not codes:
        raise BoundaryImportError("liste des départements vide")
    missing = [d for d in scope if d not in set(codes)]
    if missing:
        raise BoundaryImportError(
            "liste des départements sans les départements du périmètre: "
            + ",".join(missing)
        )
    log(f"  -> {len(codes)} departements listed")
    return listing, codes


def _download_departement(
    client: Any, dept: str, *, sections: bool, arrondissements: bool
) -> list[Response]:
    """Communes of one departement, plus its sections and arrondissements when
    asked; every response non-empty."""
    wanted = [(COMMUNES_URL, f"communes-{dept}.geojson", False, "communes")]
    if sections:
        wanted.append((SECTIONS_URL, f"sections-{dept}.json.gz", True, "sections"))
    if arrondissements and dept in PLM_DEPARTEMENTS:
        wanted.append(
            (
                ARRONDISSEMENTS_URL,
                f"arrondissements-{dept}.geojson",
                False,
                "arrondissements",
            )
        )
    responses = []
    for template, name, gz, kind in wanted:
        try:
            response = fetch(client, template.format(dept=dept), name, gz=gz)
        except Exception as exc:
            raise BoundaryImportError(
                f"département {dept}: téléchargement des {kind} en échec "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        if not response.features:
            raise BoundaryImportError(f"département {dept}: {kind} vides")
        responses.append(response)
    return responses


def download_all(
    client: Any,
    scope: list[str],
    *,
    only: list[str] | None = None,
    skip_sections: bool,
) -> Downloads:
    """Every source response; refuses before any insertion.

    The list comes first: ``only`` is validated against it (exit 2) before
    any other request.
    """
    listing, national = download_departements_list(client, scope)
    if only:
        unknown = sorted(set(only) - set(national))
        if unknown:
            raise BoundaryImportError(
                "départements inconnus de la liste nationale: " + ",".join(unknown),
                EXIT_USAGE,
            )
        communes = [d for d in national if d in set(only)]
        scoped = [d for d in scope if d in set(only)]
    else:
        communes, scoped = national, list(scope)
    polygons = download_polygons(client, scope)
    responses: list[Response] = []
    total = 0
    for dept in communes:
        in_scope = dept in scoped
        got = _download_departement(
            client,
            dept,
            sections=in_scope and not skip_sections,
            arrondissements=in_scope,
        )
        total += len(got[0].features)
        log(f"  {dept}: " + ", ".join(f"{r.name} {len(r.features)}" for r in got))
        responses.extend(got)
    log(f"  communes: {total} in {len(communes)} departements")
    return Downloads(listing, polygons, responses, communes, scoped)


def download_polygons(client: Any, scope: list[str]) -> Response:
    """Metropolitan departement polygons, covering every metropolitan scope
    departement (a DROM polygon is built from its communes)."""
    log(f"Downloading departement polygons from {DEPARTEMENTS_URL}")
    try:
        polygons = fetch(client, DEPARTEMENTS_URL, "departements.geojson")
    except Exception as exc:
        raise BoundaryImportError(
            f"polygones départementaux: téléchargement en échec "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    codes = {f["properties"].get("code") for f in polygons.features}
    missing = [d for d in scope if d not in codes and d not in DROM_DEPARTEMENTS]
    if missing:
        raise BoundaryImportError(
            "polygones départementaux sans les départements du périmètre: "
            + ",".join(missing)
        )
    log(
        f"  -> {len(polygons.features)} departements "
        "(metropolitan, kept for transports)"
    )
    return polygons


# ── Source archive ────────────────────────────────────────────────────


def source_file_lines(responses: list[Response]) -> list[str]:
    return [
        f"SOURCE_FILE {r.name} {len(r.content)} {hashlib.sha256(r.content).hexdigest()}"
        for r in responses
    ]


def archive_responses(responses: list[Response]) -> dict[str, Any] | None:
    """Archive the raw responses when the archive variables are set."""
    root = os.environ.get("SOURCE_ARCHIVE_DIR", "")
    run_id = os.environ.get("SOURCE_ARCHIVE_RUN_ID", "")
    if not root and not run_id:
        return None
    if not root or not run_id:
        raise BoundaryImportError(
            "SOURCE_ARCHIVE_DIR et SOURCE_ARCHIVE_RUN_ID vont ensemble"
        )
    from app.etl.source_archive import SourceArchiveError, archive_sources

    try:
        min_free = int(os.environ.get("SOURCE_ARCHIVE_MIN_FREE_BYTES") or 0)
    except ValueError:
        raise BoundaryImportError("SOURCE_ARCHIVE_MIN_FREE_BYTES invalide") from None
    with tempfile.TemporaryDirectory(prefix="geo-boundaries-") as tmp:
        files = []
        for response in responses:
            path = Path(tmp) / response.name
            path.write_bytes(response.content)
            files.append(path)
        try:
            record = archive_sources(
                root,
                ARCHIVE_DATASET,
                run_id,
                files,
                tmp,
                "telechargement",
                deployment_sha=os.environ.get("DEPLOYMENT_SHA") or None,
                min_free_bytes=min_free,
            )
        except SourceArchiveError as exc:
            raise BoundaryImportError(
                f"archive des sources refusée: {exc.reason} {exc.path}"
            ) from exc
    log(
        f"Archived {len(record['files'])} source files: "
        f"{ARCHIVE_DATASET}/runs/{run_id}.json"
    )
    return record


# ── Insertion ─────────────────────────────────────────────────────────


def insert_departements(conn: Any, features: list[dict]) -> int:
    with conn.cursor() as cur:
        for feature in features:
            props = feature["properties"]
            cur.execute(
                """
                INSERT INTO geo_departements (code, nom, geometry)
                VALUES (%s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
                ON CONFLICT (code) DO UPDATE SET
                    nom = EXCLUDED.nom,
                    geometry = EXCLUDED.geometry
                """,
                (
                    props.get("code", ""),
                    props.get("nom", ""),
                    json.dumps(ensure_multi(feature["geometry"])),
                ),
            )
    return len(features)


def insert_drom_polygons(conn: Any, listing: Response, droms: list[str]) -> None:
    """Insert or replace each DROM polygon by the union of its communes.

    Reads geo_communes in the current transaction (the communes just
    inserted); no row is written for a DROM without communes, which
    ``check_drom_polygons`` then refuses. ``ST_MakeValid`` and the polygon
    extraction keep the column type (MultiPolygon) on imperfect contours.
    """
    names = {
        str(f["properties"].get("code") or ""): str(f["properties"].get("nom") or "")
        for f in listing.features
    }
    with conn.cursor() as cur:
        for dept in droms:
            cur.execute(
                """
                INSERT INTO geo_departements (code, nom, geometry)
                SELECT %s, %s,
                       ST_Multi(ST_CollectionExtract(
                           ST_Union(ST_MakeValid(geometry)), 3))
                FROM geo_communes
                WHERE departement_code = %s
                HAVING count(*) > 0
                ON CONFLICT (code) DO UPDATE SET
                    nom = EXCLUDED.nom,
                    geometry = EXCLUDED.geometry
                """,
                (dept, names.get(dept, ""), dept),
            )


def check_drom_polygons(conn: Any, droms: list[str]) -> list[str]:
    """Every DROM of *droms* has a non-empty polygon, else refuse."""
    if not droms:
        return []
    rows = conn.execute(
        "SELECT code FROM geo_departements "
        "WHERE code = ANY(%s) AND NOT ST_IsEmpty(geometry)",
        (list(droms),),
    ).fetchall()
    present = {str(code) for (code,) in rows}
    missing = [dept for dept in droms if dept not in present]
    if missing:
        raise BoundaryImportError(
            "polygones DROM absents après insertion (aucune commune): "
            + ",".join(missing)
        )
    return list(droms)


def insert_communes(conn: Any, features: list[dict], dept_code: str) -> int:
    with conn.cursor() as cur:
        for feature in features:
            props = feature["properties"]
            cur.execute(
                """
                INSERT INTO geo_communes
                    (code, nom, departement_code, population, geometry)
                VALUES (%s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
                """,
                (
                    props.get("code", ""),
                    props.get("nom", ""),
                    props.get("codeDepartement", dept_code),
                    props.get("population"),
                    json.dumps(ensure_multi(feature["geometry"])),
                ),
            )
    return len(features)


def insert_arrondissements(conn: Any, features: list[dict], dept_code: str) -> int:
    with conn.cursor() as cur:
        for feature in features:
            props = feature["properties"]
            cur.execute(
                """
                INSERT INTO geo_arrondissements
                    (code, nom, departement_code, commune_code, population, geometry)
                VALUES (%s, %s, %s, %s, %s,
                        ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
                """,
                (
                    props.get("code", ""),
                    props.get("nom", ""),
                    props.get("codeDepartement", dept_code),
                    props.get("codeParent") or PLM_DEPARTEMENTS[dept_code],
                    props.get("population"),
                    json.dumps(ensure_multi(feature["geometry"])),
                ),
            )
    return len(features)


def insert_sections(conn: Any, features: list[dict], dept_code: str) -> int:
    with conn.cursor() as cur:
        for feature in features:
            props = feature["properties"]
            commune_code = props.get("commune", "")
            prefixe = props.get("prefixe", "000")
            section = props.get("code", "")
            cur.execute(
                """
                INSERT INTO geo_sections
                    (id, code, commune_code, departement_code, geometry)
                VALUES (%s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
                ON CONFLICT (id) DO UPDATE SET
                    code = EXCLUDED.code,
                    commune_code = EXCLUDED.commune_code,
                    departement_code = EXCLUDED.departement_code,
                    geometry = EXCLUDED.geometry
                """,
                (
                    props.get("id", f"{commune_code}{prefixe}{section}"),
                    section,
                    commune_code,
                    dept_code,
                    json.dumps(ensure_multi(feature["geometry"])),
                ),
            )
    return len(features)


def departement_counts(conn: Any, table: str) -> dict[str, int]:
    """Rows per departement of *table* (a code constant, never user input)."""
    rows = conn.execute(
        f"SELECT departement_code, count(*) FROM {table} GROUP BY 1"
    ).fetchall()
    return {str(code): int(n) for code, n in rows}


def check_counts(
    conn: Any,
    communes: list[str],
    scoped: list[str],
    *,
    skip_sections: bool,
) -> dict[str, dict[str, int]]:
    """Every expected departement present with a positive count, else refuse.

    geo_communes: every code of *communes* (national list or requested
    codes); geo_sections: every *scoped* departement; geo_arrondissements:
    every scoped PLM departement.
    """
    expected = {"geo_communes": communes}
    if not skip_sections and scoped:
        expected["geo_sections"] = scoped
    plm = [d for d in scoped if d in PLM_DEPARTEMENTS]
    if plm:
        expected["geo_arrondissements"] = plm
    counts = {table: departement_counts(conn, table) for table in expected}
    problems = [
        f"{table}:{dept}"
        for table, departements in expected.items()
        for dept in departements
        if counts[table].get(dept, 0) <= 0
    ]
    if problems:
        raise BoundaryImportError(
            "départements attendus absents ou vides après insertion: "
            + ", ".join(problems)
        )
    return counts


def backfill_and_refresh_commune_synthese(conn: psycopg.Connection) -> None:
    """D-07..D-09 superficie/densite backfill then v_commune_synthese rebuild.

    Same transaction as the insertion (this script is D-08's geo_communes
    trigger; app/etl/hooks.py is the demographie one).
    """
    from app.commune.matview_ddl import recreate_commune_synthese
    from app.demographie.backfill import (
        BackfillPreconditionError,
        backfill_superficie_densite,
    )

    try:
        counters = backfill_superficie_densite(conn)
    except BackfillPreconditionError as exc:
        raise BoundaryImportError(
            f"preconditions du backfill non satisfaites: {exc}"
        ) from exc
    log(
        "Backfill superficie/densite : "
        f"{counters.rows_updated}/{counters.rows_examined} lignes maj "
        f"({counters.geometry_missing} sans geometrie admin, "
        f"{counters.geometry_invalid_or_empty} invalide/vide, "
        f"{counters.population_missing} sans population)"
    )
    recreate_commune_synthese(conn)
    log("v_commune_synthese reconstruite apres backfill")


def publish(
    conn: Any,
    downloads: Downloads,
    *,
    only: list[str] | None = None,
    skip_sections: bool,
) -> dict[str, dict[str, int]]:
    """Replace the referentials in the connection's transaction; no commit.

    A restricted run (``--departements``) replaces only the requested
    departements: the others keep their rows.
    """
    n = insert_departements(conn, downloads.polygons.features)
    log(f"  geo_departements: {n} upserted (metropolitan)")
    tables = ["geo_communes", "geo_arrondissements"]
    if not skip_sections:
        tables.append("geo_sections")
    for table in tables:
        if only:
            conn.execute(
                f"DELETE FROM {table} WHERE departement_code = ANY(%s)",
                (list(only),),
            )
        else:
            conn.execute(f"DELETE FROM {table}")
    for response in downloads.responses:
        kind, dept = response.name.split(".", 1)[0].split("-", 1)
        insert = {
            "communes": insert_communes,
            "sections": insert_sections,
            "arrondissements": insert_arrondissements,
        }[kind]
        n = insert(conn, response.features, dept)
        log(f"  {kind} {dept}: {n} inserted")
    droms = [d for d in downloads.communes if d in DROM_DEPARTEMENTS]
    insert_drom_polygons(conn, downloads.listing, droms)
    counts = check_counts(
        conn, downloads.communes, downloads.scoped, skip_sections=skip_sections
    )
    built = check_drom_polygons(conn, droms)
    if built:
        log(f"  geo_departements: DROM {','.join(built)} built from their communes")
    backfill_and_refresh_commune_synthese(conn)
    return counts


# ── Main ──────────────────────────────────────────────────────────────


def scope_departements(conn: Any) -> list[str]:
    """Departements of the persistent scope; sections depend on them."""
    from app.scope.store import load_scope_sync

    scope = load_scope_sync(conn)
    if scope.is_empty:
        raise BoundaryImportError(
            "périmètre vide: aucun département hébergé (python -m app.scope set)",
            EXIT_USAGE,
        )
    return list(scope.departements)


def import_boundaries(
    conn: Any,
    client: Any,
    *,
    only: list[str] | None = None,
    skip_sections: bool = False,
) -> dict[str, dict[str, int]]:
    """Download, archive, then replace the referentials."""
    scope = scope_departements(conn)
    log(f"Scope departements: {','.join(scope)}")
    downloads = download_all(client, scope, only=only, skip_sections=skip_sections)
    everything = [downloads.listing, downloads.polygons, *downloads.responses]
    for line in source_file_lines(everything):
        print(line, flush=True)
    archive_responses(everything)
    try:
        counts = publish(conn, downloads, only=only, skip_sections=skip_sections)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    for table, by_dept in counts.items():
        log(f"  {table}: " + ", ".join(f"{d}={n}" for d, n in sorted(by_dept.items())))
    total = sum(counts["geo_communes"].values())
    log(f"  geo_communes total: {total} in {len(counts['geo_communes'])} departements")
    return counts


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.import_geo_boundaries",
        description="Import the geographic referentials (communes national, "
        "sections and arrondissements of the hosted scope)",
    )
    parser.add_argument(
        "--departements",
        help="comma-separated departements of the producer's list: their "
        "communes, and the sections and arrondissements of those in the scope "
        "(default: all)",
    )
    parser.add_argument(
        "--skip-sections",
        action="store_true",
        help="skip geo_sections (communes and polygons only)",
    )
    return parser.parse_args(argv)


def main_with(conn: Any, client: Any, argv: list[str]) -> None:
    args = _parse(argv)
    only = (
        [d.strip() for d in args.departements.split(",") if d.strip()]
        if args.departements
        else None
    )
    start = time.time()
    try:
        import_boundaries(conn, client, only=only, skip_sections=args.skip_sections)
    except BoundaryImportError as exc:
        print(f"GEO_BOUNDARIES_FAILED {exc}", flush=True)
        sys.exit(exc.exit_code)
    log(f"GEO_BOUNDARIES_OK in {time.time() - start:.0f}s")


def main(argv: list[str] | None = None) -> None:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    with (
        psycopg.connect(dsn) as conn,
        httpx.Client(follow_redirects=True) as client,
    ):
        main_with(conn, client, sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    main()
