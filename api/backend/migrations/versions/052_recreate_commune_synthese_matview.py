"""Recreate v_commune_synthese materialized view (was dropped in prod).

The matview created by migration 038 was missing in production, causing:
- /v1/health returning 503 (healthcheck verifies matview existence)
- Chat 500 errors when enriching responses with commune data

This migration is idempotent: drops if exists, then recreates with indexes.

Revision ID: 052_recreate_commune_synthese
Revises: 051_standardize_foncier_pm_name
Create Date: 2026-04-05
"""

from alembic import op

revision = "052_recreate_commune_synthese"
down_revision = "051_standardize_foncier_pm_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE")
    op.execute("""
        CREATE MATERIALIZED VIEW v_commune_synthese AS
        SELECT
            d.code_insee,
            d.nom_commune,
            d.code_departement,
            d.code_region,
            d.population,
            d.nb_menages,
            d.nb_logements,
            d.nb_logements_vacants AS nb_logements_vacants_recensement,
            d.densite,
            d.superficie,
            d.annee AS annee_demographie,
            r.revenu_median,
            r.taux_pauvrete,
            r.indice_gini,
            r.part_imposee,
            r.annee AS annee_revenus,
            z.zone AS zone_abc,
            z.reclassement AS zone_abc_reclassement,
            v.taux_vacance,
            v.nb_vacants_plus_2ans,
            v.annee AS annee_vacance,
            f.taux_tfpb,
            f.taux_th_rs,
            f.taux_teom,
            f.nom_epci,
            f.annee AS annee_fiscalite,
            MAX(CASE WHEN l.type_habitat = 'appartement' THEN l.loyer_m2 END) AS loyer_m2_appartement,
            MAX(CASE WHEN l.type_habitat = 'maison' THEN l.loyer_m2 END) AS loyer_m2_maison,
            MAX(l.annee) AS annee_loyers
        FROM demographie d
        LEFT JOIN revenus r ON r.code_insee = d.code_insee
            AND r.annee = (SELECT MAX(annee) FROM revenus)
        LEFT JOIN zonage_abc z ON z.code_insee = d.code_insee
        LEFT JOIN vacance v ON v.code_commune = d.code_insee
            AND v.annee = (SELECT MAX(annee) FROM vacance)
        LEFT JOIN fiscalite_locale f ON f.code_insee = d.code_insee
            AND f.annee = (SELECT MAX(annee) FROM fiscalite_locale)
        LEFT JOIN indicateurs_loyers l ON l.code_insee = d.code_insee
            AND l.annee = (SELECT MAX(annee) FROM indicateurs_loyers)
        WHERE d.annee = (SELECT MAX(annee) FROM demographie)
        GROUP BY
            d.code_insee, d.nom_commune, d.code_departement, d.code_region,
            d.population, d.nb_menages, d.nb_logements, d.nb_logements_vacants,
            d.densite, d.superficie, d.annee,
            r.revenu_median, r.taux_pauvrete, r.indice_gini, r.part_imposee, r.annee,
            z.zone, z.reclassement,
            v.taux_vacance, v.nb_vacants_plus_2ans, v.annee,
            f.taux_tfpb, f.taux_th_rs, f.taux_teom, f.nom_epci, f.annee
        WITH DATA
    """)
    op.execute("""
        CREATE UNIQUE INDEX idx_commune_synthese_code_insee
        ON v_commune_synthese (code_insee)
    """)
    op.execute("""
        CREATE INDEX idx_commune_synthese_dept
        ON v_commune_synthese (code_departement)
    """)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE")
