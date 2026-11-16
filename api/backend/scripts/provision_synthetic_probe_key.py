"""Provision a normal dedicated probe key supplied only through stdin.

No secret is generated, echoed, logged, or accepted as a command argument.
An optional previous key ID permits a staged rotation without revoking it.
The operator updates private configuration and retires the previous key only
after deployment and qualification. Revoked input keys are never revived.

--recette (plan 03-13) switches to the owner acceptance profile: a public
`dc_anon_` key named RECETTE_KEY_NAME with a bounded quota. Only one such key
is active at a time: provisioning a new one revokes the previous active ones
in the same transaction, since each acceptance session draws its own key and
keeps it only in its environment. The default profile is unchanged.
"""

import argparse
import json
import re
import sys
from typing import BinaryIO

import psycopg
from psycopg.rows import dict_row

KEY_NAME = "Synthetic golden probe key"
QUOTA = 500
KEY_PATTERN = r"dc_live_[A-Za-z0-9_-]{43,128}"

RECETTE_KEY_NAME = "Owner acceptance public key (03-13)"
# Bounded, and the lowest quota whose derived per-key rate (billing.plans: 200/min)
# does not throttle a session below the per-IP ceiling (120/min, C36); 5000 would
# allow 60/min.
RECETTE_QUOTA = 25_000
RECETTE_KEY_PATTERN = r"dc_anon_[A-Za-z0-9_-]{43,128}"


class ProvisioningError(RuntimeError):
    """Constant error codes only; no credentials or database diagnostics."""


class RedactedParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ProvisioningError("invalid_arguments")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ProvisioningError(code)


def read_key(stream: BinaryIO, pattern: str = KEY_PATTERN) -> str:
    raw = stream.read(258)
    require(len(raw) <= 257, "invalid_key_input")
    try:
        value = raw.decode("ascii").removesuffix("\n").removesuffix("\r")
    except UnicodeDecodeError:
        raise ProvisioningError("invalid_key_input") from None
    require(bool(re.fullmatch(pattern, value)), "invalid_key_input")
    return value


def qualified(row: dict, name: str = KEY_NAME, quota: int = QUOTA) -> bool:
    return (
        row["name"] == name
        and row["quota_monthly"] == quota
        and row["status"] == "active"
        and row["revoked"] is False
        and row["user_id"] is None
        and row["environment"] == "live"
        and row["is_default"] is False
    )


def apply(
    conn: psycopg.Connection,
    key: str,
    *,
    verify_only: bool,
    previous_key_id: int | None,
    recette: bool = False,
) -> dict:
    from app.auth.service import hash_api_key, hash_api_key_legacy

    name, quota = (RECETTE_KEY_NAME, RECETTE_QUOTA) if recette else (KEY_NAME, QUOTA)
    digest, legacy = hash_api_key(key), hash_api_key_legacy(key)
    rows = conn.execute(
        "SELECT id,key_hash,name,quota_monthly,status,revoked,user_id,"
        "environment,is_default FROM api_keys "
        "WHERE key_hash IN (%s,%s) OR name=%s OR id=%s",
        (digest, legacy, name, previous_key_id),
    ).fetchall()
    matches = [row for row in rows if row["key_hash"] in {digest, legacy}]
    if matches:
        require(len(matches) == 1, "key_collision")
        row = matches[0]
        require(
            row["key_hash"] == digest and qualified(row, name, quota),
            "key_collision_or_revoked",
        )
        return {
            "status": "verified",
            "key_id": row["id"],
            "created": False,
            "quota_monthly": quota,
        }
    require(not verify_only, "key_not_provisioned")
    active = [row for row in rows if row["status"] == "active" and not row["revoked"]]
    if recette:
        # One active acceptance key: the previous one is revoked, never adopted.
        revoked = conn.execute(
            "UPDATE api_keys SET status='revoked',revoked=TRUE "
            "WHERE name=%s AND status='active' AND revoked=FALSE RETURNING id",
            (RECETTE_KEY_NAME,),
        ).fetchall()
        inserted = conn.execute(
            "INSERT INTO api_keys (key_hash,name,quota_monthly,key_prefix,"
            "environment,is_default,status,revoked) "
            "VALUES (%s,%s,%s,%s,'live',FALSE,'active',FALSE) RETURNING id",
            (digest, RECETTE_KEY_NAME, RECETTE_QUOTA, key[:16]),
        ).fetchone()
        return {
            "status": "provisioned",
            "key_id": inserted["id"],
            "created": True,
            "quota_monthly": RECETTE_QUOTA,
            "revoked_previous": len(revoked),
        }
    if previous_key_id is not None:
        previous = [row for row in rows if row["id"] == previous_key_id]
        require(
            len(previous) == 1 and qualified(previous[0]), "previous_key_not_qualified"
        )
    require(
        not active or (len(active) == 1 and active[0]["id"] == previous_key_id),
        "active_probe_key_requires_explicit_rotation",
    )
    inserted = conn.execute(
        "INSERT INTO api_keys (key_hash,name,quota_monthly,key_prefix,"
        "environment,is_default,status,revoked) "
        "VALUES (%s,%s,%s,%s,'live',FALSE,'active',FALSE) RETURNING id",
        (digest, KEY_NAME, QUOTA, key[:16]),
    ).fetchone()
    return {
        "status": "provisioned",
        "key_id": inserted["id"],
        "created": True,
        "quota_monthly": QUOTA,
    }


def provision(
    key: str,
    *,
    verify_only: bool,
    previous_key_id: int | None = None,
    recette: bool = False,
) -> dict:
    from app.config import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row) as conn:
        if verify_only:
            conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '5s'")
        conn.execute("SET LOCAL lock_timeout = '250ms'")
        if not verify_only:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("datacommune:synthetic-probe-key",),
            )
        result = apply(
            conn,
            key,
            verify_only=verify_only,
            previous_key_id=previous_key_id,
            recette=recette,
        )
    # The connection context has committed successfully before reporting success.
    return result


def main() -> int:
    parser = RedactedParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--previous-key-id", type=int)
    parser.add_argument("--recette", action="store_true")
    try:
        args = parser.parse_args()
        require(
            args.previous_key_id is None
            or (args.previous_key_id > 0 and not args.recette),
            "invalid_previous_key_id",
        )
        key = read_key(
            sys.stdin.buffer, RECETTE_KEY_PATTERN if args.recette else KEY_PATTERN
        )
        result = provision(
            key,
            verify_only=args.verify_only,
            previous_key_id=args.previous_key_id,
            recette=args.recette,
        )
    except ProvisioningError as error:
        print(json.dumps({"status": "refused", "code": str(error)}))
        return 2
    except Exception:  # noqa: BLE001
        # Exception text can contain bound SQL parameters or a DSN.
        print(json.dumps({"status": "unavailable", "code": "provisioning_failed"}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
