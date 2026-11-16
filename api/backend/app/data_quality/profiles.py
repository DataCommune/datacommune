"""Declarative per-dataset profiles for the generic data-quality harness.

Each entry in PROFILES describes which dimensions apply to a dataset and
carries the dimension-specific configuration (full_france_min for volume,
GeoSpec for geo-bounds).  The orchestrator (run.py) iterates over PROFILES
and dispatches to the applicable dimension checkers — no dataset-specific
code paths needed.

Design decisions:
- D-09: GeoSpec stays in geo_bounds.py; imported here, never redefined.
- D-10: Only VolumeSpec is defined here (the only new *Spec for Phase 23).
        NullSpec, FkSpec, etc. are Phase 24+.
- D-03 (Phase 24): VolumeSpec declares a FRANCE-ENTIRE floor (full_france_min).
        check_volume scales it to the instance scope its caller read from the
        database (D-21) via _compute_volume_floor — never count(distinct dept)
        from the table (anti-circularity: a truncated ETL must not lower its
        own floor).
- D-05 (Phase 24): check_freshness reads etl_runs.completed_at filtered on
        status='success' (real last success, not dataset_metadata.last_updated_at
        which can be stamped on a partial/failed run).  WARN-only (never ERROR).
- D-04 (Phase 24): check_nulls uses 2 passes: required_cols exact scan (ERROR),
        expected_cols via check_null_rates (WARN): TABLESAMPLE on a large plain
        table, exact on a partitioned parent; both passes under the scan timeout.
- C1 (Phase 24): get_sync_dsn() lives in base.py so profiles.py never imports
        run.py (anti-circularity: run.py already imports profiles.py).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import psycopg
from psycopg import sql

from app.data_quality.base import Finding
from app.data_quality.business.dvf_prix_m2 import check_dvf_prix_m2
from app.data_quality.geo_bounds import GEO_SPECS, GeoSpec
from app.etl.validation import check_null_rates
from app.scope.floors import (
    departement_floor,
    national_floor,
    producer_departement_floor,
    producer_floor,
)
from app.scope.model import POINT_DEPARTEMENT_SQL, InstanceScope, scale_floor
from app.scope.producer_counts import PRODUCER_COUNTS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Spec dataclasses (frozen — immutable config objects, same convention as GeoSpec)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolumeSpec:
    """Volume check: count(*) must be >= floor scaled to configured scope.

    full_france_min: expected minimum row count for the ENTIRE FRANCE dataset.
    The effective floor is computed by _compute_volume_floor, which scales
    this value to the departements of the instance scope (D-21).

    is_national: when True, the table comes from one national file filtered to
        the hosted departements at import (D-22, e.g. equipements, foncier_pm).
        Its floor follows the scope with the density margin of
        app.scope.model.scale_floor; per-departement sources scale linearly.

    severity: "error" (default) or "warn" (D-12 P29). Use "warn" for known
        source defects outside our control (e.g. permis SITADEL quarantaine D-11).
        Default "error" = zero impact on the 19 other profiles.

    Anti-circularity (D-03): full_france_min is a declared constant, never
    derived from observed row counts or distinct department counts.
    """

    full_france_min: int
    is_national: bool = False  # national file filtered to the scope (D-22)
    # D-12 P29: default "error" = zero impact on profiles that don't set it
    severity: Literal["error", "warn"] = "error"
    # D-06/D-07 P30: notoriously partial source (e.g. permis SITADEL quarantaine).
    # Forces a WARN finding whenever the table is present (total > 0), regardless
    # of count vs floor — volume cell structurally signals source partiality, not
    # by count coincidence. An empty/collapsed table still emits ERROR (total==0
    # branch checked first); the flag never masks a dead table. Default False =
    # zero impact on all other profiles (20 entries unchanged).
    source_known_partial: bool = False
    # The source only covers fixed departements (permis: 93, KI-16): its floor
    # is that local source's floor, never scaled to the instance scope.
    local_source: bool = False
    # Departement of a published row (code constant), for the per-departement
    # volume check of a national file; same expression as the pipeline's
    # production_departement_sql (locked by test).
    departement_sql: str = "code_departement"


@dataclass(frozen=True)
class FreshnessSpec:
    """Freshness check configuration (DQ-UNIV-02, D-05).

    etl_dataset_name: value of etl_runs.dataset_name for this dataset.
    max_days: maximum acceptable age of the last successful ETL run.

    Freshness findings are always WARN — never ERROR (D-05b).
    """

    etl_dataset_name: str
    max_days: int


@dataclass(frozen=True)
class NullSpec:
    """Null check configuration (DQ-UNIV-03, D-04).

    required_cols: columns that must NEVER be null.  Any null → ERROR.
                   Checked via exact COUNT WHERE col IS NULL (no sampling).
                   Conservative list derived from DDL NOT NULL + business keys.
    expected_cols: columns expected to be mostly non-null.  Null rate above
                   expected_threshold → WARN.  Checked via check_null_rates
                   (TABLESAMPLE 1% above 1M rows).
    expected_threshold: null rate threshold in [0, 1] (default 5%).

    D-04 rationale: required scan is exact (no TABLESAMPLE) to catch rare nulls
    that sampling would miss (e.g. 1 null in 2.7M rows = 0.00004%).
    """

    required_cols: tuple[str, ...]
    expected_cols: tuple[str, ...]
    expected_threshold: float = 0.05


@dataclass(frozen=True)
class TemporalSpec:
    """Temporal check configuration (DQ-COND-01, D-02 ERROR).

    date_col:  column holding the date or year value.
    min_val:   minimum acceptable value (date or int year).  Rows below → finding.
    max_val:   maximum acceptable value (date or int year).  None = future
               dates are legitimate (open upper bound — no upper-bound check).
    col_type:  "date" for DATE/TIMESTAMP columns; "year_int" for INTEGER columns
               (e.g. copropriete.annee_reglement, encadrement_loyers.annee —
               Pitfall 1: avoid CAST to date, use pure integer comparison).
    severity:  "error" (default, D-02) or "warn" (D-05/D-12 P29).
               Use "warn" for known source defects outside our control (e.g.
               copropriete placeholders from RNIC). Default "error" = zero
               impact on the 19 other profiles.
    """

    date_col: str
    min_val: date | int
    max_val: date | int | None
    col_type: Literal["date", "year_int"] = "date"
    # D-12 P29: default "error" = zero impact on profiles that don't set it
    severity: Literal["error", "warn"] = "error"


@dataclass(frozen=True)
class DuplicatesSpec:
    """Duplicate-row check configuration (DQ-COND-02, D-05 ERROR conditional).

    natural_key: tuple of column names forming the business/natural key.
                 Any combination that appears more than once → ERROR.

    Severity: ERROR (D-05) — duplicate rows on the natural key are defects.
    """

    natural_key: tuple[str, ...]


@dataclass(frozen=True)
class FormatRule:
    """Single format rule for a column (DQ-COND-03, D-04 WARN config-driven).

    col:       column name to check.
    rule_type: "length"  — exact character length (value: int).
               "regex"   — column must match pattern (value: str regex).
               "enum"    — column must be one of the allowed values
                           (value: tuple[str, ...]).
    value:     the rule parameter — int for length, str for regex, tuple for enum.
               All values come from the spec (DQ-COND-03 lock: no hard-coded
               business literals in the checker body).

    Severity: WARN (D-04) — format violations are data quality warnings.
    """

    col: str
    rule_type: Literal["length", "regex", "enum"]
    value: int | str | tuple[str, ...]


@dataclass(frozen=True)
class FkSpec:
    """FK integrity check against geo_communes (DQ-COND-04, D-03/D-03b WARN-only).

    fk_col:            column in the target table holding the commune code.
    ref_table:         reference table (default: geo_communes).
    ref_col:           join column on the reference table.
                       Default "code" — confirmed by migration 024 DDL:
                       CREATE TABLE geo_communes (code VARCHAR(5) PRIMARY KEY, ...).
                       NOT "code_insee" (RESEARCH had an error; corrected here).
    warn_threshold_pct: orphan rate (%) above which a WARN is emitted (D-03b).
                        Default 2.0 — below threshold is considered acceptable.

    Severity: WARN-only (D-03) — no ERROR band for integrity (single threshold).
    """

    fk_col: str
    ref_table: str = "geo_communes"
    ref_col: str = "code"
    warn_threshold_pct: float = 2.0


@dataclass(frozen=True)
class DatasetProfile:
    """Declarative per-dataset configuration for the generic orchestrator.

    geo:          None if the dataset has no spatial dimension (e.g. demographie).
    volume:       None if volume checking is not applicable (rare; all pilots have it).
    freshness:    None if freshness checking is not configured.
    nulls:        None if null checking is not configured.
    temporal:     None if temporal checking is not applicable (N/A = honest D-01).
    duplicates:   None if duplicate checking is not applicable (N/A = honest D-01).
    format_rules: empty tuple () if no format rules (falsy = N/A honest).
    integrity:    None if FK integrity checking is not applicable (N/A = honest D-01).
    """

    dataset: str
    table: str
    geo: GeoSpec | None = None
    volume: VolumeSpec | None = None
    freshness: FreshnessSpec | None = None
    nulls: NullSpec | None = None
    temporal: TemporalSpec | None = None
    duplicates: DuplicatesSpec | None = None
    format_rules: tuple[FormatRule, ...] = ()
    integrity: FkSpec | None = None
    # [P26] business hook — callable dispatched by orchestrator if not None (D-02/D-04).
    # direction autorisée: profiles → business (C1 préservé).
    business_hook: Callable[..., list[Finding]] | None = None


# ---------------------------------------------------------------------------
# Volume floor computation — scope-aware (D-03 anti-circularity)
# ---------------------------------------------------------------------------


def _compute_volume_floor(
    full_france_min: int, is_national: bool, scope: InstanceScope, dataset: str
) -> int:
    """Scale a full-France volume floor to the departements of *scope*.

    Anti-circularity (D-03): *scope* is the instance scope read from the
    database by the caller (D-21), an intentional operator declaration —
    NEVER count(distinct code_departement) in the target table. A truncated
    ETL cannot lower its own detection floor.

    Args:
        full_france_min: declared minimum row count for France-entire dataset.
        is_national: the table comes from a national file filtered to the
            hosted departements at import (D-22): sum of the per-departement
            floors of app.scope.floors (80 % of the measured reference, or a
            documented margin on the proportional share).
        scope: instance scope bound by the caller; no default, no hidden
            configuration.
        dataset: profile key (transport is the transports pipeline).

    Returns:
        Effective minimum row count for the scope; full_france_min unchanged
        when the scope covers the 102 departements.
    """
    if is_national:
        return national_floor(
            _FLOOR_DATASET.get(dataset, dataset), scope.departements, full_france_min
        )
    return scale_floor(full_france_min, len(scope.departements))


# Profile key -> pipeline dataset name used by app.scope.floors.
_FLOOR_DATASET = {"transport": "transports"}


# ---------------------------------------------------------------------------
# Freshness — date-independent seam + checker (DQ-UNIV-02, D-05)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Return the current UTC time. Patchable seam for date-independent tests.

    Defined locally (not imported from app.datasets.freshness) so that tests
    can patch 'app.data_quality.profiles._now' directly without risking
    cross-module patch placement issues (pitfall 1).
    """
    return datetime.now(UTC)


