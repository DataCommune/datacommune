"""Publication guard of the imported tables read outside the commune lock.

``bdnb_batiment_parcelle`` and ``rnb_liens`` are swapped by their pipeline,
then journalled in ``etl_publications``; their readers take no commune
publication lock (04.7-19 review, P2). Kept apart from
``publication_readiness``: the DPE widening of ``app.logements.queries`` is
also loaded by offline scripts, which must not open the application settings.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Catalogue key -> production table. For both pipelines the dataset journalled
# in ``etl_publications`` bears the name of its table: the lookup below filters
# on both, through ``ix_etl_publications_latest (dataset_name, id DESC)``.
IMPORTED_TABLES = {"bdnb": "bdnb_batiment_parcelle", "rnb": "rnb_liens"}
_LOCKS = {
    table: text(f"LOCK TABLE {table} IN ACCESS SHARE MODE")
    for table in IMPORTED_TABLES.values()
}

# A table is read only while it IS the one its last acted publication
# measured: the OID that publication recorded for this very table. Unlike
# ``guard_publication``, a later failed or pending run does not withdraw it: a
# run refused before its swap left the acted table in place, and a long import
# must not black out the readers. A swap whose publication then failed leaves
# another OID in place: refused. A publication without the table's OID
# proves nothing: refused. A table never journalled stays readable (imports
# older than the journal); journalled but never published, it is not.
_TABLES_NON_ACTEES = text("""
    SELECT count(*) FROM unnest(CAST(:tables AS text[])) t(name)
    WHERE EXISTS (
        SELECT 1 FROM etl_publications e
        WHERE e.dataset_name = t.name AND e.table_name = t.name
    )
    AND (
        SELECT (p.relation_oids ->> t.name)::oid FROM etl_publications p
        WHERE p.dataset_name = t.name AND p.table_name = t.name
          AND p.status = 'published'
        ORDER BY p.id DESC LIMIT 1
    ) IS DISTINCT FROM to_regclass(t.name)::oid
""")


async def publication_actee(session: AsyncSession, tables: list[str]) -> bool:
    """Whether each of *tables* is still the table of its last acted publication.

    The ACCESS SHARE lock is taken before the OID is compared and held to the
    end of the caller's transaction: the swap (``ALTER TABLE ... RENAME``)
    then waits for the read, so the table checked is the table read.
    """
    if not tables:
        return True
    for table in sorted(tables):
        await session.execute(_LOCKS[table])
    non_actees = (
        await session.execute(_TABLES_NON_ACTEES, {"tables": tables})
    ).scalar_one()
    return non_actees == 0
