"""Unit test: the two DPE SELECT paths project the shared column constant.

DETTE #3 — ``diagnostics_dpe`` is read by two hand-written queries
(``dpe_par_point`` and ``zone_resolve_dpe``) whose column lists drifted once
already (commit 8ee847a enriched the point path only). Both now interpolate
``DPE_SELECT_COLUMNS``; this test drives each function with a fake session,
captures the real SQL it builds, and asserts every shared column appears in
both SELECTs — so a future edit that drops the constant from one path fails
here instead of silently shipping a poorer payload.
"""

import pytest

from app.dpe.columns import DPE_SELECT_COLUMNS
from app.dpe.queries import dpe_par_point
from app.zone.queries import zone_resolve_dpe

pytestmark = pytest.mark.unit


class _EmptyResult:
    """Minimal stand-in for a SQLAlchemy Result — yields no rows."""

    def __iter__(self):
        return iter(())

    def mappings(self):
        return self

    def first(self):
        return None

    def scalar(self):
        return 0


class _CapturingSession:
    """Fake AsyncSession that records the SQL text instead of hitting a DB."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return _EmptyResult()


async def test_point_and_zone_dpe_share_column_constant():
    point_sess = _CapturingSession()
    await dpe_par_point(point_sess, lat=47.2, lon=-1.5)
    point_sql = point_sess.statements[-1]

    zone_sess = _CapturingSession()
    await zone_resolve_dpe(zone_sess, zone_type="commune", code="44109")
    # zone runs a COUNT(*) then the data SELECT; pick the one with the columns.
    zone_sql = next(s for s in zone_sess.statements if "numero_dpe" in s)

    for col in DPE_SELECT_COLUMNS:
        assert col in point_sql, f"{col} missing from dpe_par_point SELECT"
        assert col in zone_sql, f"{col} missing from zone_resolve_dpe SELECT"


def test_ademe_fields_present_in_constant():
    """Regression guard for the exact drift of 8ee847a."""
    for col in (
        "periode_construction",
        "type_energie_principale_chauffage",
        "cout_total_5_usages",
        "qualite_isolation_enveloppe",
    ):
        assert col in DPE_SELECT_COLUMNS
