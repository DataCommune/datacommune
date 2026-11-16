#!/usr/bin/env python3
"""Extract frozen 44/85 fidelity references from a DataCommune database (D-25, D-26).

Produces, in ``--out-dir``:

- ``level1_counts.json``    -- row counts per table x departement (44, 85, total);
- ``level2_panel.json``     -- ``v_commune_synthese`` rows of a fixed commune panel;
- ``lovac_false_zeros.json``-- KI-D1 recount of LOVAC false zeros;
- ``millesimes.json``       -- ``dataset_metadata``, last successful ``etl_runs``
                               per dataset, DVF years per departement;
- ``manifest.json``         -- name, bytes and SHA-256 of every file above.

With ``--dvf-history-dir``, also freezes the 44/85 DVF rows older than
``--dvf-history-before`` as gzip CSV (D-18b: the dump may be the only copy of
the pre-2021 years).

Read-only (session forced to ``READ ONLY``), stdlib + ``psycopg`` 3 only, no
import from the ``app`` package: it runs inside the old API image. A missing
table or column is recorded as ``"absent"``, an unpopulated materialized view
as ``"non_peuplee"``, a relation that cannot be read (permission, broken view)
as ``"illisible"`` with the database error -- never an exception. Output is
deterministic: two extractions of the same database differ only by
``extracted_at`` in the manifest.

Usage::

    OLD_DB_URL=postgresql://... python extract_fidelity_reference.py \\
        --dsn-env OLD_DB_URL --panel 44109,85047 --out-dir refs/ \\
        --source-label legacy-prod-2026-09 \\
        [--dvf-history-dir /mnt/d/dvf --dvf-history-before 2021-01-01]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

DEPARTEMENTS_CIBLES: tuple[str, ...] = ("44", "85")

# Identifiers below are the only table names ever interpolated into SQL
# (T-02-01-04): nothing comes from user input.
REFERENCE_TABLES: tuple[str, ...] = (
    "adresses",
    "parcelles",
    "mutations",
    "mutations_grain",
    "mutation_parcelles",
    "diagnostics_dpe",
    "batiments",
    "eligibilite_fixe",
    "rnb_liens",
    "foncier_pm",
    "equipements",
    "copropriete",
    "antennes",
    "carte_scolaire",
    "arrets_transport",
    "gares",
    "monuments_historiques",
    "indicateurs_loyers",
    "encadrement_loyers",
    "fiscalite_locale",
    "zonage_abc",
    "revenus",
    "demographie",
    "vacance",
    "permis",
    "v_commune_synthese",
    "scoring_renov_target",
    "geo_communes",
    "geo_departements",
    "geo_sections",
)

# Columns that directly hold a departement code, in preference order.
DEPARTEMENT_COLUMNS: tuple[str, ...] = ("code_departement", "departement_code")
# Columns whose first five characters are an INSEE commune code.
INSEE_COLUMNS: tuple[str, ...] = (
    "code_insee",
    "code_commune",
    "commune_code",
    "cle_ban",
    "id_parcelle",
)
# Tables whose primary key *is* the departement code.
DEPARTEMENT_KEY_TABLES: dict[str, str] = {"geo_departements": "code"}

DVF_HISTORY_TABLES: tuple[str, ...] = ("mutations", "mutations_grain")
ETL_RUN_COLUMNS: tuple[str, ...] = (
    "id",
    "dataset_name",
    "started_at",
    "completed_at",
    "status",
    "total_rows_loaded",
    "total_duration_seconds",
)

_INSEE_RE = re.compile(r"^[0-9][0-9AB][0-9]{3}$")

# panel.json is the hand-written panel input (codes + selection reason); it is
# hashed when it sits in --out-dir, so the manifest covers every reference file.
OUTPUT_FILES: tuple[str, ...] = (
    "panel.json",
    "level1_counts.json",
    "level2_panel.json",
    "lovac_false_zeros.json",
    "millesimes.json",
)


# --------------------------------------------------------------------- pure --


def departement_from_insee(code: str | None) -> str | None:
    """Departement of an INSEE commune code: 3 chars overseas (97x/98x), else 2."""
    if not code or len(code) < 5:
        return None
    prefix = code[:2].upper()
    if prefix in {"97", "98"}:
        return code[:3]
    return prefix


def _insee_departement_sql(expr: sql.Composable) -> sql.Composable:
    """SQL twin of :func:`departement_from_insee` applied to ``left(expr, 5)``."""
    return sql.SQL(
        "CASE WHEN left({e}, 2) IN ('97', '98') THEN left({e}, 3) "
        "ELSE upper(left({e}, 2)) END"
    ).format(e=expr)


def to_jsonable(value: Any) -> Any:
    """Exact, deterministic serialization of a database value."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return str(value)


