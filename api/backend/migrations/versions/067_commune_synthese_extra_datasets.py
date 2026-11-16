"""Extend v_commune_synthese with 7 commune-level dataset aggregates.

Adds per-dataset intermediate matviews (dvf, dpe, equipements, scolaire,
monuments, foncier_pm, connectivite) and LEFT JOINs them into
v_commune_synthese. Mirrors app/commune/matview_ddl.py -- keep in sync.

Revision ID: 067_commune_synthese_extra_datasets
Revises: 066_restore_scoring_matview
Create Date: 2026-06-15
"""

from alembic import op

revision = "067_commune_synthese_extra_datasets"
down_revision = "066_restore_scoring_matview"
branch_labels = None
depends_on = None

_INTERMEDIATE_MATVIEWS = (
    (
        "v_commune_dvf",
        """
        CREATE MATERIALIZED VIEW v_commune_dvf AS
        SELECT
            code_commune AS code_insee,
            ROUND((percentile_cont(0.5) WITHIN GROUP (
                ORDER BY valeur_fonciere / surface_reelle_bati
            ))::numeric, 0)::int AS prix_m2_median,
            count(*)::int AS nb_mutations_12m
        FROM mutations
        WHERE date_mutation >= (
                  SELECT max(date_mutation) FROM mutations
              ) - INTERVAL '12 months'
          AND surface_reelle_bati > 0
          AND valeur_fonciere > 0
          AND code_type_local IN ('1', '2')
        GROUP BY code_commune
        WITH DATA
        """,
    ),
    (
        "v_commune_dpe",
        """
        CREATE MATERIALIZED VIEW v_commune_dpe AS
        SELECT
            code_insee,
            ROUND(
                count(*) FILTER (WHERE etiquette_dpe IN ('F', 'G')) * 100.0
                / NULLIF(count(*) FILTER (
                    WHERE etiquette_dpe IN ('A','B','C','D','E','F','G')
                  ), 0),
                1
            ) AS dpe_passoires_pct,
            (ARRAY['A','B','C','D','E','F','G'])[
                (percentile_disc(0.5) WITHIN GROUP (
                    ORDER BY position(etiquette_dpe IN 'ABCDEFG')
                ) FILTER (
                    WHERE etiquette_dpe IN ('A','B','C','D','E','F','G')
                ))
            ] AS dpe_median,
            (percentile_disc(0.5) WITHIN GROUP (
                ORDER BY annee_construction
            ))::int AS annee_construction_mediane
        FROM diagnostics_dpe
        GROUP BY code_insee
        WITH DATA
        """,
    ),
    (
        "v_commune_equipements",
        """
        CREATE MATERIALIZED VIEW v_commune_equipements AS
        SELECT
            code_commune AS code_insee,
            SUM(nb_equipements)::int AS nb_equipements
        FROM equipements
        GROUP BY code_commune
        WITH DATA
        """,
    ),
    (
        "v_commune_scolaire",
        """
        CREATE MATERIALIZED VIEW v_commune_scolaire AS
        SELECT
            code_insee,
            count(DISTINCT code_rne)::int AS nb_etablissements_scolaires
        FROM carte_scolaire
        GROUP BY code_insee
        WITH DATA
        """,
    ),
    (
        "v_commune_monuments",
        """
        CREATE MATERIALIZED VIEW v_commune_monuments AS
        SELECT
            code_insee,
            count(*)::int AS nb_monuments
        FROM monuments_historiques
        GROUP BY code_insee
        WITH DATA
        """,
    ),
    (
        "v_commune_foncier_pm",
        """
        CREATE MATERIALIZED VIEW v_commune_foncier_pm AS
        SELECT
            code_commune AS code_insee,
            count(DISTINCT numero_siren)::int AS nb_proprietaires_pm,
            ROUND(SUM(contenance_parcelle)::numeric / 10000, 1) AS surface_pm_ha
        FROM foncier_pm
        GROUP BY code_commune
        WITH DATA
        """,
    ),
    (
        "v_commune_connectivite",
        """
        CREATE MATERIALIZED VIEW v_commune_connectivite AS
        SELECT
            code_insee,
            ROUND(
                count(DISTINCT id_imb) FILTER (WHERE technologie = 'FO') * 100.0
                / NULLIF(count(DISTINCT id_imb), 0),
                1
            ) AS fibre_pct
        FROM eligibilite_fixe
        GROUP BY code_insee
        WITH DATA
        """,
    ),
)

