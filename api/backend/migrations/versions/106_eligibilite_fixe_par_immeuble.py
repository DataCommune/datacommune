"""Store ``eligibilite_fixe`` at one row per ARCEP building, its offers coded.

Revision ID: 106_eligibilite_fixe_par_immeuble
Revises: 105_dvf_adresse_suffixe
Create Date: 2026-10-14

Plan 04.1-16 (numbered 106: 103 to 105 were taken by plans 04.1-10, 13 and
14). ``eligibilite_fixe`` held one row per offer of a building (13.3 M rows
in 44/85, about 13 per building); it now holds one row per distinct
(``id_imb``, ``code_insee``, ``voie``, ``numero``, ``complement``) with
``offres smallint[]``, codes of the dictionary ``eligibilite_offres``
(operator, technology, speed). Measured on the work base (plan 04.1-02,
``eligibilite-forme.json``): one address per ``id_imb``, 25 817 addresses
carrying several ``id_imb`` (kept as distinct rows, never merged by address,
Astra review n. 11), no duplicated offer. Duplicates would be kept anyway:
``array_agg`` keeps a multiset.

The connectivity routes and ``v_commune_connectivite`` read the offers back
by ``unnest(offres)`` joined to the dictionary. The commune view chain is
recreated with the application DDL of this revision (frozen copy below,
``tests/test_commune_view_migration_snapshot.py``), as migration 098 did.

The table and the chain get new OIDs: the latest publication of each
dataset naming one of them is re-anchored on the new relations (the
publication guard compares ``etl_publications.relation_oids`` with
``to_regclass``); only the entries holding the OID read before the DROP
move, an entry already stale stays stale (WR-03 of the 04.4 review,
2026-10-22, after this revision was applied on the work base and the public base: it
changes nothing there, it applies to a base migrated later). The data
served is the published one, regrouped.

Downgrade rebuilds the offer rows by ``unnest`` (lossless but for the
technical ``id``), recreates the chain of migration 098, re-anchors, and
drops the dictionary. Operation: API stopped or maintenance window, both
directions under the commune publication lock. Applied on local disposable
PostGIS only; the work base is migrated with Ianis's agreement (plan 04.1-19).
"""

import importlib.util
from pathlib import Path

from alembic import op

revision: str = "106_eligibilite_fixe_par_immeuble"
down_revision: str = "105_dvf_adresse_suffixe"
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


_DICTIONNAIRE = """
    CREATE TABLE eligibilite_offres (
        code SMALLINT PRIMARY KEY,
        operateur TEXT,
        technologie TEXT NOT NULL,
        debit_max TEXT,
        UNIQUE NULLS NOT DISTINCT (operateur, technologie, debit_max)
    )
"""
_PAR_IMMEUBLE = """
    CREATE TABLE eligibilite_fixe_par_immeuble (
        id BIGSERIAL PRIMARY KEY,
        id_imb TEXT NOT NULL,
        code_insee TEXT NOT NULL,
        voie TEXT,
        numero TEXT,
        complement TEXT,
        code_departement TEXT NOT NULL,
        offres SMALLINT[] NOT NULL
    )
"""
_PAR_OFFRE = """
    CREATE TABLE eligibilite_fixe_par_offre (
        id SERIAL PRIMARY KEY,
        id_imb TEXT NOT NULL,
        code_insee TEXT NOT NULL,
        voie TEXT,
        numero TEXT,
        complement TEXT,
        technologie TEXT NOT NULL,
        debit_max TEXT,
        operateur TEXT,
        code_departement TEXT NOT NULL
    )
"""
# Same names as migration 010 and ArcepPipeline.STAGING_INDEX_SPECS.
_INDEXES = (
    "CREATE INDEX idx_eligibilite_code_insee ON eligibilite_fixe (code_insee)",
    "CREATE INDEX idx_eligibilite_code_insee_voie "
    "ON eligibilite_fixe (code_insee, voie)",
    "CREATE INDEX idx_eligibilite_id_imb ON eligibilite_fixe (id_imb)",
)
# OIDs of the relations a migration is about to replace, read before the
# DROP, as a JSON object name -> oid (text).
_OIDS_AVANT = """
    SELECT coalesce(jsonb_object_agg(n, to_regclass(n)::oid::bigint), '{}')::text
    FROM unnest(%(noms)s::text[]) n
    WHERE to_regclass(n) IS NOT NULL
"""
# Re-anchor the latest publication of every dataset (see the docstring):
# an entry moves to the new OID only when it held the OID read before the
# migration (WR-03, review 04.4). An entry already stale for another reason
# (relation replaced outside a publication, OIDs left by a restore) keeps
# its value, so the publication guard still refuses it.
_REANCRAGE = """
    WITH anciens AS (
        SELECT name, oid FROM jsonb_each_text(%(anciens)s::jsonb) a(name, oid)
    ), latest AS (
        SELECT DISTINCT ON (dataset_name) id, status, relation_oids
        FROM etl_publications
        ORDER BY dataset_name, id DESC
    ), fixed AS (
        SELECT l.id, jsonb_object_agg(
            r.key,
            CASE WHEN r.value #>> '{}' = a.oid AND to_regclass(r.key) IS NOT NULL
                 THEN to_jsonb(to_regclass(r.key)::oid::bigint)
                 ELSE r.value
            END
        ) AS oids
        FROM latest l
        CROSS JOIN LATERAL jsonb_each(l.relation_oids) r
        LEFT JOIN anciens a ON a.name = r.key
        WHERE l.status = 'published'
        GROUP BY l.id
    )
    UPDATE etl_publications p SET relation_oids = f.oids
    FROM fixed f
    WHERE p.id = f.id AND p.relation_oids IS DISTINCT FROM f.oids
"""


