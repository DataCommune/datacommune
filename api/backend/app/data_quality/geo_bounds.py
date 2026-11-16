"""Generic spatial-bounds audit: do a dataset's geometries fall in the
department they claim to belong to?

This is the parameterized, per-department generalization of the ad-hoc France
bounding-box check in checkers/dvf.py (_check_geom_bbox) and the ST_IsValid
check in checkers/batiments.py. One implementation, N config entries — never
one copy per dataset.

Semantics (per-row department consistency, meaningful in prod and dev):
each row declares a department via `dept_expr`; we verify its geometry is
covered by that department's polygon in geo_departements. The Equans-class
"coordinates loaded in Lambert-93 (EPSG:2154) but stored as EPSG:4326" bug
puts every point far outside France, so out_of_dept approaches 100% — it is
the positive control this detector must flag.

Usage:
    python -m app.data_quality.geo_bounds                  # all registered datasets
    python -m app.data_quality.geo_bounds equipements      # one dataset
    python -m app.data_quality.geo_bounds -o /tmp/geo      # custom output dir
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

from app.config import settings
from app.data_quality.base import AuditResult, Finding
from app.data_quality.report import write_report, write_summary

# Reference table: 1 MultiPolygon(4326) per department, populated by
# scripts/import_geo_boundaries.py. Departments are matched on `code`.
REFERENCE_TABLE = "geo_departements"

# French department code from a 5-char INSEE commune code. Corsica codes
# already start with "2A"/"2B"; overseas codes start with 97/98 and need 3
# chars. Everything else is the first 2 chars.
INSEE_TO_DEPT = (
    "CASE WHEN left({col}, 2) IN ('97', '98') "
    "THEN left({col}, 3) ELSE left({col}, 2) END"
)

# Above this share of geometries outside their declared department, raise an
# ERROR — a reprojection/CRS bug rather than a handful of boundary outliers.
OUT_OF_DEPT_ERROR_PCT = 5.0
# A small share is expected (boundary GPS jitter, merged communes). Below the
# error bar but above this, surface a WARN.
OUT_OF_DEPT_WARN_PCT = 0.5
# Share of rows whose declared department has no reference polygon. Usually
# historical/foreign codes; informational unless it is large.
DEPT_UNMATCHED_WARN_PCT = 5.0

# INVALID banding (D-02) : WARN des le 1er invalide (PAS de WARN-floor),
# contrairement a _flag_out_of_dept qui a un WARN-floor a OUT_OF_DEPT_WARN_PCT=0.5%.
# Un invalide est toujours un signal -> jamais silencieux. ERROR seulement
# au-dessus de INVALID_ERROR_PCT (effondrement structurel).
# Calibration : cadastre 34/1,28M=0,003% -> WARN, effondrement >=1% -> ERROR,
# 0 invalide -> rien.  (C3 — ecart semantique assume vs _flag_out_of_dept)
INVALID_WARN_PCT = 0.0  # placeholder semantique : WARN si invalid > 0 (pas de floor %)
INVALID_ERROR_PCT = 1.0  # >=1% invalides = effondrement structurel -> ERROR


@dataclass(frozen=True)
class GeoSpec:
    """How to bounds-check one spatial dataset.

    dept_expr is a SQL expression (over the audited table aliased `t`) that
    yields the VARCHAR department code to join against geo_departements.code.

    spatial_mode (D-07, DQ-GEO-02): when True, the department is derived by
    spatial containment (ST_Covers against geo_departements) instead of being
    read from a dept_expr column. dept_expr is ignored in spatial mode — pass
    an empty string. The relevant check is no_dept (point outside all France
    polygons = ERROR); out_of_dept is N/A (dept is always matched by construction).

    out_of_dept_error_pct (D-09): when set, overrides the global
    OUT_OF_DEPT_ERROR_PCT for this dataset.  Use to calibrate datasets where
    out_of_dept is a structural false positive rather than a coordinate defect
    (e.g. BDTOPO: the IGN archive partition label != geographic department, so
    cross-boundary buildings are legitimately out-of-dept by label).

    no_dept_warn_threshold_pct (FIX-05 / 28-05): applies to spatial_mode=True
    datasets only. When set, the no_dept check uses a banded approach instead
    of an unconditional ERROR:
      - no_dept_pct < no_dept_warn_threshold_pct  → WARN (legitimate fraction)
      - no_dept_pct >= no_dept_warn_threshold_pct → ERROR (CRS bug suspected)
    Use when the dataset legitimately includes stops/points outside metropolitan
    France (e.g. DOM, CTOM, cross-border routes in the GTFS consolidated feed)
    so that a real CRS bug (~100% out-of-bounds) is still caught as ERROR while
    the legitimate fraction only surfaces as WARN.
    """

    dataset: str
    table: str
    geom_col: str
    dept_expr: str
    spatial_mode: bool = False
    out_of_dept_error_pct: float | None = None
    no_dept_warn_threshold_pct: float | None = None


# Only datasets whose (table, geom_col, dept_expr) are confirmed from their
# migration DDL are registered. Adding a dataset is one line — never a new
# code path.
GEO_SPECS: dict[str, GeoSpec] = {
    # Point datasets.
    "equipements": GeoSpec("equipements", "equipements", "geom", "t.code_departement"),
    "antennes": GeoSpec(
        "antennes", "antennes", "geom", INSEE_TO_DEPT.format(col="t.code_insee")
    ),
    "dvf": GeoSpec("dvf", "mutations", "position", "t.code_departement"),
    # Polygon datasets.
    # FIX-04 / D-09: out_of_dept_error_pct raised to 20% for batiments.
    # True mechanism (28-DIAGNOSTIC-batiments.md — revised after refutation):
    # IGN BDTOPO departmental archives include cross-boundary buildings — buildings
    # physically located in neighbouring departments (44/79/49/17) are bundled in
    # the dept-85 archive. The pipeline tags all of them code_departement='85'
    # (the archive/partition label, pipeline.py l.368-374), but their WGS84
    # coordinates are correct real positions in the neighbour departments.
    # Evidence: dept-85 polygon area = 6 773 km² (not eroded); 82% of out-of-dept
    # rows are 1-5 km from the dept-85 border (too deep for a polygon-precision
    # artefact); all 157 863 rows carry code_departement='85' (archive label).
    # Out-of-dept is therefore NOT a coordinate-quality signal for BDTOPO; it
    # reflects the archive partition label vs true geographic location.
    # The 20% threshold exists solely to avoid a false ERROR on this structural
    # false positive while still catching a catastrophic CRS bug (~100% out-of-dept
    # for Lambert-93 coordinates stored as WGS-84). Real out-of-France = 0.027%.
    "batiments": GeoSpec(
        "batiments",
        "batiments",
        "geometrie",
        "t.code_departement",
        out_of_dept_error_pct=20.0,
    ),
    "cadastre": GeoSpec("cadastre", "parcelles", "geometrie", "t.code_departement"),
    # D-06 : 3 datasets a dept derivable par code — portent GEO_SPECS a 8.
    # Execution dans Plan 06. Geom col = position (confirme par grep DDL migrations).
    "ban": GeoSpec("ban", "adresses", "position", "t.code_departement"),
    "dpe": GeoSpec("dpe", "diagnostics_dpe", "position", "t.code_departement"),
    "monuments": GeoSpec(
        "monuments", "monuments_historiques", "position", "t.code_departement"
    ),
    # D-07 / DQ-GEO-02 : datasets without a dept column — dept derived by
    # spatial containment (ST_Covers). spatial_mode=True selects the spatial
    # branch in audit_geo_bounds; dept_expr is unused (empty string).
    # Geom columns confirmed from DDL migrations:
    #   migration 012 : arrets_transport.position GEOMETRY(Point, 4326)
    #   migration 028 : encadrement_loyers.geom   GEOMETRY(POINT, 4326)
    # FIX-05 / D-09 / 28-DIAGNOSTIC-transport.md: the 20 292 hors-France stops
    # (5.55%) are NOT a CRS bug. The GTFS consolidated feed (transport.data.gouv.fr)
    # includes stops from French overseas departments (DOM 971-974: Martinique 9 045,
    # Réunion 6 808, Guyane 765 = 81.9%) and cross-border routes (FlixBus, BlaBlaCar,
    # SNCF international — 2 935 stops in Spain, UK, Morocco, etc. = 14.5%).
    # Zero Lambert-93 coords confirmed: no point has X or Y in [100 000, 1 300 000].
    # Pipeline ST_SetSRID(ST_MakePoint(stop_lon, stop_lat), 4326) is correct —
    # GTFS is WGS84 by specification (GTFS §1.3).
    # geo_departements covers 96 metropolitan departments only (codes 01-95); DOM
    # codes 97x are absent → DOM stops are mechanically hors-France (D-10 artefact,
    # same as equipements 28-03). Data must NOT be excluded at ingestion (D-09).
    # Since 04.1-06 the referential import adds the 5 DROM polygons (union of
    # their communes): DROM stops fall in a polygon, the threshold stays a ceiling.
    # Calibration: no_dept_warn_threshold_pct=6.0 > 5.55% measured fraction,
    # +0.5% safety margin for DOM/cross-border network growth. A real CRS bug
    # (Lambert-93 stored as WGS-84) would produce ~100% hors-France → still ERROR.
    # "transport" = canonical dataset key per CLAUDE.md Dataset Map
    "transport": GeoSpec(
        "transport",
        "arrets_transport",
        "position",
        "",
        spatial_mode=True,
        no_dept_warn_threshold_pct=6.0,
    ),
    "encadrement_loyers": GeoSpec(
        "encadrement_loyers", "encadrement_loyers", "geom", "", spatial_mode=True
    ),
}


def _sync_dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def _reference_ready(conn: psycopg.Connection) -> int:
    """Return the geo_departements row count (0 if absent/empty)."""
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {REFERENCE_TABLE}")
            row = cur.fetchone()
        return int(row[0]) if row else 0
    except psycopg.Error:
        conn.rollback()
        return 0


def audit_geo_bounds(conn: psycopg.Connection, spec: GeoSpec) -> AuditResult:
    """Run the spatial-bounds + validity audit for one dataset.

    Single scan computes, over non-null geometries:
      - invalid        : ST_IsValid is false (degenerate ring, self-intersect)
      - out_of_dept    : representative point (ST_PointOnSurface) not covered by
                         the declared department's polygon
      - dept_unmatched : declared department absent from geo_departements

    out_of_dept uses a representative interior point rather than full containment
    so a parcel near a (simplified) department border is not a false positive, and
    points and polygons are handled uniformly. It is evaluated only on valid
    geometries so an invalid polygon is reported once (as invalid) rather than
    throwing inside ST_PointOnSurface/ST_Covers.
    """
    geom = spec.geom_col
    result = AuditResult(dataset=spec.dataset, table=spec.table, row_count=0)

    ref_count = _reference_ready(conn)
    if ref_count == 0:
        result.findings.append(
            Finding(
                severity="ERROR",
                category="geo",
                title="Polygones de reference absents",
                description=(
                    f"`{REFERENCE_TABLE}` est vide ou absente. Lancer "
                    "scripts/import_geo_boundaries.py avant le check geo."
                ),
                fix_hint="docker compose exec api python "
                "/app/scripts/import_geo_boundaries.py",
            )
        )
        return result

    try:
        conn.execute("SET LOCAL statement_timeout = '20min'")
    except psycopg.Error:
        conn.rollback()

    # --- Spatial mode (D-07, DQ-GEO-02) -----------------------------------
    # For datasets without a dept column (arrets_transport, encadrement_loyers),
    # the dept is derived by ST_Covers containment against geo_departements.
    # out_of_dept is N/A (by construction a point is always in its matched dept).
    # The only geo check is no_dept: point outside ALL dept polygons = hors France.
    # This branch is an early return — the classic out_of_dept branch below is
    # NOT reached (additive strategy, positive control P23 intact).
    if spec.spatial_mode:
        spatial_query = f"""
            SELECT
                count(*) AS total,
                count(*) FILTER (
                    WHERE t.{geom} IS NOT NULL
                ) AS with_geom,
                count(*) FILTER (
                    WHERE t.{geom} IS NOT NULL AND NOT ST_IsValid(t.{geom})
                ) AS invalid,
                count(*) FILTER (
                    WHERE t.{geom} IS NOT NULL
                      AND ST_IsValid(t.{geom})
                      AND NOT EXISTS (
                          SELECT 1 FROM {REFERENCE_TABLE} d
                          WHERE ST_Covers(d.geometry, t.{geom})
                      )
                ) AS no_dept
            FROM {spec.table} t
        """
        try:
            with conn.cursor() as cur:
                cur.execute(spatial_query)
                total, with_geom, invalid, no_dept = cur.fetchone()
        except psycopg.Error as exc:
            conn.rollback()
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="geo",
                    title="Echec du check geo spatial",
                    description=f"Requete spatiale en erreur : {exc}",
                )
            )
            return result

        result.row_count = total
        result.stats["lignes_avec_geometrie"] = f"{with_geom:,}".replace(",", " ")

        if with_geom == 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="geo",
                    title="Aucune geometrie a verifier",
                    description=f"Colonne `{geom}` entierement NULL.",
                )
            )
            return result

        invalid_pct = invalid / with_geom * 100
        no_dept_pct = no_dept / with_geom * 100
        result.stats["geometries_invalides_pct"] = f"{invalid_pct:.2f}%"
        result.stats["hors_france_pct"] = f"{no_dept_pct:.2f}%"

        _flag_invalid(result, spec, invalid, with_geom)
        _flag_no_dept(result, spec, no_dept, with_geom)
        return result
    # --- End spatial mode --------------------------------------------------

    query = f"""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE t.{geom} IS NOT NULL) AS with_geom,
            count(*) FILTER (
                WHERE t.{geom} IS NOT NULL AND NOT ST_IsValid(t.{geom})
            ) AS invalid,
            count(*) FILTER (
                WHERE t.{geom} IS NOT NULL AND d.code IS NULL
            ) AS dept_unmatched,
            count(*) FILTER (
                WHERE t.{geom} IS NOT NULL
                  AND d.code IS NOT NULL
                  AND ST_IsValid(t.{geom})
                  AND NOT ST_Covers(d.geometry, ST_PointOnSurface(t.{geom}))
            ) AS out_of_dept
        FROM {spec.table} t
        LEFT JOIN {REFERENCE_TABLE} d ON d.code = {spec.dept_expr}
    """
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            total, with_geom, invalid, dept_unmatched, out_of_dept = cur.fetchone()
    except psycopg.Error as exc:
        conn.rollback()
        result.findings.append(
            Finding(
                severity="ERROR",
                category="geo",
                title="Echec du check geo",
                description=f"Requete spatiale en erreur : {exc}",
            )
        )
        return result

    result.row_count = total
    result.stats["lignes_avec_geometrie"] = f"{with_geom:,}".replace(",", " ")

    if with_geom == 0:
        result.findings.append(
            Finding(
                severity="WARN",
                category="geo",
                title="Aucune geometrie a verifier",
                description=f"Colonne `{geom}` entierement NULL.",
            )
        )
        return result

    out_pct = out_of_dept / with_geom * 100
    invalid_pct = invalid / with_geom * 100
    unmatched_pct = dept_unmatched / with_geom * 100
    result.stats["hors_departement_pct"] = f"{out_pct:.2f}%"
    result.stats["geometries_invalides_pct"] = f"{invalid_pct:.2f}%"
    result.stats["departement_non_resolu_pct"] = f"{unmatched_pct:.2f}%"

    _flag_out_of_dept(result, spec, out_of_dept, out_pct)
    _flag_invalid(result, spec, invalid, with_geom)
    _flag_unmatched(result, spec, dept_unmatched, unmatched_pct)

    return result


def _flag_out_of_dept(
    result: AuditResult, spec: GeoSpec, out_of_dept: int, out_pct: float
) -> None:
    error_pct = (
        spec.out_of_dept_error_pct
        if spec.out_of_dept_error_pct is not None
        else OUT_OF_DEPT_ERROR_PCT
    )
    if out_pct >= error_pct:
        result.findings.append(
            Finding(
                severity="ERROR",
                category="geo",
                title=(
                    f"Geometries hors departement : {out_pct:.1f}% "
                    f"({out_of_dept:,} lignes)".replace(",", " ")
                ),
                description=(
                    "Part anormale de points/polygones hors du departement "
                    "declare. Cause typique : coordonnees chargees en Lambert-93 "
                    "(EPSG:2154) sans reprojection vers WGS84 (EPSG:4326)."
                ),
                count=out_of_dept,
                sample_query=(
                    # ST_PointOnSurface keeps the repro query valid for polygon
                    # datasets (batiments) too, where ST_X/ST_Y on the raw
                    # geometry would be undefined.
                    f"SELECT {spec.dept_expr} AS dept_declare, "
                    f"ST_X(ST_PointOnSurface(t.{spec.geom_col})) AS x, "
                    f"ST_Y(ST_PointOnSurface(t.{spec.geom_col})) AS y "
                    f"FROM {spec.table} t "
                    f"LEFT JOIN {REFERENCE_TABLE} d ON d.code = {spec.dept_expr} "
                    f"WHERE t.{spec.geom_col} IS NOT NULL AND d.code IS NOT NULL "
                    f"AND ST_IsValid(t.{spec.geom_col}) "
                    f"AND NOT ST_Covers(d.geometry, t.{spec.geom_col}) LIMIT 10;"
                ),
                fix_hint=(
                    "Reprojeter dans l'ETL : ST_Transform(ST_SetSRID(geom, 2154), "
                    "4326) avant le COPY, ou corriger le mapping lat/long source."
                ),
            )
        )
    elif out_pct >= OUT_OF_DEPT_WARN_PCT:
        result.findings.append(
            Finding(
                severity="WARN",
                category="geo",
                title=(
                    f"Geometries hors departement : {out_pct:.2f}% "
                    f"({out_of_dept:,} lignes)".replace(",", " ")
                ),
                description=(
                    "Quelques geometries hors du departement declare. Jitter de "
                    "frontiere ou communes fusionnees possibles ; surveiller."
                ),
                count=out_of_dept,
            )
        )


def _flag_no_dept(
    result: AuditResult, spec: GeoSpec, no_dept: int, with_geom: int
) -> None:
    """Flag points that do not fall inside any department polygon (spatial mode).

    Semantics (D-07): in spatial mode the dept is derived by containment, so a
    point with no matching polygon is outside France entirely — typical cause is
    Lambert-93 coordinates stored as WGS-84 (CRS mismatch).

    Default behaviour (no_dept_warn_threshold_pct is None): always ERROR when
    no_dept > 0 (there is no acceptable fraction of points hors France).

    Banded behaviour (no_dept_warn_threshold_pct is set, FIX-05): applies to
    datasets that legitimately include points outside metropolitan France
    (DOM, CTOM, cross-border routes). The threshold separates a known legitimate
    fraction (→ WARN) from a CRS-bug level fraction (→ ERROR):
      - no_dept_pct <  no_dept_warn_threshold_pct → WARN  (legitimate fraction)
      - no_dept_pct >= no_dept_warn_threshold_pct → ERROR (CRS bug suspected)
    A real Lambert-93 CRS bug would produce ~100%, always above any reasonable
    threshold → still caught as ERROR.
    """
    if with_geom == 0 or no_dept == 0:
        return

    no_dept_pct = no_dept / with_geom * 100
    threshold = spec.no_dept_warn_threshold_pct

    # Determine severity: banded (threshold set) or unconditional ERROR.
    title_prefix = (
        f"{no_dept:,} points hors de tout departement (hors France)"
    ).replace(",", " ")

    if threshold is not None and no_dept_pct < threshold:
        severity = "WARN"
        description = (
            f"{no_dept_pct:.2f}% des points ne tombent dans aucun polygone de "
            "geo_departements. Ces points sont des arrets GTFS legitimes hors "
            "metropole (DOM/CTOM/transfrontaliers) - faux positif DB locale "
            f"(geo_departements = metrique uniquement). "
            f"Seuil WARN/ERROR calibre a {threshold:.1f}% (FIX-05 / 28-05)."
        )
        fix_hint = (
            "Faux positif : arrets DOM/transfrontaliers absents de geo_departements "
            "(DB locale metrique uniquement). Se resout en ~0 sur DB nationale "
            "avec DOM. Cf. 28-DIAGNOSTIC-transport.md."
        )
    else:
        severity = "ERROR"
        description = (
            "Ces points ne tombent dans aucun polygone de geo_departements. "
            "Cause typique : coordonnees Lambert-93 (EPSG:2154) stockees "
            "comme WGS-84 (EPSG:4326) sans reprojection."
        )
        fix_hint = (
            "Reprojeter dans l'ETL : "
            f"ST_Transform(ST_SetSRID({spec.geom_col}, 2154), 4326) "
            "avant le COPY, ou corriger le mapping lat/long source."
        )

    result.findings.append(
        Finding(
            severity=severity,
            category="geo",
            title=title_prefix,
            description=description,
            count=no_dept,
            sample_query=(
                f"SELECT ST_X(t.{spec.geom_col}) AS x, "
                f"ST_Y(t.{spec.geom_col}) AS y "
                f"FROM {spec.table} t "
                f"WHERE t.{spec.geom_col} IS NOT NULL "
                f"AND ST_IsValid(t.{spec.geom_col}) "
                f"AND NOT EXISTS ("
                f"SELECT 1 FROM {REFERENCE_TABLE} d "
                f"WHERE ST_Covers(d.geometry, t.{spec.geom_col})"
                f") LIMIT 10;"
            ),
            fix_hint=fix_hint,
        )
    )


def _flag_invalid(
    result: AuditResult, spec: GeoSpec, invalid: int, with_geom: int
) -> None:
    # D-02 banding : PAS de WARN-floor en % (cf. C3 — ecart vs _flag_out_of_dept).
    # Un seul invalide est toujours au moins un WARN. ERROR si effondrement structurel.
    if with_geom == 0 or invalid == 0:
        return
    invalid_pct = invalid / with_geom * 100
    if invalid_pct >= INVALID_ERROR_PCT:
        result.findings.append(
            Finding(
                severity="ERROR",
                category="geo",
                title=(
                    f"Geometries invalides : {invalid_pct:.1f}% "
                    f"({invalid:,} lignes)".replace(",", " ")
                ),
                description=(
                    "ST_IsValid renvoie FALSE sur une part anormale des geometries "
                    "(auto-intersection, anneau invalide). Effondrement structurel."
                ),
                count=invalid,
                sample_query=(
                    f"SELECT ST_IsValidReason({spec.geom_col}) FROM {spec.table} "
                    f"WHERE {spec.geom_col} IS NOT NULL "
                    f"AND NOT ST_IsValid({spec.geom_col}) LIMIT 10;"
                ),
                fix_hint=f"Appliquer ST_MakeValid({spec.geom_col}) dans l'ETL.",
            )
        )
    else:
        result.findings.append(
            Finding(
                severity="WARN",
                category="geo",
                title=(
                    f"Geometries invalides : {invalid_pct:.3f}% "
                    f"({invalid:,} lignes)".replace(",", " ")
                ),
                description=(
                    "ST_IsValid renvoie FALSE (auto-intersection, anneau invalide). "
                    "Poignee d'invalides, surveiller."
                ),
                count=invalid,
                sample_query=(
                    f"SELECT ST_IsValidReason({spec.geom_col}) FROM {spec.table} "
                    f"WHERE {spec.geom_col} IS NOT NULL "
                    f"AND NOT ST_IsValid({spec.geom_col}) LIMIT 10;"
                ),
                fix_hint=f"Appliquer ST_MakeValid({spec.geom_col}) dans l'ETL.",
            )
        )


def _flag_unmatched(
    result: AuditResult, spec: GeoSpec, dept_unmatched: int, unmatched_pct: float
) -> None:
    if unmatched_pct >= DEPT_UNMATCHED_WARN_PCT:
        result.findings.append(
            Finding(
                severity="WARN",
                category="geo",
                title=(
                    f"Departement non resolu : {unmatched_pct:.1f}% "
                    f"({dept_unmatched:,} lignes)".replace(",", " ")
                ),
                description=(
                    f"Le code departement derive ne correspond a aucune ligne de "
                    f"`{REFERENCE_TABLE}`. Codes historiques/etrangers ou "
                    "geo_departements incomplet."
                ),
                count=dept_unmatched,
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DataCommune generic geo-bounds audit (ST_Covers + ST_IsValid)."
    )
    parser.add_argument(
        "datasets",
        nargs="*",
        help=f"Datasets to audit (default: all). Known: {list(GEO_SPECS)}.",
    )
    parser.add_argument(
        "-o",
        "--out",
        default=".planning/audit-geo",
        help="Output directory for reports (default: .planning/audit-geo).",
    )
    args = parser.parse_args()

    targets = args.datasets or list(GEO_SPECS)
    unknown = [d for d in targets if d not in GEO_SPECS]
    if unknown:
        print(f"Unknown datasets: {unknown}. Known: {list(GEO_SPECS)}")
        return 2

    out_dir = Path(args.out)
    dsn = _sync_dsn()

    results: list[AuditResult] = []
    with psycopg.connect(dsn) as conn:
        conn.set_read_only(True)
        for name in targets:
            print(f"[geo] auditing {name}...", flush=True)
            result = audit_geo_bounds(conn, GEO_SPECS[name])
            path = write_report(result, out_dir)
            print(
                f"  -> {len(result.errors)} ERR, "
                f"{len(result.warnings)} WARN, "
                f"{len(result.infos)} INFO -> {path}"
            )
            results.append(result)

    summary_path = write_summary(results, out_dir)
    print(f"[geo] summary written to {summary_path}")

    # Non-zero exit when any dataset has an ERROR-level geo finding.
    return 1 if any(r.errors for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
