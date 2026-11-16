"""Install the application DDL of the commune view chain on migrated bases.

Revision ID: 098_commune_views_application_ddl
Revises: 097_instance_scope

A base built by the migration chain alone kept the definitions of migrations
067/068/088 until a commune-source publication rebuilt the chain
(``recreate_commune_synthese``): unknown counters served as 0 (KI-D4),
equipements summed over every vintage, no rent objects. Decision of Ianis
(2026-09-22, plan 02-09): this migration applies the application chain.

Migrations never import app code (tests/test_migrations_never_import_app.py,
incident of 2026-08-25: migration 073 imported matview_ddl.CREATE_SQL and
broke every fresh chain when plan 57-09 changed that DDL). The statements
below are therefore a VERBATIM, frozen copy of
``app.commune.matview_ddl.commune_synthese_statements()`` at this revision
(SEUIL_ND_COMMUNE and the residential classes resolved to literals).
tests/test_commune_view_migration_snapshot.py fails as soon as the
application DDL moves away from this copy: every change of the DDL needs a
new migration that ships the new snapshot.

Operation: run with the API stopped or in a maintenance window. Both
directions first take the exclusive commune publication lock
(``pg_advisory_xact_lock(PUBLICATION_LOCK_ID)``, held to the end of the
migration transaction): no publication runs concurrently, and readers of the
synthesis wait for the lock (15 s) then answer 503 until it is released. The
chain is DROPped and CREATEd with data even when it already carries the
application DDL; on a populated base this lasts about as long as the view
rebuild of one commune-source publication (the DVF median dominates).

``downgrade`` restores the definitions and indexes found at upgrade time,
saved in ``commune_matview_definitions_098``. That table stays after the
upgrade (it is what makes the downgrade exact) and is therefore part of
every dump taken afterwards.

Check after a restore: ``python -m scripts.check_commune_views``. A column
added later to one of the five tables the synthesis reads with ``SELECT *``
(demographie, revenus, vacance, fiscalite_locale, indicateurs_loyers) shows
as drift without any change of meaning: expected, resolved by a
commune-source publication.
"""

from alembic import op
from sqlalchemy import text

revision = "098_commune_views_application_ddl"
down_revision = "097_instance_scope"
branch_labels = None
depends_on = None

_BACKUP = "commune_matview_definitions_098"

# app.commune.dependencies.PUBLICATION_LOCK_ID (0x434F523230), copied as a
# literal (migrations never import app code); kept equal by
# tests/test_commune_view_migration_snapshot.py.
PUBLICATION_LOCK_ID = 289093595696

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

# Frozen copy of commune_synthese_statements() (see module docstring).
STATEMENTS: tuple[str, ...] = (
    "DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE",
    "DROP MATERIALIZED VIEW IF EXISTS v_commune_dvf CASCADE",
    """
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
        """,
    "CREATE UNIQUE INDEX idx_v_commune_dvf_code_insee ON v_commune_dvf (code_insee)",
    "DROP MATERIALIZED VIEW IF EXISTS v_commune_dpe CASCADE",
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
        -- Documentary population, not dwellings: retain legacy unknown,
        -- expired and replaced diagnostics, regardless of geometry or regime.
        WHERE NOT is_tombstone
          AND source_state NOT IN (
              'absent_from_complete_scope', 'explicitly_deactivated'
          )
        GROUP BY code_insee
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
        FROM eligibilite_fixe
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


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(f"SELECT pg_advisory_xact_lock({PUBLICATION_LOCK_ID})")
    op.execute(
        f"CREATE TABLE {_BACKUP} ("
        "position int PRIMARY KEY, name text NOT NULL, definition text NOT NULL, "
        "indexes text[] NOT NULL)"
    )
    # Saved in dependency order: intermediates first, synthese last.
    bind.execute(
        text(f"""
            INSERT INTO {_BACKUP} (position, name, definition, indexes)
            SELECT names.position, m.matviewname, m.definition,
                   COALESCE(
                       (SELECT array_agg(i.indexdef ORDER BY i.indexname)
                        FROM pg_indexes i
                        WHERE i.schemaname = m.schemaname
                          AND i.tablename = m.matviewname),
                       '{{}}'::text[])
            FROM unnest(CAST(:names AS text[])) WITH ORDINALITY
                AS names(name, position)
            JOIN pg_matviews m
              ON m.matviewname = names.name AND m.schemaname = current_schema()
        """),
        {"names": list(VIEW_NAMES)},
    )
    for statement in STATEMENTS:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(f"SELECT pg_advisory_xact_lock({PUBLICATION_LOCK_ID})")
    saved = bind.execute(
        text(f"SELECT name, definition, indexes FROM {_BACKUP} ORDER BY position")
    ).fetchall()
    for name in reversed(VIEW_NAMES):
        bind.exec_driver_sql(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
    for name, definition, indexes in saved:
        body = definition.strip().rstrip(";")
        bind.exec_driver_sql(f"CREATE MATERIALIZED VIEW {name} AS {body} WITH DATA")
        for index in indexes:
            bind.exec_driver_sql(index)
    op.execute(f"DROP TABLE {_BACKUP}")
