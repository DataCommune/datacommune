"""Canonical DDL for the v_commune_synthese materialized view.

This mirrors the definition installed by migration
``068_fix_commune_dvf_price_m2``. It lives here -- not only in
the migration -- because the daily refresh job must DROP + CREATE the
matview rather than REFRESH it.

Why DROP + CREATE and not REFRESH: the ETL swaps tables by renaming
(``revenus`` -> ``revenus_old``, ``revenus_staging`` -> ``revenus``). A
materialized view stays bound to its source tables by OID, so after the
rename it follows the renamed ``_old`` table. ``REFRESH`` (even
CONCURRENTLY) then recomputes from the stale ``_old`` table -- only a
DROP + CREATE re-binds the view onto the freshly swapped tables.

v_commune_synthese LEFT JOINs a set of per-dataset intermediate matviews
(``v_commune_dvf``, ``v_commune_dpe``, ...), each aggregated to commune
level. They are kept separate so each dataset's heavy aggregation (DVF
price median, DPE distribution) is isolated and independently rebindable
after its own source-table ETL swap. The whole chain is rebuilt together
by ``recreate_commune_synthese`` (intermediates first, then the synthese).

``v_commune_dvf`` (D-14, Phase 57) reads ``mutations_grain``, not
``mutations``: ``mutations_grain`` is already reconstructed and qualified,
one row per mutation (D-01), so this matview's filters (residential class,
real sale, non-VEFA, single commune) apply to whole mutations, never to
individual detail lines. This is the blocking invariant of Phase 57 -- a filter applied
BEFORE grouping can strip a mutation's commercial lines while keeping its
full sale price, inflating ``prix_m2`` (the Pornichet bug); the grouping
already happened once, in the ETL, so this matview only ever filters
AFTER, on already-qualified rows.

``v_departement_synthese`` (plan 04.7-10) is rebuilt with the chain but
reads no view of it: one row per departement, over the communes and
arrondissements of the referential, with the sale filter, the documentary
population and the population row of the commune pages (shared fragments
below: one rule, two aggregations).

``v_commune_dvf_annuel`` (migration 115) is rebuilt with the chain too and
reads no view of it: the sales ``v_commune_dvf`` retains, per closed calendar
year, for the price trend of the commune page.

Keep this in sync with the most recent matview migration: migration 115
(after 098, 106, 111 and 112) freezes a copy of commune_synthese_statements() and
tests/test_commune_view_migration_snapshot.py fails on any divergence -- a
change of this DDL needs a new migration shipping the new snapshot.
"""

import re
from typing import Any

import psycopg

from app.common.geo import ARRONDISSEMENT_TO_COMMUNE, COMMUNE_TO_ARRONDISSEMENTS
from app.dvf.couverture import (
    ANNEES_TENDANCE,
    CLASSES_STATISTIQUES,
    SEUIL_ND_COMMUNE,
)

# Closed vocabulary from app.dvf.couverture, interpolated as a static SQL
# literal list -- CLASSES_STATISTIQUES is a Python module constant, never
# user input (T-57-32 covers the same posture for SEUIL_ND_COMMUNE below).
_CLASSES_STATISTIQUES_SQL = ", ".join(f"'{c}'" for c in CLASSES_STATISTIQUES)

# Paris, Lyon and Marseille: DVF is published at the arrondissement only, so the
# commune row of ``v_commune_dvf`` is built on the whole set (plan 04.7-09, D4).
# A code stored at the commune itself counts as one of its arrondissements.
# Static codes of ``app.common.geo``, never request input.
_PLM_CODES_SQL = ", ".join(
    f"('{code}', '{commune}')"
    for code, commune in (
        *ARRONDISSEMENT_TO_COMMUNE.items(),
        *((commune, commune) for commune in COMMUNE_TO_ARRONDISSEMENTS),
    )
)
_PLM_COMMUNES_SQL = ", ".join(f"'{commune}'" for commune in COMMUNE_TO_ARRONDISSEMENTS)