def check_freshness(conn: psycopg.Connection, profile: DatasetProfile) -> list[Finding]:
    """Check that the last successful ETL run is not older than max_days (D-05).

    Reads etl_runs.completed_at WHERE status='success' to get the real last
    success timestamp.  Never uses dataset_metadata.last_updated_at (which can
    be stamped on a partial or failed run — D-05 déviation assumée).

    Returns at most 1 finding, always WARN (never ERROR — D-05b).

    Args:
        conn: psycopg sync connection (read-only, provided by orchestrator).
        profile: DatasetProfile with a non-None FreshnessSpec.
    """
    assert profile.freshness is not None, "check_freshness called without FreshnessSpec"

    spec = profile.freshness
    result = conn.execute(
        "SELECT MAX(completed_at) FROM etl_runs"
        " WHERE dataset_name = %(name)s AND status = 'success'",
        {"name": spec.etl_dataset_name},
    )
    row = result.fetchone()

    if row is None or row[0] is None:
        return [
            Finding(
                severity="WARN",
                category="freshness",
                title=f"Freshness : aucun succès ETL pour '{spec.etl_dataset_name}'",
                description=(
                    f"Aucun run avec status='success' trouvé dans etl_runs "
                    f"pour dataset_name='{spec.etl_dataset_name}'."
                ),
                sample_query=(
                    f"SELECT MAX(completed_at) FROM etl_runs"
                    f" WHERE dataset_name='{spec.etl_dataset_name}'"
                    f" AND status='success';"
                ),
                fix_hint=f"Rejouer l'ETL pour '{spec.etl_dataset_name}'.",
            )
        ]

    last_success: datetime = row[0]
    age = _now() - last_success

    if age > timedelta(days=spec.max_days):
        age_days = age.days
        return [
            Finding(
                severity="WARN",
                category="freshness",
                title=(
                    f"Freshness : données périmées depuis {age_days} jours"
                    f" (max={spec.max_days})"
                ),
                description=(
                    f"Dernier succès ETL pour '{spec.etl_dataset_name}'"
                    f" il y a {age_days} jours"
                    f" (seuil = {spec.max_days} jours)."
                ),
                sample_query=(
                    f"SELECT MAX(completed_at) FROM etl_runs"
                    f" WHERE dataset_name='{spec.etl_dataset_name}'"
                    f" AND status='success';"
                ),
                fix_hint=f"Rejouer l'ETL pour '{spec.etl_dataset_name}'.",
            )
        ]

    return []


# ---------------------------------------------------------------------------
# Nulls — 2-pass checker (DQ-UNIV-03, D-04, C1, C4)
# ---------------------------------------------------------------------------


