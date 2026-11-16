#!/usr/bin/env python3
"""Mine local Docker candidates for the golden ``no_dvf`` category.

The command is deliberately interactive: candidate identities and clear-text
addresses are printed to the local console only, never written to a report.
Run it from ``api/backend`` with a PostgreSQL DSN stored in ``DATABASE_URL``.
Only loopback hosts and the Docker Compose ``db`` service are accepted.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.dpe.attribution import classify_dpe_attribution
from app.dpe.queries import _ATTRIBUTION_COLS
from scripts.verify_golden_cases import (
    DPE_COUNT_MAX,
    GoldenSecurityError,
    _open_read_only_connection,
    assert_select_only,
    derive_category,
)

DEPARTEMENTS: tuple[str, ...] = ("44", "85")
LOCAL_DOCKER_HOSTS: frozenset[str] = frozenset(
    {"db", "localhost", "127.0.0.1", "::1", "host.docker.internal"}
)

ADDRESS_SEED_SQL = """
SELECT DISTINCT ON (a.ban_id)
       a.ban_id, a.numero, a.rep, a.nom_voie, a.code_postal,
       a.code_commune, a.nom_commune
FROM adresses a
JOIN diagnostics_dpe d
  ON d.identifiant_ban = a.ban_id
 AND d.code_departement = %(departement)s
WHERE a.code_departement = %(departement)s
  AND a.ban_id IS NOT NULL
  AND a.position IS NOT NULL
ORDER BY a.ban_id, a.id
LIMIT %(scan_limit)s
"""

PARCEL_SQL = """
SELECT DISTINCT p.id_parcelle
FROM adresses a
JOIN parcelles p
  ON p.code_departement = %(departement)s
 AND ST_Intersects(p.geometrie, a.position)
WHERE a.code_departement = %(departement)s
  AND a.ban_id = %(ban_id)s
ORDER BY p.id_parcelle
LIMIT 2
"""

DVF_EXISTS_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM mutations m
    WHERE m.code_departement = %(departement)s
      AND m.id_parcelle = %(id_parcelle)s
) AS present
"""

_DPE_SELECT = ",\n       ".join(f"d.{column}" for column in _ATTRIBUTION_COLS)
DPE_ROWS_SQL = f"""
SELECT {_DPE_SELECT}
FROM diagnostics_dpe d
WHERE d.code_departement = %(departement)s
  AND d.identifiant_ban = %(ban_id)s
ORDER BY d.numero_dpe
LIMIT %(fetch_limit)s
"""

ALLOWED_SQL: frozenset[str] = frozenset(
    {ADDRESS_SEED_SQL, PARCEL_SQL, DVF_EXISTS_SQL, DPE_ROWS_SQL}
)


@dataclass(frozen=True, slots=True)
class Candidate:
    departement: str
    address: Mapping[str, Any]
    id_parcelle: str
    dpe_status: str
    dvf_state: str
    dpe_state: str
    category: str


def _sync_dsn(raw_dsn: str) -> str:
    """Accept the application's async URL without exposing it on the CLI."""
    return raw_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


def _require_local_docker_dsn(raw_dsn: str) -> str:
    dsn = _sync_dsn(raw_dsn)
    try:
        host = conninfo_to_dict(dsn).get("host", "")
    except psycopg.ProgrammingError as exc:
        raise GoldenSecurityError("database DSN is invalid") from exc
    if host not in LOCAL_DOCKER_HOSTS:
        raise GoldenSecurityError("database host is not local Docker")
    return dsn


def _execute_rows(
    conn: psycopg.Connection[Any], sql: str, params: Mapping[str, Any]
) -> list[dict[str, Any]]:
    assert_select_only(sql, ALLOWED_SQL)
    return list(conn.execute(sql, params).fetchall())


def _unique_parcel(
    conn: psycopg.Connection[Any], departement: str, ban_id: str
) -> str | None:
    rows = _execute_rows(
        conn,
        PARCEL_SQL,
        {"departement": departement, "ban_id": ban_id},
    )
    return str(rows[0]["id_parcelle"]) if len(rows) == 1 else None


def _has_dvf(conn: psycopg.Connection[Any], departement: str, id_parcelle: str) -> bool:
    rows = _execute_rows(
        conn,
        DVF_EXISTS_SQL,
        {"departement": departement, "id_parcelle": id_parcelle},
    )
    return bool(rows[0]["present"])


