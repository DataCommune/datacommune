#!/usr/bin/env python3
"""Ephemeral API key of the 02-18 latency measurement (Ianis, 2026-09-26).

``run -- <measure_route_latencies arguments>`` does, in ONE process:

1. create the key ``02-18 latency measurement`` (``quota_monthly = 1000``, no
   user, not default, never internal nor unlimited) with the generator of the
   application (``generate_api_key``); only its HMAC digest and its visible
   prefix reach the database;
2. put the plain value in this process's environment (``DC_MEASURE_KEY``)
   and run ``measure_route_latencies.main(... --api-key-env DC_MEASURE_KEY)``;
3. in a ``finally`` -- the measure failing included -- drop the variable,
   revoke the key (``revoked = TRUE, status = 'revoked'``), read back its row
   and its usage of the month, and send one request with the revoked value,
   which the API must refuse (401/403).

The plain value is never printed, written, logged nor passed on a command
line; the status report (``--status-out``) carries the key id, its prefix,
the usage before/after and the revocation proof only.

``revoke --id <n>``: fallback revocation of the measurement key (refuses any
other key), for a run killed before its ``finally``.

Run with the work base tool ``wb-tool.sh`` (service DSN: the key is written).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

KEY_NAME = "02-18 latency measurement"
QUOTA = 1000
MEASURE_ENV = "DC_MEASURE_KEY"


def create_key(conn: Any) -> dict[str, Any]:
    """Insert the measurement key; refuse while another one is active."""
    from app.auth.service import generate_api_key

    active = conn.execute(
        "SELECT id FROM api_keys WHERE name = %s AND status = 'active' AND NOT revoked",
        (KEY_NAME,),
    ).fetchall()
    if active:
        raise RuntimeError(f"an active measurement key exists: {active[0]['id']}")
    plaintext, digest, prefix = generate_api_key("live")
    row = conn.execute(
        "INSERT INTO api_keys (key_hash, name, quota_monthly, key_prefix, "
        "environment, is_default, status, revoked) "
        "VALUES (%s, %s, %s, %s, 'live', FALSE, 'active', FALSE) RETURNING id",
        (digest, KEY_NAME, QUOTA, prefix),
    ).fetchone()
    return {"id": row["id"], "prefix": prefix, "plaintext": plaintext}


def revoke_key(conn: Any, key_id: int) -> dict[str, Any]:
    """Revoke the measurement key *key_id*; any other key is refused."""
    row = conn.execute(
        "UPDATE api_keys SET revoked = TRUE, status = 'revoked' "
        "WHERE id = %s AND name = %s "
        "RETURNING id, name, status, revoked, quota_monthly",
        (key_id, KEY_NAME),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"key {key_id} is not the measurement key")
    return dict(row)


def usage(conn: Any, key_id: int, month: str) -> int:
    row = conn.execute(
        "SELECT request_count FROM quota_usage WHERE api_key_id = %s AND month = %s",
        (key_id, month),
    ).fetchone()
    return int(row["request_count"]) if row else 0


def run_with_key(
    connect: Callable[[], Any],
    measure_argv: list[str],
    *,
    measure: Callable[[list[str]], int],
    probe: Callable[[str], int],
    month: str,
    env: MutableMapping[str, str],
) -> dict[str, Any]:
    conn = connect()
    created = create_key(conn)
    plaintext = created.pop("plaintext")
    report: dict[str, Any] = {
        "cle": {"id": created["id"], "prefixe": created["prefix"], "nom": KEY_NAME},
        "quota_monthly": QUOTA,
        "mois": month,
        "usage_avant": usage(conn, created["id"], month),
        "creee_le": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    env[MEASURE_ENV] = plaintext
    try:
        report["mesure_code_sortie"] = measure(
            [*measure_argv, "--api-key-env", MEASURE_ENV]
        )
    except Exception as exc:  # the key is revoked whatever happened  # noqa: BLE001
        report["mesure_code_sortie"] = f"exception:{type(exc).__name__}"
    finally:
        env.pop(MEASURE_ENV, None)
        report["revocation"] = revoke_key(conn, created["id"])
        report["revoquee_le"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        report["usage_apres"] = usage(conn, created["id"], month)
        try:
            status: int | str = probe(plaintext)
        except Exception as exc:  # noqa: BLE001
            status = f"exception:{type(exc).__name__}"
        del plaintext
        report["sonde_ancienne_cle"] = status
        report["revocation_prouvee"] = (
            report["revocation"]["revoked"] is True
            and report["revocation"]["status"] == "revoked"
            and status in (401, 403)
        )
    return report


def _connect() -> Any:
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    return psycopg.connect(
        dsn, connect_timeout=10, autocommit=True, row_factory=dict_row
    )


def _probe(base_url: str) -> Callable[[str], int]:
    def probe(key: str) -> int:
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            return client.get("/v1/datasets", headers={"X-API-Key": key}).status_code

    return probe


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    measure_argv: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, measure_argv = argv[:cut], argv[cut + 1 :]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("run", "revoke"))
    parser.add_argument("--status-out", type=Path)
    parser.add_argument("--id", type=int)
    args = parser.parse_args(argv)
    if args.mode == "revoke":
        if args.id is None:
            parser.error("--id is required")
        with _connect() as conn:
            print(json.dumps(revoke_key(conn, args.id)))
        return 0
    if args.status_out is None or "--api-key-env" in measure_argv:
        parser.error("--status-out is required; the key env name is set here")
    from scripts.measure_route_latencies import main as measure_main
    from scripts.measure_route_latencies import require_loopback

    base_url = measure_argv[measure_argv.index("--base-url") + 1]
    require_loopback(base_url)
    with _connect() as conn:
        report = run_with_key(
            lambda: conn,
            measure_argv,
            measure=measure_main,
            probe=_probe(base_url),
            month=datetime.now(UTC).strftime("%Y-%m"),
            env=os.environ,
        )
    args.status_out.parent.mkdir(parents=True, exist_ok=True)
    args.status_out.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"MEASUREMENT_KEY id={report['cle']['id']} prefix={report['cle']['prefixe']} "
        f"usage={report['usage_avant']}->{report['usage_apres']} "
        f"revoked={report['revocation_prouvee']} "
        f"measure_exit={report['mesure_code_sortie']}"
    )
    return 0 if report["revocation_prouvee"] else 1


if __name__ == "__main__":
    sys.exit(main())