def _dvf_retained(
    alias: str,
    *,
    one_commune: str,
    plm_exclusion: str = "",
    closed_years: bool = False,
) -> str:
    """WHERE body of the sales a DVF statistic retains (T-01 of plan 04.7-10).

    The window (12 months up to the latest sale of ``mutations_grain``), the
    residential classes, a real sale outside VEFA, a positive surface and
    price: one rule for the commune rows, the PLM rows and the departements.
    *one_commune* is how a group keeps a sale of one commune only;
    *plm_exclusion* an optional clause after the classes. The clause order is
    the one of migration 111, so the deparsed views do not drift.
    *closed_years* swaps the window alone for the closed calendar years of the
    CTE ``annees`` (``v_commune_dvf_annuel``): the same sales, year by year.
    """
    p = f"{alias}." if alias else ""
    window = (
        f"""{p}date_mutation >= (SELECT debut FROM annees)
          AND {p}date_mutation < (SELECT fin FROM annees)"""
        if closed_years
        else f"""{p}date_mutation >= (
                  SELECT max(date_mutation) FROM mutations_grain
              ) - INTERVAL '12 months'"""
    )
    return f"""{window}
          AND {p}classe_mutation IN ({_CLASSES_STATISTIQUES_SQL}){plm_exclusion}
          AND {p}est_vente = true
          AND {p}hors_stats_vefa = false
          AND {one_commune}
          AND {p}surface_bati_totale > 0
          AND {p}valeur_fonciere > 0"""


# Paris, Lyon, Marseille: a sale (one row ``g`` of ``mutations_grain``, the
# ``plm`` row of its code) is one of its commune's when it has one commune, or
# when every line of the mutation (read in ``mutations``) is in that same
# commune; a sale reaching another commune is excluded as everywhere.
_PLM_SAME_COMMUNE = """(
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
          )"""

# The sales of a commune row (ordinary commune or arrondissement: its own code,
# one commune) and of a PLM commune row (its arrondissements, ``g`` and ``plm``).
_COMMUNE_PLM_EXCLUSION = (
    "\n          AND (code_commune IS NULL OR code_commune NOT IN "
    f"({_PLM_COMMUNES_SQL}))"
)
_COMMUNE_SALES = _dvf_retained(
    "", one_commune="nb_communes = 1", plm_exclusion=_COMMUNE_PLM_EXCLUSION
)
_PLM_SALES = _dvf_retained("g", one_commune=_PLM_SAME_COMMUNE)

# The medians and counts of one group of sales, shared by the two selects of
# ``v_commune_dvf`` and by ``v_departement_synthese``: the rule of a commune is
# the rule of a PLM commune and of a departement.
_DVF_AGGREGATES = f"""
            CASE WHEN count(*) >= {SEUIL_ND_COMMUNE} THEN
                ROUND((percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY valeur_fonciere / surface_bati_totale
                ))::numeric, 0)::int
            END AS prix_m2_median,
            count(*)::int AS nb_mutations_12m,
            CASE WHEN count(*) FILTER (
                WHERE classe_mutation = 'residentiel_maison'
            ) >= {SEUIL_ND_COMMUNE} THEN
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
            ) >= {SEUIL_ND_COMMUNE} THEN
                ROUND((percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY valeur_fonciere / surface_bati_totale
                ) FILTER (
                    WHERE classe_mutation = 'residentiel_appartement'
                ))::numeric, 0)::int
            END AS prix_m2_median_appartement,
            count(*) FILTER (
                WHERE classe_mutation = 'residentiel_appartement'
            )::int AS nb_mutations_12m_appartement"""

