"""Add nom_departement and nom_region to v_commune_synthese matview.

The Explorer registry declares `nom_departement` and `nom_region` as
queryable columns on `v_commune_synthese`, but migration 052 never
included them. The resulting SELECT failed with "column does not exist"
and Explorer silently skipped the `commune` dataset via graceful
degradation -- producing empty results for "Commune (synthèse)" and
"--" placeholders when joined with other datasets.

Source: COALESCE across revenus / fiscalite_locale / vacance, all of
which carry these names (see migrations 027, 029, 035). Falls back in
that order to cover communes missing from one source but present in
another.

Revision ID: 059_commune_synthese_add_nom_dept_region
Revises: 057_cleanup_ban_swap_orphans
Create Date: 2026-04-15
"""

from alembic import op

revision = "059_commune_synthese_add_nom_dept_region"
down_revision = "057_cleanup_ban_swap_orphans"
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
            COALESCE(r.nom_departement, f.nom_departement, v.nom_departement) AS nom_departement,
            d.code_region,
            COALESCE(r.nom_region, f.nom_region, v.nom_region) AS nom_region,
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
            r.nom_departement, r.nom_region,
            r.revenu_median, r.taux_pauvrete, r.indice_gini, r.part_imposee, r.annee,
            z.zone, z.reclassement,
            v.nom_departement, v.nom_region,
            v.taux_vacance, v.nb_vacants_plus_2ans, v.annee,
            f.nom_departement, f.nom_region,
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
