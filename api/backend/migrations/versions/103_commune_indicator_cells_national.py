"""Accept commune indicator cells of every hostable departement.

Revision ID: 103_commune_indicator_cells_national
Revises: 102_scoring_scope_national
Create Date: 2026-10-13

Plan 04.1-10 (decision of plan 09, option a: qualification contract
``dvf-delivery-qualification.v3``). Migration 095 constrained
``commune_indicator_result_cells.code_insee`` to ``^(44|85)[0-9]{3}$``: no build
of another departement could be stored. This revision replaces that check with
the hosted commune rule of ``app.commune.indicator_schemas`` (a departement of
``DEPARTEMENTS_HEBERGEABLES``, then two digits after an overseas departement,
three otherwise; departement read as ``dc_departement_from_insee`` does). A
closed list is widened, no free pattern is opened: overseas collectivities
(975, 977, 978, 98x, D-123) and unknown codes stay refused. The list is copied
here (a migration never imports the application); a test compares both lists.

Upgrade only widens: every stored 44/85 cell stays valid, nothing is rewritten
(``ADD CONSTRAINT`` checks the existing rows). The table keeps its
immutability triggers; ``ALTER TABLE`` does not fire them.

Downgrade gives the 44/85 check back, and refuses (raises, the transaction of
``migrations/env.py`` rolls back) while a cell outside 44/85 is stored: it
never deletes a row of this immutable table.

Applied on local disposable PostGIS and on the temoin base only; the served
base of the work base container is migrated with Ianis's agreement (plan 04.1-19).
"""

from alembic import op
from sqlalchemy import text

revision: str = "103_commune_indicator_cells_national"
down_revision: str = "102_scoring_scope_national"
branch_labels: str | None = None
depends_on: str | None = None

# Copy of app.scope.model.DEPARTEMENTS_HEBERGEABLES (101 codes, 2026-10-13),
# checked equal by tests/test_scope_locks.py.
DEPARTEMENTS_HEBERGEABLES: tuple[str, ...] = (
    "01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
    "11", "12", "13", "14", "15", "16", "17", "18", "19", "2A",
    "2B", "21", "22", "23", "24", "25", "26", "27", "28", "29",
    "30", "31", "32", "33", "34", "35", "36", "37", "38", "39",
    "40", "41", "42", "43", "44", "45", "46", "47", "48", "49",
    "50", "51", "52", "53", "54", "55", "56", "57", "58", "59",
    "60", "61", "62", "63", "64", "65", "66", "67", "68", "69",
    "70", "71", "72", "73", "74", "75", "76", "77", "78", "79",
    "80", "81", "82", "83", "84", "85", "86", "87", "88", "89",
    "90", "91", "92", "93", "94", "95",
    "971", "972", "973", "974", "976",
)  # fmt: skip

TABLE = "commune_indicator_result_cells"
CONSTRAINT = "commune_indicator_result_cells_code_insee_check"
# Migration 095: CHECK (code_insee ~ '^(44|85)[0-9]{3}$'), named by PostgreSQL.
CHECK_095 = r"code_insee ~ '^(44|85)[0-9]{3}$'"

# Waiting behind a build holding the table would block the deploy: fail fast.
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


def hosted_check(departements: tuple[str, ...]) -> str:
    """Hosted commune code of *departements*, as SQL over ``code_insee``."""
    codes = ",".join(f"'{code}'" for code in departements)
    return (
        "code_insee ~ '^([0-9]{2}|2[AB])[0-9]{3}$' AND "
        "CASE WHEN left(code_insee, 2) = '97' THEN left(code_insee, 3) "
        f"ELSE left(code_insee, 2) END IN ({codes})"
    )


def _replace(check: str) -> None:
    op.execute(
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {CONSTRAINT}, "
        f"ADD CONSTRAINT {CONSTRAINT} CHECK ({check})"
    )


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    _replace(hosted_check(DEPARTEMENTS_HEBERGEABLES))


def downgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    outside = (
        op.get_bind()
        .execute(text(f"SELECT count(*) FROM {TABLE} WHERE NOT ({CHECK_095})"))
        .scalar_one()
    )
    if outside:
        raise RuntimeError(
            f"{outside} commune indicator cell(s) outside 44/85 are stored; "
            "the immutable table is never rewritten: downgrade refused"
        )
    _replace(CHECK_095)
