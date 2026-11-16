"""Plan 04.2-03: ``permis`` is keyed on (numero_dossier, destination).

Replayed through Alembic on the real chain (108 -> 109 -> 108): at 108 the
second destination row of one dossier is refused; at 109 it is accepted, a
conflicting copy without destination is still refused, and the downgrade
refuses, naming the count, while a dossier holds two rows. Synthetic data
(``SYN-``).

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_migration_109_permis_dossier_destination_key.py
"""

from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
AVANT = "108_dvf_grain_valeur_fonciere"
APRES = "109_permis_dossier_destination_key"
INSERT = (
    "INSERT INTO permis (numero_dossier, destination, surface_plancher, annee) "
    "VALUES ('SYN-PC-1', %s, %s, 2022)"
)


def _alembic(dsn: str, action: str, revision: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", dsn)
        context.setattr(settings, "database_url", dsn)
        getattr(command, action)(cfg, revision)


def _unique(conn) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'permis'::regclass AND contype = 'u'"
        ).fetchall()
    ]


def test_key_follows_the_destination_and_downgrade_refuses_shared_dossiers(
    isolated_pg_dsn,
):
    dsn = isolated_pg_dsn
    _alembic(dsn, "upgrade", AVANT)
    with psycopg.connect(dsn) as conn:
        assert _unique(conn) == ["UNIQUE (numero_dossier)"]
        conn.execute(INSERT, ("Habitation - Logement", 3476))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(INSERT, ("Habitation - Hébergement", 5873))
        conn.rollback()
        oid = conn.execute("SELECT 'permis'::regclass::oid").fetchone()[0]
        conn.execute(INSERT, ("Habitation - Logement", 3476))
        conn.commit()

    _alembic(dsn, "upgrade", APRES)
    with psycopg.connect(dsn) as conn:
        assert _unique(conn) == [
            "UNIQUE NULLS NOT DISTINCT (numero_dossier, destination)"
        ]
        assert conn.execute("SELECT 'permis'::regclass::oid").fetchone()[0] == oid
        conn.execute(INSERT, ("Habitation - Hébergement", 5873))
        conn.commit()
        conn.execute(INSERT, (None, 1))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(INSERT, (None, 2))
        conn.rollback()

    with pytest.raises(Exception, match="1 dossiers hold several destination rows"):
        _alembic(dsn, "downgrade", AVANT)
    with psycopg.connect(dsn) as conn:
        assert _unique(conn) == [
            "UNIQUE NULLS NOT DISTINCT (numero_dossier, destination)"
        ]
        conn.execute("DELETE FROM permis WHERE destination LIKE '%Hébergement'")
        conn.commit()

    _alembic(dsn, "downgrade", AVANT)
    with psycopg.connect(dsn) as conn:
        assert _unique(conn) == ["UNIQUE (numero_dossier)"]
        assert conn.execute("SELECT count(*) FROM permis").fetchone()[0] == 1