# Documentary population of the DPE figures, not dwellings: retain legacy
# unknown, expired and replaced diagnostics, regardless of geometry or regime.
# Shared by ``v_commune_dpe`` and ``v_departement_synthese``.
_DPE_POPULATION = """NOT is_tombstone
          AND source_state NOT IN (
              'absent_from_complete_scope', 'explicitly_deactivated'
          )"""

# Share of F and G among the known classes, in points (one decimal).
_DPE_PART_FG = """ROUND(
                count(*) FILTER (WHERE etiquette_dpe IN ('F', 'G')) * 100.0
                / NULLIF(count(*) FILTER (
                    WHERE etiquette_dpe IN ('A','B','C','D','E','F','G')
                  ), 0),
                1
            )"""

# The aggregates of one group of diagnostics, shared by the two selects of
# ``v_commune_dpe``.
_DPE_AGGREGATES = f"""
            {_DPE_PART_FG} AS dpe_passoires_pct,
            (ARRAY['A','B','C','D','E','F','G'])[
                (percentile_disc(0.5) WITHIN GROUP (
                    ORDER BY position(etiquette_dpe IN 'ABCDEFG')
                ) FILTER (
                    WHERE etiquette_dpe IN ('A','B','C','D','E','F','G')
                ))
            ] AS dpe_median,
            (percentile_disc(0.5) WITHIN GROUP (
                ORDER BY annee_construction
            ))::int AS annee_construction_mediane"""

DROP_SQL = "DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE"

# Per-dataset intermediate matviews, each one row per commune keyed on
# ``code_insee``. v_commune_synthese LEFT JOINs them. Order is irrelevant
# (none depends on another); each is rebuilt + re-indexed independently.
INTERMEDIATE_MATVIEWS: tuple[tuple[str, str], ...] = (
    (
        "v_commune_dvf",
        f"""
        CREATE MATERIALIZED VIEW v_commune_dvf AS
        WITH plm(code, commune_code) AS (VALUES {_PLM_CODES_SQL})
        SELECT
            code_commune AS code_insee,{_DVF_AGGREGATES}
        FROM mutations_grain
        -- PLM communes are the second select below, never a group of this one.
        -- ``code_commune`` is a deterministic technical representative (MIN
        -- during reconstruction), not evidence that a multi-commune mutation
        -- belongs to that commune: keep those mutations in the history, but
        -- exclude them from commune statistics (nb_communes = 1).
        WHERE {_COMMUNE_SALES}
        GROUP BY code_commune
        UNION ALL
        -- Paris, Lyon, Marseille: the commune row reads every sale of its
        -- arrondissements, one row of ``mutations_grain`` being one mutation, so
        -- a sale on two arrondissements counts once. The arrondissement rows
        -- above are unchanged.
        SELECT
            plm.commune_code AS code_insee,{_DVF_AGGREGATES}
        FROM mutations_grain g
        JOIN plm ON plm.code = g.code_commune
        WHERE {_PLM_SALES}
        GROUP BY plm.commune_code
        WITH DATA
        """,
    ),
    (
        "v_commune_dpe",
        # Aggregates over diagnostics_dpe, intentionally independent from the
        # row-projection column list in app/dpe/columns.py (DPE_SELECT_COLUMNS).
        # Paris, Lyon, Marseille follow the rule of ``v_commune_dvf`` (plan
        # 04.7-09, D4): the commune row reads the documents coded at the commune
        # and at its arrondissements, each document once (``numero_dpe`` is
        # unique, one code per row); the other rows are unchanged.
        f"""
        CREATE MATERIALIZED VIEW v_commune_dpe AS
        WITH plm(code, commune_code) AS (VALUES {_PLM_CODES_SQL})
        SELECT
            code_insee,{_DPE_AGGREGATES}
        FROM diagnostics_dpe
        WHERE {_DPE_POPULATION}
          -- PLM communes are the second select below, never a group of this one.
          AND (code_insee IS NULL OR code_insee NOT IN ({_PLM_COMMUNES_SQL}))
        GROUP BY code_insee
        UNION ALL
        SELECT
            plm.commune_code AS code_insee,{_DPE_AGGREGATES}
        FROM diagnostics_dpe
        JOIN plm ON plm.code = diagnostics_dpe.code_insee
        WHERE {_DPE_POPULATION}
        GROUP BY plm.commune_code
        WITH DATA
        """,
    ),
    (
        "v_commune_equipements",
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
    ),
    (
        "v_commune_scolaire",
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
    ),
    (
        "v_commune_monuments",
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
    ),
    (
        "v_commune_foncier_pm",
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
    ),
    (
        "v_commune_connectivite",
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
    ),
)

