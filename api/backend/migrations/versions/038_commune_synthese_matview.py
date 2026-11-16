"""Create materialized view v_commune_synthese aggregating 6 commune datasets.

Revision ID: 038_commune_synthese
Revises: 037_geog_trgm_communes
Create Date: 2026-03-14

Joins demographie, revenus, zonage_abc, vacance, fiscalite_locale, and
indicateurs_loyers into a single pre-computed view (~35K rows) for the
GET /v1/commune endpoint.  Each source is filtered to its latest year.
Loyers are pivoted into loyer_m2_appartement / loyer_m2_maison columns.

A UNIQUE index on code_insee enables REFRESH CONCURRENTLY.
"""

from alembic import op

revision = "038_commune_synthese"
down_revision = "037_geog_trgm_communes"
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
            -- demographie
            d.population,
            d.nb_menages,
            d.nb_logements,
            d.nb_logements_vacants AS nb_logements_vacants_recensement,
            d.densite,
            d.superficie,
            d.annee AS annee_demographie,
            -- revenus
            r.revenu_median,
            r.taux_pauvrete,
            r.indice_gini,
            r.part_imposee,
            r.annee AS annee_revenus,
            -- zonage_abc
            z.zone AS zone_abc,
            z.reclassement AS zone_abc_reclassement,
            -- vacance
            v.taux_vacance,
            v.nb_vacants_plus_2ans,
            v.annee AS annee_vacance,
            -- fiscalite
            f.taux_tfpb,
            f.taux_th_rs,
            f.taux_teom,
            f.nom_epci,
            f.annee AS annee_fiscalite,
            -- loyers (pivot)
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
