"""Insert source offer rows into the per-building ``eligibilite_fixe`` (04.1-16).

The tests describe broadband rows as the ARCEP source does (one offer per
row); the table stores one row per building with ``offres``, codes of
``eligibilite_offres``. Each call stores one building row holding one offer:
the reads (``unnest(offres)``) and ``fibre_pct`` (distinct ``id_imb``) see
exactly the rows the test wrote.
"""

from __future__ import annotations

from typing import Any

from psycopg import sql


def code_offre(
    conn: Any, technologie: str | None, operateur: str | None, debit_max: str | None
) -> int:
    """The dictionary code of a triple, added after the largest one if new."""
    conn.execute(
        "INSERT INTO eligibilite_offres (code, operateur, technologie, debit_max) "
        "SELECT (SELECT coalesce(max(code), 0) + 1 FROM eligibilite_offres), "
        "%(o)s, %(t)s, %(d)s WHERE NOT EXISTS ("
        "SELECT 1 FROM eligibilite_offres WHERE operateur IS NOT DISTINCT FROM %(o)s "
        "AND technologie IS NOT DISTINCT FROM %(t)s "
        "AND debit_max IS NOT DISTINCT FROM %(d)s)",
        {"o": operateur, "t": technologie, "d": debit_max},
    )
    return conn.execute(
        "SELECT code FROM eligibilite_offres WHERE operateur IS NOT DISTINCT FROM %s "
        "AND technologie IS NOT DISTINCT FROM %s AND debit_max IS NOT DISTINCT FROM %s",
        (operateur, technologie, debit_max),
    ).fetchone()[0]


def inserer_offre(
    conn: Any,
    *,
    technologie: str | None,
    operateur: str | None = None,
    debit_max: str | None = None,
    **colonnes: Any,
) -> None:
    """One building row (*colonnes*: code_insee, id_imb, voie...) with one offer."""
    code = code_offre(conn, technologie, operateur, debit_max)
    noms = [*colonnes, "offres"]
    conn.execute(
        sql.SQL("INSERT INTO eligibilite_fixe ({}) VALUES ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, noms)),
            sql.SQL(", ").join(sql.Placeholder() * len(noms)),
        ),
        [*colonnes.values(), [code]],
    )