_CREATE_SYNTHESE = """
CREATE MATERIALIZED VIEW v_commune_synthese AS
SELECT
    d.code_insee,
    d.nom_commune,
    d.code_departement,
    COALESCE(r.nom_departement, f.nom_departement, v.nom_departement)
        AS nom_departement,
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
    MAX(CASE WHEN l.type_habitat = 'appartement' THEN l.loyer_m2 END)
        AS loyer_m2_appartement,
    MAX(CASE WHEN l.type_habitat = 'maison' THEN l.loyer_m2 END)
        AS loyer_m2_maison,
    MAX(l.annee) AS annee_loyers,
    dvf.prix_m2_median,
    dvf.nb_mutations_12m,
    dpe.dpe_passoires_pct,
    dpe.dpe_median,
    dpe.annee_construction_mediane,
    eq.nb_equipements,
    sc.nb_etablissements_scolaires,
    mon.nb_monuments,
    fpm.nb_proprietaires_pm,
    fpm.surface_pm_ha,
    con.fibre_pct
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
LEFT JOIN v_commune_dvf dvf ON dvf.code_insee = d.code_insee
LEFT JOIN v_commune_dpe dpe ON dpe.code_insee = d.code_insee
LEFT JOIN v_commune_equipements eq ON eq.code_insee = d.code_insee
LEFT JOIN v_commune_scolaire sc ON sc.code_insee = d.code_insee
LEFT JOIN v_commune_monuments mon ON mon.code_insee = d.code_insee
LEFT JOIN v_commune_foncier_pm fpm ON fpm.code_insee = d.code_insee
LEFT JOIN v_commune_connectivite con ON con.code_insee = d.code_insee
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
    f.taux_tfpb, f.taux_th_rs, f.taux_teom, f.nom_epci, f.annee,
    dvf.prix_m2_median, dvf.nb_mutations_12m,
    dpe.dpe_passoires_pct, dpe.dpe_median, dpe.annee_construction_mediane,
    eq.nb_equipements, sc.nb_etablissements_scolaires, mon.nb_monuments,
    fpm.nb_proprietaires_pm, fpm.surface_pm_ha, con.fibre_pct
WITH DATA
"""

# Previous (6-dataset) synthese definition, restored on downgrade. Mirrors
# migration 059_commune_synthese_add_nom_dept_region.
_CREATE_SYNTHESE_V059 = """
CREATE MATERIALIZED VIEW v_commune_synthese AS
SELECT
    d.code_insee,
    d.nom_commune,
    d.code_departement,
    COALESCE(r.nom_departement, f.nom_departement, v.nom_departement)
        AS nom_departement,
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
    MAX(CASE WHEN l.type_habitat = 'appartement' THEN l.loyer_m2 END)
        AS loyer_m2_appartement,
    MAX(CASE WHEN l.type_habitat = 'maison' THEN l.loyer_m2 END)
        AS loyer_m2_maison,
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
"""

_SYNTHESE_INDEXES = (
    "CREATE UNIQUE INDEX idx_commune_synthese_code_insee "
    "ON v_commune_synthese (code_insee)",
    "CREATE INDEX idx_commune_synthese_dept ON v_commune_synthese (code_departement)",
)


def upgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE")
    for name, create_sql in _INTERMEDIATE_MATVIEWS:
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
        op.execute(create_sql)
        op.execute(f"CREATE UNIQUE INDEX idx_{name}_code_insee ON {name} (code_insee)")
    op.execute(_CREATE_SYNTHESE)
    for sql in _SYNTHESE_INDEXES:
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE")
    for name, _ in _INTERMEDIATE_MATVIEWS:
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
    op.execute(_CREATE_SYNTHESE_V059)
    for sql in _SYNTHESE_INDEXES:
        op.execute(sql)