# The census row of a commune page: its latest vintage. Shared by the synthese
# and by the population of ``v_departement_synthese``.
_DEMOGRAPHIE_LATEST = """SELECT DISTINCT ON (code_insee) * FROM demographie
    ORDER BY code_insee, annee DESC NULLS LAST"""

CREATE_SQL = f"""
CREATE MATERIALIZED VIEW v_commune_synthese AS
-- Annual tables are unique on commune/year (rent: commune/type/year).
-- Select the latest available vintage at that grain, never another commune's
-- newest year. Do not fill missing fields from an older row.
WITH demographie_latest AS (
    {_DEMOGRAPHIE_LATEST}
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
"""

INDEX_SQLS = (
    "CREATE UNIQUE INDEX idx_commune_synthese_code_insee "
    "ON v_commune_synthese (code_insee)",
    "CREATE INDEX idx_commune_synthese_dept ON v_commune_synthese (code_departement)",
)

# One row per departement having at least one retained sale, document or
# census row (plan 04.7-10, decision 1). Narrow on purpose (threat T-09): the
# identity, the communes and the principal ones are read in the referential at
# request time. Every block is the union of the sets of the commune pages of
# the departement (shared fragments above), so its counts are the sums of
# theirs: the PLM commune row counted, its arrondissement rows never added.
DEPARTEMENT_VIEW = "v_departement_synthese"
# A sale of an ordinary commune or arrondissement: one commune; of a code of
# Paris, Lyon or Marseille: the rule of its PLM commune page.
_DEPARTEMENT_SALES = _dvf_retained(
    "g",
    one_commune=(
        "CASE WHEN plm.code IS NULL THEN g.nb_communes = 1\n"
        f"          ELSE {_PLM_SAME_COMMUNE} END"
    ),
)
DEPARTEMENT_CREATE_SQL = f"""
CREATE MATERIALIZED VIEW v_departement_synthese AS
WITH plm(code, commune_code) AS (VALUES {_PLM_CODES_SQL}),
-- The departement of a code is the one of its commune in geo_communes. An
-- arrondissement of Paris, Lyon or Marseille takes the one of its commune
-- through the fixed list ``plm``, the list the commune pages read, so a sale
-- they count is counted here even when geo_arrondissements is not loaded for
-- the departement. A code absent from both (orphan) belongs to no departement.
referentiel(code, departement) AS (
    SELECT code, departement_code FROM geo_communes
    UNION
    SELECT plm.code, c.departement_code
    FROM plm JOIN geo_communes c ON c.code = plm.commune_code
), ventes AS (
    -- A sale of an ordinary commune counts when its page retains it (one
    -- commune), a sale of Paris, Lyon or Marseille by the rule of its commune
    -- page; one row of mutations_grain is one mutation, hence counted once.
    SELECT
        r.departement,
        ((SELECT max(date_mutation) FROM mutations_grain)
            - INTERVAL '12 months')::date AS fenetre_debut,
        (SELECT max(date_mutation) FROM mutations_grain)
            AS fenetre_fin,{_DVF_AGGREGATES}
    FROM mutations_grain g
    JOIN referentiel r ON r.code = g.code_commune
    LEFT JOIN plm ON plm.code = g.code_commune
    WHERE {_DEPARTEMENT_SALES}
    GROUP BY r.departement
), documents AS (
    SELECT
        r.departement,
        count(*) FILTER (WHERE etiquette_dpe = 'A')::int AS dpe_a,
        count(*) FILTER (WHERE etiquette_dpe = 'B')::int AS dpe_b,
        count(*) FILTER (WHERE etiquette_dpe = 'C')::int AS dpe_c,
        count(*) FILTER (WHERE etiquette_dpe = 'D')::int AS dpe_d,
        count(*) FILTER (WHERE etiquette_dpe = 'E')::int AS dpe_e,
        count(*) FILTER (WHERE etiquette_dpe = 'F')::int AS dpe_f,
        count(*) FILTER (WHERE etiquette_dpe = 'G')::int AS dpe_g,
        count(*) FILTER (
            WHERE etiquette_dpe IS NULL
               OR etiquette_dpe NOT IN ('A','B','C','D','E','F','G')
        )::int AS dpe_inconnue,
        count(*)::int AS dpe_total,
        count(*) FILTER (
            WHERE etiquette_dpe IN ('A','B','C','D','E','F','G')
        )::int AS dpe_classes_connues,
        {_DPE_PART_FG} AS dpe_part_fg_pct,
        min(date_etablissement_dpe) AS dpe_date_min,
        max(date_etablissement_dpe) AS dpe_date_max
    FROM diagnostics_dpe d
    JOIN referentiel r ON r.code = d.code_insee
    WHERE {_DPE_POPULATION}
    GROUP BY r.departement
), habitants AS (
    -- The communes only: Paris once (75056), never its arrondissements. The
    -- vintage is the commune's own; a single one for the whole sum, or none.
    SELECT
        c.departement_code AS departement,
        sum(d.population)::bigint AS population,
        count(*)::int AS population_communes,
        CASE WHEN count(DISTINCT d.annee) = 1 THEN max(d.annee) END
            AS annee_population
    FROM ({_DEMOGRAPHIE_LATEST}) d
    JOIN geo_communes c ON c.code = d.code_insee
    WHERE d.population IS NOT NULL
    GROUP BY c.departement_code
)
SELECT
    COALESCE(v.departement, doc.departement, h.departement) AS code_departement,
    v.fenetre_debut,
    v.fenetre_fin,
    v.prix_m2_median_maison,
    v.nb_mutations_12m_maison,
    v.prix_m2_median_appartement,
    v.nb_mutations_12m_appartement,
    doc.dpe_a, doc.dpe_b, doc.dpe_c, doc.dpe_d, doc.dpe_e, doc.dpe_f, doc.dpe_g,
    doc.dpe_inconnue,
    doc.dpe_total,
    doc.dpe_classes_connues,
    doc.dpe_part_fg_pct,
    doc.dpe_date_min,
    doc.dpe_date_max,
    h.population,
    h.population_communes,
    h.annee_population
FROM ventes v
FULL JOIN documents doc ON doc.departement = v.departement
FULL JOIN habitants h ON h.departement = COALESCE(v.departement, doc.departement)
WITH DATA
"""