def oids_avant(bind, noms: list[str]) -> str | None:
    """OIDs of *noms* before their replacement; None without a journal."""
    if not bind.exec_driver_sql(
        "SELECT to_regclass('etl_publications') IS NOT NULL"
    ).scalar():
        return None
    return bind.exec_driver_sql(_OIDS_AVANT, {"noms": noms}).scalar()


def reancrer(bind, anciens: str | None) -> None:
    """Move the entries holding an OID of *anciens* to the new relations."""
    if anciens is not None:
        bind.exec_driver_sql(_REANCRAGE, {"anciens": anciens})


def _remplacer(nouvelle: str, statements: tuple[str, ...]) -> None:
    """Drop the chain and the old table, rename *nouvelle*, rebuild the chain."""
    bind = op.get_bind()
    anciens = oids_avant(bind, ["eligibilite_fixe", *VIEW_NAMES])
    for name in reversed(VIEW_NAMES):
        bind.exec_driver_sql(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
    op.execute("DROP TABLE eligibilite_fixe")
    op.execute(f"ALTER TABLE {nouvelle} RENAME TO eligibilite_fixe")
    op.execute(f"ALTER SEQUENCE {nouvelle}_id_seq RENAME TO eligibilite_fixe_id_seq")
    op.execute(
        f"ALTER TABLE eligibilite_fixe RENAME CONSTRAINT {nouvelle}_pkey "
        "TO eligibilite_fixe_pkey"
    )
    for index in _INDEXES:
        op.execute(index)
    for statement in statements:
        bind.exec_driver_sql(statement)
    reancrer(bind, anciens)


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(LOCK_TIMEOUT)
    bind.exec_driver_sql(f"SELECT pg_advisory_xact_lock({PUBLICATION_LOCK_ID})")
    op.execute("LOCK TABLE eligibilite_fixe IN ACCESS EXCLUSIVE MODE")
    op.execute(_DICTIONNAIRE)
    op.execute("""
        INSERT INTO eligibilite_offres (code, operateur, technologie, debit_max)
        SELECT row_number() OVER (ORDER BY technologie, operateur, debit_max),
               operateur, technologie, debit_max
        FROM (
            SELECT DISTINCT operateur, technologie, debit_max FROM eligibilite_fixe
        ) triplets
    """)
    op.execute(_PAR_IMMEUBLE)
    op.execute("""
        INSERT INTO eligibilite_fixe_par_immeuble
            (id_imb, code_insee, voie, numero, complement, code_departement, offres)
        SELECT e.id_imb, e.code_insee, e.voie, e.numero, e.complement,
               e.code_departement, array_agg(o.code ORDER BY o.code)
        FROM eligibilite_fixe e
        JOIN eligibilite_offres o
          ON o.operateur IS NOT DISTINCT FROM e.operateur
         AND o.technologie = e.technologie
         AND o.debit_max IS NOT DISTINCT FROM e.debit_max
        GROUP BY 1, 2, 3, 4, 5, 6
    """)
    _remplacer("eligibilite_fixe_par_immeuble", STATEMENTS)


def _statements_098() -> tuple[str, ...]:
    """The chain of migration 098, read from its file (never app code)."""
    path = Path(__file__).with_name("098_commune_views_application_ddl.py")
    spec = importlib.util.spec_from_file_location("migration_098", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STATEMENTS


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(LOCK_TIMEOUT)
    bind.exec_driver_sql(f"SELECT pg_advisory_xact_lock({PUBLICATION_LOCK_ID})")
    op.execute("LOCK TABLE eligibilite_fixe IN ACCESS EXCLUSIVE MODE")
    op.execute(_PAR_OFFRE)
    op.execute("""
        INSERT INTO eligibilite_fixe_par_offre
            (id_imb, code_insee, voie, numero, complement, technologie,
             debit_max, operateur, code_departement)
        SELECT e.id_imb, e.code_insee, e.voie, e.numero, e.complement,
               o.technologie, o.debit_max, o.operateur, e.code_departement
        FROM eligibilite_fixe e
        CROSS JOIN LATERAL unnest(e.offres) AS u(code)
        JOIN eligibilite_offres o ON o.code = u.code
        ORDER BY e.id, u.code
    """)
    _remplacer("eligibilite_fixe_par_offre", _statements_098())
    op.execute("DROP TABLE eligibilite_offres")
