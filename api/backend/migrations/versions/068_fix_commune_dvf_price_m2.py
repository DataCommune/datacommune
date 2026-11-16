"""Fix commune DVF price/m2 aggregation.

Revision ID: 068_fix_commune_dvf_price_m2
Revises: 067_commune_synthese_extra_datasets
Create Date: 2026-06-30
"""

from alembic import op

revision = "068_fix_commune_dvf_price_m2"
down_revision = "067_commune_synthese_extra_datasets"
branch_labels = None
depends_on = None


_CREATE_DVF_CORRECTED = """
CREATE MATERIALIZED VIEW v_commune_dvf AS
WITH mutation_level AS (
    SELECT
        code_commune,
        id_mutation,
        max(valeur_fonciere) AS valeur_fonciere,
        sum(surface_reelle_bati) AS surface_reelle_bati
    FROM mutations
    WHERE date_mutation >= (
              SELECT max(date_mutation) FROM mutations
          ) - INTERVAL '12 months'
      AND surface_reelle_bati > 0
      AND valeur_fonciere > 0
      AND code_type_local IN ('1', '2')
    GROUP BY code_commune, id_mutation
)
SELECT
    code_commune AS code_insee,
    ROUND((percentile_cont(0.5) WITHIN GROUP (
        ORDER BY valeur_fonciere / surface_reelle_bati
    ))::numeric, 0)::int AS prix_m2_median,
    count(*)::int AS nb_mutations_12m
FROM mutation_level
GROUP BY code_commune
WITH DATA
"""

_CREATE_DVF_OLD = """
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
"""


def _create_synthese(*, coalesce_counts: bool) -> str:
    nb_mutations = (
        "COALESCE(dvf.nb_mutations_12m, 0)::int AS nb_mutations_12m"
        if coalesce_counts
        else "dvf.nb_mutations_12m"
    )
    nb_equipements = (
        "COALESCE(eq.nb_equipements, 0)::int AS nb_equipements"
        if coalesce_counts
        else "eq.nb_equipements"
    )
    nb_scolaire = (
        "COALESCE(sc.nb_etablissements_scolaires, 0)::int "
        "AS nb_etablissements_scolaires"
        if coalesce_counts
        else "sc.nb_etablissements_scolaires"
    )
    nb_monuments = (
        "COALESCE(mon.nb_monuments, 0)::int AS nb_monuments"
        if coalesce_counts
        else "mon.nb_monuments"
    )
    nb_pm = (
        "COALESCE(fpm.nb_proprietaires_pm, 0)::int AS nb_proprietaires_pm"
        if coalesce_counts
        else "fpm.nb_proprietaires_pm"
    )

    return f"""
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
    {nb_mutations},
    dpe.dpe_passoires_pct,
    dpe.dpe_median,
    dpe.annee_construction_mediane,
    {nb_equipements},
    {nb_scolaire},
    {nb_monuments},
    {nb_pm},
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


_SYNTHESE_INDEXES = (
    "CREATE UNIQUE INDEX idx_commune_synthese_code_insee "
    "ON v_commune_synthese (code_insee)",
    "CREATE INDEX idx_commune_synthese_dept ON v_commune_synthese (code_departement)",
)


def _rebuild(*, dvf_sql: str, coalesce_counts: bool) -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_dvf CASCADE")
    op.execute(dvf_sql)
    op.execute(
        "CREATE UNIQUE INDEX idx_v_commune_dvf_code_insee ON v_commune_dvf (code_insee)"
    )
    op.execute(_create_synthese(coalesce_counts=coalesce_counts))
    for sql in _SYNTHESE_INDEXES:
        op.execute(sql)


def upgrade() -> None:
    _rebuild(dvf_sql=_CREATE_DVF_CORRECTED, coalesce_counts=True)


def downgrade() -> None:
    _rebuild(dvf_sql=_CREATE_DVF_OLD, coalesce_counts=False)