# The yearly series of the price trend (decision of Ianis, 2026-11-11; measure in
# the internal note 2026-11-11-tendance-prix-mesure). One row per code
# (commune, arrondissement, PLM commune), class and closed calendar year: the
# median per square metre and the count of the sales ``v_commune_dvf`` would
# retain that year. Rebuilt with the chain, read by no view of it.
#
# A year is closed when the base holds it whole: the year of the latest sale
# when that sale is a 31 December, the one before otherwise, so a half-year
# delivery never enters the series. The window is the ``ANNEES_TENDANCE`` closed
# years up to that one. Under ``SEUIL_ND_COMMUNE`` sales the median is null and
# the count stays the real one, as in ``v_commune_dvf``.
ANNUEL_VIEW = "v_commune_dvf_annuel"
_ANNUEL_AGGREGATES = f"""
    date_part('year', date_mutation)::int AS annee,
    CASE WHEN count(*) >= {SEUIL_ND_COMMUNE} THEN
        ROUND((percentile_cont(0.5) WITHIN GROUP (
            ORDER BY valeur_fonciere / surface_bati_totale
        ))::numeric, 0)::int
    END AS prix_m2_median,
    count(*)::int AS nb_ventes"""
_ANNUEL_COMMUNE_SALES = _dvf_retained(
    "",
    one_commune="nb_communes = 1",
    plm_exclusion=_COMMUNE_PLM_EXCLUSION,
    closed_years=True,
)
_ANNUEL_PLM_SALES = _dvf_retained("g", one_commune=_PLM_SAME_COMMUNE, closed_years=True)
ANNUEL_CREATE_SQL = f"""
CREATE MATERIALIZED VIEW v_commune_dvf_annuel AS
WITH plm(code, commune_code) AS (VALUES {_PLM_CODES_SQL}),
annees(debut, fin) AS (
    SELECT make_date(derniere.annee - {ANNEES_TENDANCE - 1}, 1, 1),
           make_date(derniere.annee + 1, 1, 1)
    FROM (
        SELECT date_part('year', max(date_mutation) + 1)::int - 1 AS annee
        FROM mutations_grain
    ) derniere
)
SELECT
    code_commune AS code_insee,
    classe_mutation,{_ANNUEL_AGGREGATES}
FROM mutations_grain
-- PLM communes are the second select below, never a group of this one.
WHERE {_ANNUEL_COMMUNE_SALES}
GROUP BY code_commune, classe_mutation, date_part('year', date_mutation)
UNION ALL
-- Paris, Lyon, Marseille: the rule of the commune row of ``v_commune_dvf``.
SELECT
    plm.commune_code AS code_insee,
    g.classe_mutation,{_ANNUEL_AGGREGATES}
FROM mutations_grain g
JOIN plm ON plm.code = g.code_commune
WHERE {_ANNUEL_PLM_SALES}
GROUP BY plm.commune_code, g.classe_mutation, date_part('year', date_mutation)
WITH DATA
"""