def dumps_sorted(payload: Any) -> str:
    """Deterministic JSON: sorted keys, stable separators, trailing newline."""
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_json(path: Path, payload: Any) -> None:
    path.write_bytes(dumps_sorted(payload).encode("utf-8"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_panel(raw: str) -> list[str]:
    """``--panel``: a JSON file (list, or object with ``codes``) or a comma list."""
    candidate = Path(raw)
    if raw.endswith(".json") and candidate.is_file():
        data = json.loads(candidate.read_text(encoding="utf-8"))
        codes = data["codes"] if isinstance(data, dict) else data
    else:
        codes = [c.strip() for c in raw.split(",") if c.strip()]
    codes = sorted({str(c).upper() for c in codes})
    bad = [c for c in codes if not _INSEE_RE.match(c)]
    if bad:
        raise SystemExit(f"invalid INSEE codes in --panel: {bad}")
    return codes


def write_manifest(
    out_dir: Path,
    *,
    source_label: str,
    alembic_version: str | None,
    methods: dict[str, Any],
    extracted_at: str,
    script_sha256: str,
    extra_files: Sequence[Path] = (),
) -> Path:
    """Write ``manifest.json``: name, bytes and SHA-256 of each ``OUTPUT_FILES``.

    Only the known reference files are hashed: a stray file left in
    ``out_dir`` (e.g. a sizes report) never enters the manifest.

    ``extra_files`` (the DVF history, stored outside ``out_dir``) are listed
    under ``dvf_history`` by file name.
    """
    files = [
        {"name": p.name, "bytes": p.stat().st_size, "sha256": file_sha256(p)}
        for p in (out_dir / name for name in sorted(OUTPUT_FILES))
        if p.is_file()
    ]
    history = [
        {"name": p.name, "bytes": p.stat().st_size, "sha256": file_sha256(p)}
        for p in sorted(extra_files, key=lambda p: p.name)
    ]
    manifest = {
        "files": files,
        "dvf_history": history,
        "source": {"label": source_label, "alembic_version": alembic_version},
        "extracted_at": extracted_at,
        "script_sha256": script_sha256,
        "departements": list(DEPARTEMENTS_CIBLES),
        "methods": methods,
    }
    path = out_dir / "manifest.json"
    write_json(path, manifest)
    return path


# ------------------------------------------------------------------ catalog --


class Catalog:
    """Reads relation kind, population state and columns once per relation."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self._cache: dict[str, tuple[str | None, bool, list[str]]] = {}

    def describe(self, name: str) -> tuple[str | None, bool, list[str]]:
        """Return ``(relkind or None if absent, populated, columns)``."""
        if name not in self._cache:
            row = self.conn.execute(
                "SELECT c.oid, c.relkind, c.relispopulated FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = %s "
                "AND c.relkind IN ('r', 'p', 'm', 'v')",
                (name,),
            ).fetchone()
            if row is None:
                self._cache[name] = (None, False, [])
            else:
                cols = [
                    r[0]
                    for r in self.conn.execute(
                        "SELECT attname FROM pg_attribute WHERE attrelid = %s "
                        "AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
                        (row[0],),
                    ).fetchall()
                ]
                self._cache[name] = (row[1], bool(row[2]), cols)
        return self._cache[name]

    def status(self, name: str) -> str:
        kind, populated, _ = self.describe(name)
        if kind is None:
            return "absent"
        if kind == "m" and not populated:
            return "non_peuplee"
        return "ok"

    def has_partition(self, parent: str, child: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent "
                "JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND p.relname = %s AND c.relname = %s",
                (parent, child),
            ).fetchone()
            is not None
        )


def _scalar(conn: psycopg.Connection, query: sql.Composable, params: Any = None) -> Any:
    return conn.execute(query, params).fetchone()[0]


def _table(name: str) -> sql.Composable:
    return sql.Identifier("public", name)


# Plan 04.1-16: ``eligibilite_fixe`` stores one row per building, the source
# offer rows in ``offres``; it is counted in source rows, like its archive.
_COLONNE_OFFRES_SQL = (
    "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
    "AND table_name = %s AND column_name = 'offres'"
)


def _row_count_sql(conn: psycopg.Connection, table: str) -> sql.Composable:
    """``count(*)``, or the offer rows of the per-building eligibility table."""
    if (
        table == "eligibilite_fixe"
        and conn.execute(_COLONNE_OFFRES_SQL, (table,)).fetchone()
    ):
        return sql.SQL("coalesce(sum(cardinality(offres)), 0)")
    return sql.SQL("count(*)")


def _guarded(
    conn: psycopg.Connection, read: Callable[[], Any]
) -> tuple[Any, str | None]:
    """Run ``read`` inside a savepoint; a database error yields a reason.

    Keeps the extraction going when one relation is unreadable (permission,
    broken view): the error is recorded as ``"<class>:<sqlstate>"``.
    """
    try:
        with conn.transaction():
            return read(), None
    except psycopg.Error as exc:
        return None, f"{type(exc).__name__}:{getattr(exc, 'sqlstate', None) or ''}"


# ------------------------------------------------------------------ level 1 --


def choose_method(catalog: Catalog, table: str) -> dict[str, Any]:
    """Pick how rows of ``table`` are attributed to a departement."""
    kind, _, cols = catalog.describe(table)
    if kind == "p" and all(
        catalog.has_partition(table, f"{table}_{d.lower()}")
        for d in DEPARTEMENTS_CIBLES
    ):
        return {"method": "partition", "column": None}
    if table in DEPARTEMENT_KEY_TABLES and DEPARTEMENT_KEY_TABLES[table] in cols:
        return {"method": "code_departement", "column": DEPARTEMENT_KEY_TABLES[table]}
    for col in DEPARTEMENT_COLUMNS:
        if col in cols:
            return {"method": "code_departement", "column": col}
    for col in INSEE_COLUMNS:
        if col in cols:
            return {"method": "departement_from_insee", "column": col}
    # No departement attribution possible (e.g. geometry-only stops): the
    # total is still counted, the 44/85 counts are null with a reason.
    return {
        "method": "absent",
        "column": None,
        "motif": "aucune colonne de departement ni de code INSEE",
    }


def level1_counts(conn: psycopg.Connection, catalog: Catalog) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for table in REFERENCE_TABLES:
        status = catalog.status(table)
        if status != "ok":
            tables[table] = {
                "status": status,
                "method": None,
                "column": None,
                "counts": None,
            }
            continue
        method = choose_method(catalog, table)
        counts, motif = _guarded(conn, lambda t=table, m=method: _count(conn, t, m))
        if motif is not None:
            tables[table] = {
                "status": "illisible",
                **method,
                "counts": None,
                "motif": motif,
            }
            continue
        tables[table] = {"status": "ok", **method, "counts": counts}
    return {"departements": list(DEPARTEMENTS_CIBLES), "tables": tables}


def _count(
    conn: psycopg.Connection, table: str, method: dict[str, Any]
) -> dict[str, int | None]:
    compte = _row_count_sql(conn, table)
    counts: dict[str, int | None] = {
        "total": int(
            _scalar(conn, sql.SQL("SELECT {} FROM {}").format(compte, _table(table)))
        )
    }
    for dept in DEPARTEMENTS_CIBLES:
        if method["method"] == "partition":
            query = sql.SQL("SELECT count(*) FROM {}").format(
                _table(f"{table}_{dept.lower()}")
            )
            counts[dept] = int(_scalar(conn, query))
        elif method["method"] == "code_departement":
            query = sql.SQL("SELECT {} FROM {} WHERE {} = %s").format(
                compte, _table(table), sql.Identifier(method["column"])
            )
            counts[dept] = int(_scalar(conn, query, (dept,)))
        elif method["method"] == "departement_from_insee":
            expr = _insee_departement_sql(sql.Identifier(method["column"]))
            query = sql.SQL("SELECT {} FROM {} WHERE {} = %s").format(
                compte, _table(table), expr
            )
            counts[dept] = int(_scalar(conn, query, (dept,)))
        else:
            counts[dept] = None
    return counts


# ------------------------------------------------------------------ level 2 --


def level2_panel(
    conn: psycopg.Connection, catalog: Catalog, panel: list[str]
) -> dict[str, Any]:
    source = "v_commune_synthese"
    status = catalog.status(source)
    payload: dict[str, Any] = {
        "source": source,
        "status": status,
        "panel": panel,
        "rows": {},
        "missing": [],
    }
    if status != "ok":
        return payload
    _, _, cols = catalog.describe(source)
    if "code_insee" not in cols:
        payload["status"] = "absent"
        payload["motif"] = "colonne code_insee absente"
        return payload

    def read() -> tuple[list[str], list[tuple[Any, ...]]]:
        cur = conn.execute(
            sql.SQL(
                "SELECT * FROM {} WHERE code_insee = ANY(%s) ORDER BY code_insee"
            ).format(_table(source)),
            (panel,),
        )
        return [d.name for d in cur.description], cur.fetchall()

    result, motif = _guarded(conn, read)
    if result is None:
        payload["status"] = "illisible"
        payload["motif"] = motif
        return payload
    names, rows = result
    for row in rows:
        record = {
            name: to_jsonable(value) for name, value in zip(names, row, strict=True)
        }
        payload["rows"][record["code_insee"]] = record
    payload["columns"] = names
    payload["missing"] = [c for c in panel if c not in payload["rows"]]
    return payload


# ---------------------------------------------------------------- LOVAC KI-D1 --


def lovac_false_zeros(conn: psycopg.Connection, catalog: Catalog) -> dict[str, Any]:
    """KI-D1: taux_vacance = 0 with no usable private-housing denominator."""
    table = "vacance"
    payload: dict[str, Any] = {
        "table": table,
        "rule": "taux_vacance = 0 AND "
        "(nb_logements_prives IS NULL OR nb_logements_prives <= 0)",
    }
    status = catalog.status(table)
    _, _, cols = catalog.describe(table)
    required = {"taux_vacance", "nb_logements_prives", "code_commune"}
    if status != "ok" or not required <= set(cols):
        payload.update(
            status="absent",
            counts=None,
            codes_44_85=[],
            missing_columns=sorted(required - set(cols)),
        )
        return payload
    dept_expr = (
        sql.Identifier("code_departement")
        if "code_departement" in cols
        else _insee_departement_sql(sql.Identifier("code_commune"))
    )
    has_year = "annee" in cols
    query = sql.SQL(
        "SELECT code_commune, {annee}, {dept} FROM {t} "
        "WHERE taux_vacance = 0 "
        "AND (nb_logements_prives IS NULL OR nb_logements_prives <= 0) "
        "ORDER BY code_commune, 2"
    ).format(
        annee=sql.Identifier("annee") if has_year else sql.SQL("NULL::int"),
        dept=dept_expr,
        t=_table(table),
    )
    rows, motif = _guarded(conn, lambda: conn.execute(query).fetchall())
    if rows is None:
        payload.update(status="illisible", counts=None, codes_44_85=[], motif=motif)
        return payload
    counts = {"total": len(rows), **{d: 0 for d in DEPARTEMENTS_CIBLES}}
    codes = []
    for code, annee, dept in rows:
        if dept in DEPARTEMENTS_CIBLES:
            counts[dept] += 1
            codes.append({"code_commune": code, "annee": annee})
    payload.update(status="ok", counts=counts, codes_44_85=codes)
    return payload


# ---------------------------------------------------------------- millesimes --


def collect_millesimes(conn: psycopg.Connection, catalog: Catalog) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    def read_metadata() -> list[dict[str, Any]]:
        cur = conn.execute(
            sql.SQL("SELECT * FROM {} ORDER BY dataset_name").format(
                _table("dataset_metadata")
            )
        )
        names = [d.name for d in cur.description]
        return [
            {n: to_jsonable(v) for n, v in zip(names, row, strict=True)}
            for row in cur.fetchall()
        ]

    if catalog.status("dataset_metadata") == "ok":
        rows, motif = _guarded(conn, read_metadata)
        payload["dataset_metadata"] = (
            {"status": "ok", "rows": rows}
            if motif is None
            else {"status": "illisible", "rows": [], "motif": motif}
        )
    else:
        payload["dataset_metadata"] = {"status": "absent", "rows": []}

    if catalog.status("etl_runs") == "ok":
        _, _, cols = catalog.describe("etl_runs")
        present = [c for c in ETL_RUN_COLUMNS if c in cols]
        query = sql.SQL(
            "SELECT DISTINCT ON (dataset_name) {cols} FROM {t} "
            "WHERE status = 'success' ORDER BY dataset_name, started_at DESC, id DESC"
        ).format(
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in present),
            t=_table("etl_runs"),
        )
        rows, motif = _guarded(conn, lambda q=query: conn.execute(q).fetchall())
        if rows is None:
            payload["etl_runs_last_success"] = {
                "status": "illisible",
                "rows": [],
                "motif": motif,
            }
        else:
            payload["etl_runs_last_success"] = {
                "status": "ok",
                "columns": present,
                "absent_columns": [c for c in ETL_RUN_COLUMNS if c not in cols],
                "rows": [
                    {n: to_jsonable(v) for n, v in zip(present, row, strict=True)}
                    for row in rows
                ],
            }
    else:
        payload["etl_runs_last_success"] = {"status": "absent", "rows": []}

    dvf: dict[str, Any] = {}
    for table in DVF_HISTORY_TABLES:
        _, _, cols = catalog.describe(table)
        if catalog.status(table) != "ok" or not {
            "code_departement",
            "date_mutation",
        } <= set(cols):
            dvf[table] = {"status": "absent", "years": {}}
            continue
        query = sql.SQL(
            "SELECT code_departement, EXTRACT(YEAR FROM date_mutation)::int, "
            "count(*) FROM {} GROUP BY 1, 2 ORDER BY 1, 2"
        ).format(_table(table))
        rows, motif = _guarded(conn, lambda q=query: conn.execute(q).fetchall())
        if rows is None:
            dvf[table] = {"status": "illisible", "years": {}, "motif": motif}
            continue
        years: dict[str, dict[str, int]] = {}
        for dept, year, count in rows:
            years.setdefault(str(dept), {})[str(year)] = int(count)
        dvf[table] = {"status": "ok", "years": years}
    payload["dvf_years_by_departement"] = dvf
    return payload


# --------------------------------------------------------------- DVF history --


def _copy_to_gzip(conn: psycopg.Connection, query: sql.Composable, target: Path) -> int:
    """COPY a query as CSV into a reproducible gzip (mtime 0, no file name).

    Returns the number of data rows, counted with a CSV reader so that
    multi-line quoted fields are not over-counted.
    """
    with (
        target.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz,
    ):
        copy_sql = sql.SQL("COPY ({}) TO STDOUT WITH (FORMAT csv, HEADER true)").format(
            query
        )
        with conn.cursor().copy(copy_sql) as copy:
            for chunk in copy:
                gz.write(bytes(chunk))
    with gzip.open(target, "rt", encoding="utf-8", newline="") as handle:
        rows = sum(1 for _ in csv.reader(handle))
    return max(rows - 1, 0)


def _guarded_copy(
    conn: psycopg.Connection, query: sql.Composable, target: Path, written: list[Path]
) -> dict[str, Any]:
    rows, motif = _guarded(conn, lambda: _copy_to_gzip(conn, query, target))
    if motif is not None:
        target.unlink(missing_ok=True)
        return {"status": "illisible", "motif": motif}
    written.append(target)
    return {"status": "ok", "file": target.name, "rows": rows}


def extract_dvf_history(
    conn: psycopg.Connection, catalog: Catalog, out_dir: Path, before: date
) -> tuple[list[Path], dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    depts = sql.SQL(", ").join(sql.Literal(d) for d in DEPARTEMENTS_CIBLES)
    cutoff = sql.Literal(before.isoformat())
    written: list[Path] = []
    report: dict[str, Any] = {"before": before.isoformat(), "tables": {}}

    def order_by(cols: list[str]) -> sql.Composable:
        keys = ["id"] if "id" in cols else cols
        return sql.SQL(", ").join(sql.Identifier(c) for c in keys)

    for table in DVF_HISTORY_TABLES:
        _, _, cols = catalog.describe(table)
        if catalog.status(table) != "ok" or not {
            "code_departement",
            "date_mutation",
        } <= set(cols):
            report["tables"][table] = {"status": "absent"}
            continue
        query = sql.SQL(
            "SELECT * FROM {t} WHERE code_departement IN ({d}) "
            "AND date_mutation < {c}::date ORDER BY code_departement, {o}"
        ).format(t=_table(table), d=depts, c=cutoff, o=order_by(cols))
        target = out_dir / f"{table}_44_85_before_{before.isoformat()}.csv.gz"
        report["tables"][table] = _guarded_copy(conn, query, target, written)

    # mutation_parcelles is joined to the grain through id_mutation.
    _, _, mp_cols = catalog.describe("mutation_parcelles")
    _, _, grain_cols = catalog.describe("mutations_grain")
    if (
        catalog.status("mutation_parcelles") == "ok"
        and "id_mutation" in mp_cols
        and {"id_mutation", "code_departement", "date_mutation"} <= set(grain_cols)
    ):
        query = sql.SQL(
            "SELECT mp.* FROM {mp} mp WHERE mp.id_mutation IN ("
            "SELECT g.id_mutation FROM {g} g WHERE g.code_departement IN ({d}) "
            "AND g.date_mutation < {c}::date) ORDER BY {o}"
        ).format(
            mp=_table("mutation_parcelles"),
            g=_table("mutations_grain"),
            d=depts,
            c=cutoff,
            o=sql.SQL(", ").join(
                sql.SQL("mp.{}").format(sql.Identifier(c))
                for c in (["id"] if "id" in mp_cols else mp_cols)
            ),
        )
        target = (
            out_dir / f"mutation_parcelles_44_85_before_{before.isoformat()}.csv.gz"
        )
        report["tables"]["mutation_parcelles"] = _guarded_copy(
            conn, query, target, written
        )
    else:
        report["tables"]["mutation_parcelles"] = {"status": "absent"}
    return written, report


# ---------------------------------------------------------------------- main --


def read_dsn(env_name: str) -> str:
    dsn = os.environ.get(env_name)
    if not dsn:
        raise SystemExit(f"environment variable {env_name} is empty or unset")
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


def open_read_only(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, connect_timeout=10)
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    conn.commit()
    return conn


def alembic_version(conn: psycopg.Connection) -> str | None:
    if (
        conn.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]
        is None
    ):
        return None
    rows, motif = _guarded(
        conn,
        lambda: conn.execute(
            "SELECT version_num FROM alembic_version ORDER BY 1"
        ).fetchall(),
    )
    if motif is not None:
        return f"illisible:{motif}"
    return ",".join(r[0] for r in rows) if rows else None


def extract(
    conn: psycopg.Connection,
    *,
    panel: list[str],
    out_dir: Path,
    source_label: str,
    dvf_history_dir: Path | None = None,
    dvf_history_before: date | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(conn)
    level1 = level1_counts(conn, catalog)
    write_json(out_dir / "level1_counts.json", level1)
    write_json(out_dir / "level2_panel.json", level2_panel(conn, catalog, panel))
    write_json(out_dir / "lovac_false_zeros.json", lovac_false_zeros(conn, catalog))
    millesimes = collect_millesimes(conn, catalog)

    extra: list[Path] = []
    if dvf_history_dir is not None:
        extra, report = extract_dvf_history(
            conn, catalog, dvf_history_dir, dvf_history_before or date(2021, 1, 1)
        )
        millesimes["dvf_history_extract"] = report
    write_json(out_dir / "millesimes.json", millesimes)

    methods = {
        table: {
            k: entry[k] for k in ("status", "method", "column", "motif") if k in entry
        }
        for table, entry in level1["tables"].items()
    }
    return write_manifest(
        out_dir,
        source_label=source_label,
        alembic_version=alembic_version(conn),
        methods=methods,
        extracted_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        script_sha256=file_sha256(Path(__file__)),
        extra_files=extra,
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dsn-env",
        default="DATABASE_URL",
        help="environment variable holding the DSN (default DATABASE_URL)",
    )
    parser.add_argument(
        "--panel",
        required=True,
        help="panel of INSEE codes: JSON file or comma-separated list",
    )
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--source-label",
        required=True,
        help="label of the source database recorded in the manifest",
    )
    parser.add_argument(
        "--dvf-history-dir",
        type=Path,
        default=None,
        help="directory receiving the gzip CSV of old 44/85 DVF rows",
    )
    parser.add_argument(
        "--dvf-history-before",
        type=date.fromisoformat,
        default=date(2021, 1, 1),
        help="keep DVF rows strictly before this date (default 2021-01-01)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    panel = parse_panel(args.panel)
    with open_read_only(read_dsn(args.dsn_env)) as conn:
        manifest = extract(
            conn,
            panel=panel,
            out_dir=args.out_dir,
            source_label=args.source_label,
            dvf_history_dir=args.dvf_history_dir,
            dvf_history_before=args.dvf_history_before,
        )
    print(f"MANIFEST {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
