"""Migration 114 and the storage of the error reports, on a real PostgreSQL.

The route tests replace the two database calls; here they run for real against
the migrated schema: what a report stores (and that no column can hold the
address of the visitor), the 24 h count behind the daily ceiling, the retention
enforced on the write path and by the weekly job, the bounds repeated by the
table, and the downgrade.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.scheduler import jobs
from app.signalement.schemas import SignalementDepot
from app.signalement.service import compter_24h, enregistrer

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
COLONNES = [
    "id",
    "reference",
    "created_at",
    "page",
    "titre",
    "lignes",
    "source",
    "version_site",
    "largeur",
    "user_agent",
    "commentaire",
    "email",
]
DEPOT = SignalementDepot(
    commentaire="Le prix médian des maisons paraît dix fois trop bas.",
    email="Lecteur@Example.fr",
    page="/adresse/44109_1234_00012",
    titre="Prix médian des maisons",
    lignes="dvf.prix_m2_median_maison=312",
    source="DVF",
    version_site="81d3926f",
    largeur=1280,
    user_agent="Mozilla/5.0",
)


def _alembic() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


@pytest.fixture(scope="module")
def base(isolated_pg_dsn):
    """The isolated database migrated to head; DSN of the module."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", isolated_pg_dsn)
        patch.setattr(settings, "database_url", isolated_pg_dsn)
        command.upgrade(_alembic(), "head")
        yield isolated_pg_dsn


@pytest.fixture()
def vide(base):
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute("TRUNCATE signalements RESTART IDENTITY")
    return base


def _service(dsn: str, appel):
    """Run *appel(session)* with the asyncpg session the API uses."""

    async def jouer():
        engine = create_async_engine(
            dsn.replace("postgresql://", "postgresql+asyncpg://")
        )
        try:
            async with AsyncSession(engine) as session:
                return await appel(session)
        finally:
            await engine.dispose()

    return asyncio.run(jouer())


def _vieillir(dsn: str, reference: str, jours: int) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE signalements SET created_at = now() - make_interval(days => %s) "
            "WHERE reference = %s",
            (jours, reference),
        )


def _references(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        return {r[0] for r in conn.execute("SELECT reference FROM signalements")}


def test_the_table_has_no_column_for_the_address_of_the_visitor(base):
    with psycopg.connect(base) as conn:
        colonnes = [
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'signalements' "
                "ORDER BY ordinal_position"
            )
        ]

    assert colonnes == COLONNES


def test_a_report_is_stored_as_sent_and_counted(vide):
    _service(vide, lambda db: enregistrer(db, DEPOT, "0123456789"))

    with psycopg.connect(vide) as conn:
        ligne = conn.execute(
            "SELECT reference, page, titre, lignes, source, version_site, largeur, "
            "user_agent, commentaire, email, created_at > now() - interval '1 minute' "
            "FROM signalements"
        ).fetchall()
    assert ligne == [
        (
            "0123456789",
            DEPOT.page,
            DEPOT.titre,
            DEPOT.lignes,
            DEPOT.source,
            DEPOT.version_site,
            1280,
            DEPOT.user_agent,
            DEPOT.commentaire,
            "lecteur@example.fr",
            True,
        )
    ]
    assert _service(vide, compter_24h) == 1


def test_the_daily_count_ignores_what_is_older_than_24_hours(vide):
    _service(vide, lambda db: enregistrer(db, DEPOT, "AAAAAAAAAA"))
    _service(vide, lambda db: enregistrer(db, DEPOT, "BBBBBBBBBB"))
    _vieillir(vide, "AAAAAAAAAA", 2)

    assert _service(vide, compter_24h) == 1


def test_storing_a_report_drops_those_past_their_retention(vide):
    _service(vide, lambda db: enregistrer(db, DEPOT, "AAAAAAAAAA"))
    _service(vide, lambda db: enregistrer(db, DEPOT, "BBBBBBBBBB"))
    _vieillir(vide, "AAAAAAAAAA", settings.signalement_conservation_jours + 1)
    _vieillir(vide, "BBBBBBBBBB", settings.signalement_conservation_jours - 1)

    _service(vide, lambda db: enregistrer(db, DEPOT, "CCCCCCCCCC"))

    assert _references(vide) == {"BBBBBBBBBB", "CCCCCCCCCC"}


def test_the_weekly_job_drops_them_too_when_nobody_writes(vide):
    _service(vide, lambda db: enregistrer(db, DEPOT, "AAAAAAAAAA"))
    _service(vide, lambda db: enregistrer(db, DEPOT, "BBBBBBBBBB"))
    _vieillir(vide, "AAAAAAAAAA", settings.signalement_conservation_jours + 1)

    assert jobs.purge_signalements() == 1

    assert _references(vide) == {"BBBBBBBBBB"}


@pytest.mark.parametrize(
    ("colonne", "valeur"),
    [
        ("commentaire", "court"),
        ("commentaire", "x" * 2001),
        ("reference", "pas-une-ref"),
        ("page", "https://ailleurs.example/"),
        ("page", "//ailleurs.example"),
        ("email", "a" * 255),
        ("largeur", -1),
    ],
)
def test_the_table_refuses_what_the_route_would_refuse(vide, colonne, valeur):
    champs = {
        "reference": "0123456789",
        "commentaire": DEPOT.commentaire,
        colonne: valeur,
    }
    noms = ", ".join(champs)
    marques = ", ".join(["%s"] * len(champs))

    with (
        psycopg.connect(vide) as conn,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        conn.execute(
            f"INSERT INTO signalements ({noms}) VALUES ({marques})",
            list(champs.values()),
        )


def test_the_purge_does_nothing_where_the_table_does_not_exist_yet(base):
    """Last of the module: it downgrades. A base still at 113 keeps its weekly job."""
    command.downgrade(_alembic(), "113_bdnb_batiment_parcelle")
    with psycopg.connect(base) as conn:
        assert conn.execute("SELECT to_regclass('public.signalements')").fetchone() == (
            None,
        )

    assert jobs.purge_signalements() == 0
