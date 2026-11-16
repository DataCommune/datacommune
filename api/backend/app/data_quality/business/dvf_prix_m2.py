"""Business hook: DVF prix au m² out-of-range detector.

Isolated module — imports base.py ONLY (anti-cycle C1).
Called by the generic orchestrator via DatasetProfile.business_hook.

Severity: WARN (D-03) — DVF does not ERROR on extreme prix/m².
DoS mitigation: SET LOCAL statement_timeout = '10min' (T-26-01-02).
SQL injection mitigation: bounds passed as %s parameters (T-26-01-01).
"""

import psycopg

from app.data_quality.base import Finding

# Verbatim from checkers/dvf.py lines 54-55 — source of truth for bounds.
PRIX_M2_MIN = 100  # Below -> probable data error (terrain non bati counted as bati).
PRIX_M2_MAX = 50_000  # Above -> probable outlier.

_QUERY = """
SELECT count(*) FROM {table}
WHERE nature_mutation = 'Vente'
  AND valeur_fonciere IS NOT NULL
  AND surface_reelle_bati IS NOT NULL
  AND surface_reelle_bati > 0
  AND valeur_fonciere > 0
  AND (valeur_fonciere / surface_reelle_bati < %s
    OR valeur_fonciere / surface_reelle_bati > %s)
"""

_SAMPLE_QUERY = (
    "SELECT id_mutation, valeur_fonciere, surface_reelle_bati, "
    "round(valeur_fonciere/surface_reelle_bati, 0) AS prix_m2 "
    "FROM mutations "
    "WHERE nature_mutation = 'Vente' "
    "AND surface_reelle_bati > 0 AND valeur_fonciere > 0 "
    "AND (valeur_fonciere/surface_reelle_bati < 100 "
    "OR valeur_fonciere/surface_reelle_bati > 50000) "
    "ORDER BY prix_m2 DESC LIMIT 10;"
)


def check_dvf_prix_m2(
    conn: psycopg.Connection,
    profile: object,  # DatasetProfile — not imported to preserve anti-cycle C1
) -> list[Finding]:
    """Detect DVF mutations with aberrant prix au m² (WARN-only, D-03).

    Counts Vente rows where prix/m2 falls outside [PRIX_M2_MIN, PRIX_M2_MAX].
    Returns one WARN Finding if any bad rows are found, [] otherwise.

    Args:
        conn: read-only psycopg connection (T-26-01-03, already set by orchestrator).
        profile: DatasetProfile instance — used to obtain the target table name.

    Returns:
        list[Finding]: one WARN finding if bad rows > 0, empty list if all clean.
    """
    # T-26-01-02: raise statement timeout before full scan of 15M+ partitioned rows.
    conn.execute("SET LOCAL statement_timeout = '10min'")

    table = getattr(profile, "table", "mutations")
    cursor = conn.execute(_QUERY.format(table=table), (PRIX_M2_MIN, PRIX_M2_MAX))
    row = cursor.fetchone()
    bad = row[0] if row else 0

    if bad > 0:
        return [
            Finding(
                severity="WARN",
                category="business",
                title=f"Prix au m2 aberrant sur Vente : {bad} lignes",
                description=(
                    f"Prix/m2 hors [{PRIX_M2_MIN}, {PRIX_M2_MAX}] euros. "
                    "Probablement terrain nu compte comme bati ou saisie erronee."
                ),
                count=bad,
                sample_query=_SAMPLE_QUERY,
            )
        ]
    return []
