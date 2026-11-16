"""Restore scoring matview after source-table ETL swaps.

Revision ID: 066_restore_scoring_matview
Revises: 065_restore_etl_constraints
Create Date: 2026-06-13
"""

from alembic import op

revision = "066_restore_scoring_matview"
down_revision = "065_restore_etl_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS scoring_renov_target CASCADE")
    op.execute("""
        CREATE MATERIALIZED VIEW scoring_renov_target AS
        WITH dpe_fg AS (
            SELECT numero_dpe, date_etablissement_dpe, annee_construction,
                   surface_habitable, etiquette_dpe, type_installation_chauffage,
                   code_postal, code_departement, position
            FROM diagnostics_dpe
            WHERE etiquette_dpe IN ('F', 'G')
              AND position IS NOT NULL
              AND code_departement IN ('44', '33')
              AND surface_habitable > 0
              AND surface_habitable < 10000
        ),
        dpe_with_parcelle AS (
            SELECT DISTINCT ON (d.numero_dpe)
                d.numero_dpe, d.date_etablissement_dpe, d.annee_construction,
                d.surface_habitable, d.etiquette_dpe,
                d.type_installation_chauffage, d.code_postal,
                d.code_departement, p.id_parcelle, p.commune,
                p.contenance, d.position
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
    """)
    op.execute("""
        CREATE UNIQUE INDEX idx_scoring_numero_dpe
        ON scoring_renov_target (numero_dpe)
    """)
    op.execute("""
        CREATE INDEX idx_scoring_position
        ON scoring_renov_target USING GIST (position)
    """)
    op.execute("""
        CREATE INDEX idx_scoring_code_departement
        ON scoring_renov_target (code_departement)
    """)
    op.execute("""
        CREATE INDEX idx_scoring_etiquette_dpe
        ON scoring_renov_target (etiquette_dpe)
    """)
    op.execute("""
        CREATE INDEX idx_scoring_commune
        ON scoring_renov_target (commune)
    """)
    op.execute("""
        INSERT INTO matview_metadata (
            matview_name,
            last_refresh_at,
            last_refresh_status,
            freshness_threshold_days
        )
        VALUES ('scoring_renov_target', NOW(), 'success', 2)
        ON CONFLICT (matview_name) DO UPDATE SET
            last_refresh_at = NOW(),
            last_refresh_status = 'success',
            last_error = NULL
    """)


def downgrade() -> None:
    op.execute(
        "DELETE FROM matview_metadata WHERE matview_name = 'scoring_renov_target'"
    )
    op.execute("DROP MATERIALIZED VIEW IF EXISTS scoring_renov_target CASCADE")
