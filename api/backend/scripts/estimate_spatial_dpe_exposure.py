#!/usr/bin/env python3
"""Estimate how often the pre-51 DPE path served a nearby diagnostic.

Measured default run (local Docker database, department 85, 2026-08-20):
85.7% of addresses with a DPE displayed by the pre-51 path relied on spatial
fallback (842/982; Wilson 95% CI [83.4%, 87.8%]). The deterministic sample used
seed 51092026 and contained 1,000 BAN addresses.

The pre-51 resolve path called ``dpe_par_point`` with a 500 metre radius and a
limit of three. Phase 51 instead fetches diagnostics by the exact BAN identity
and applies ``classify_dpe_attribution``. This script compares those paths on a
deterministic sample of BAN addresses and reports four exhaustive buckets:

``spatial-only``
    The former path returned at least one row and the exact policy is absent.
``exact``
    The exact policy elects one individual diagnostic.
``building``
    Exact rows exist but elect no individual diagnostic. The policy statuses
    ``building`` and ``multiple`` are grouped here for the four-bucket report.
``aucun``
    Neither path finds a reportable diagnostic.

The headline exposure is the share of addresses with a pre-51 displayed DPE
whose exact policy is not ``exact`` (``spatial-only`` plus ``building`` when
the former spatial path returned rows). It therefore measures the addresses
that lost an individually attributed DPE under the honest Phase 51 policy.

Safety is structural as well as conventional: only loopback/Docker hostnames
are accepted, PostgreSQL connections set ``default_transaction_read_only=on``,
and a SQLAlchemy execution guard rejects every application statement that does
not start with ``SELECT``. The script prints aggregates only, never BAN ids,
addresses or coordinates.

Usage, from ``api/backend``::

    uv run python -m scripts.estimate_spatial_dpe_exposure
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.dpe.attribution import classify_dpe_attribution
from app.dpe.queries import dpe_par_identifiant_ban, dpe_par_point

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://datacommune:devpassword@localhost:5432/datacommune"
)
DEFAULT_DEPARTMENT = "85"
DEFAULT_SAMPLE_SIZE = 1000
DEFAULT_SEED = 51092026
PRE51_RADIUS_M = 500.0
PRE51_LIMIT = 3
LOCAL_DATABASE_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "db"})
CATEGORIES = ("spatial-only", "exact", "building", "aucun")

Category = Literal["spatial-only", "exact", "building", "aucun"]

SAMPLE_ADDRESSES_SQL = text("""
    SELECT ban_id,
           code_commune,
           nom_commune,
           ST_Y(position) AS latitude,
           ST_X(position) AS longitude
    FROM adresses
    WHERE code_departement = :department
      AND ban_id IS NOT NULL
      AND btrim(ban_id) <> ''
      AND position IS NOT NULL
    ORDER BY md5(ban_id || :seed), ban_id
    LIMIT :sample_size
""")

READ_ONLY_STATUS_SQL = text("""
    SELECT current_setting('transaction_read_only') AS transaction_read_only
