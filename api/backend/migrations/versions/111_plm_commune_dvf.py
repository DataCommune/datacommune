"""Commune row of Paris, Lyon and Marseille priced on its arrondissements.

Revision ID: 111_plm_commune_dvf
Revises: 110_logement_thrs_mandat
Create Date: 2026-10-31

Plan 04.7-09 (decision 4). DVF is published at the arrondissement for Paris
(75101-75120), Lyon (69381-69389) and Marseille (13201-13216) and never at the
commune: ``v_commune_dvf`` grouped ``mutations_grain.code_commune`` as read, so
``v_commune_synthese`` joined on ``code_commune`` gave 75056, 69123 and 13055
no price at all (profile, directory, comparison).

``v_commune_dvf`` now carries, besides the unchanged rows of every commune and
every arrondissement, one row per PLM commune computed on the sales of all its
arrondissements. ``mutations_grain`` has one row per mutation, so a sale on two
arrondissements counts once. A sale attached to several arrondissements
(``nb_communes > 1``) is kept only when every line of the mutation (read in
``mutations``) is in that same commune; one reaching another commune stays
excluded, as the rule ``nb_communes = 1`` does for every other commune.

The chain is recreated with the application DDL of this revision (frozen copy
below, ``tests/test_commune_view_migration_snapshot.py``) as migrations 098
and 106 did; migrations never import app code. The views get new OIDs: the
latest publication of each dataset naming one of them is re-anchored on the
new relations, with the helpers of migration 106 (WR-03 of the 04.4 review).
The data served is the published one, regrouped.

``v_commune_dpe`` follows the same rule: the PLM commune row aggregates the
documents coded at the commune and at its arrondissements, each once
(``numero_dpe`` is unique and carries one code); other rows are unchanged.

No table changes: ``downgrade`` recreates the chain of migration 106.
Operation: API stopped or maintenance window, both directions under the
commune publication lock (readers of the synthesis wait for it, then answer
503). Written without a database at hand: to be played on the PostGIS harness
(``tests/integration/test_plm_arrondissements.py``) before any other base.
"""

import importlib.util
from pathlib import Path

from alembic import op

revision: str = "111_plm_commune_dvf"
down_revision: str = "110_logement_thrs_mandat"
branch_labels: str | None = None
depends_on: str | None = None

# app.commune.dependencies.PUBLICATION_LOCK_ID, copied as a literal
# (migrations never import app code); kept equal by
# tests/test_commune_view_migration_snapshot.py.
PUBLICATION_LOCK_ID = 289093595696
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"

# The chain, intermediates first, the synthese last.
VIEW_NAMES: tuple[str, ...] = (
    "v_commune_dvf",
    "v_commune_dpe",
    "v_commune_equipements",
    "v_commune_scolaire",
    "v_commune_monuments",
    "v_commune_foncier_pm",
    "v_commune_connectivite",
    "v_commune_synthese",
)

