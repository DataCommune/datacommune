"""Canonical DDL for the communes_lookup materialized view.

The BAN ETL swaps ``adresses`` by renaming tables. Materialized views remain
bound to source table OIDs, so a refresh would keep reading the stale
``adresses_old`` table and the deferred cleanup may drop the view via CASCADE.
DROP + CREATE rebinds it to the current production table.
"""

from typing import Any

import psycopg

DROP_SQL = "DROP MATERIALIZED VIEW IF EXISTS communes_lookup CASCADE"

CREATE_SQL = """
CREATE MATERIALIZED VIEW communes_lookup AS
SELECT DISTINCT
    nom_commune,
    unaccent(lower(nom_commune)) AS nom_commune_norm,
    code_commune
FROM adresses
WHERE nom_commune IS NOT NULL
WITH DATA
"""

INDEX_SQLS = (
    "CREATE INDEX idx_communes_lookup_norm ON communes_lookup (nom_commune_norm)",
    "CREATE UNIQUE INDEX idx_communes_lookup_uniq "
    "ON communes_lookup (nom_commune, code_commune)",
)


def recreate_communes_lookup(conn: "psycopg.Connection[Any]") -> None:
    """Recreate communes_lookup transactionally against current adresses."""
    conn.execute(DROP_SQL)
    conn.execute(CREATE_SQL)
    for sql in INDEX_SQLS:
        conn.execute(sql)