def _dpe_rows(
    conn: psycopg.Connection[Any], departement: str, ban_id: str
) -> tuple[list[dict[str, Any]], bool]:
    rows = _execute_rows(
        conn,
        DPE_ROWS_SQL,
        {
            "departement": departement,
            "ban_id": ban_id,
            "fetch_limit": DPE_COUNT_MAX + 1,
        },
    )
    return rows[:DPE_COUNT_MAX], len(rows) > DPE_COUNT_MAX


def _classify_candidate(
    conn: psycopg.Connection[Any], departement: str, address: Mapping[str, Any]
) -> Candidate | None:
    ban_id = str(address["ban_id"])
    id_parcelle = _unique_parcel(conn, departement, ban_id)
    if id_parcelle is None or _has_dvf(conn, departement, id_parcelle):
        return None
    rows, truncated = _dpe_rows(conn, departement, ban_id)
    attribution = classify_dpe_attribution(ban_id, rows, exact_truncated=truncated)
    if attribution.status != "exact":
        return None
    states = {"dvf_state": "out_of_coverage", "dpe_state": "value"}
    category = derive_category(states)
    if category != "no_dvf":
        raise GoldenSecurityError("derived candidate category is not no_dvf")
    return Candidate(
        departement=departement,
        address=address,
        id_parcelle=id_parcelle,
        dpe_status=attribution.status,
        category=category,
        **states,
    )


def _mine_department(
    conn: psycopg.Connection[Any], departement: str, limit: int, scan_limit: int
) -> list[Candidate]:
    seeds = _execute_rows(
        conn,
        ADDRESS_SEED_SQL,
        {"departement": departement, "scan_limit": scan_limit},
    )
    candidates: list[Candidate] = []
    for address in seeds:
        candidate = _classify_candidate(conn, departement, address)
        if candidate is not None:
            candidates.append(candidate)
        if len(candidates) == limit:
            break
    return candidates


def _console_address(address: Mapping[str, Any]) -> str:
    street = " ".join(
        str(value).strip()
        for value in (
            address.get("numero"),
            address.get("rep"),
            address.get("nom_voie"),
        )
        if value
    )
    locality = " ".join(
        str(value).strip()
        for value in (address.get("code_postal"), address.get("nom_commune"))
        if value
    )
    return ", ".join(part for part in (street, locality) if part)


def _print_candidates(departement: str, candidates: Sequence[Candidate]) -> None:
    print(f"departement={departement} candidates={len(candidates)}")
    for index, candidate in enumerate(candidates, start=1):
        print(f"  candidate={index}")
        print(f"    address={_console_address(candidate.address)}")
        print(f"    ban_id={candidate.address['ban_id']}")
        print(f"    code_insee={candidate.address['code_commune']}")
        print(f"    id_parcelle={candidate.id_parcelle}")
        print(f"    dpe_status={candidate.dpe_status}")
        print(f"    dpe_state={candidate.dpe_state}")
        print(f"    dvf_state={candidate.dvf_state}")
        print(f"    derived_category={candidate.category}")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--departement",
        action="append",
        choices=DEPARTEMENTS,
        dest="departements",
        help="Department to mine; repeat the option (default: 44 and 85).",
    )
    parser.add_argument("--limit", type=_positive_int, default=5)
    parser.add_argument("--scan-limit", type=_positive_int, default=2_000)
    parser.add_argument("--dsn-env", default="DATABASE_URL")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not sys.stdout.isatty():
        raise GoldenSecurityError("candidate output requires an interactive console")
    raw_dsn = os.getenv(args.dsn_env)
    if not raw_dsn:
        raise GoldenSecurityError("database DSN environment variable is missing")
    dsn = _require_local_docker_dsn(raw_dsn)
    departements = tuple(dict.fromkeys(args.departements or DEPARTEMENTS))
    conn = _open_read_only_connection(dsn)
    try:
        results = {
            departement: _mine_department(
                conn, departement, args.limit, args.scan_limit
            )
            for departement in departements
        }
    finally:
        conn.rollback()
        conn.close()
    for departement, candidates in results.items():
        _print_candidates(departement, candidates)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GoldenSecurityError, psycopg.Error, OSError, ValueError):
        print("no_dvf candidate mining blocked", file=sys.stderr)
        raise SystemExit(1) from None
