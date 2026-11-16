"""Unit tests for the nearest-parcel snapping rule (app/resolve/snap.py).

Pure logic, no DB: the decision only reads (id_parcelle, distance_m) pairs.
Distances of CASE-SNAP-REAL-* reproduce the measured production case where a
BAN entrance point landed on the roadway: 0.0 m to the abutting parcel, 9.6 m
and 11.4 m to the next two. Identifiers are synthetic.

The last section guards the SQL shape of the fallback query (same capture
pattern as tests/test_resolve_dvf_parcelle.py) — no live DB involved.
"""

import re

import pytest

pytestmark = pytest.mark.unit

from app.cadastre import queries as cadastre_queries
from app.resolve.snap import (
    SNAP_MARGIN_M,
    SNAP_RADIUS_M,
    select_snapped_candidates,
)

_A = "85047000AD0123"
_B = "85047000AD0124"
_C = "85047000AD0125"


def _row(id_parcelle: str, distance_m: float | None) -> dict:
    return {"id_parcelle": id_parcelle, "distance_m": distance_m}


def test_no_candidate_stays_a_typed_absence() -> None:
    """CASE-SNAP-ABS-01: nothing within the radius -> nothing to expose."""
    assert select_snapped_candidates([]) == []


def test_single_candidate_is_canonical() -> None:
    """CASE-SNAP-ONE-02: one nearby parcel -> exact, whatever the distance."""
    rows = [_row(_A, 7.2)]
    assert select_snapped_candidates(rows) == rows


def test_clear_winner_beyond_margin_is_canonical() -> None:
    """CASE-SNAP-WIN-03: a gap of at least SNAP_MARGIN_M elects the closest."""
    rows = [_row(_A, 1.0), _row(_B, 1.0 + SNAP_MARGIN_M + 0.5)]
    assert select_snapped_candidates(rows) == [rows[0]]


def test_margin_boundary_is_inclusive() -> None:
    """CASE-SNAP-EDGE-04: exactly SNAP_MARGIN_M still counts as a winner."""
    rows = [_row(_A, 2.0), _row(_B, 2.0 + SNAP_MARGIN_M)]
    assert select_snapped_candidates(rows) == [rows[0]]


def test_tie_below_margin_stays_ambiguous() -> None:
    """CASE-SNAP-TIE-05: neighbours too close -> no silent pick, full list."""
    rows = [_row(_A, 1.0), _row(_B, 2.9)]
    assert select_snapped_candidates(rows) == rows


def test_ambiguous_list_is_ordered_on_id_parcelle() -> None:
    """CASE-SNAP-ORDER-06: the ambiguous list keeps the stable order already
    exposed by the strict point lookup, whatever the distance order."""
    closest, runner_up = _row(_C, 1.0), _row(_A, 1.5)
    assert select_snapped_candidates([closest, runner_up]) == [runner_up, closest]


def test_real_roadway_case_elects_the_abutting_parcel() -> None:
    """CASE-SNAP-REAL-07: 0.0 m vs 9.6 m vs 11.4 m -> the abutting parcel."""
    rows = [_row(_A, 0.0), _row(_B, 9.6), _row(_C, 11.4)]
    assert select_snapped_candidates(rows) == [rows[0]]


def test_missing_distance_fails_closed_to_ambiguous() -> None:
    """CASE-SNAP-NULL-08: an unmeasured candidate never wins the snap."""
    rows = [_row(_A, 0.0), _row(_B, None)]
    assert select_snapped_candidates(rows) == rows


def test_input_list_is_never_mutated() -> None:
    """CASE-SNAP-PURE-09: the rule is pure — the caller's list is untouched."""
    rows = [_row(_C, 1.0), _row(_A, 1.5)]
    snapshot = [dict(row) for row in rows]
    select_snapped_candidates(rows)
    assert rows == snapshot


def test_constants_stay_within_the_agreed_envelope() -> None:
    """CASE-SNAP-CONST-10: a bounded radius and a non-zero decision margin."""
    assert SNAP_RADIUS_M == 10.0
    assert SNAP_MARGIN_M == 3.0


# ---------------------------------------------------------------------------
# Query shape (SQL-level guards)
# ---------------------------------------------------------------------------


class _FakeResult:
    def __iter__(self):
        return iter(())


class _CaptureSession:
    def __init__(self, captured: dict):
        self._captured = captured

    async def execute(self, sql, params):
        self._captured["sql"] = str(sql)
        self._captured["params"] = params
        return _FakeResult()


@pytest.mark.asyncio
async def test_parcelles_proches_is_index_assisted_and_metric() -> None:
    """CASE-SNAP-SQL-11: bbox stage on the raw geometry (the GIST index of
    migration 003 is on 4326 geometry, not geography), exact metric stage on
    geography, deterministic order, no silent truncation."""
    captured: dict = {}
    await cadastre_queries.parcelles_proches(
        _CaptureSession(captured), 47.212028, -1.551375, rayon_m=SNAP_RADIUS_M
    )

    sql = captured["sql"]
    compact = re.sub(r"\s+", " ", sql)
    assert "ST_DWithin( geometrie, ST_SetSRID" in compact, (
        "bbox stage must run on the indexed geometry column, not on geography"
    )
    assert "geometrie::geography" in sql, "exact stage must be metric"
    assert "<= :rayon_m" in sql, "the degree padding must be re-filtered in metres"
    assert "ORDER BY distance_m, id_parcelle" in sql, "deterministic order"
    assert "LIMIT" not in sql, "candidates API must not silently truncate"
    assert captured["params"]["rayon_m"] == SNAP_RADIUS_M, captured["params"]


@pytest.mark.asyncio
async def test_parcelle_par_point_stays_a_strict_intersection() -> None:
    """CASE-SNAP-SQL-12: the shared strict lookup is untouched by the snap —
    still point-in-polygon, still ordered on id_parcelle, still no radius."""
    captured: dict = {}
    await cadastre_queries.parcelle_par_point(
        _CaptureSession(captured), 47.212028, -1.551375
    )

    sql = captured["sql"]
    assert "ST_Intersects" in sql, "strict lookup must stay point-in-polygon"
    assert "ST_DWithin" not in sql, "strict lookup must not become a radius search"
    assert "ORDER BY id_parcelle" in sql, "stable order preserved"