def annuel_statements() -> list[str]:
    """DROP, CREATE and unique index of ``v_commune_dvf_annuel``."""
    return [
        f"DROP MATERIALIZED VIEW IF EXISTS {ANNUEL_VIEW}",
        ANNUEL_CREATE_SQL,
        "CREATE UNIQUE INDEX idx_commune_dvf_annuel_code_classe_annee "
        f"ON {ANNUEL_VIEW} (code_insee, classe_mutation, annee)",
    ]


def departement_statements() -> list[str]:
    """DROP, CREATE and unique index of ``v_departement_synthese``."""
    return [
        f"DROP MATERIALIZED VIEW IF EXISTS {DEPARTEMENT_VIEW}",
        DEPARTEMENT_CREATE_SQL,
        "CREATE UNIQUE INDEX idx_departement_synthese_code_departement "
        f"ON {DEPARTEMENT_VIEW} (code_departement)",
    ]


# The whole chain, intermediates first, then the synthese, then the
# departements and the yearly series (which read no view of the chain).
COMMUNE_VIEW_NAMES: tuple[str, ...] = (
    *(name for name, _ in INTERMEDIATE_MATVIEWS),
    "v_commune_synthese",
    DEPARTEMENT_VIEW,
    ANNUEL_VIEW,
)


def commune_synthese_statements() -> list[str]:
    """Ordered DDL that DROPs and CREATEs the chain (runtime and migrations).

    The single definition of the chain: ``recreate_commune_synthese`` runs it
    after each commune-source publication (and the daily rebuild), migrations
    098, 106, 111, 112 and 115 freeze it for migrated bases.
    """
    statements = [DROP_SQL]
    for name, create_sql in INTERMEDIATE_MATVIEWS:
        statements += [
            f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE",
            create_sql,
            f"CREATE UNIQUE INDEX idx_{name}_code_insee ON {name} (code_insee)",
        ]
    return [
        *statements,
        CREATE_SQL,
        *INDEX_SQLS,
        *departement_statements(),
        *annuel_statements(),
    ]


