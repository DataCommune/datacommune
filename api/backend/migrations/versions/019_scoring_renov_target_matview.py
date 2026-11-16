"""Create scoring_renov_target materialized view.

Revision ID: 019_scoring_matview
Revises: 018_scheduling_monitoring
Create Date: 2026-02-17

Builds a materialized view that cross-joins DPE (F/G labels) with cadastre
parcelles (ST_Intersects) and DVF mutations (last sale per parcel).

v1 MVP scope: departments 44 and 33 only (~2M DPE rows).
Expansion to all departments is planned for a later phase.
"""

revision = "019_scoring_matview"
down_revision = "018_scheduling_monitoring"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("""
        CREATE MATERIALIZED VIEW scoring_renov_target AS

        WITH dpe_fg AS (
            -- Pre-filter DPE to F/G labels, scoped to depts 44+33 (v1 MVP).
            -- DPE positions are already in WGS84 (SRID 4326), same as parcelles.
            SELECT numero_dpe, date_etablissement_dpe, annee_construction,
                   surface_habitable, etiquette_dpe, type_installation_chauffage,
                   code_postal, code_departement,
                   position
            FROM diagnostics_dpe
            WHERE etiquette_dpe IN ('F', 'G')
              AND position IS NOT NULL
              AND code_departement IN ('44', '33')
        ),

        dpe_with_parcelle AS (
            -- Spatial join: DPE point-in-polygon with parcelles.
            -- DISTINCT ON (d.numero_dpe) prevents duplicates when a DPE point
            -- sits on a parcel boundary and matches multiple parcelles.
            -- Ties broken by largest parcel (contenance DESC).
            SELECT DISTINCT ON (d.numero_dpe)
                d.numero_dpe, d.date_etablissement_dpe, d.annee_construction,
                d.surface_habitable, d.etiquette_dpe, d.type_installation_chauffage,
                d.code_postal, d.code_departement,
                p.id_parcelle, p.commune, p.contenance,
                d.position
            FROM dpe_fg d
            JOIN parcelles p ON ST_Intersects(p.geometrie, d.position)
            ORDER BY d.numero_dpe, p.contenance DESC
        ),

        last_mutation AS (
            -- Last DVF sale per parcelle via DISTINCT ON
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

    # Unique index on numero_dpe -- required for REFRESH CONCURRENTLY
    op.execute("""
        CREATE UNIQUE INDEX idx_scoring_numero_dpe
        ON scoring_renov_target (numero_dpe)
    """)

    # GIST index on position -- spatial queries
    op.execute("""
        CREATE INDEX idx_scoring_position
        ON scoring_renov_target USING GIST (position)
    """)

    # B-tree index on code_departement -- most common API filter
    op.execute("""
        CREATE INDEX idx_scoring_code_departement
        ON scoring_renov_target (code_departement)
    """)

    # B-tree index on etiquette_dpe -- DPE label filter
    op.execute("""
        CREATE INDEX idx_scoring_etiquette_dpe
        ON scoring_renov_target (etiquette_dpe)
    """)

    # B-tree index on commune -- commune-level queries
    op.execute("""
        CREATE INDEX idx_scoring_commune
        ON scoring_renov_target (commune)
    """)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS scoring_renov_target CASCADE")
