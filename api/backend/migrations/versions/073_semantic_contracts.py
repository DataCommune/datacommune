"""Convert fiscalite_locale rate columns from fraction to percentage points.

Revision ID: 073_semantic_contracts
Revises: 072_mutations_idx
Create Date: 2026-07-14

DATA-01/D-01/D-02 (Phase 43): the four fiscalite_locale rate columns
(taux_tfpb, taux_tfpnb, taux_th_rs, taux_teom) were stored as a fraction
(NUMERIC(6,5), migration 027) while contracts/fiche-fields.v1.json
(43-03) documents them as percentage points (unit=percent_point,
fraction_digits=2) -- and the DGFiP source itself is already
percentage points (e.g. "40,01" means 40.01%). app/etl_fiscalite/pipeline.py
was dividing by 100 before insert; every Fiche consumer then compensated
with an implicit *100 at the view layer. This migration fixes the storage
side: convert once, widen the column, never divide again.

Repeats -- transactionally, right before mutating -- the exact preconditions
verified read-only by scripts/preflight_semantic_contracts.py: abort unless
all four columns share one known storage type (legacy fraction NUMERIC(6,5)
or already-migrated points NUMERIC(5,2)), if a value is negative or cannot
fit the reversible NUMERIC(5,2) target exactly, or if fiscalite_locale has an
unknown dependent view/matview. Values do not identify the unit: legitimate
global TFNB rates can exceed 100 points, hence legacy ratios can exceed 1.
The preflight script alone cannot close the TOCTOU window between "preflight
says OK" and "migration starts" -- only this in-transaction precondition can.

Order of operations (T-43-04): capture the CURRENT definition and indexes of
v_commune_synthese -> DROP it (a materialized view cannot survive ALTER
COLUMN TYPE on a column it selects) -> ALTER the four columns -> recreate
v_commune_synthese exactly as captured (the seven per-dataset intermediate
matviews it LEFT JOINs are untouched -- they don't depend on
fiscalite_locale).

Why "as captured" and not app/commune/matview_ddl.py (2026-08-25): this
migration used to import CREATE_SQL from the app, i.e. the matview DDL at
`head`. A migration frozen at revision 073 must never depend on head code:
as soon as 57-09 made that DDL read columns that only migration 088 creates
(`v_commune_dvf.prix_m2_median_maison`), every FRESH chain 0 -> head broke
here (`column dvf.prix_m2_median_maison does not exist`, ci-data-contracts
2026-08-25) while every already-migrated database was unaffected. Restoring
what was dropped is the only definition that is correct at THIS revision,
whatever later migrations do to the matview (088 rebuilds it anyway).
Guarded by tests/test_migrations_never_import_app.py.

Idempotent by construction: if the columns are already NUMERIC(5,2) (state
"points" -- this migration re-run out of band, or a downgrade/upgrade
cycle), the ALTER step is skipped; only the matview rebuild runs, which is
itself DROP-IF-EXISTS+CREATE-safe. A non-negative/representability
postcondition runs after either path.

Downgrade fails closed: refuses to guess a reversal unless every rate
column is currently in the points state it expects (same discipline as
upgrade's own preconditions) -- protects against reverting a table that
received new points-format writes it can't safely re-derive a fraction from.
"""

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

# Local copy of matview_ddl.DROP_SQL: migrations never import app code (a
# migration is frozen at its revision, the app is not -- see module docstring).
DROP_SQL = "DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE"

revision: str = "073_semantic_contracts"
down_revision: str = "072_mutations_idx"
branch_labels: str | None = None
depends_on: str | None = None

TABLE = "fiscalite_locale"
RATE_COLUMNS: tuple[str, ...] = (
    "taux_tfpb",
    "taux_tfpnb",
    "taux_th_rs",
    "taux_teom",
)
KNOWN_DEPENDENT_VIEWS = frozenset({"v_commune_synthese"})

