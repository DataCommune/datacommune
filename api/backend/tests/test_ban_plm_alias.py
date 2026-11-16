"""Unit tests for resolve_plm_alias + _detect_city PLM bareword short-circuit.

These tests run WITHOUT any DB connection and never skip. They are the
definitive non-regression coverage for Phase 21 must-have #3: bareword
"paris" / "lyon" / "marseille" must never resolve to Le Touquet-Paris-Plage
(62826) regardless of communes_lookup seed state.

Why no DB: resolve_plm_alias() is a pure function over a dict, and the
_detect_city wiring short-circuits BEFORE any session.execute call — so
a MagicMock session proves the bypass path without needing a seeded
adresses table (which the prior test_ban_geocode_paris_plm.py required
and hence skipped on dev).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ban.queries import _detect_city
from app.common.geo import PLM_CITY_ALIASES, resolve_plm_alias

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# resolve_plm_alias — pure helper tests
# ---------------------------------------------------------------------------


def test_resolve_plm_alias_paris():
    assert resolve_plm_alias("paris") == "75056"


def test_resolve_plm_alias_lyon():
    assert resolve_plm_alias("lyon") == "69123"


def test_resolve_plm_alias_marseille():
    assert resolve_plm_alias("marseille") == "13055"


def test_resolve_plm_alias_case_insensitive():
    assert resolve_plm_alias("PARIS") == "75056"
    assert resolve_plm_alias("Paris") == "75056"
    assert resolve_plm_alias(" paris ") == "75056"
    assert resolve_plm_alias("LyOn") == "69123"


def test_resolve_plm_alias_returns_none_for_non_plm():
    assert resolve_plm_alias("le touquet") is None
    assert resolve_plm_alias("nice") is None
    assert resolve_plm_alias("toulouse") is None
    assert resolve_plm_alias("") is None


def test_resolve_plm_alias_returns_none_for_arrondissement_phrases():
    # Multi-token phrases must fall through to communes_lookup so numero
    # scoring ranks the right arrondissement. The helper is last-token
    # pure: caller is responsible for splitting tokens first.
    assert resolve_plm_alias("paris 1er") is None
    assert resolve_plm_alias("paris 4e") is None
    assert resolve_plm_alias("lyon 3e") is None


def test_plm_city_aliases_exported():
    # Drift guard: the public dict must contain all 3 PLM cities.
    assert set(PLM_CITY_ALIASES.keys()) == {"paris", "lyon", "marseille"}
    assert PLM_CITY_ALIASES["paris"] == "75056"
    assert PLM_CITY_ALIASES["lyon"] == "69123"
    assert PLM_CITY_ALIASES["marseille"] == "13055"


# ---------------------------------------------------------------------------
# _detect_city wiring tests — prove the short-circuit bypasses communes_lookup
# ---------------------------------------------------------------------------


def _strict_mock_session():
    """Session mock whose execute() raises if called.

    Used to prove the PLM short-circuit returns BEFORE querying the DB.
    """
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(
        side_effect=AssertionError(
            "communes_lookup must NOT be queried for PLM bareword"
        )
    )
    return mock_session


def _lookup_miss_session():
    """Session mock whose execute() returns an empty result (no row found).

    Used to simulate the pre-fix failure mode where communes_lookup has no
    'Paris'/'Lyon'/'Marseille' row: first() returns None, city detection
    falls through. begin_nested() is a sync context manager on the real
    session, so patch it as such.
    """
    mock_session = MagicMock()
    empty_result = MagicMock()
    empty_result.first = MagicMock(return_value=None)
    mock_session.execute = AsyncMock(return_value=empty_result)

    class _SavepointCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    mock_session.begin_nested = MagicMock(return_value=_SavepointCM())
    return mock_session


async def test_detect_city_paris_bareword_bypasses_lookup():
    """_detect_city returns Paris codes for 'paris' bareword without hitting DB."""
    mock_session = _strict_mock_session()

    result = await _detect_city(mock_session, "1 rue de la paix paris")

    assert result is not None
    street, city, codes = result
    assert street == "1 rue de la paix"
    assert city == "paris"
    assert "75056" in codes
    assert "75101" in codes
    assert "75120" in codes
    # Crucially: Le Touquet must NOT be in the search set.
    assert "62826" not in codes


async def test_detect_city_lyon_bareword_bypasses_lookup():
    mock_session = _strict_mock_session()

    result = await _detect_city(mock_session, "place bellecour lyon")

    assert result is not None
    street, city, codes = result
    assert street == "place bellecour"
    assert city == "lyon"
    assert "69123" in codes
    assert "69381" in codes
    assert "69389" in codes


async def test_detect_city_marseille_bareword_bypasses_lookup():
    mock_session = _strict_mock_session()

    result = await _detect_city(mock_session, "rue paradis marseille")

    assert result is not None
    street, city, codes = result
    assert street == "rue paradis"
    assert city == "marseille"
    assert "13055" in codes
    assert "13201" in codes
    assert "13216" in codes


async def test_detect_city_le_touquet_does_not_short_circuit():
    """Non-PLM city tokens must still flow through communes_lookup.

    We use a mock whose execute returns no row (simulates no seed in dev).
    The short-circuit must NOT fire (resolve_plm_alias('touquet') is None),
    so execute IS called and the function returns None because the lookup
    found nothing. Asserting execute.called proves the branch selected.
    """
    mock_session = _lookup_miss_session()

    result = await _detect_city(mock_session, "le touquet")

    # Short-circuit skipped (tokens=['le','touquet'], last='touquet' not PLM).
    # Lookup path entered, found nothing -> returns None.
    assert result is None
    assert mock_session.execute.called


async def test_detect_city_postal_present_does_not_short_circuit():
    """When the query contains a 5-digit postal, short-circuit must NOT fire.

    '1 rue X 75002 paris' has '75002' in the query -> existing explicit-postal
    routing must take precedence so Strategy A filters by code_postal=75002.
    Without this guard, the short-circuit would hijack the query and expand
    to all 20 Paris arrondissements, losing the postal-specific filter.
    """
    mock_session = _lookup_miss_session()

    result = await _detect_city(mock_session, "1 rue X 75002 paris")

    # Short-circuit skipped (_RE_POSTAL matched), lookup entered, no row.
    assert result is None
    assert mock_session.execute.called


async def test_detect_city_paris_single_token_does_not_short_circuit():
    """Single-token query 'paris' must not short-circuit (empty street_part).

    The short-circuit requires len(tokens) >= 2 so street_part is never empty.
    For a bare 'paris' query we return None (no street to resolve); the
    geocoder falls through to Strategy B/D on the full query. The existing
    communes_lookup loop is also skipped because max_city_tokens is 0
    when len(tokens) == 1 (keeps at least 1 token for street). So execute
    is NEVER called and result is None — proving the short-circuit did
    not fire and no DB lookup was attempted either.
    """
    mock_session = _strict_mock_session()

    result = await _detect_city(mock_session, "paris")

    assert result is None
    assert not mock_session.execute.called
