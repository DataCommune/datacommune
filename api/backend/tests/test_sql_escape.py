"""Unit tests for LIKE-wildcard escaping and zone offset cap (audit L3).

Covers QE-02 / QE-03 / LB-05 / AD-03:
- app.common.sql_escape.escape_like() behaviour (%, _, backslash)
- per-site proof that a user "%" is bound as a literal (escaped + ESCAPE clause)
- zone/queries.py deep-OFFSET cap

Pattern: recording stub session — no DB required.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.common.sql_escape import escape_like

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingSession:
    """Stub AsyncSession: records (sql, params) and returns canned results."""

    def __init__(self, results):
        # results: list of result objects consumed in order by execute()
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, sql, params=None):
        self.calls.append((str(sql), params or {}))
        return self._results.pop(0)


class _Row:
    def __init__(self, mapping):
        self._mapping = mapping


class _RowsResult:
    """Result usable as `for row in result` and via .mappings().all()."""

    def __init__(self, rows):
        self._rows = [_Row(r) for r in rows]

    def __iter__(self):
        return iter(self._rows)

    def mappings(self):
        rows = self._rows
        return SimpleNamespace(all=lambda: [r._mapping for r in rows])


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def scalar_one(self):
        return self._value


def _empty_rows():
    return _RowsResult([])


# ---------------------------------------------------------------------------
# 1. Helper itself
# ---------------------------------------------------------------------------


class TestEscapeLike:
    def test_percent_is_escaped(self):
        assert escape_like("50%") == r"50\%"

    def test_underscore_is_escaped(self):
        assert escape_like("a_b") == r"a\_b"

    def test_backslash_is_doubled_first(self):
        assert escape_like(r"back\slash") == r"back\\slash"

    def test_backslash_then_wildcard(self):
        # Order matters: the literal \% input must become \\\% not \% (kept wildcard)
        assert escape_like(r"\%") == r"\\\%"

    def test_plain_string_unchanged(self):
        assert escape_like("rue de la paix") == "rue de la paix"

    def test_empty_string_unchanged(self):
        assert escape_like("") == ""


# ---------------------------------------------------------------------------
# 2. Per-site wiring — a "%" input must be bound escaped with ESCAPE clause
# ---------------------------------------------------------------------------


async def test_search_communes_escapes_q():
    from app.zone.queries import search_communes

    session = _RecordingSession([_empty_rows()])
    await search_communes(session, q="notre%dame")
    sql, params = session.calls[0]
    assert params["q"] == r"notre\%dame"
    assert "escape" in sql.lower()


async def test_list_communes_escapes_q():
    from app.zone.queries import list_communes

    session = _RecordingSession([_ScalarResult(0), _empty_rows()])
    await list_communes(session, q="saint%etienne_mont")
    count_sql, count_params = session.calls[0]
    data_sql, data_params = session.calls[1]
    expected = r"saint\%etienne\_mont"
    assert count_params["q"] == expected
    assert data_params["q"] == expected
    assert "escape" in count_sql.lower()
    assert "escape" in data_sql.lower()


async def test_scolaire_escapes_nom_voie():
    from app.scolaire.queries import college_de_secteur

    session = _RecordingSession([_empty_rows()])
    await college_de_secteur(
        session, code_insee="44109", nom_voie="rue% de_", numero=10
    )
    sql, params = session.calls[0]
    assert params["nom_voie"] == r"rue\% de\_"
    assert "escape" in sql.lower()


async def test_foncier_pm_escapes_denomination():
    from app.foncier_pm.queries import foncier_pm_par_codes

    session = _RecordingSession([_empty_rows()])
    await foncier_pm_par_codes(session, codes=["75101"], denomination="saint%_go")
    sql, params = session.calls[0]
    assert params["denomination"] == r"%saint\%\_go%"
    assert "escape" in sql.lower()


async def test_admin_requests_escapes_endpoint_filter():
    from app.admin.requests import list_requests

    db = _RecordingSession([_empty_rows(), _ScalarResult(0)])
    admin = SimpleNamespace(id=1, email="admin@example.com", is_admin=True)

    await list_requests(
        admin=admin,
        db=db,
        endpoint="/v1%50",
        date_from=_NOW,
    )

    data_sql, data_params = db.calls[0]
    count_sql, count_params = db.calls[1]
    assert data_params["endpoint"] == r"/v1\%50"
    assert count_params["endpoint"] == r"/v1\%50"
    assert "escape" in data_sql.lower()
    assert "escape" in count_sql.lower()

    # CSV export path shares the same escaping via export_csv -> _fetch_batch
    from app.admin.requests import export_csv

    db2 = _RecordingSession([_empty_rows()])
    response = await export_csv(
        admin=admin,
        db=db2,
        endpoint="/v1%",
        date_from=_NOW,
    )
    async for _chunk in response.body_iterator:
        pass
    batch_sql, batch_params = db2.calls[0]
    assert batch_params["endpoint"] == r"/v1\%"
    assert "escape" in batch_sql.lower()


# ---------------------------------------------------------------------------
# 3. Zone offset cap (QE-02)
# ---------------------------------------------------------------------------


async def test_list_communes_offset_capped():
    from app.zone.queries import MAX_OFFSET, list_communes

    session = _RecordingSession([_ScalarResult(0), _empty_rows()])
    await list_communes(session, page=10**9, per_page=50)
    _, data_params = session.calls[1]
    assert data_params["offset"] == MAX_OFFSET


async def test_zone_resolve_dvf_offset_capped():
    from app.zone.queries import MAX_OFFSET, zone_resolve_dvf

    session = _RecordingSession([_ScalarResult(0), _empty_rows()])
    await zone_resolve_dvf(session, zone_type="departement", code="44", page=20_000_000)
    _, data_params = session.calls[1]
    assert data_params["offset"] == MAX_OFFSET


async def test_zone_resolve_dpe_offset_capped():
    from app.zone.queries import MAX_OFFSET, zone_resolve_dpe

    session = _RecordingSession([_ScalarResult(0), _empty_rows()])
    await zone_resolve_dpe(session, zone_type="commune", code="44109", page=5_000_000)
    _, data_params = session.calls[1]
    assert data_params["offset"] == MAX_OFFSET


async def test_zone_offset_normal_page_unaffected():
    from app.zone.queries import zone_resolve_dvf

    session = _RecordingSession([_ScalarResult(0), _empty_rows()])
    await zone_resolve_dvf(session, zone_type="commune", code="44109", page=3)
    _, data_params = session.calls[1]
    assert data_params["offset"] == 50


@pytest.mark.parametrize("bad", ["a\\b", "100%", "snake_case", r"%_\%"])
def test_escape_like_every_wildcard_is_escaped(bad):
    """Property: every remaining % or _ in the output is preceded by '\'."""
    out = escape_like(bad)
    for i, ch in enumerate(out):
        if ch in "%_":
            assert i > 0 and out[i - 1] == "\\"