_RATIO_PRECISION_SCALE = (6, 5)
_POINTS_PRECISION_SCALE = (5, 2)
_TARGET_MAX = 999.99

_RATE_COLUMNS_IN_CLAUSE = ", ".join(f"'{col}'" for col in RATE_COLUMNS)


def _check_preconditions(conn: Connection) -> str:
    """Repeat the preflight's checks transactionally. Returns storage state.

    Raises RuntimeError -- aborting the whole migration transaction -- on
    any unexpected/mixed storage type, unrepresentable value or unknown
    dependency.
    """
    rows = conn.execute(
        text(
            "SELECT column_name, data_type, numeric_precision, numeric_scale "
            "FROM information_schema.columns "
            f"WHERE table_name = :table "
            f"AND column_name IN ({_RATE_COLUMNS_IN_CLAUSE})"
        ),
        {"table": TABLE},
    ).fetchall()
    types = {
        r.column_name: (r.data_type, r.numeric_precision, r.numeric_scale) for r in rows
    }

    missing = set(RATE_COLUMNS) - types.keys()
    if missing:
        raise RuntimeError(f"073: colonnes manquantes sur {TABLE}: {sorted(missing)}")

    for col in RATE_COLUMNS:
        data_type, precision, scale = types[col]
        if data_type != "numeric" or (precision, scale) not in (
            _RATIO_PRECISION_SCALE,
            _POINTS_PRECISION_SCALE,
        ):
            raise RuntimeError(
                f"073: type/precision inattendus pour {col}: "
                f"{data_type}({precision},{scale}) -- attendu "
                f"numeric{_RATIO_PRECISION_SCALE} ou numeric{_POINTS_PRECISION_SCALE}"
            )

    configurations = {(precision, scale) for _, precision, scale in types.values()}
    if configurations == {_RATIO_PRECISION_SCALE}:
        state = "ratio"
    elif configurations == {_POINTS_PRECISION_SCALE}:
        state = "points"
    else:
        raise RuntimeError(
            "073: types de stockage mixtes sur fiscalite_locale -- les quatre "
            "taux doivent etre tous numeric(6,5) ou tous numeric(5,2)"
        )

    multiplier = 100 if state == "ratio" else 1
    for col in RATE_COLUMNS:
        row = conn.execute(
            text(
                "SELECT count(*) FILTER ("
                f"WHERE {col} < 0 "
                f"OR ROUND({col} * :multiplier, 2) > :target_max "
                f"OR {col} * :multiplier != ROUND({col} * :multiplier, 2)"
                ") AS invalid "
                f"FROM {TABLE}"
            ),
            {"multiplier": multiplier, "target_max": _TARGET_MAX},
        ).one()
        if row.invalid:
            raise RuntimeError(
                f"073: {row.invalid} valeur(s) de {col} negative(s) ou non "
                f"representable(s) exactement en numeric{_POINTS_PRECISION_SCALE}"
            )

    deps = {
        r.dependent_view
        for r in conn.execute(
            text(
                "SELECT DISTINCT dependent_view.relname AS dependent_view "
                "FROM pg_depend "
                "JOIN pg_rewrite ON pg_depend.objid = pg_rewrite.oid "
                "JOIN pg_class AS dependent_view "
                "  ON pg_rewrite.ev_class = dependent_view.oid "
                "JOIN pg_class AS source_table "
                "  ON pg_depend.refobjid = source_table.oid "
                "WHERE source_table.relname = :table "
                "  AND dependent_view.relname != :table"
            ),
            {"table": TABLE},
        )
    }
    unknown = deps - KNOWN_DEPENDENT_VIEWS
    if unknown:
        raise RuntimeError(
            f"073: dépendances inconnues sur {TABLE}: {sorted(unknown)} -- "
            f"seule {sorted(KNOWN_DEPENDENT_VIEWS)} est prise en compte par "
            "cette migration"
        )

    return state


