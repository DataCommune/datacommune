"""Core audit primitives: Finding, DatasetAuditor, helpers.

get_sync_dsn() is defined here (not in run.py or profiles.py) to break the
profiles↔run import cycle (C1):  run.py imports profiles.py, so profiles.py
must never import run.py.  base.py has no dependency on either → safe anchor.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

import psycopg

Severity = Literal["INFO", "WARN", "ERROR"]
Category = Literal[
    "volume",
    "freshness",
    "nulls",
    "geo",
    "temporal",
    "integrity",
    "business",
    "duplicates",
    "format",
    "proxy",
]


@dataclass
class Finding:
    """One anomaly discovered by an auditor."""

    severity: Severity
    category: Category
    title: str
    description: str
    count: int | None = None
    sample_query: str | None = None
    fix_hint: str | None = None


@dataclass
class AuditResult:
    """Everything a dataset audit produced."""

    dataset: str
    table: str
    row_count: int
    findings: list[Finding] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)
    dimensions_run: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "ERROR"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "WARN"]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "INFO"]


def get_sync_dsn() -> str:
    """Return a psycopg-compatible sync DSN from settings.database_url.

    Shared sync DSN helper — lives in base.py (no import of profiles/run) to
    break the profiles↔run cycle (C1).

    run.py._sync_dsn() uses the same transformation; its optional repointage
    to this function is deferred to plan 24-06 which owns run.py.
    """
    from app.config import settings  # local import to keep base.py importable

    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


class DatasetAuditor(ABC):
    """One auditor per dataset. Owns all checks for that dataset."""

    name: str
    table: str

    @abstractmethod
    def run(self, conn: psycopg.Connection) -> AuditResult:
        """Execute every check and return a result with findings."""
        ...

    # -- helpers shared by subclasses ----------------------------------------

    @staticmethod
    def scalar(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int | None:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        return None if row is None else row[0]

    @staticmethod
    def rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[tuple]:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    @staticmethod
    def null_rates(
        conn: psycopg.Connection, table: str, columns: list[str]
    ) -> dict[str, tuple[int, int]]:
        """For each column return (null_count, total_count)."""
        if not columns:
            return {}
        total_sql = f"SELECT count(*) FROM {table}"
        total = DatasetAuditor.scalar(conn, total_sql) or 0
        selects = ", ".join(
            f"sum(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END) AS {c}_nulls"
            for c in columns
        )
        with conn.cursor() as cur:
            cur.execute(f"SELECT {selects} FROM {table}")
            row = cur.fetchone()
        result: dict[str, tuple[int, int]] = {}
        for i, c in enumerate(columns):
            nulls = int(row[i] or 0) if row else 0
            result[c] = (nulls, total)
        return result