def recreate_commune_synthese(conn: "psycopg.Connection[Any]") -> None:
    """DROP + CREATE the commune matview chain to re-bind it onto current tables.

    Rebuilds the per-dataset intermediate matviews first, then the
    synthese that LEFT JOINs them. DDL is transactional in PostgreSQL, so
    when run inside a transaction readers keep seeing the old views until
    COMMIT -- there is no "relation does not exist" window. The caller owns
    the commit.
    """
    for sql in commune_synthese_statements():
        conn.execute(sql)


_MATVIEW_HEAD = re.compile(r"CREATE MATERIALIZED VIEW (\w+) AS")
_WITH_DATA_TAIL = re.compile(r"\s*WITH DATA\s*$")


def _as_temp_view(create_sql: str) -> str:
    """The application DDL of one matview as a session-local plain view."""
    if not _MATVIEW_HEAD.search(create_sql) or not _WITH_DATA_TAIL.search(create_sql):
        raise ValueError("not a CREATE MATERIALIZED VIEW ... WITH DATA statement")
    body = _WITH_DATA_TAIL.sub("", create_sql)
    return _MATVIEW_HEAD.sub(r"CREATE TEMP VIEW \1 AS", body, count=1)


def _deparsed(conn: "psycopg.Connection[Any]", alias: str, body: str) -> str:
    conn.execute(f"CREATE TEMP VIEW {alias} AS {body.strip().rstrip(';')}")
    return conn.execute(
        "SELECT pg_get_viewdef(%s::regclass)", (f"pg_temp.{alias}",)
    ).fetchone()[0]


def commune_view_drift(conn: "psycopg.Connection[Any]") -> list[str]:
    """Views of the chain whose installed definition is not the application DDL.

    Read-only check for a base built by migrations or restored from a dump:
    both sides are deparsed through session-local temporary views, in a
    transaction that is always rolled back -- nothing is created, dropped or
    refreshed. A definition restored from a dump is deparsed text re-parsed
    (``ARRAY[...]`` casts change form), so both sides are compared after one
    more round trip. A missing view counts as drift.
    """
    installed = dict(
        conn.execute(
            "SELECT matviewname, definition FROM pg_matviews "
            "WHERE schemaname = current_schema() AND matviewname = ANY(%s)",
            (list(COMMUNE_VIEW_NAMES),),
        ).fetchall()
    )
    drift = []
    with conn.transaction(force_rollback=True):
        for name, create_sql in (
            *INTERMEDIATE_MATVIEWS,
            ("v_commune_synthese", CREATE_SQL),
            (DEPARTEMENT_VIEW, DEPARTEMENT_CREATE_SQL),
            (ANNUEL_VIEW, ANNUEL_CREATE_SQL),
        ):
            # The application view under its own name: the synthese then
            # reads the application intermediates, never the installed ones.
            # A base older than the application (a table without a column the
            # DDL reads, e.g. eligibilite_fixe.offres before migration 106)
            # cannot even hold it: that view drifts.
            try:
                with conn.transaction():
                    conn.execute(_as_temp_view(create_sql))
            except (psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedTable):
                drift.append(name)
                continue
            first = conn.execute(
                "SELECT pg_get_viewdef(%s::regclass)", (f"pg_temp.{name}",)
            ).fetchone()[0]
            expected = _deparsed(conn, f"{name}_expected", first)
            if name not in installed or (
                _deparsed(conn, f"{name}_installed", installed[name]) != expected
            ):
                drift.append(name)
    return drift
