"""Rebuild v_commune_dvf on mutations_grain (D-14), maison/appartement split.

Revision ID: 088_v_commune_dvf_mutation_grain
Revises: 087_dvf_mutations_grain
Create Date: 2026-08-25

Phase 57 (DVF au grain mutation et attribution residentielle), plan 09.
Mirrors app/commune/matview_ddl.py -- keep in sync (module docstring). The
SQL strings below are copied VERBATIM (same indentation) from the resolved
output of that module's INTERMEDIATE_MATVIEWS["v_commune_dvf"] and
CREATE_SQL, so a text diff against the module's live output is exact, not
approximate.

Why this migration exists: `v_commune_dvf` used to filter
`code_type_local IN ('1', '2')` INSIDE a CTE, BEFORE `GROUP BY code_commune,
id_mutation` -- a mixed mutation (residential + commercial lines) lost its
commercial rows there while keeping the FULL sale price in
`valeur_fonciere`, inflating `prix_m2` (the Pornichet bug, D-14's blocking
invariant). `mutations_grain` (migration 087) is already reconstructed and
qualified one row per mutation, so this matview now only ever filters
AFTER that grouping happened, once, in the ETL.

Multi-commune mutations remain available in history, but are excluded from
commune statistics. Their stored ``code_commune = min(code_commune)`` is a
deterministic technical representative, not evidence that the whole sale
belongs to that commune.

Migrations never import app code (087's DEPARTEMENTS list applies the same
discipline) -- SEUIL_ND_COMMUNE and the residential class list are
redefined locally as literals so this migration stays runnable unchanged
even if app.dvf.couverture's values are re-arbitrated later. Their value
here (5, the two `residentiel_*` classes) is a frozen snapshot of C-01/C-03
as arbitrated in 57-VERDICT-COUVERTURE.md on 2026-08-24 -- NOT a live
reference; if the verdict changes, a NEW migration ships the new snapshot.

DROP + CREATE, never REFRESH (matview_ddl.py module docstring): a
materialized view stays bound to its source tables by OID, so REFRESH
after an ETL rename-swap would read the stale `_old` table.
"""

from alembic import op

revision = "088_v_commune_dvf_mutation_grain"
down_revision = "087_dvf_mutations_grain"
branch_labels = None
depends_on = None


# -- New definition (D-14): reads mutations_grain, filters AFTER
# qualification. Verbatim copy of matview_ddl.INTERMEDIATE_MATVIEWS
# resolved for SEUIL_ND_COMMUNE=5 (C-01) and
# CLASSES_STATISTIQUES=('residentiel_maison', 'residentiel_appartement')
# (C-03).
_CREATE_DVF_MUTATION_GRAIN = """
        CREATE MATERIALIZED VIEW v_commune_dvf AS
        SELECT
            code_commune AS code_insee,
            CASE WHEN count(*) >= 5 THEN
                ROUND((percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY valeur_fonciere / surface_bati_totale
                ))::numeric, 0)::int
            END AS prix_m2_median,
            count(*)::int AS nb_mutations_12m,
            CASE WHEN count(*) FILTER (
                WHERE classe_mutation = 'residentiel_maison'
            ) >= 5 THEN
                ROUND((percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY valeur_fonciere / surface_bati_totale
                ) FILTER (
                    WHERE classe_mutation = 'residentiel_maison'
                ))::numeric, 0)::int
            END AS prix_m2_median_maison,
            count(*) FILTER (
                WHERE classe_mutation = 'residentiel_maison'
            )::int AS nb_mutations_12m_maison,
            CASE WHEN count(*) FILTER (
                WHERE classe_mutation = 'residentiel_appartement'
            ) >= 5 THEN
                ROUND((percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY valeur_fonciere / surface_bati_totale
                ) FILTER (
                    WHERE classe_mutation = 'residentiel_appartement'
                ))::numeric, 0)::int
            END AS prix_m2_median_appartement,
            count(*) FILTER (
                WHERE classe_mutation = 'residentiel_appartement'
            )::int AS nb_mutations_12m_appartement
        FROM mutations_grain
        WHERE date_mutation >= (
                  SELECT max(date_mutation) FROM mutations_grain
              ) - INTERVAL '12 months'
          AND classe_mutation IN ('residentiel_maison', 'residentiel_appartement')
          AND est_vente = true
          AND hors_stats_vefa = false
          -- ``code_commune`` is a deterministic technical representative
          -- (MIN during reconstruction), not evidence that a multi-commune
          -- mutation belongs to that commune. Keep those mutations in the
          -- history, but exclude them from commune statistics.
          AND nb_communes = 1
          AND surface_bati_totale > 0
          AND valeur_fonciere > 0
        GROUP BY code_commune
        WITH DATA
        """