# Frozen copy of commune_synthese_statements() at this revision.
STATEMENTS: tuple[str, ...] = (
    "DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE",
    "DROP MATERIALIZED VIEW IF EXISTS v_commune_dvf CASCADE",
    """
        CREATE MATERIALIZED VIEW v_commune_dvf AS
        WITH plm(code, commune_code) AS (VALUES ('75101', '75056'), ('75102', '75056'), ('75103', '75056'), ('75104', '75056'), ('75105', '75056'), ('75106', '75056'), ('75107', '75056'), ('75108', '75056'), ('75109', '75056'), ('75110', '75056'), ('75111', '75056'), ('75112', '75056'), ('75113', '75056'), ('75114', '75056'), ('75115', '75056'), ('75116', '75056'), ('75117', '75056'), ('75118', '75056'), ('75119', '75056'), ('75120', '75056'), ('69381', '69123'), ('69382', '69123'), ('69383', '69123'), ('69384', '69123'), ('69385', '69123'), ('69386', '69123'), ('69387', '69123'), ('69388', '69123'), ('69389', '69123'), ('13201', '13055'), ('13202', '13055'), ('13203', '13055'), ('13204', '13055'), ('13205', '13055'), ('13206', '13055'), ('13207', '13055'), ('13208', '13055'), ('13209', '13055'), ('13210', '13055'), ('13211', '13055'), ('13212', '13055'), ('13213', '13055'), ('13214', '13055'), ('13215', '13055'), ('13216', '13055'), ('75056', '75056'), ('69123', '69123'), ('13055', '13055'))
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
          -- PLM communes are the second select below, never a group of this one.
          AND (code_commune IS NULL OR code_commune NOT IN ('75056', '69123', '13055'))
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
        UNION ALL
        -- Paris, Lyon, Marseille: the commune row reads every sale of its
        -- arrondissements, one row of ``mutations_grain`` being one mutation, so
        -- a sale on two arrondissements counts once. The arrondissement rows
        -- above are unchanged. A sale attached to several arrondissements
        -- (nb_communes > 1) stays in only when all its lines are in this same
        -- commune; a sale reaching another commune is excluded as everywhere.
        SELECT
            plm.commune_code AS code_insee,
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
        FROM mutations_grain g
        JOIN plm ON plm.code = g.code_commune
        WHERE g.date_mutation >= (
                  SELECT max(date_mutation) FROM mutations_grain
              ) - INTERVAL '12 months'
          AND g.classe_mutation IN ('residentiel_maison', 'residentiel_appartement')
          AND g.est_vente = true
          AND g.hors_stats_vefa = false
          AND (
              g.nb_communes = 1
              OR (
                  g.nb_communes > 1
                  AND NOT EXISTS (
                      SELECT 1
                      FROM mutations m
                      LEFT JOIN plm other ON other.code = m.code_commune
                      WHERE m.code_departement = g.code_departement
                        AND m.id_mutation = g.id_mutation
                        AND m.code_commune IS NOT NULL
                        AND other.commune_code IS DISTINCT FROM plm.commune_code
                  )
              )
          )
          AND g.surface_bati_totale > 0
          AND g.valeur_fonciere > 0
        GROUP BY plm.commune_code
        WITH DATA
        """,
    "CREATE UNIQUE INDEX idx_v_commune_dvf_code_insee ON v_commune_dvf (code_insee)",
    "DROP MATERIALIZED VIEW IF EXISTS v_commune_dpe CASCADE",
    """
        CREATE MATERIALIZED VIEW v_commune_dpe AS
        WITH plm(code, commune_code) AS (VALUES ('75101', '75056'), ('75102', '75056'), ('75103', '75056'), ('75104', '75056'), ('75105', '75056'), ('75106', '75056'), ('75107', '75056'), ('75108', '75056'), ('75109', '75056'), ('75110', '75056'), ('75111', '75056'), ('75112', '75056'), ('75113', '75056'), ('75114', '75056'), ('75115', '75056'), ('75116', '75056'), ('75117', '75056'), ('75118', '75056'), ('75119', '75056'), ('75120', '75056'), ('69381', '69123'), ('69382', '69123'), ('69383', '69123'), ('69384', '69123'), ('69385', '69123'), ('69386', '69123'), ('69387', '69123'), ('69388', '69123'), ('69389', '69123'), ('13201', '13055'), ('13202', '13055'), ('13203', '13055'), ('13204', '13055'), ('13205', '13055'), ('13206', '13055'), ('13207', '13055'), ('13208', '13055'), ('13209', '13055'), ('13210', '13055'), ('13211', '13055'), ('13212', '13055'), ('13213', '13055'), ('13214', '13055'), ('13215', '13055'), ('13216', '13055'), ('75056', '75056'), ('69123', '69123'), ('13055', '13055'))
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
        -- Documentary population, not dwellings: retain legacy unknown,
        -- expired and replaced diagnostics, regardless of geometry or regime.
        WHERE NOT is_tombstone
          AND source_state NOT IN (
              'absent_from_complete_scope', 'explicitly_deactivated'
          )
          -- PLM communes are the second select below, never a group of this one.
          AND (code_insee IS NULL OR code_insee NOT IN ('75056', '69123', '13055'))
        GROUP BY code_insee
        UNION ALL
        SELECT
            plm.commune_code AS code_insee,
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
        JOIN plm ON plm.code = diagnostics_dpe.code_insee
        WHERE NOT is_tombstone
          AND source_state NOT IN (
              'absent_from_complete_scope', 'explicitly_deactivated'
          )
        GROUP BY plm.commune_code
        WITH DATA
        """,
    "CREATE UNIQUE INDEX idx_v_commune_dpe_code_insee ON v_commune_dpe (code_insee)",
    "DROP MATERIALIZED VIEW IF EXISTS v_commune_equipements CASCADE",
    """
        CREATE MATERIALIZED VIEW v_commune_equipements AS
        WITH latest AS (
            SELECT code_commune, MAX(annee) AS annee
            FROM equipements GROUP BY code_commune
        )
        SELECT
            e.code_commune AS code_insee,
            -- Source-row weights, not distinct physical establishments. An
            -- incomplete/negative weight population cannot attest a total.
            CASE WHEN count(*) = count(e.nb_equipements)
                AND MIN(e.nb_equipements) >= 0
                THEN SUM(e.nb_equipements)::int END AS nb_equipements,
            MAX(e.annee) AS annee_equipements
        FROM equipements e
        JOIN latest y ON y.code_commune = e.code_commune AND y.annee = e.annee
        GROUP BY e.code_commune
        WITH DATA
        """,
    "CREATE UNIQUE INDEX idx_v_commune_equipements_code_insee ON v_commune_equipements (code_insee)",
    "DROP MATERIALIZED VIEW IF EXISTS v_commune_scolaire CASCADE",
    """
        CREATE MATERIALIZED VIEW v_commune_scolaire AS
        SELECT
            code_insee,
            -- RNE references in the loaded sector map, not all schools in
            -- the commune. Missing identities are not evidence of zero.
            CASE WHEN count(*) = count(NULLIF(TRIM(code_rne), ''))
                THEN count(DISTINCT TRIM(code_rne))::int
            END AS nb_etablissements_scolaires
        FROM carte_scolaire
        GROUP BY code_insee
        WITH DATA
        """,
    "CREATE UNIQUE INDEX idx_v_commune_scolaire_code_insee ON v_commune_scolaire (code_insee)",
    "DROP MATERIALIZED VIEW IF EXISTS v_commune_monuments CASCADE",
    """
        CREATE MATERIALIZED VIEW v_commune_monuments AS
        SELECT
            code_insee,
            -- Merimee notice reference, not geometry or physical monuments.
            CASE WHEN count(*) = count(NULLIF(TRIM(reference), ''))
                THEN count(DISTINCT TRIM(reference))::int END AS nb_monuments
        FROM monuments_historiques
        GROUP BY code_insee
        WITH DATA
        """,
    "CREATE UNIQUE INDEX idx_v_commune_monuments_code_insee ON v_commune_monuments (code_insee)",
    "DROP MATERIALIZED VIEW IF EXISTS v_commune_foncier_pm CASCADE",
    """
        CREATE MATERIALIZED VIEW v_commune_foncier_pm AS
        WITH proprietaires AS (
            SELECT code_commune,
                CASE WHEN count(*) = count(NULLIF(TRIM(numero_siren), ''))
                    THEN count(DISTINCT TRIM(numero_siren))::int
                END AS nb_proprietaires_pm
            FROM foncier_pm
            GROUP BY code_commune
        ), parcelles_uniques AS (
            SELECT code_commune, code_parcelle,
                -- Repeated owners/rights describe the same parcel area.
                -- Conflicting or entirely missing areas remain unknown.
                CASE WHEN NULLIF(TRIM(code_parcelle), '') IS NOT NULL
                    AND count(*) = count(contenance_parcelle)
                    AND MIN(contenance_parcelle) >= 0
                    AND MIN(contenance_parcelle) = MAX(contenance_parcelle)
                    THEN MIN(contenance_parcelle)
                END AS contenance_parcelle
            FROM foncier_pm
            GROUP BY code_commune, code_parcelle
        )
        SELECT
            p.code_commune AS code_insee,
            p.nb_proprietaires_pm,
            -- Do not label a partial sum as the commune's total parcel area.
            CASE WHEN count(*) = count(u.contenance_parcelle) THEN
                ROUND(SUM(u.contenance_parcelle)::numeric / 10000, 1)
            END AS surface_pm_ha
        FROM proprietaires p
        JOIN parcelles_uniques u ON u.code_commune = p.code_commune
        GROUP BY p.code_commune, p.nb_proprietaires_pm
        WITH DATA
        """,
    "CREATE UNIQUE INDEX idx_v_commune_foncier_pm_code_insee ON v_commune_foncier_pm (code_insee)",
    "DROP MATERIALIZED VIEW IF EXISTS v_commune_connectivite CASCADE",
    """
        CREATE MATERIALIZED VIEW v_commune_connectivite AS
        -- One row per building and its offers (plan 04.1-16): the offer rows
        -- of the source are read back by unnest, the definition is unchanged.
        WITH offres AS (
            SELECT e.code_insee, e.id_imb, o.technologie
            FROM eligibilite_fixe e
            CROSS JOIN LATERAL unnest(e.offres) AS u(code)
            JOIN eligibilite_offres o ON o.code = u.code
        )
        SELECT
            code_insee,
            -- A percentage needs an identified source population and a known
            -- technology for every loaded record; missing collection is NULL.
            CASE WHEN count(*) = count(NULLIF(TRIM(id_imb), ''))
                AND count(*) = count(NULLIF(TRIM(technologie), '')) THEN ROUND(
                count(DISTINCT TRIM(id_imb)) FILTER (
                    WHERE technologie = 'FO') * 100.0
                / NULLIF(count(DISTINCT TRIM(id_imb)), 0),
                1
            ) END AS fibre_pct
        FROM offres
        GROUP BY code_insee
        WITH DATA
        """,
    "CREATE UNIQUE INDEX idx_v_commune_connectivite_code_insee ON v_commune_connectivite (code_insee)",
    """
CREATE MATERIALIZED VIEW v_commune_synthese AS
-- Annual tables are unique on commune/year (rent: commune/type/year).
-- Select the latest available vintage at that grain, never another commune's
-- newest year. Do not fill missing fields from an older row.
WITH demographie_latest AS (
    SELECT DISTINCT ON (code_insee) * FROM demographie
    ORDER BY code_insee, annee DESC NULLS LAST
), revenus_latest AS (
    SELECT DISTINCT ON (code_insee) * FROM revenus
    ORDER BY code_insee, annee DESC NULLS LAST
), vacance_latest AS (
    SELECT DISTINCT ON (code_commune) * FROM vacance
    ORDER BY code_commune, annee DESC NULLS LAST
), fiscalite_latest AS (
    SELECT DISTINCT ON (code_insee) * FROM fiscalite_locale
    ORDER BY code_insee, annee DESC NULLS LAST
), loyers_latest AS (
    SELECT DISTINCT ON (code_insee, type_habitat) * FROM indicateurs_loyers
    ORDER BY code_insee, type_habitat, annee DESC NULLS LAST
)
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
    CASE WHEN v.nb_logements_prives > 0 AND v.nb_logements_vacants >= 0
        THEN v.taux_vacance END AS taux_vacance,
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
    -- A single year would mislabel one rent when the types have different
    -- vintages. Each nested prediction always carries its own source year.
    CASE WHEN count(DISTINCT l.annee) FILTER (
        WHERE l.type_habitat IN ('appartement', 'maison')) = 1
        THEN MAX(l.annee) FILTER (
            WHERE l.type_habitat IN ('appartement', 'maison'))
    END AS annee_loyers,
    (jsonb_agg(DISTINCT to_jsonb(l)) FILTER (
        WHERE l.type_habitat = 'appartement')) -> 0 AS loyers_appartement,
    (jsonb_agg(DISTINCT to_jsonb(l)) FILTER (
        WHERE l.type_habitat = 'maison')) -> 0 AS loyers_maison,
    dvf.prix_m2_median,
    dvf.nb_mutations_12m::int AS nb_mutations_12m,
    dvf.prix_m2_median_maison,
    dvf.nb_mutations_12m_maison::int AS nb_mutations_12m_maison,
    dvf.prix_m2_median_appartement,
    dvf.nb_mutations_12m_appartement::int
        AS nb_mutations_12m_appartement,
    dpe.dpe_passoires_pct,
    dpe.dpe_median,
    dpe.annee_construction_mediane,
    eq.nb_equipements::int AS nb_equipements,
    eq.annee_equipements,
    sc.nb_etablissements_scolaires::int
        AS nb_etablissements_scolaires,
    mon.nb_monuments::int AS nb_monuments,
    fpm.nb_proprietaires_pm::int AS nb_proprietaires_pm,
    fpm.surface_pm_ha,
    con.fibre_pct
FROM demographie_latest d
LEFT JOIN revenus_latest r ON r.code_insee = d.code_insee
LEFT JOIN zonage_abc z ON z.code_insee = d.code_insee
LEFT JOIN vacance_latest v ON v.code_commune = d.code_insee
LEFT JOIN fiscalite_latest f ON f.code_insee = d.code_insee
LEFT JOIN loyers_latest l ON l.code_insee = d.code_insee
LEFT JOIN v_commune_dvf dvf ON dvf.code_insee = d.code_insee
LEFT JOIN v_commune_dpe dpe ON dpe.code_insee = d.code_insee
LEFT JOIN v_commune_equipements eq ON eq.code_insee = d.code_insee
LEFT JOIN v_commune_scolaire sc ON sc.code_insee = d.code_insee
LEFT JOIN v_commune_monuments mon ON mon.code_insee = d.code_insee
LEFT JOIN v_commune_foncier_pm fpm ON fpm.code_insee = d.code_insee
LEFT JOIN v_commune_connectivite con ON con.code_insee = d.code_insee
GROUP BY
    d.code_insee, d.nom_commune, d.code_departement, d.code_region,
    d.population, d.nb_menages, d.nb_logements, d.nb_logements_vacants,
    d.densite, d.superficie, d.annee,
    r.nom_departement, r.nom_region,
    r.revenu_median, r.taux_pauvrete, r.indice_gini, r.part_imposee, r.annee,
    z.zone, z.reclassement,
    v.nom_departement, v.nom_region,
    v.taux_vacance, v.nb_vacants_plus_2ans, v.annee,
    v.nb_logements_prives, v.nb_logements_vacants,
    f.nom_departement, f.nom_region,
    f.taux_tfpb, f.taux_th_rs, f.taux_teom, f.nom_epci, f.annee,
    dvf.prix_m2_median, dvf.nb_mutations_12m,
    dvf.prix_m2_median_maison, dvf.nb_mutations_12m_maison,
    dvf.prix_m2_median_appartement, dvf.nb_mutations_12m_appartement,
    dpe.dpe_passoires_pct, dpe.dpe_median, dpe.annee_construction_mediane,
    eq.nb_equipements, eq.annee_equipements,
    sc.nb_etablissements_scolaires, mon.nb_monuments,
    fpm.nb_proprietaires_pm, fpm.surface_pm_ha, con.fibre_pct
WITH DATA
""",
    "CREATE UNIQUE INDEX idx_commune_synthese_code_insee ON v_commune_synthese (code_insee)",
    "CREATE INDEX idx_commune_synthese_dept ON v_commune_synthese (code_departement)",
)


def _migration_106():
    """Migration 106 read from its file: its chain and its re-anchoring."""
    path = Path(__file__).with_name("106_eligibilite_fixe_par_immeuble.py")
    spec = importlib.util.spec_from_file_location("migration_106", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recreate(statements: tuple[str, ...]) -> None:
    """Recreate the chain under the publication lock, then re-anchor the journal."""
    bind = op.get_bind()
    helpers = _migration_106()
    op.execute(LOCK_TIMEOUT)
    bind.exec_driver_sql(f"SELECT pg_advisory_xact_lock({PUBLICATION_LOCK_ID})")
    anciens = helpers.oids_avant(bind, list(VIEW_NAMES))
    for statement in statements:
        bind.exec_driver_sql(statement)
    helpers.reancrer(bind, anciens)


def upgrade() -> None:
    _recreate(STATEMENTS)


def downgrade() -> None:
    _recreate(_migration_106().STATEMENTS)
