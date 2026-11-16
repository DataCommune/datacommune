"""Create rnb_liens: local BAN<->RNB link table from the RNB departmental export.

Revision ID: 079_rnb_liens
Revises: 078_dpe_rnb_fields
Create Date: 2026-08-05

PLAN-RATTACHEMENT-DPE-2026-08-05 Lot B. Materializes locally what
``app.logements.rnb_keys.cles_ban_batiment_rnb`` currently fetches LIVE from
rnb-api.beta.gouv.fr on every public fiche request (Rule 2 widening, TTL cache
+ fail-open 6a). Lot C replaces that live call with a SQL lookup on this table;
this migration only creates the table, it changes NO attachment rule
(moratorium in force).

Source: Referentiel National des Batiments (RNB), departmental CSV exports
``https://rnb-opendata.s3.fr-par.scw.cloud/files/RNB_<dept>.csv.zip``.
Producer: beta.gouv.fr / CSTB. Licence: **Licence Ouverte 2.0** (same entry as
the existing ``rnb`` proxy in ``app.common.source_catalog``). Attribution is
carried by that catalog entry and by ``docs/etl-runbook.md`` section 2.

Grain: ONE ROW PER (id_rnb, cle_ban) PAIR. A building carries several BAN
interoperability keys exactly when it has several entrances (the corner-building
case R4-02 exists for), so the pair is the natural key. Buildings with zero
addresses are NOT ingested: they carry no BAN link, and the live call already
drops them (``if cles:`` in ``_cles_par_batiment``) -- keeping the local join
equivalent to the call it replaces.

Columns:
  - id_rnb VARCHAR(32): RNB building id (12 chars observed). Width mirrors
    ``diagnostics_dpe.id_rnb`` (migration 078) so the Lot C building-level join
    compares two identically-declared columns.
  - cle_ban VARCHAR(50): BAN interoperability key (``44132_1490_00026``,
    30 chars observed). Width mirrors ``diagnostics_dpe.identifiant_ban``
    (migration 075), the column it will be joined against.
  - code_departement VARCHAR(3): stamped from the source FILE (one export per
    department), same convention as the BAN pipeline. Drives the ETL scope
    gates, never a spatial claim.
  - statut VARCHAR(32): RNB building status (``constructed``, ``demolished``,
    ...). Ingested UNFILTERED on purpose: the live RNB call being replaced does
    not filter on status either, so filtering here would break the Lot B
    equivalence gate. Lot C decides what, if anything, to exclude.
  - parcelles TEXT[]: cadastral parcel ids the building footprint intersects,
    ORDERED BY DESCENDING ``bdg_cover_ratio``, so ``parcelles[1]`` is the
    principal parcel. The ratios themselves are deliberately NOT stored: the
    live call returns every intersecting building with no ratio threshold, so
    no current or Lot C consumer reads them. Re-ingesting is the fix if one
    ever appears.
  - millesime DATE: upstream publication date, read from the export's HTTP
    ``Last-Modified`` header. NULL when the header is absent -- never
    substituted by an import timestamp (PROV-03).

Indexes -- one per access path of the Lot C target query, no speculative index:
  1. UNIQUE (id_rnb, cle_ban): natural key AND the "building -> all its BAN
     keys" path (leading-column scan on id_rnb). No separate id_rnb index.
  2. (cle_ban): the entry point -- "this resolved ban_id -> its building(s)".
     Lot C joins 2. then 1.: ``FROM rnb_liens l1 JOIN rnb_liens l2 USING
     (id_rnb) WHERE l1.cle_ban = $1``.
  3. GIN (parcelles): "this parcel -> its buildings", the exact shape of the
     ``query_by_parcelle`` call being replaced, and what the equivalence gate
     replays.
  No index on code_departement: two distinct values on the 44/85 instance --
  a btree there would never be chosen.

Non-partitioned (~2M rows expected for 44+85), unlike the department-partitioned
ETL tables. Table is created EMPTY: the ``rnb_liens`` ETL fills it, and ADR-0003
ordering applies (migrate before the first run).
"""

from alembic import op

revision: str = "079_rnb_liens"
down_revision: str = "078_dpe_rnb_fields"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create the rnb_liens table and its three access-path indexes."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS rnb_liens (
            id BIGSERIAL PRIMARY KEY,
            id_rnb VARCHAR(32) NOT NULL,
            cle_ban VARCHAR(50) NOT NULL,
            code_departement VARCHAR(3) NOT NULL,
            statut VARCHAR(32),
            parcelles TEXT[] NOT NULL DEFAULT '{}',
            millesime DATE,
            UNIQUE (id_rnb, cle_ban)
        );
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_rnb_liens_cle_ban ON rnb_liens (cle_ban);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_rnb_liens_parcelles "
        "ON rnb_liens USING GIN (parcelles);"
    )


def downgrade() -> None:
    """Drop the rnb_liens table (indexes go with it)."""
    op.execute("DROP TABLE IF EXISTS rnb_liens CASCADE;")