def check_nulls(  # noqa: C901
    conn: psycopg.Connection, profile: DatasetProfile, *, stats: dict | None = None
) -> list[Finding]:
    """Exact required checks plus bounded expected scans on the same connection."""
    assert profile.nulls is not None, "check_nulls called without a NullSpec"

    spec = profile.nulls
    scans = {
        "expected": len(spec.required_cols) + bool(spec.expected_cols),
        "completed": 0,
        "checks": [],
    }
    if stats is not None:
        stats["null_scans"] = scans

    if not spec.required_cols and not spec.expected_cols:
        return []

    findings: list[Finding] = []

    # Required columns are exact zero-tolerance checks, read in ONE exact pass:
    # one scan per column could not finish on the national DPE (16.7 M rows,
    # 18 GB, first national import 2026-10-30). If that pass fails (a missing
    # column, a timeout), each column is scanned alone in its own savepoint, so
    # one failure never hides the verdict of the others.
    from app.etl.validation import scan_timeout_sql

    timeout = scan_timeout_sql()
    table = sql.Identifier("public", profile.table)

    def _scan(columns: tuple[str, ...]) -> tuple[int, list[int]]:
        with conn.transaction():
            conn.execute(timeout)
            conn.execute("SET LOCAL lock_timeout = '2s'")
            filters = sql.SQL(", ").join(
                sql.SQL("count(*) FILTER (WHERE {} IS NULL)").format(sql.Identifier(c))
                for c in columns
            )
            row = conn.execute(
                sql.SQL("SELECT count(*), {} FROM {}").format(filters, table)
            ).fetchone()
            if row is None or len(row) != len(columns) + 1:
                raise RuntimeError("Required NULL scan returned no result")
            total = int(row[0])
            if total == 0:
                raise RuntimeError("Required NULL scan has no observations")
            return total, [int(n) for n in row[1:]]

    one_pass: dict[str, tuple[int, int]] = {}
    if len(spec.required_cols) > 1:
        try:
            total, counts = _scan(tuple(spec.required_cols))
            one_pass = {
                c: (total, n) for c, n in zip(spec.required_cols, counts, strict=True)
            }
        except Exception:  # noqa: BLE001
            one_pass = {}

    for col in spec.required_cols:
        try:
            if col in one_pass:
                total, count = one_pass[col]
            else:
                total, (count,) = _scan((col,))
            scans["completed"] += 1
            scans["checks"].append(
                {
                    "column": col,
                    "kind": "required",
                    "status": "failed" if count else "passed",
                    "null_count": count,
                    "observed": total,
                    "method": "exact",
                }
            )
            if count:
                findings.append(
                    Finding(
                        severity="ERROR",
                        category="nulls",
                        title=f"Nulls required : {count} null(s) dans '{col}'",
                        description=(
                            f"La colonne {profile.table}.{col} contient "
                            f"{count} NULL; tolérance zéro."
                        ),
                        count=count,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            scans["checks"].append(
                {"column": col, "kind": "required", "status": "unavailable"}
            )
            findings.append(
                Finding(
                    severity="ERROR",
                    category="nulls",
                    title=f"Contrôle NULL required indisponible : {col}",
                    description=(
                        f"Scan non exécuté pour {profile.table}.{col}: "
                        f"{type(exc).__name__}."
                    ),
                )
            )

    # PASS 2: EXPECTED on the same connection and bounded savepoint.
    # ------------------------------------------------------------------
    if spec.expected_cols:
        try:
            flagged = check_null_rates(
                "",
                profile.table,
                spec.expected_threshold,
                conn=conn,
                columns=spec.expected_cols,
            )
            scans["completed"] += 1
            scans["checks"].append(
                {
                    "kind": "expected",
                    "status": "failed" if flagged else "passed",
                    "observation": getattr(flagged, "evidence", {}),
                }
            )
        except Exception as exc:  # noqa: BLE001
            scans["checks"].append(
                {
                    "kind": "expected",
                    "status": "unavailable",
                    "observation": getattr(exc, "evidence", {}),
                }
            )
            findings.append(
                Finding(
                    severity="ERROR",
                    category="nulls",
                    title="Contrôle NULL expected indisponible",
                    description=(
                        f"Scan non exécuté pour {profile.table}: "
                        f"{type(exc).__name__}. Aucun verdict de conformité."
                    ),
                )
            )
            flagged = []
        expected_set = set(spec.expected_cols)
        for entry in flagged:
            col = entry["column"]
            if col not in expected_set:
                continue
            null_rate = entry["null_rate"]
            findings.append(
                Finding(
                    severity="WARN",
                    category="nulls",
                    title=(
                        f"Nulls expected : {null_rate:.1%} null(s) dans '{col}'"
                        f" (seuil={spec.expected_threshold:.0%})"
                    ),
                    description=(
                        f"La colonne `{col}` de `{profile.table}` a un taux"
                        f" de nulls de {null_rate:.1%}"
                        f" (seuil = {spec.expected_threshold:.0%})."
                    ),
                    count=None,
                    sample_query=(
                        f'SELECT count(*) FROM "{profile.table}" WHERE "{col}" IS NULL;'
                    ),
                    fix_hint=(
                        f"Vérifier la source ETL pour '{col}' dans '{profile.table}'."
                    ),
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Volume checker
# ---------------------------------------------------------------------------


def effective_volume_floor(
    volume: VolumeSpec, scope: InstanceScope, dataset: str
) -> int:
    """Floor check_volume applies: scaled to *scope*, except a local source.

    A dataset with producer counts (plan 04.1-17) sums 80 % of the stored
    rows its producer published for each hosted departement of *scope*.
    """
    if volume.local_source:
        return volume.full_france_min
    producteur = producer_floor(
        _FLOOR_DATASET.get(dataset, dataset), scope.departements, "lignes_stockees"
    )
    if producteur is not None:
        return producteur
    return _compute_volume_floor(
        volume.full_france_min, volume.is_national, scope, dataset
    )


def departement_volume_shortfalls(
    conn: psycopg.Connection, profile: DatasetProfile, scope: InstanceScope
) -> list[tuple[str, int, int]]:
    """(departement, rows, floor) of each hosted departement under its floor.

    National files filtered to a restricted scope, and datasets with producer
    counts on any scope (plan 04.1-17): one truncated departement is reported
    even when another one fills the total.
    """
    volume = profile.volume
    assert volume is not None
    dataset = _FLOOR_DATASET.get(profile.dataset, profile.dataset)
    if dataset in PRODUCER_COUNTS and not volume.local_source and scope.departements:
        floors = {
            code: floor
            for code in scope.departements
            if (floor := producer_departement_floor(dataset, code, "lignes_stockees"))
            is not None
        }
    elif (
        not volume.is_national
        or volume.local_source
        or scope.covers_all_departements
        or not scope.departements
    ):
        return []
    else:
        floors = {
            code: departement_floor(dataset, code, volume.full_france_min)
            for code in scope.departements
        }
    expression = volume.departement_sql
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {expression}, count(*) FROM {profile.table} "
            f"WHERE {expression} = ANY(%s) GROUP BY 1",
            (list(scope.departements),),
        )
        counts = {row[0]: int(row[1]) for row in cur.fetchall()}
    return [
        (code, counts.get(code, 0), floor)
        for code, floor in floors.items()
        if counts.get(code, 0) < floor
    ]


def check_volume(
    conn: psycopg.Connection, profile: DatasetProfile, scope: InstanceScope
) -> tuple[int, list[Finding]]:
    """Scope-aware volume check: count(*) vs VolumeSpec.full_france_min scaled to scope.

    *scope* is the instance scope the caller read from the database (D-21).

    Returns (row_count, findings).  findings is empty if count >= floor.
    No statement_timeout needed — count(*) is instantaneous on PostgreSQL.
    """
    assert profile.volume is not None, "check_volume called without a VolumeSpec"

    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {profile.table}")
        row = cur.fetchone()
    total = int(row[0]) if row else 0

    # D-03: compute floor from the declared scope, not from table content.
    floor = effective_volume_floor(profile.volume, scope, profile.dataset)
    shortfalls = departement_volume_shortfalls(conn, profile, scope) if total else []

    logger.debug(
        "volume floor: dataset=%s full_france_min=%d is_national=%s "
        "scaled_floor=%d observed=%d",
        profile.dataset,
        profile.volume.full_france_min,
        profile.volume.is_national,
        floor,
        total,
    )

    findings: list[Finding] = []
    # D-12 P29: severity from spec (default "error", "warn" for known source defects)
    sev = profile.volume.severity.upper()

    if total == 0:
        # D-07 P30: an empty table is always ERROR regardless of VolumeSpec.severity.
        # source_known_partial (or any severity="warn") must never downgrade a dead
        # table to WARN — a completely empty table signals catastrophic data loss.
        findings.append(
            Finding(
                severity="ERROR",
                category="volume",
                title="Table vide",
                description=f"`{profile.table}` contient 0 lignes.",
                count=0,
                sample_query=f"SELECT count(*) FROM {profile.table};",
                fix_hint="Rejouer l'ETL pour ce dataset.",
            )
        )
    elif total < floor:
        findings.append(
            Finding(
                severity=sev,
                category="volume",
                title=(f"Volume sous le plancher ({total:,} < {floor:,})").replace(
                    ",", " "
                ),
                description=(
                    f"`{profile.table}` contient {total:,} lignes, "
                    f"plancher effectif (scope-aware) = {floor:,}. "
                    "ETL partiel ou source amputée."
                ).replace(",", " "),
                count=total,
                sample_query=f"SELECT count(*) FROM {profile.table};",
                fix_hint=f"Rejouer l'ETL pour '{profile.dataset}'.",
            )
        )
    elif shortfalls:
        detail = ", ".join(f"{code}: {rows} < {low}" for code, rows, low in shortfalls)
        findings.append(
            Finding(
                severity=sev,
                category="volume",
                title=f"Volume sous le plancher par département ({detail})",
                description=(
                    f"`{profile.table}` : département(s) sous leur plancher "
                    f"({detail}). ETL partiel ou source amputée."
                ),
                count=total,
                sample_query=(
                    f"SELECT {profile.volume.departement_sql}, count(*) "
                    f"FROM {profile.table} GROUP BY 1;"
                ),
                fix_hint=f"Rejouer l'ETL pour '{profile.dataset}'.",
            )
        )
    # D-06/D-07 P30: source notoirement partielle → force WARN si table présente.
    # CRITICAL: this branch is elif (not if) — placed AFTER the total==0 branch so a
    # dead table always falls into ERROR above, never here. total > 0 is strictly
    # required; the existing total < floor branch already covers under-floor rows.
    elif profile.volume.source_known_partial and total > 0:
        findings.append(
            Finding(
                severity="WARN",
                category="volume",
                title="Source partielle connue",
                description=(
                    f"`{profile.table}` contient {total:,} lignes, mais la source "
                    "upstream est notoirement amputée (SITADEL pour permis — "
                    "quarantaine D-06 P30). Le count observé ne reflète pas la "
                    "couverture réelle du dataset."
                ).replace(",", " "),
                count=total,
                sample_query=f"SELECT count(*) FROM {profile.table};",
                fix_hint=(
                    "Attendre la réparation de la source upstream (SITADEL) ou "
                    "documenter le périmètre partiel dans la certification."
                ),
            )
        )
    # No upper-bound check in Phase 23 (WARN on excess = Phase 24+).

    return total, findings


# ---------------------------------------------------------------------------
# Conditional dimension checkers (DQ-COND-01..04, Phase 25)
# ---------------------------------------------------------------------------
# All 4 checkers follow the same conventions as check_nulls (see above):
#   1. assert guard at top
#   2. SET LOCAL statement_timeout before any scan
#   3. f-string double-quoted identifiers for execution; sql.Identifier for logging
#   4. Finding with all fields populated; empty list on green
#   5. conn passed as argument — NEVER call get_sync_dsn() (anti-cycle C1)
# ---------------------------------------------------------------------------


def check_temporal(conn: psycopg.Connection, profile: DatasetProfile) -> list[Finding]:
    """Check that temporal column values fall within [min_val, max_val].

    DQ-COND-01, D-02 ERROR.

    Branches on spec.col_type:
    - "date":     DATE/TIMESTAMP column — bounds passed as date params via %s.
    - "year_int": INTEGER year column — pure integer comparison (no CAST to date,
                  avoids DataError on out-of-range values — Pitfall 1).

    max_val=None means the upper bound is open (future dates are legitimate — D-02).
    Returns [] immediately if profile.temporal is None (N/A, honest — D-01).

    Severity: ERROR (D-02) — out-of-range temporal values are data defects.

    Args:
        conn: psycopg sync connection (read-only, provided by orchestrator).
        profile: DatasetProfile; returns [] immediately if temporal is None.
    """
    if profile.temporal is None:
        return []

    spec = profile.temporal
    findings: list[Finding] = []
    # D-12 P29: severity from spec (default "error", "warn" for known source defects)
    sev = spec.severity.upper()

    conn.execute("SET LOCAL statement_timeout = '5min'")

    # -- below-min check -------------------------------------------------------
    if spec.col_type == "year_int":
        # Integer year comparison — pure SQL integer, no date cast (Pitfall 1)
        logger.debug(
            "check_temporal year_int below-min: %s",
            sql.SQL("SELECT count(*) FROM {t} WHERE {c} < {v}").format(
                t=sql.Identifier(profile.table),
                c=sql.Identifier(spec.date_col),
                v=sql.Literal(spec.min_val),
            ),
        )
        result = conn.execute(
            f'SELECT count(*) FROM "{profile.table}" WHERE "{spec.date_col}" < %s',
            (spec.min_val,),
        )
    else:
        # DATE/TIMESTAMP column — pass date param via %s
        logger.debug(
            "check_temporal date below-min: %s",
            sql.SQL("SELECT count(*) FROM {t} WHERE {c} < {v}").format(
                t=sql.Identifier(profile.table),
                c=sql.Identifier(spec.date_col),
                v=sql.Literal(str(spec.min_val)),
            ),
        )
        result = conn.execute(
            f'SELECT count(*) FROM "{profile.table}" WHERE "{spec.date_col}" < %s',
            (spec.min_val,),
        )

    row = result.fetchone()
    count_below = int(row[0]) if row else 0
    if count_below > 0:
        findings.append(
            Finding(
                severity=sev,
                category="temporal",
                title=(
                    f"Temporel : {count_below} ligne(s) avant"
                    f" min_val ({spec.min_val}) dans '{spec.date_col}'"
                ),
                description=(
                    f"La colonne `{spec.date_col}` de `{profile.table}` contient"
                    f" {count_below} valeur(s) antérieure(s) à {spec.min_val}."
                    f" Valeurs hors plage temporelle déclarée (D-02)."
                ),
                count=count_below,
                sample_query=(
                    f'SELECT count(*) FROM "{profile.table}"'
                    f" WHERE \"{spec.date_col}\" < '{spec.min_val}';"
                ),
                fix_hint=(
                    f"Vérifier la source ETL pour '{spec.date_col}' dans"
                    f" '{profile.table}' — aucune valeur avant {spec.min_val}."
                ),
            )
        )

    # -- above-max check (skip if max_val is None — open upper bound) ----------
    if spec.max_val is not None:
        result2 = conn.execute(
            f'SELECT count(*) FROM "{profile.table}" WHERE "{spec.date_col}" > %s',
            (spec.max_val,),
        )
        row2 = result2.fetchone()
        count_above = int(row2[0]) if row2 else 0
        if count_above > 0:
            findings.append(
                Finding(
                    severity=sev,
                    category="temporal",
                    title=(
                        f"Temporel : {count_above} ligne(s) après"
                        f" max_val ({spec.max_val}) dans '{spec.date_col}'"
                    ),
                    description=(
                        f"La colonne `{spec.date_col}` de `{profile.table}` contient"
                        f" {count_above} valeur(s) postérieure(s) à {spec.max_val}."
                        f" Valeurs hors plage temporelle déclarée (D-02)."
                    ),
                    count=count_above,
                    sample_query=(
                        f'SELECT count(*) FROM "{profile.table}"'
                        f" WHERE \"{spec.date_col}\" > '{spec.max_val}';"
                    ),
                    fix_hint=(
                        f"Vérifier la source ETL pour '{spec.date_col}' dans"
                        f" '{profile.table}' — aucune valeur après {spec.max_val}."
                    ),
                )
            )

    return findings


def check_duplicates(
    conn: psycopg.Connection, profile: DatasetProfile
) -> list[Finding]:
    """Check for duplicate rows on the natural key (DQ-COND-02, D-05 ERROR).

    Counts groups in the GROUP BY natural_key subquery that have count(*) > 1.
    Any duplicate groups → ERROR (D-05).
    Returns [] immediately if profile.duplicates is None (N/A, honest — D-01).

    Severity: ERROR (D-05) — duplicate rows on the natural key are data defects.

    Args:
        conn: psycopg sync connection (read-only, provided by orchestrator).
        profile: DatasetProfile; returns [] immediately if duplicates is None.
    """
    if profile.duplicates is None:
        return []

    spec = profile.duplicates
    findings: list[Finding] = []

    conn.execute("SET LOCAL statement_timeout = '10min'")

    # Build GROUP BY clause from natural key columns
    group_cols = ", ".join(f'"{col}"' for col in spec.natural_key)
    key_display = ", ".join(spec.natural_key)

    logger.debug(
        "check_duplicates: %s",
        sql.SQL(
            "SELECT count(*) FROM "
            "(SELECT 1 FROM {t} GROUP BY {cols} HAVING count(*) > 1) sub"
        ).format(
            t=sql.Identifier(profile.table),
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in spec.natural_key),
        ),
    )

    result = conn.execute(
        f"SELECT count(*) FROM ("
        f'SELECT 1 FROM "{profile.table}" GROUP BY {group_cols} HAVING count(*) > 1'
        f") sub"
    )
    row = result.fetchone()
    dup_groups = int(row[0]) if row else 0

    if dup_groups > 0:
        findings.append(
            Finding(
                severity="ERROR",
                category="duplicates",
                title=(
                    f"Doublons : {dup_groups} groupe(s) de doublons"
                    f" sur la clé ({key_display})"
                ),
                description=(
                    f"`{profile.table}` contient {dup_groups} combinaison(s)"
                    f" de ({key_display}) dupliquées (count(*) > 1)."
                    f" Clé naturelle non unique (D-05)."
                ),
                count=dup_groups,
                sample_query=(
                    f'SELECT {group_cols}, count(*) FROM "{profile.table}"'
                    f" GROUP BY {group_cols} HAVING count(*) > 1 LIMIT 10;"
                ),
                fix_hint=(
                    f"Dédupliquer la table '{profile.table}' sur ({key_display})"
                    f" — vérifier la source ETL."
                ),
            )
        )

    return findings


def check_format(conn: psycopg.Connection, profile: DatasetProfile) -> list[Finding]:
    """Check column values against format rules (DQ-COND-03, D-04 WARN).

    Iterates profile.format_rules; one SQL query per rule:
    - "length": count WHERE length(col) != value AND col IS NOT NULL → WARN
    - "regex":  count WHERE col !~ pattern AND col IS NOT NULL → WARN
    - "enum":   count WHERE col NOT IN (values...) AND col IS NOT NULL → WARN

    All values (lengths, patterns, enums) come from the FormatRule spec.
    Zero hard-coded business literals in this function (DQ-COND-03 lock).
    Returns [] immediately if profile.format_rules is empty (falsy — N/A, D-01).

    Severity: WARN (D-04) — format violations are data quality warnings.

    Args:
        conn: psycopg sync connection (read-only, provided by orchestrator).
        profile: DatasetProfile; returns [] immediately if format_rules is empty.
    """
    if not profile.format_rules:
        return []

    conn.execute("SET LOCAL statement_timeout = '5min'")
    findings: list[Finding] = []

    for rule in profile.format_rules:
        if rule.rule_type == "length":
            length_val = int(rule.value)  # type: ignore[arg-type]
            logger.debug(
                "check_format length: %s col=%s length=%d",
                sql.Identifier(profile.table),
                sql.Identifier(rule.col),
                length_val,
            )
            result = conn.execute(
                f'SELECT count(*) FROM "{profile.table}"'
                f' WHERE length("{rule.col}") != %s'
                f' AND "{rule.col}" IS NOT NULL',
                (length_val,),
            )
            row = result.fetchone()
            count = int(row[0]) if row else 0
            if count > 0:
                findings.append(
                    Finding(
                        severity="WARN",
                        category="format",
                        title=(
                            f"Format length : {count} valeur(s) de longueur"
                            f" ≠ {length_val} dans '{rule.col}'"
                        ),
                        description=(
                            f"La colonne `{rule.col}` de `{profile.table}` contient"
                            f" {count} valeur(s) dont la longueur"
                            f" diffère de {length_val} caractères (D-04)."
                        ),
                        count=count,
                        sample_query=(
                            f'SELECT count(*) FROM "{profile.table}"'
                            f' WHERE length("{rule.col}") != {length_val}'
                            f' AND "{rule.col}" IS NOT NULL;'
                        ),
                        fix_hint=(
                            f"Vérifier la normalisation de '{rule.col}'"
                            f" dans '{profile.table}'"
                            f" (longueur attendue = {length_val})."
                        ),
                    )
                )

        elif rule.rule_type == "regex":
            pattern = str(rule.value)
            logger.debug(
                "check_format regex: %s col=%s pattern=%s",
                sql.Identifier(profile.table),
                sql.Identifier(rule.col),
                pattern,
            )
            result = conn.execute(
                f'SELECT count(*) FROM "{profile.table}"'
                f' WHERE "{rule.col}" !~ %s'
                f' AND "{rule.col}" IS NOT NULL',
                (pattern,),
            )
            row = result.fetchone()
            count = int(row[0]) if row else 0
            if count > 0:
                findings.append(
                    Finding(
                        severity="WARN",
                        category="format",
                        title=(
                            f"Format regex : {count} valeur(s) hors pattern"
                            f" dans '{rule.col}'"
                        ),
                        description=(
                            f"La colonne `{rule.col}` de `{profile.table}` contient"
                            f" {count} valeur(s) ne correspondant pas au pattern"
                            f" `{pattern}` (D-04)."
                        ),
                        count=count,
                        sample_query=(
                            f'SELECT count(*) FROM "{profile.table}"'
                            f" WHERE \"{rule.col}\" !~ '{pattern}'"
                            f' AND "{rule.col}" IS NOT NULL;'
                        ),
                        fix_hint=(
                            f"Vérifier la normalisation de '{rule.col}'"
                            f" dans '{profile.table}' (pattern = {pattern})."
                        ),
                    )
                )

        elif rule.rule_type == "enum":
            enum_vals: tuple[str, ...] = rule.value  # type: ignore[assignment]
            # Build parameterized NOT IN — values come from spec (DQ-COND-03 lock)
            placeholders = ", ".join(["%s"] * len(enum_vals))
            logger.debug(
                "check_format enum: %s col=%s values=%s",
                sql.Identifier(profile.table),
                sql.Identifier(rule.col),
                enum_vals,
            )
            result = conn.execute(
                f'SELECT count(*) FROM "{profile.table}"'
                f' WHERE "{rule.col}" NOT IN ({placeholders})'
                f' AND "{rule.col}" IS NOT NULL',
                enum_vals,
            )
            row = result.fetchone()
            count = int(row[0]) if row else 0
            if count > 0:
                findings.append(
                    Finding(
                        severity="WARN",
                        category="format",
                        title=(
                            f"Format enum : {count} valeur(s) hors enum"
                            f" dans '{rule.col}'"
                        ),
                        description=(
                            f"La colonne `{rule.col}` de `{profile.table}` contient"
                            f" {count} valeur(s) absentes de l'enum autorisé"
                            f" {enum_vals} (D-04)."
                        ),
                        count=count,
                        sample_query=(
                            f'SELECT "{rule.col}", count(*) FROM "{profile.table}"'
                            f' WHERE "{rule.col}" NOT IN {enum_vals}'
                            f' AND "{rule.col}" IS NOT NULL'
                            f' GROUP BY "{rule.col}" LIMIT 10;'
                        ),
                        fix_hint=(
                            f"Vérifier la normalisation de '{rule.col}'"
                            f" dans '{profile.table}'"
                            f" — valeurs attendues : {enum_vals}."
                        ),
                    )
                )

    return findings


def check_integrity(conn: psycopg.Connection, profile: DatasetProfile) -> list[Finding]:
    """Check FK integrity against geo_communes (DQ-COND-04, D-03/D-03b WARN-only).

    LEFT JOIN to spec.ref_table on spec.ref_col to find orphan rows (fk_col not
    present in geo_communes.code).  Computes orphan_pct = orphans / total_non_null.
    WARN if orphan_pct > spec.warn_threshold_pct.  No ERROR band (D-03).
    Returns [] immediately if profile.integrity is None (N/A, honest — D-01).
    Returns [] immediately if total_non_null == 0 (no non-null rows to check).

    Severity: WARN-only (D-03) — integrity issues are data quality warnings.
    ref_col default "code" confirmed by migration 024 DDL (NOT "code_insee").

    Args:
        conn: psycopg sync connection (read-only, provided by orchestrator).
        profile: DatasetProfile; returns [] immediately if integrity is None.
    """
    if profile.integrity is None:
        return []

    spec = profile.integrity

    conn.execute("SET LOCAL statement_timeout = '10min'")

    logger.debug(
        "check_integrity: %s fk=%s ref=%s.%s",
        sql.Identifier(profile.table),
        sql.Identifier(spec.fk_col),
        sql.Identifier(spec.ref_table),
        sql.Identifier(spec.ref_col),
    )

    result = conn.execute(
        f"SELECT"
        f'  count(*) FILTER (WHERE t."{spec.fk_col}" IS NOT NULL) AS total_non_null,'
        f"  count(*) FILTER ("
        f'    WHERE t."{spec.fk_col}" IS NOT NULL'
        f'      AND ref."{spec.ref_col}" IS NULL'
        f"  ) AS orphans"
        f' FROM "{profile.table}" t'
        f" LEFT JOIN {spec.ref_table} ref"
        f'   ON ref."{spec.ref_col}" = t."{spec.fk_col}"'
    )
    row = result.fetchone()
    if row is None:
        return []

    total_non_null, orphans = int(row[0]), int(row[1])
    if total_non_null == 0:
        return []

    orphan_pct = orphans / total_non_null * 100

    if orphan_pct > spec.warn_threshold_pct:
        return [
            Finding(
                severity="WARN",
                category="integrity",
                title=(
                    f"Intégrité FK : {orphan_pct:.1f}% orphelins"
                    f" ({orphans}/{total_non_null}) dans '{spec.fk_col}'"
                ),
                description=(
                    f"La colonne `{spec.fk_col}` de `{profile.table}` contient"
                    f" {orphans} valeur(s) absentes de `{spec.ref_table}"
                    f".{spec.ref_col}` ({orphan_pct:.1f}%"
                    f" > seuil {spec.warn_threshold_pct}%)"
                    f" (D-03b)."
                ),
                count=orphans,
                sample_query=(
                    f'SELECT t."{spec.fk_col}", count(*) FROM "{profile.table}" t'
                    f" LEFT JOIN {spec.ref_table} ref"
                    f'   ON ref."{spec.ref_col}" = t."{spec.fk_col}"'
                    f' WHERE t."{spec.fk_col}" IS NOT NULL'
                    f' AND ref."{spec.ref_col}" IS NULL'
                    f' GROUP BY t."{spec.fk_col}" LIMIT 10;'
                ),
                fix_hint=(
                    f"Vérifier les valeurs de '{spec.fk_col}' dans '{profile.table}'"
                    f" — elles doivent toutes exister"
                    f" dans `{spec.ref_table}.{spec.ref_col}`."
                ),
            )
        ]

    return []


# ---------------------------------------------------------------------------
# PROFILES — 21 datasets (Phase 23 pilots + Phase 25 extended coverage)
# Conditional specs (temporal/duplicates/format_rules/integrity) declared only
# where DDL gives a confident signal (D-01 conservative).
# Convention: omit field entirely when N/A (default None/() handles it) —
# NEVER set temporal=None or integrity=None explicitly.
# ---------------------------------------------------------------------------

# Current year helper — used by year_int temporal specs to avoid hard-coded year.
_CURRENT_YEAR: int = date.today().year

# D-02 P29: ANFR planned commissioning dates are legitimate data (antennes).
# Calculated at import time — scheduler runs run.py as subprocess so each
# scheduled run gets a fresh import (no staleness). A hard-coded date(2028,...)
# would re-flag planned dates after 2 years without a code change.
_ANTENNES_MAX_DATE: date = date.today() + timedelta(days=2 * 365)

PROFILES: dict[str, DatasetProfile] = {
    # ------------------------------------------------------------------
    # antennes — ANFR mobile towers
    # temporal: date_mise_en_service DATE [1990, _ANTENNES_MAX_DATE]
    # format: code_insee len=5
    # integrity: code_insee → geo_communes
    # ------------------------------------------------------------------
    "antennes": DatasetProfile(
        dataset="antennes",
        table="antennes",
        geo=GEO_SPECS["antennes"],
        # National file, filtered to the hosted departements at import (D-22).
        volume=VolumeSpec(
            full_france_min=659_000,  # [VERIFIED national 2026-06]
            is_national=True,
            departement_sql="dc_departement_from_insee(code_insee)",
        ),
        freshness=FreshnessSpec(etl_dataset_name="antennes", max_days=180),
        temporal=TemporalSpec(
            date_col="date_mise_en_service",
            min_val=date(1990, 1, 1),
            # D-02 P29: ANFR planned commissioning dates up to 2 years future are
            # legitimate (operators pre-declare future sites). Using dynamic constant
            # avoids re-flagging as time passes.
            max_val=_ANTENNES_MAX_DATE,
        ),
        format_rules=(FormatRule(col="code_insee", rule_type="length", value=5),),
        # Tolérance P38 D-04 : 5.3% orphelins code_insee (43 519/825 079,
        # cert 33-05). Ne salit pas la Fiche — antennes n'est jamais rendu
        # sur Commune/Bien (grep frontend/src/lib/data/*.ts, RESEARCH), seulement
        # sur sa carte de « Données & méthode », lue dans /v1/datasets, qui ne
        # sert aucune valeur de la table (03-11). Toléré, pas de fix.
        integrity=FkSpec(fk_col="code_insee"),
    ),
    # ------------------------------------------------------------------
    # ban — Base Adresses Nationales (partitioned — no duplicates D-01)
    # format: code_postal regex 5d + code_commune len=5
    # integrity: code_commune → geo_communes
    # ------------------------------------------------------------------
    "ban": DatasetProfile(
        dataset="ban",
        table="adresses",
        # Superseded by the producer counts (plan 04.1-17, app.scope.floors
        # .producer_floor): kept only for a base without them.
        # count 44+85 = 1 019 793 -> scoped floor ~80% = 800 000.
        # full_france_min remains extrapolated so a 44,85 instance scope
        # scales back to the scoped floor in _compute_volume_floor.
        volume=VolumeSpec(
            full_france_min=40_800_000,
        ),
        freshness=FreshnessSpec(etl_dataset_name="ban", max_days=365),
        format_rules=(
            FormatRule(col="code_postal", rule_type="regex", value=r"^\d{5}$"),
            FormatRule(col="code_commune", rule_type="length", value=5),
        ),
        integrity=FkSpec(fk_col="code_commune"),
    ),
    # ------------------------------------------------------------------
    # batiments — BDTOPO buildings (partitioned, no code commune col)
    # All 4 conditional dims = N/A (D-01)
    # ------------------------------------------------------------------
    "batiments": DatasetProfile(
        dataset="batiments",
        table="batiments",
        # Acquitté P38 D-04 : 15.30% hors-dept déjà documenté et calibré
        # (geo_bounds.py GEO_SPECS["batiments"], out_of_dept_error_pct=20.0 —
        # artefact de partition IGN BDTOPO, pas un bug de coordonnées).
        # Ne salit pas la Fiche (batiments absent de bien-fiche.ts/
        # commune-fiche.ts). Formalisé, aucun changement de code.
        geo=GEO_SPECS["batiments"],
        # count 44+85 = 2 319 238 -> scoped floor ~80% = 1 800 000.
        # Extrapolated full-France equivalent: 1_800_000 * 102 / 2.
        volume=VolumeSpec(
            full_france_min=91_800_000,
        ),
        freshness=FreshnessSpec(etl_dataset_name="batiments", max_days=365),
    ),
    # ------------------------------------------------------------------
    # cadastre — DGFIP cadastral parcels (partitioned — no duplicates D-01)
    # format: commune len=5 (col named "commune" but holds a 5-char code)
    # integrity: N/A — colonne "commune" ambiguë (D-01 conservateur)
    # ------------------------------------------------------------------
    "cadastre": DatasetProfile(
        dataset="cadastre",
        table="parcelles",
        geo=GEO_SPECS["cadastre"],
        # Superseded by the producer counts (plan 04.1-17, app.scope.floors
        # .producer_floor): kept only for a base without them.
        # count 44+85 = 2 850 109 -> scoped floor ~80% = 2 200 000.
        # Extrapolated full-France equivalent: 2_200_000 * 102 / 2.
        volume=VolumeSpec(
            full_france_min=112_200_000,
        ),
        # Cadastre updated annually (max 365 days)
        freshness=FreshnessSpec(etl_dataset_name="cadastre", max_days=365),
        nulls=NullSpec(
            required_cols=("code_departement", "id_parcelle"),
            expected_cols=("commune", "geometrie"),
        ),
        format_rules=(FormatRule(col="commune", rule_type="length", value=5),),
        # integrity: N/A — "commune" col usage ambiguous for FK vs partition key
    ),
    # ------------------------------------------------------------------
    # connectivite — ARCEP fixed broadband eligibility
    # format: code_insee len=5
    # integrity: code_insee → geo_communes
    # ------------------------------------------------------------------
    "connectivite": DatasetProfile(
        dataset="connectivite",
        table="eligibilite_fixe",
        # Superseded by the producer counts (plan 04.1-17, app.scope.floors
        # .producer_floor): kept only for a base without them.
        # One row per ARCEP building since plan 04.1-16 (offers in ``offres``).
        # Buildings 44+85 = 1 018 201 (mesures/avant/eligibilite-forme.json,
        # 2026-10-12) -> scoped floor ~80% = 800 000. PROVISIONAL extrapolated
        # full-France equivalent 800_000 * 102 / 2, recalibrated per
        # departement on the producer counts by plan 04.1-17.
        volume=VolumeSpec(
            full_france_min=40_800_000,
        ),
        freshness=FreshnessSpec(etl_dataset_name="connectivite", max_days=365),
        format_rules=(FormatRule(col="code_insee", rule_type="length", value=5),),
        integrity=FkSpec(fk_col="code_insee"),
    ),
    # ------------------------------------------------------------------
    # copropriete — RNIC condominiums
    # temporal: annee_reglement year_int [1900, current_year]
    #   D-03/D-07 P29: SQL diagnostic (2026-06-13) showed distribution
    #   1900-1949 (2202 rows for 1949, 2047 for 1901, etc.) = legitimate
    #   pre-war règlements de copropriété (natural distribution, NOT 1900/9999
    #   mass placeholders). min_val widened from 1950 to 1900.
    #   169 future anomalies (max observed: 9182) > _CURRENT_YEAR → still ERROR.
    # duplicates: numero_immatriculation (UNIQUE DDL verified)
    # format: code_postal regex 5d + code_commune len=5
    # integrity: code_commune → geo_communes
    # ------------------------------------------------------------------
    "copropriete": DatasetProfile(
        dataset="copropriete",
        table="copropriete",
        # National file, filtered to the hosted departements at import (D-22).
        volume=VolumeSpec(
            full_france_min=510_000,  # [VERIFIED national 2026-06]
            is_national=True,
            departement_sql="dc_departement_from_insee(code_commune)",
        ),
        freshness=FreshnessSpec(etl_dataset_name="copropriete", max_days=365),
        temporal=TemporalSpec(
            date_col="annee_reglement",
            # D-03 P29: widened from 1950->1900 — pre-war regulations are legitimate
            # (diagnostic: 1900-1949 natural distribution, not placeholders)
            # D-05 P29: severity=warn — 32 rows < 1900 (min=101) + 169 rows >
            # current_year (max=9182) are genuine RNE source anomalies (0.03% of
            # 637k) — cannot fix upstream; matrice honnête 🟠 > 🔴 (CALIBRATION.md)
            # Acquitté P38 D-04 : copropriete n'est jamais rendu sur la Fiche
            # (marketing only) — pas de fix, formalisation seule.
            min_val=1900,
            max_val=_CURRENT_YEAR,
            col_type="year_int",
            severity="warn",
        ),
        duplicates=DuplicatesSpec(natural_key=("numero_immatriculation",)),
        format_rules=(
            FormatRule(col="code_postal", rule_type="regex", value=r"^\d{5}$"),
            FormatRule(col="code_commune", rule_type="length", value=5),
        ),
        # Ticket backlog P38 D-04 : 62.2% orphelins code_commune (396 379/
        # 637 286, cert 33-05) — inhabituellement élevé. Cause [ASSUMED],
        # non confirmée : convention code RNIC potentiellement différente
        # d'INSEE. Ne salit pas la Fiche (copropriete absent de bien-fiche.ts/
        # commune-fiche.ts) → pas de root fix ce cycle. Voir 38-WARN-TRIAGE.md.
        integrity=FkSpec(fk_col="code_commune"),
    ),
    # ------------------------------------------------------------------
    # demographie — INSEE demographic stats (commune-level)
    # duplicates: (code_insee, annee) UNIQUE DDL verified
    # format: code_insee len=5
    # integrity: code_insee → geo_communes
    # ------------------------------------------------------------------
    "demographie": DatasetProfile(
        dataset="demographie",
        table="demographie",
        geo=None,  # non-spatial dataset — proves harness works without geometry
        # National file, filtered to the hosted departements at import (D-22).
        volume=VolumeSpec(
            full_france_min=28_000,  # [VERIFIED national 2026-06]
            is_national=True,
        ),
        # INSEE demographic data updated annually (max 365 days)
        freshness=FreshnessSpec(etl_dataset_name="demographie", max_days=365),
        # code_departement is NULLABLE in demographie — never in required (D-04)
        # Population has INSEE secret statistique → tolerant threshold (5%)
        nulls=NullSpec(
            required_cols=("code_insee", "annee"),
            expected_cols=("population", "code_departement"),
            expected_threshold=0.05,
        ),
        duplicates=DuplicatesSpec(natural_key=("code_insee", "annee")),
        format_rules=(FormatRule(col="code_insee", rule_type="length", value=5),),
        integrity=FkSpec(fk_col="code_insee"),
    ),
    # ------------------------------------------------------------------
    # dpe — ADEME diagnostics de performance énergétique
    # All 4 conditional dims declared (most complete table — D-01 4/4)
    # temporal: date_etablissement_dpe DATE [2006-01-01, today]
    # duplicates: numero_dpe (UNIQUE DDL verified)
    # format: code_postal regex + code_insee len + etiquette_dpe enum + ges enum
    # integrity: code_insee → geo_communes
    # ------------------------------------------------------------------
    "dpe": DatasetProfile(
        dataset="dpe",
        table="diagnostics_dpe",
        geo=GEO_SPECS["dpe"],
        # Superseded by the producer counts (plan 04.1-17, app.scope.floors
        # .producer_floor): kept only for a base without them.
        # count 44+85 = 464 067 -> scoped floor ~80% = 350 000.
        # Extrapolated full-France equivalent: 350_000 * 102 / 2.
        volume=VolumeSpec(
            full_france_min=17_850_000,
        ),
        freshness=FreshnessSpec(etl_dataset_name="dpe", max_days=180),
        temporal=TemporalSpec(
            date_col="date_etablissement_dpe",
            min_val=date(2006, 1, 1),
            max_val=date.today(),
        ),
        duplicates=DuplicatesSpec(natural_key=("numero_dpe",)),
        format_rules=(
            FormatRule(col="code_postal", rule_type="regex", value=r"^\d{5}$"),
            FormatRule(col="code_insee", rule_type="length", value=5),
            FormatRule(
                col="etiquette_dpe",
                rule_type="enum",
                value=("A", "B", "C", "D", "E", "F", "G", "N"),
            ),
            FormatRule(
                col="etiquette_ges",
                rule_type="enum",
                value=("A", "B", "C", "D", "E", "F", "G", "N"),
            ),
        ),
        integrity=FkSpec(fk_col="code_insee"),
    ),
    # ------------------------------------------------------------------
    # dvf — DGFiP mutations foncières (partitioned — no duplicates D-01)
    # temporal: date_mutation DATE [2014-01-01, today]
    # format: code_postal regex 5d + code_commune len=5
    # integrity: code_commune → geo_communes
    # ------------------------------------------------------------------
    "dvf": DatasetProfile(
        dataset="dvf",
        table="mutations",
        geo=GEO_SPECS["dvf"],
        # Superseded by the producer counts (plan 04.1-17, app.scope.floors
        # .producer_floor): kept only for a base without them.
        # count 44+85 = 698 299 -> scoped floor ~80% = 550 000.
        # Extrapolated full-France equivalent: 550_000 * 102 / 2.
        volume=VolumeSpec(
            full_france_min=28_050_000,
        ),
        freshness=FreshnessSpec(etl_dataset_name="dvf", max_days=365),
        temporal=TemporalSpec(
            date_col="date_mutation",
            min_val=date(2014, 1, 1),
            max_val=date.today(),
        ),
        format_rules=(
            FormatRule(col="code_postal", rule_type="regex", value=r"^\d{5}$"),
            FormatRule(col="code_commune", rule_type="length", value=5),
        ),
        integrity=FkSpec(fk_col="code_commune"),
        # [P26] business hook — prix/m² outlier detection, WARN-only (D-02/D-03)
        # Tolérance P38 D-04 : impact Fiche = 3 259 lignes (0.47%) avec prix/m²
        # hors [100, 50 000] € (cert 33-05) peuvent apparaître dans mapDvfRows.
        # Le prix lui-même est une transaction DGFiP réelle — masquer cacherait
        # une vraie vente ; seul le ratio prix/m² est aberrant (probable terrain
        # nu compté comme bâti). Diagnostic root back-loggé (garde display-layer
        # optionnelle dans dvf/queries.py) — voir 38-WARN-TRIAGE.md.
        business_hook=check_dvf_prix_m2,
    ),
    # ------------------------------------------------------------------
    # encadrement_loyers — DRIHL rent control zones
    # temporal: annee year_int [2015, current_year+1] (re-publication future ok)
    # duplicates: 6-col composite UNIQUE (verified DDL mig 028)
    # format: N/A — code_insee_quartier is sub-commune / PLM arrondissements
    # integrity: N/A — Pitfall 5: arrondissements PLM absent from geo_communes
    # ------------------------------------------------------------------
    "encadrement_loyers": DatasetProfile(
        dataset="encadrement_loyers",
        table="encadrement_loyers",
        geo=GEO_SPECS["encadrement_loyers"],
        # National file, filtered to the hosted departements at import (D-22).
        # Note: dataset covers only zones tendues (Paris + IDF + quelques métropoles)
        # and is intentionally sparse outside covered markets.
        volume=VolumeSpec(
            full_france_min=14_000,  # [VERIFIED national 2026-06]
            is_national=True,
            departement_sql="dc_departement_from_insee(code_insee_quartier)",
        ),
        freshness=FreshnessSpec(etl_dataset_name="encadrement_loyers", max_days=365),
        temporal=TemporalSpec(
            date_col="annee",
            min_val=2015,
            max_val=_CURRENT_YEAR + 1,
            col_type="year_int",
        ),
        duplicates=DuplicatesSpec(
            natural_key=(
                "annee",
                "ville",
                "numero_quartier",
                "nb_pieces",
                "epoque_construction",
                "type_location",
            )
        ),
        # format_rules omitted — N/A (code_insee_quartier = sous-commune)
        # integrity omitted — N/A (Pitfall 5: arrondissements PLM)
    ),
    # ------------------------------------------------------------------
    # equipements — BPE INSEE public services
    # format: code_commune len=5
    # integrity: code_commune → geo_communes
    # ------------------------------------------------------------------
    "equipements": DatasetProfile(
        dataset="equipements",
        table="equipements",
        geo=GEO_SPECS["equipements"],
        # count national = 2 827 407 → floor ~80% = 2 261 926 (P27-03)
        # is_national=True: national BPE file, filtered to the scope (D-22)
        volume=VolumeSpec(
            # [VERIFIED dept85 2026-06] kept ~88% for BPE inter-year stability
            full_france_min=2_500_000,
            is_national=True,  # national file filtered to the scope (D-22)
        ),
        # BPE = Base Permanente des Équipements, updated annually (max 180 days)
        freshness=FreshnessSpec(etl_dataset_name="equipements", max_days=180),
        # required: NOT NULL in DDL + core business keys; expected: nullable cols
        nulls=NullSpec(
            required_cols=("type_equipement", "code_commune", "annee"),
            expected_cols=("code_departement", "geom"),
        ),
        format_rules=(FormatRule(col="code_commune", rule_type="length", value=5),),
        # Tolérance P38 D-04 : impact Fiche = 7.1% orphelins code_commune
        # (193 988/2 722 677, cert 33-05) → sous-comptage possible de
        # c.nb_equipements (commune-fiche.ts) pour les communes affectées.
        # Diagnostic root back-loggé ([ASSUMED] codes fusionnés/historiques,
        # non confirmé) — voir 38-WARN-TRIAGE.md.
        integrity=FkSpec(fk_col="code_commune"),
    ),
    # ------------------------------------------------------------------
    # fiscalite — DGFiP local tax data
    # duplicates: (code_insee, annee) UNIQUE DDL verified
    # format: code_insee len=5
    # integrity: code_insee → geo_communes
    # ------------------------------------------------------------------
    "fiscalite": DatasetProfile(
        dataset="fiscalite",
        table="fiscalite_locale",
        # National file, filtered to the hosted departements at import (D-22).
        volume=VolumeSpec(
            full_france_min=140_000,  # [VERIFIED national 2026-06]
            is_national=True,
        ),
        freshness=FreshnessSpec(etl_dataset_name="fiscalite", max_days=365),
        duplicates=DuplicatesSpec(natural_key=("code_insee", "annee")),
        format_rules=(FormatRule(col="code_insee", rule_type="length", value=5),),
        integrity=FkSpec(fk_col="code_insee"),
    ),
    # ------------------------------------------------------------------
    # foncier_pm — MAJIC persons morales (partitioned — no duplicates D-01)
    # format: code_commune len=5
    # integrity: code_commune → geo_communes
    # ------------------------------------------------------------------
    "foncier_pm": DatasetProfile(
        dataset="foncier_pm",
        table="foncier_pm",
        # count national = 21 074 593 → floor ~80% = 16 859 674 (P27-03)
        # is_national=True: national MAJIC PM files, filtered to the scope (D-22)
        volume=VolumeSpec(
            full_france_min=16_800_000,  # [VERIFIED dept85 2026-06] ~80% of 21M
            is_national=True,  # national file filtered to the scope (D-22)
        ),
        freshness=FreshnessSpec(etl_dataset_name="foncier_pm", max_days=365),
        format_rules=(FormatRule(col="code_commune", rule_type="length", value=5),),
        integrity=FkSpec(fk_col="code_commune"),
    ),
    # ------------------------------------------------------------------
    # loyers — CLAMEUR / OLAP rental indicators
    # duplicates: (code_insee, type_habitat, annee) UNIQUE DDL verified
    # format: code_insee len=5
    # integrity: code_insee → geo_communes
    # ------------------------------------------------------------------
    "loyers": DatasetProfile(
        dataset="loyers",
        table="indicateurs_loyers",
        # National file, filtered to the hosted departements at import (D-22).
        volume=VolumeSpec(
            full_france_min=112_000,  # [VERIFIED national 2026-06]
            is_national=True,
        ),
        freshness=FreshnessSpec(etl_dataset_name="loyers", max_days=365),
        duplicates=DuplicatesSpec(natural_key=("code_insee", "type_habitat", "annee")),
        format_rules=(FormatRule(col="code_insee", rule_type="length", value=5),),
        integrity=FkSpec(fk_col="code_insee"),
    ),
    # ------------------------------------------------------------------
    # monuments — Mérimée/Palissy historical monuments
    # duplicates: reference [ASSUMED — B-tree index, MEDIUM signal]
    # format: code_insee len=5
    # integrity: code_insee → geo_communes
    # ------------------------------------------------------------------
    "monuments": DatasetProfile(
        dataset="monuments",
        table="monuments_historiques",
        # Acquitté P38 D-04 : 0.86% hors-dept déjà documenté (voir ci-dessous,
        # artefact DOM). Feeds Fiche via c.nb_monuments (commune-fiche.ts) —
        # mais l'impact est déjà qualifié/calibré, pas de nouveau fix.
        geo=GEO_SPECS["monuments"],
        # National file, filtered to the hosted departements at import (D-22).
        # Note: 716 hors-bounds = DOM monuments (971/972/974/976) — structural artefact
        # geo_departements = France métro only (D-10 P28). Remains 🟠 by design.
        volume=VolumeSpec(
            full_france_min=35_000,  # [VERIFIED national 2026-06]
            is_national=True,
        ),
        freshness=FreshnessSpec(etl_dataset_name="monuments", max_days=365),
        duplicates=DuplicatesSpec(natural_key=("reference",)),
        format_rules=(FormatRule(col="code_insee", rule_type="length", value=5),),
        # Tolérance P38 D-04 : impact Fiche = 6.8% orphelins code_insee
        # (3 009/43 967, cert 33-05) → sous-comptage possible de
        # c.nb_monuments (commune-fiche.ts) pour les communes affectées.
        # Diagnostic root back-loggé — voir 38-WARN-TRIAGE.md.
        integrity=FkSpec(fk_col="code_insee"),
    ),
    # ------------------------------------------------------------------
    # permis: partial local resource, publicly quarantined (COR-07).
    # Historical date_depot was fabricated from commencement; assess the
    # distinct source decision date instead. Stock volume is not coverage.
    # ------------------------------------------------------------------
    "permis": DatasetProfile(
        dataset="permis",
        table="permis",
        volume=VolumeSpec(
            # Preserve the historical unpartitioned-stock guard: the source is
            # local to departement 93 (KI-16), not a national file, so its
            # floor is never scaled to the scope (CR-01: no collapse to ~5).
            full_france_min=582,
            local_source=True,
            severity="warn",
            source_known_partial=True,
        ),
        freshness=FreshnessSpec(etl_dataset_name="permis", max_days=180),
        temporal=TemporalSpec(
            date_col="date_decision",
            min_val=date(2005, 1, 1),
            max_val=None,
        ),
        # One row per destination of a dossier (migration 109).
        duplicates=DuplicatesSpec(natural_key=("numero_dossier", "destination")),
        format_rules=(FormatRule(col="code_commune", rule_type="length", value=5),),
        integrity=FkSpec(fk_col="code_commune"),
    ),
    # ------------------------------------------------------------------
    # revenus — INSEE Filosofi income data
    # duplicates: (code_insee, annee) UNIQUE DDL verified
    # format: code_insee len=5
    # integrity: code_insee → geo_communes
    # ------------------------------------------------------------------
    "revenus": DatasetProfile(
        dataset="revenus",
        table="revenus",
        # National file, filtered to the hosted departements at import (D-22).
        volume=VolumeSpec(
            full_france_min=28_000,  # [VERIFIED national 2026-06]
            is_national=True,
        ),
        freshness=FreshnessSpec(etl_dataset_name="revenus", max_days=365),
        duplicates=DuplicatesSpec(natural_key=("code_insee", "annee")),
        format_rules=(FormatRule(col="code_insee", rule_type="length", value=5),),
        integrity=FkSpec(fk_col="code_insee"),
    ),
    # ------------------------------------------------------------------
    # scolaire — DEPP carte scolaire
    # format: code_postal regex 5d + code_insee len=5
    # integrity: code_insee → geo_communes
    # ------------------------------------------------------------------
    "scolaire": DatasetProfile(
        dataset="scolaire",
        table="carte_scolaire",
        # National file, filtered to the hosted departements at import (D-22).
        volume=VolumeSpec(
            full_france_min=417_000,  # [VERIFIED national 2026-06]
            is_national=True,
            departement_sql="dc_departement_from_insee(code_insee)",
        ),
        freshness=FreshnessSpec(etl_dataset_name="scolaire", max_days=365),
        format_rules=(
            FormatRule(col="code_postal", rule_type="regex", value=r"^\d{5}$"),
            FormatRule(col="code_insee", rule_type="length", value=5),
        ),
        # Tolérance P38 D-04 : impact Fiche = 4.7% orphelins code_insee
        # (24 369/520 675, cert 33-05) → sous-comptage possible de
        # c.nb_etablissements_scolaires (commune-fiche.ts) pour les communes
        # affectées. Diagnostic root back-loggé — voir 38-WARN-TRIAGE.md.
        integrity=FkSpec(fk_col="code_insee"),
    ),
    # ------------------------------------------------------------------
    # transport — GTFS transit stops (spatial_mode, no commune col)
    # All 4 conditional dims = N/A (D-01)
    # ------------------------------------------------------------------
    "transport": DatasetProfile(
        dataset="transport",
        table="arrets_transport",
        # Acquitté P38 D-04 : 5.55% hors-France déjà documenté et calibré
        # (geo_bounds.py GEO_SPECS["transport"], no_dept_warn_threshold_pct=6.0 —
        # DOM + trajets transfrontaliers légitimes, pas un bug CRS). Ne salit
        # pas la Fiche (transport absent de bien-fiche.ts/commune-fiche.ts).
        # Formalisé, aucun changement de code.
        geo=GEO_SPECS["transport"],
        # National file, filtered to the hosted departements at import (D-22).
        volume=VolumeSpec(
            full_france_min=292_000,  # [VERIFIED national 2026-06]
            is_national=True,
            departement_sql=POINT_DEPARTEMENT_SQL.format(point="position"),
        ),
        # D-04 P38 root fix: ETL logs runs under 'transports' (plural) via
        # PIPELINE_MAP + pipeline.dataset_name — the singular key here made
        # this freshness check permanently blind (cert 33-05 WARN #13,
        # test_freshness.py::test_transport_freshness_uses_plural_dataset_name).
        freshness=FreshnessSpec(etl_dataset_name="transports", max_days=180),
    ),
    # ------------------------------------------------------------------
    # vacance — LOVAC vacant housing
    # duplicates: (code_commune, annee) UNIQUE DDL verified
    # format: code_commune len=5
    # integrity: code_commune → geo_communes
    # ------------------------------------------------------------------
    "vacance": DatasetProfile(
        dataset="vacance",
        table="vacance",
        # National file, filtered to the hosted departements at import (D-22).
        volume=VolumeSpec(
            full_france_min=28_000,  # [VERIFIED national 2026-06]
            is_national=True,
        ),
        freshness=FreshnessSpec(etl_dataset_name="vacance", max_days=365),
        duplicates=DuplicatesSpec(natural_key=("code_commune", "annee")),
        format_rules=(FormatRule(col="code_commune", rule_type="length", value=5),),
        integrity=FkSpec(fk_col="code_commune"),
    ),
    # ------------------------------------------------------------------
    # zonage_abc — Ministère du Logement ABC zoning
    # temporal: date_arrete DATE [1990-01-01, None] (future revisions possible)
    # duplicates: code_insee (UNIQUE DDL verified)
    # format: code_insee len=5 + zone enum [Abis,A,B1,B2,C]
    # integrity: code_insee → geo_communes
    # ------------------------------------------------------------------
    "zonage_abc": DatasetProfile(
        dataset="zonage_abc",
        table="zonage_abc",
        # National file, filtered to the hosted departements at import (D-22).
        volume=VolumeSpec(
            full_france_min=28_000,  # [VERIFIED national 2026-06]
            is_national=True,
        ),
        freshness=FreshnessSpec(etl_dataset_name="zonage_abc", max_days=365),
        temporal=TemporalSpec(
            date_col="date_arrete",
            min_val=date(1990, 1, 1),
            max_val=None,  # future arrêtés are legitimate (periodic updates)
        ),
        duplicates=DuplicatesSpec(natural_key=("code_insee",)),
        format_rules=(
            FormatRule(col="code_insee", rule_type="length", value=5),
            FormatRule(
                col="zone",
                rule_type="enum",
                value=("Abis", "A", "B1", "B2", "C"),
            ),
        ),
        integrity=FkSpec(fk_col="code_insee"),
    ),
}