# -- Pre-D-14 definition (reads `mutations`, filters BEFORE grouping in a
# CTE). Restored ONLY by downgrade(), for reversibility -- this
# DELIBERATELY reintroduces the anti-pattern D-14 fixes (a mixed mutation
# can lose its commercial lines while keeping its full sale price). Never
# use this SQL outside of downgrade().
_CREATE_DVF_OLD = """
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


def _create_synthese(*, dvf_breakdown: bool) -> str:
    """Build v_commune_synthese SQL, with or without the D-14 4-column split.

    Verbatim copy of matview_ddl.CREATE_SQL (dvf_breakdown=True branch).
    dvf_breakdown=False reproduces the pre-D-14 shape (2 dvf fields only),
    used exclusively by downgrade().
    """
    dvf_extra_select = (
        """
    dvf.prix_m2_median_maison,
    COALESCE(dvf.nb_mutations_12m_maison, 0)::int AS nb_mutations_12m_maison,
    dvf.prix_m2_median_appartement,
    COALESCE(dvf.nb_mutations_12m_appartement, 0)::int
        AS nb_mutations_12m_appartement,"""
        if dvf_breakdown
        else ""
    )
    dvf_extra_group_by = (
        "\n    dvf.prix_m2_median_maison, dvf.nb_mutations_12m_maison,"
        "\n    dvf.prix_m2_median_appartement, dvf.nb_mutations_12m_appartement,"
        if dvf_breakdown
        else ""
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
    COALESCE(dvf.nb_mutations_12m, 0)::int AS nb_mutations_12m,{dvf_extra_select}
    dpe.dpe_passoires_pct,
    dpe.dpe_median,
    dpe.annee_construction_mediane,
    COALESCE(eq.nb_equipements, 0)::int AS nb_equipements,
    COALESCE(sc.nb_etablissements_scolaires, 0)::int
        AS nb_etablissements_scolaires,
    COALESCE(mon.nb_monuments, 0)::int AS nb_monuments,
    COALESCE(fpm.nb_proprietaires_pm, 0)::int AS nb_proprietaires_pm,
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
    dvf.prix_m2_median, dvf.nb_mutations_12m,{dvf_extra_group_by}
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


def _rebuild(*, dvf_sql: str, dvf_breakdown: bool) -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_dvf CASCADE")
    op.execute(dvf_sql)
    op.execute(
        "CREATE UNIQUE INDEX idx_v_commune_dvf_code_insee ON v_commune_dvf (code_insee)"
    )
    op.execute(_create_synthese(dvf_breakdown=dvf_breakdown))
    for sql in _SYNTHESE_INDEXES:
        op.execute(sql)


def upgrade() -> None:
    """Rebuild v_commune_dvf on mutations_grain, filters applied after qualification."""
    _rebuild(dvf_sql=_CREATE_DVF_MUTATION_GRAIN, dvf_breakdown=True)


def downgrade() -> None:
    """Restore the pre-D-14 definition (reads `mutations`, filters before grouping).

    This deliberately reintroduces the anti-pattern D-14 fixes (see
    _CREATE_DVF_OLD docstring above) -- it exists ONLY so this migration is
    reversible, never as a recommended target state.
    """
    _rebuild(dvf_sql=_CREATE_DVF_OLD, dvf_breakdown=False)
