"""Recreate scoring_renov_target without a frozen departement, if it exists.

Revision ID: 102_scoring_scope_national
Revises: 101_dpe_scope_national
Create Date: 2026-10-13

Plan 04.1-08 (ROADMAP 4.1 criterion 4). Migration 066 created the
materialized view ``scoring_renov_target`` on DPE of 44 and 33 only
(``code_departement IN ('44', '33')``), recorded as the last schema lock of
``app.scope.model.SCOPE_LOCKS``. ``diagnostics_dpe`` holds only the hosted
departements: the filter is dropped, nothing replaces it.

The view is not always present: an ETL swap of a source table drops it with
``CASCADE``, and the scoring surface is quarantined (COR-09/13/15,
``app.common.unqualified_surfaces``), so nothing recreates it. Measured
absent on the work base on 2026-10-13 (``to_regclass`` NULL). Upgrade
and downgrade therefore act only when a materialized view of that name
exists: drop, create with data, indexes and freshness metadata (the steps of
migration 066). A relation of that name which is not a materialized view is
refused, never dropped.

The DDL is copied here (a migration never imports the application);
``CREATE_SQL`` equals ``app.scoring.matview_ddl.CREATE_SQL`` and
``CREATE_SQL_066`` the view of migration 066, checked by
tests/test_scope_locks.py; the effect is proven on PostGIS by
tests/integration/test_scoring_scope_national.py.
"""

from alembic import op
from sqlalchemy import text

revision: str = "102_scoring_scope_national"
down_revision: str = "101_dpe_scope_national"
branch_labels: str | None = None
depends_on: str | None = None

# Waiting behind an import holding a source table would block the deploy.
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"

DROP_SQL = "DROP MATERIALIZED VIEW IF EXISTS scoring_renov_target CASCADE"

_DPE_FG_066 = """
    FROM diagnostics_dpe
    WHERE etiquette_dpe IN ('F', 'G')
      AND position IS NOT NULL
      AND code_departement IN ('44', '33')
      AND surface_habitable > 0
"""
_DPE_FG_102 = """
    FROM diagnostics_dpe
    WHERE etiquette_dpe IN ('F', 'G')
      AND position IS NOT NULL
      AND surface_habitable > 0
"""

# Copy of app.scoring.matview_ddl.CREATE_SQL at this revision.
CREATE_SQL = """
CREATE MATERIALIZED VIEW scoring_renov_target AS
WITH dpe_fg AS (
    SELECT numero_dpe, date_etablissement_dpe, annee_construction,
           surface_habitable, etiquette_dpe, type_installation_chauffage,
           code_postal, code_departement, position
    FROM diagnostics_dpe
    WHERE etiquette_dpe IN ('F', 'G')
      AND position IS NOT NULL
      AND surface_habitable > 0
      AND surface_habitable < 10000
),
dpe_with_parcelle AS (
    SELECT DISTINCT ON (d.numero_dpe)
        d.numero_dpe, d.date_etablissement_dpe, d.annee_construction,
        d.surface_habitable, d.etiquette_dpe, d.type_installation_chauffage,
        d.code_postal, d.code_departement,
        p.id_parcelle, p.commune, p.contenance, d.position
    FROM dpe_fg d
    JOIN parcelles p ON ST_Intersects(p.geometrie, d.position)
    ORDER BY d.numero_dpe, p.contenance DESC
),
last_mutation AS (
    SELECT DISTINCT ON (id_parcelle)
        id_parcelle,
        date_mutation AS date_derniere_vente,
        valeur_fonciere AS prix_derniere_vente,
        adresse_numero,
        adresse_nom_voie
    FROM mutations
    WHERE id_parcelle IS NOT NULL
    ORDER BY id_parcelle, date_mutation DESC
)
SELECT
    dp.numero_dpe,
    dp.date_etablissement_dpe AS date_dpe,
    dp.annee_construction,
    dp.surface_habitable,
    dp.etiquette_dpe,
    dp.type_installation_chauffage AS type_chauffage,
    dp.code_postal,
    dp.code_departement,
    dp.commune,
    dp.id_parcelle,
    dp.contenance,
    lm.date_derniere_vente,
    lm.prix_derniere_vente,
    CONCAT_WS(' ', lm.adresse_numero, lm.adresse_nom_voie) AS adresse,
    dp.position
FROM dpe_with_parcelle dp
LEFT JOIN last_mutation lm ON dp.id_parcelle = lm.id_parcelle
WITH DATA
"""
# Migration 066: the same view, frozen on 44 and 33.
CREATE_SQL_066 = CREATE_SQL.replace(_DPE_FG_102, _DPE_FG_066)

INDEX_SQLS = (
    "CREATE UNIQUE INDEX idx_scoring_numero_dpe ON scoring_renov_target (numero_dpe)",
    "CREATE INDEX idx_scoring_position ON scoring_renov_target USING GIST (position)",
    "CREATE INDEX idx_scoring_code_departement "
    "ON scoring_renov_target (code_departement)",
    "CREATE INDEX idx_scoring_etiquette_dpe ON scoring_renov_target (etiquette_dpe)",
    "CREATE INDEX idx_scoring_commune ON scoring_renov_target (commune)",
)

METADATA_SQL = """
    INSERT INTO matview_metadata (
        matview_name, last_refresh_at, last_refresh_status, freshness_threshold_days
    )
    VALUES ('scoring_renov_target', NOW(), 'success', 2)
    ON CONFLICT (matview_name) DO UPDATE SET
        last_refresh_at = NOW(),
        last_refresh_status = 'success',
        last_error = NULL
"""


def _view_exists() -> bool:
    """True for the materialized view; refuse any other relation of that name."""
    kind = (
        op.get_bind()
        .execute(
            text(
                "SELECT c.relkind FROM pg_class c "
                "WHERE c.oid = to_regclass('scoring_renov_target')"
            )
        )
        .scalar()
    )
    if kind is None:
        return False
    if kind != "m":
        raise RuntimeError(
            "refused: scoring_renov_target exists but is not a materialized "
            f"view (relkind {kind!r}); migration 102_scoring_scope_national "
            "drops nothing else"
        )
    return True


def _recreate(create_sql: str) -> None:
    if not _view_exists():
        return
    op.execute(LOCK_TIMEOUT)
    op.execute(DROP_SQL)
    op.execute(create_sql)
    for statement in INDEX_SQLS:
        op.execute(statement)
    op.execute(METADATA_SQL)


def upgrade() -> None:
    _recreate(CREATE_SQL)


def downgrade() -> None:
    _recreate(CREATE_SQL_066)