""")


@dataclass(frozen=True)
class SampleAddress:
    """Minimum private input retained in memory for one measurement."""

    ban_id: str
    code_commune: str
    nom_commune: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Measurement:
    """Redacted result for one sampled address."""

    code_commune: str
    nom_commune: str
    category: Category
    pre51_displayed: bool
    exposed: bool


def _assert_select_only(statement: object) -> None:
    sql = str(statement).lstrip()
    if not sql.upper().startswith("SELECT"):
        raise RuntimeError("read-only guard rejected a non-SELECT statement")


def _guard_application_statement(
    _connection: object,
    clauseelement: object,
    _multiparams: object,
    _params: object,
    _execution_options: object,
) -> None:
    _assert_select_only(clauseelement)


def _validate_local_database_url(database_url: str) -> None:
    url = make_url(database_url)
    if url.drivername != "postgresql+asyncpg":
        raise ValueError("database URL must use postgresql+asyncpg")
    if url.host not in LOCAL_DATABASE_HOSTS:
        raise ValueError("database host must be local Docker or loopback")


def _create_read_only_engine(database_url: str) -> AsyncEngine:
    _validate_local_database_url(database_url)
    engine = create_async_engine(
        database_url,
        pool_size=1,
        max_overflow=0,
        connect_args={
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": "120000",
            }
        },
    )
    event.listen(engine.sync_engine, "before_execute", _guard_application_statement)
    return engine


def _address_from_row(row: object) -> SampleAddress:
    values = row._mapping  # type: ignore[attr-defined]
    return SampleAddress(
        ban_id=str(values["ban_id"]),
        code_commune=str(values["code_commune"] or "inconnu"),
        nom_commune=str(values["nom_commune"] or "inconnue"),
        latitude=float(values["latitude"]),
        longitude=float(values["longitude"]),
    )


async def _sample_addresses(
    session: AsyncSession,
    department: str,
    sample_size: int,
    seed: int,
) -> list[SampleAddress]:
    result = await session.execute(
        SAMPLE_ADDRESSES_SQL,
        {
            "department": department,
            "sample_size": sample_size,
            "seed": str(seed),
        },
    )
    return [_address_from_row(row) for row in result]


def _category(policy_status: str, pre51_displayed: bool) -> Category:
    if policy_status == "exact":
        return "exact"
    if policy_status in {"building", "multiple"}:
        return "building"
    if policy_status != "absent":
        raise RuntimeError(f"unexpected attribution status: {policy_status}")
    return "spatial-only" if pre51_displayed else "aucun"


async def _measure_address(
    session: AsyncSession,
    address: SampleAddress,
) -> Measurement:
    spatial_rows = await dpe_par_point(
        session,
        address.latitude,
        address.longitude,
        rayon_m=PRE51_RADIUS_M,
        limit=PRE51_LIMIT,
    )
    exact_rows, exact_truncated = await dpe_par_identifiant_ban(session, address.ban_id)
    decision = classify_dpe_attribution(
        address.ban_id,
        exact_rows,
        exact_truncated=exact_truncated,
    )
    pre51_displayed = bool(spatial_rows)
    category = _category(decision.status, pre51_displayed)
    return Measurement(
        code_commune=address.code_commune,
        nom_commune=address.nom_commune,
        category=category,
        pre51_displayed=pre51_displayed,
        exposed=pre51_displayed and decision.status != "exact",
    )


async def _collect(
    session: AsyncSession,
    addresses: Sequence[SampleAddress],
) -> list[Measurement]:
    measurements: list[Measurement] = []
    for index, address in enumerate(addresses, start=1):
        measurements.append(await _measure_address(session, address))
        if index % 100 == 0:
            print(f"progress={index}/{len(addresses)}", file=sys.stderr)
    return measurements


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z**2 / total
    centre = (proportion + z**2 / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2))
        / denominator
    )
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _percent(value: int, total: int) -> str:
    return "n/a" if total == 0 else f"{value / total * 100:.1f}%"


def _category_cell(counts: Counter[str], category: str, total: int) -> str:
    count = counts[category]
    return f"{count} ({_percent(count, total)})"


def _render_global(measurements: Sequence[Measurement]) -> list[str]:
    counts = Counter(item.category for item in measurements)
    total = len(measurements)
    lines = ["global:", "category | count | proportion | 95% CI"]
    for category in CATEGORIES:
        low, high = _wilson_interval(counts[category], total)
        lines.append(
            f"{category} | {counts[category]} | "
            f"{_percent(counts[category], total)} | "
            f"[{low * 100:.1f}%, {high * 100:.1f}%]"
        )
    return lines


def _group_by_commune(
    measurements: Sequence[Measurement],
) -> dict[tuple[str, str], list[Measurement]]:
    grouped: dict[tuple[str, str], list[Measurement]] = defaultdict(list)
    for item in measurements:
        grouped[(item.code_commune, item.nom_commune)].append(item)
    return grouped


def _render_communes(measurements: Sequence[Measurement]) -> list[str]:
    lines = [
        "by_commune:",
        "code | commune | n | spatial-only | exact | building | aucun | exposure/pre51",
    ]
    for (code, name), rows in sorted(_group_by_commune(measurements).items()):
        counts = Counter(item.category for item in rows)
        displayed = sum(item.pre51_displayed for item in rows)
        exposed = sum(item.exposed for item in rows)
        safe_name = name.replace("|", "/")
        cells = [_category_cell(counts, category, len(rows)) for category in CATEGORIES]
        lines.append(
            f"{code} | {safe_name} | {len(rows)} | "
            f"{' | '.join(cells)} | {_percent(exposed, displayed)}"
        )
    return lines


def _render_report(
    measurements: Sequence[Measurement],
    department: str,
    requested_sample_size: int,
    seed: int,
) -> str:
    displayed = sum(item.pre51_displayed for item in measurements)
    exposed = sum(item.exposed for item in measurements)
    low, high = _wilson_interval(exposed, displayed)
    header = [
        "Spatial DPE exposure estimate (read-only)",
        f"department={department}",
        f"sample_size_requested={requested_sample_size}",
        f"sample_size_observed={len(measurements)}",
        f"seed={seed}",
        f"pre51_path=radius_m={PRE51_RADIUS_M:g},limit={PRE51_LIMIT}",
        "new_policy=exact BAN identity; multiple grouped under building",
        "transaction_read_only=on",
    ]
    final = [
        f"pre51_displayed={displayed}",
        f"pre51_exposed={exposed}",
        "FINAL: "
        f"{_percent(exposed, displayed)} of addresses with a pre-51 displayed DPE "
        "had no exact individual attribution "
        f"(Wilson 95% CI [{low * 100:.1f}%, {high * 100:.1f}%]).",
    ]
    return "\n".join(
        [
            *header,
            "",
            *_render_global(measurements),
            "",
            *final,
            "",
            *_render_communes(measurements),
        ]
    )


async def _run(args: argparse.Namespace) -> str:
    engine = _create_read_only_engine(args.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            status = await session.scalar(READ_ONLY_STATUS_SQL)
            if status != "on":
                raise RuntimeError("database transaction is not read-only")
            addresses = await _sample_addresses(
                session, args.department, args.sample_size, args.seed
            )
            if len(addresses) != args.sample_size:
                raise RuntimeError(
                    "department does not contain the requested sample size"
                )
            measurements = await _collect(session, addresses)
    finally:
        await engine.dispose()
    return _render_report(measurements, args.department, args.sample_size, args.seed)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        help="Local Docker PostgreSQL URL (defaults to DATABASE_URL or dev DB)",
    )
    parser.add_argument("--department", default=DEFAULT_DEPARTMENT)
    parser.add_argument(
        "--sample-size", type=_positive_int, default=DEFAULT_SAMPLE_SIZE
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    try:
        print(asyncio.run(_run(args)))
    except (OSError, RuntimeError, SQLAlchemyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
