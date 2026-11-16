"""Give ``mutations_grain.valeur_fonciere`` the type of ``mutations``: NUMERIC(15,2).

Revision ID: 108_dvf_grain_valeur_fonciere
Revises: 107_drop_unused_indexes
Create Date: 2026-10-21

Plan 04.4-07 (A9-01, D-05; numbered 108: the head read before writing was
``107_drop_unused_indexes``). Migration 015 widened ``mutations.valeur_fonciere``
to NUMERIC(15,2) because national DVF holds amounts of 10^10 EUR and more;
087 created ``mutations_grain`` at NUMERIC(12,2), so the reconstruction of
such a mutation fails with ``numeric field overflow`` and the whole import
with it. 087 is not rewritten: this revision changes the type.

PostgreSQL refuses to change the type of a column read by a view: under the
commune publication lock and ``lock_timeout``, the commune view chain is
dropped, the type changed (no table rewrite: same scale, wider precision),
and the chain recreated with the frozen DDL of migration 106 (unchanged
since; ``tests/test_commune_view_migration_snapshot.py`` keeps 106 equal to
the application DDL). 106 is read from its file, as 106 reads 098: the chain,
the lock literal and the re-anchoring query are not copied. A view reading
``mutations_grain`` outside that chain makes the ``ALTER`` fail and the
migration roll back, never pass silently.

The chain gets new OIDs: the latest publication of each dataset is
re-anchored by 106 (only the entries holding the OIDs read before the DROP,
WR-03), so the publication guard keeps admitting the data served, which are
recomputed from the same rows, and still refuses a publication already stale.

Downgrade counts the amounts beyond NUMERIC(12,2) under the same locks and
refuses, naming the count, while any remains (PostgreSQL would only report a
``numeric field overflow``); otherwise the same sequence gives NUMERIC(12,2)
back. Operation: API stopped, both directions in one transaction. Applied on
local disposable PostGIS only; the work base is migrated with Ianis's agreement
(plan 04.4-04).
"""

import importlib.util
from pathlib import Path

from alembic import op

revision: str = "108_dvf_grain_valeur_fonciere"
down_revision: str = "107_drop_unused_indexes"
branch_labels: str | None = None
depends_on: str | None = None

# Largest amount NUMERIC(12,2) holds (migration 087).
MAX_NUMERIC_12_2 = "9999999999.99"


def _migration_106():
    """Chain, lock literal and re-anchoring query of 106, read from its file."""
    path = Path(__file__).with_name("106_eligibilite_fixe_par_immeuble.py")
    spec = importlib.util.spec_from_file_location("migration_106", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verrouiller(m106) -> None:
    """Publication lock first, then the table: the order of a publication."""
    op.execute(m106.LOCK_TIMEOUT)
    op.get_bind().exec_driver_sql(
        f"SELECT pg_advisory_xact_lock({m106.PUBLICATION_LOCK_ID})"
    )
    op.execute("LOCK TABLE mutations_grain IN ACCESS EXCLUSIVE MODE")


def _changer_type(m106, type_sql: str) -> None:
    bind = op.get_bind()
    anciens = m106.oids_avant(bind, ["mutations_grain", *m106.VIEW_NAMES])
    for name in reversed(m106.VIEW_NAMES):
        bind.exec_driver_sql(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
    op.execute(
        f"ALTER TABLE mutations_grain ALTER COLUMN valeur_fonciere TYPE {type_sql}"
    )
    for statement in m106.STATEMENTS:
        bind.exec_driver_sql(statement)
    m106.reancrer(bind, anciens)


def upgrade() -> None:
    m106 = _migration_106()
    _verrouiller(m106)
    _changer_type(m106, "NUMERIC(15, 2)")


def downgrade() -> None:
    m106 = _migration_106()
    _verrouiller(m106)
    trop_grands = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT count(*) FROM mutations_grain "
            f"WHERE abs(valeur_fonciere) > {MAX_NUMERIC_12_2}"
        )
        .scalar()
    )
    if trop_grands:
        raise RuntimeError(
            f"Downgrade refused: {trop_grands} mutations_grain amount(s) exceed "
            f"NUMERIC(12,2) (> {MAX_NUMERIC_12_2}); nothing changed"
        )
    _changer_type(m106, "NUMERIC(12, 2)")