def _capture_v_commune_synthese(conn: Connection) -> tuple[str, list[str]] | None:
    """Return (SELECT body, index DDLs) of the live matview, or None if absent.

    Read BEFORE any DROP. The definition is whatever the chain built at this
    revision (067/068 on a fresh chain), never the app's head DDL -- see the
    module docstring for the 2026-08-25 breakage this prevents.
    """
    exists = conn.execute(
        text("SELECT to_regclass('v_commune_synthese') IS NOT NULL")
    ).scalar()
    if not exists:
        return None
    viewdef = conn.execute(
        text("SELECT pg_get_viewdef('v_commune_synthese'::regclass, true)")
    ).scalar()
    if not viewdef:
        return None
    indexes = [
        r[0]
        for r in conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'v_commune_synthese' ORDER BY indexname"
            )
        )
    ]
    return str(viewdef).rstrip().rstrip(";"), indexes


def _rebuild_v_commune_synthese(captured: tuple[str, list[str]] | None) -> None:
    """DROP + CREATE v_commune_synthese from its captured definition (T-43-04).

    Only the synthese itself is touched: the seven intermediate matviews it
    LEFT JOINs don't select from fiscalite_locale and are left untouched.
    Nothing to rebuild when the chain had no matview to capture.
    """
    op.execute(DROP_SQL)
    if captured is None:
        return
    viewdef, indexes = captured
    op.execute(f"CREATE MATERIALIZED VIEW v_commune_synthese AS {viewdef}")
    for sql in indexes:
        op.execute(sql)


def upgrade() -> None:
    conn = op.get_bind()
    state = _check_preconditions(conn)
    captured = _capture_v_commune_synthese(conn)

    if state == "ratio":
        op.execute(DROP_SQL)  # matview must go before ALTER COLUMN TYPE
        for col in RATE_COLUMNS:
            op.execute(
                f"ALTER TABLE {TABLE} ALTER COLUMN {col} TYPE NUMERIC(5, 2) "
                f"USING ROUND({col} * 100, 2)"
            )
    # state == "points": already migrated -- ALTER skipped (idempotent).

    _rebuild_v_commune_synthese(captured)

    # Postcondition: every rate is non-negative and target-representable.
    out_of_range_clause = " OR ".join(
        f"{c} < 0 OR {c} > {_TARGET_MAX}" for c in RATE_COLUMNS
    )
    row = conn.execute(
        text(f"SELECT count(*) FILTER (WHERE {out_of_range_clause}) FROM {TABLE}")
    ).scalar_one()
    if row:
        raise RuntimeError(
            f"073: {row} ligne(s) hors de la plage technique "
            f"[0, {_TARGET_MAX}] points apres conversion -- annulation"
        )


def downgrade() -> None:
    conn = op.get_bind()

    rows = conn.execute(
        text(
            "SELECT column_name, numeric_precision, numeric_scale "
            "FROM information_schema.columns "
            f"WHERE table_name = :table "
            f"AND column_name IN ({_RATE_COLUMNS_IN_CLAUSE})"
        ),
        {"table": TABLE},
    ).fetchall()
    types = {r.column_name: (r.numeric_precision, r.numeric_scale) for r in rows}

    if any(types.get(c) != _POINTS_PRECISION_SCALE for c in RATE_COLUMNS):
        raise RuntimeError(
            "073 downgrade: les colonnes de taux de fiscalite_locale ne "
            "sont pas toutes dans l'état points attendu -- refus de "
            "deviner une conversion (fail-closed, même discipline que les "
            "préconditions d'upgrade)"
        )

    captured = _capture_v_commune_synthese(conn)
    op.execute(DROP_SQL)
    for col in RATE_COLUMNS:
        op.execute(
            f"ALTER TABLE {TABLE} ALTER COLUMN {col} TYPE NUMERIC(6, 5) "
            f"USING ROUND({col} / 100, 5)"
        )
    _rebuild_v_commune_synthese(captured)
