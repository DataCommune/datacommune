"""Canonical DDL for the scoring_renov_target materialized view.

The DPE, cadastre, and DVF ETLs replace source tables by rename. Recreating
the matview after those swaps rebinds it to the new table OIDs.
"""

from typing import Any

import psycopg

from app.common.unqualified_surfaces import refuse

DROP_SQL = "DROP MATERIALIZED VIEW IF EXISTS scoring_renov_target CASCADE"

# The dpe_fg CTE below selects its own diagnostics_dpe columns on purpose: this
# is a scoring aggregate on a refresh schedule, not a row projection, so it does
# not consume app/dpe/columns.py (DPE_SELECT_COLUMNS).
# No departement filter (plan 04.1-08, migration 102): diagnostics_dpe holds
# only the hosted departements; migration 066 froze ('44', '33').
CREATE_SQL = """
CREATE MATERIALIZED VIEW scoring_renov_target AS
WITH dpe_fg AS (
    SELECT numero_dpe, date_etablissement_dpe, annee_construction,
           surface_habitable, etiquette_dpe, type_installation_chauffage,
           code_postal, code_departement, position
    FROM diagnostics_dpe
    WHERE etiquette_dpe IN ('F', 'G')
      AND position IS NOT NULL
      AND surface_habitable > 0
      AND surface_habitable < 10000
),
dpe_with_parcelle AS (
    SELECT DISTINCT ON (d.numero_dpe)
        d.numero_dpe, d.date_etablissement_dpe, d.annee_construction,
        d.surface_habitable, d.etiquette_dpe, d.type_installation_chauffage,
        d.code_postal, d.code_departement,
        p.id_parcelle, p.commune, p.contenance, d.position
    FROM dpe_fg d
    JOIN parcelles p ON ST_Intersects(p.geometrie, d.position)
    ORDER BY d.numero_dpe, p.contenance DESC
),
last_mutation AS (
    SELECT DISTINCT ON (id_parcelle)
        id_parcelle,
        date_mutation AS date_derniere_vente,
        valeur_fonciere AS prix_derniere_vente,
        adresse_numero,
        adresse_nom_voie
    FROM mutations
    WHERE id_parcelle IS NOT NULL
    ORDER BY id_parcelle, date_mutation DESC
)
SELECT
    dp.numero_dpe,
    dp.date_etablissement_dpe AS date_dpe,
    dp.annee_construction,
    dp.surface_habitable,
    dp.etiquette_dpe,
    dp.type_installation_chauffage AS type_chauffage,
    dp.code_postal,
    dp.code_departement,
    dp.commune,
    dp.id_parcelle,
    dp.contenance,
    lm.date_derniere_vente,
    lm.prix_derniere_vente,
    CONCAT_WS(' ', lm.adresse_numero, lm.adresse_nom_voie) AS adresse,
    dp.position
FROM dpe_with_parcelle dp
LEFT JOIN last_mutation lm ON dp.id_parcelle = lm.id_parcelle
WITH DATA
"""

INDEX_SQLS = (
    "CREATE UNIQUE INDEX idx_scoring_numero_dpe ON scoring_renov_target (numero_dpe)",
    "CREATE INDEX idx_scoring_position ON scoring_renov_target USING GIST (position)",
    "CREATE INDEX idx_scoring_code_departement "
    "ON scoring_renov_target (code_departement)",
    "CREATE INDEX idx_scoring_etiquette_dpe ON scoring_renov_target (etiquette_dpe)",
    "CREATE INDEX idx_scoring_commune ON scoring_renov_target (commune)",
)


def recreate_scoring_renov_target(conn: "psycopg.Connection[Any]") -> None:
    """Retain legacy DDL for review, but forbid any unqualified computation."""
    refuse("scoring_renov")
