"""Unit gates for mutation-grain DVF zone queries (D-25)."""

from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from app.dvf.couverture import CLASSES_STATISTIQUES, SEUIL_ND_ZONE
from app.zone.queries import MAX_OFFSET, zone_resolve_dvf, zone_stats_dvf
from app.zone.schemas import ZoneStats

pytestmark = pytest.mark.unit


class _Row:
    def __init__(self, values: Mapping[str, Any]) -> None:
        self._mapping = values


class _Mappings:
    def __init__(self, row: Mapping[str, Any] | None) -> None:
        self._row = row

    def first(self) -> Mapping[str, Any] | None:
        return self._row


class _Result:
    def __init__(
        self,
        *,
        scalar: int | None = None,
        rows: list[Mapping[str, Any]] | None = None,
        mapping: Mapping[str, Any] | None = None,
    ) -> None:
        self._scalar = scalar
        self._rows = rows or []
        self._mapping = mapping

    def scalar(self) -> int | None:
        return self._scalar

    def mappings(self) -> _Mappings:
        return _Mappings(self._mapping)

    def __iter__(self) -> Iterator[_Row]:
        return iter(_Row(row) for row in self._rows)


class _CaptureSession:
    def __init__(self, *results: _Result) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append((str(statement), dict(params)))
        return self.results.pop(0)


def _stats_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "prix_median_m2": 3200.0,
        "nb_transactions": 12,
        "surface_moyenne": 71.0,
        "prix_median_m2_maison": 3500.0,
        "nb_transactions_maison": 7,
        "prix_median_m2_appartement": 2900.0,
        "nb_transactions_appartement": 5,
    }
    row.update(overrides)
    return row


async def test_zone_resolve_reads_mutation_grain_once_per_query() -> None:
    session = _CaptureSession(_Result(scalar=0), _Result(rows=[]))

    await zone_resolve_dvf(session, zone_type="commune", code="44109")  # type: ignore[arg-type]

    assert len(session.calls) == 2
    assert all("FROM mutations_grain" in sql for sql, _ in session.calls)
    assert all("FROM mutations " not in sql for sql, _ in session.calls)


async def test_zone_stats_uses_qualified_mutation_population() -> None:
    session = _CaptureSession(_Result(mapping=_stats_row()))

    await zone_stats_dvf(session, "commune", "44109")  # type: ignore[arg-type]

    sql, params = session.calls[0]
    assert "classe_mutation = ANY(:classes_statistiques)" in sql
    assert "est_vente = true" in sql
    assert "hors_stats_vefa = false" in sql
    assert params["classes_statistiques"] == list(CLASSES_STATISTIQUES)


async def test_zone_stats_excludes_multi_commune_mutations() -> None:
    session = _CaptureSession(_Result(mapping=_stats_row()))

    await zone_stats_dvf(session, "departement", "44")  # type: ignore[arg-type]

    sql, _ = session.calls[0]
    assert "nb_communes = 1" in sql


async def test_zone_resolve_keeps_multi_commune_mutations_in_history() -> None:
    session = _CaptureSession(_Result(scalar=0), _Result(rows=[]))

    await zone_resolve_dvf(session, zone_type="departement", code="44")  # type: ignore[arg-type]

    assert all("nb_communes = 1" not in sql for sql, _ in session.calls)


async def test_zone_stats_counts_rows_without_distinct_workaround() -> None:
    session = _CaptureSession(_Result(mapping=_stats_row()))

    await zone_stats_dvf(session, "departement", "44")  # type: ignore[arg-type]

    sql, _ = session.calls[0]
    assert "COUNT(*) AS nb_transactions" in sql
    assert "COUNT(DISTINCT id_mutation)" not in sql
    assert "surface_reelle_bati" not in sql


async def test_zone_stats_masks_median_below_threshold_behavior() -> None:
    low_count = SEUIL_ND_ZONE - 1
    session = _CaptureSession(
        _Result(
            mapping=_stats_row(
                prix_median_m2_appartement=None,
                nb_transactions_appartement=low_count,
            )
        )
    )

    result = await zone_stats_dvf(session, "commune", "44109")  # type: ignore[arg-type]
    stats = ZoneStats(**result)

    assert stats.nb_transactions_appartement == low_count
    assert stats.prix_median_m2_appartement is None
    sql, params = session.calls[0]
    assert "CASE WHEN COUNT(*) FILTER" in sql
    assert params["seuil"] == SEUIL_ND_ZONE


async def test_zone_resolve_binds_all_untrusted_values() -> None:
    attack = "44' OR 1=1 --"
    session = _CaptureSession(_Result(scalar=0), _Result(rows=[]))

    await zone_resolve_dvf(
        session,  # type: ignore[arg-type]
        zone_type="commune",
        code=attack,
        surface_min=12.5,
        surface_max=250.0,
        prix_min=1000.0,
        prix_max=900000.0,
        type_local="Maison",
        date_min="2024-01-01' OR true --",
        date_max="2024-12-31",
    )

    for sql, params in session.calls:
        assert attack not in sql
        # A commune is read on a bound list of codes (PLM arrondissements).
        assert params["codes"] == [attack]
        assert params["surface_min"] == 12.5
        assert params["classe_mutation_filtre"] == "residentiel_maison"
        assert "OR true" not in sql


async def test_zone_stats_binds_zone_code_instead_of_interpolating_it() -> None:
    attack = "44' UNION SELECT pg_sleep(10) --"
    session = _CaptureSession(_Result(mapping=_stats_row()))

    await zone_stats_dvf(session, "departement", attack)  # type: ignore[arg-type]

    sql, params = session.calls[0]
    assert attack not in sql
    assert params["code"] == attack
    assert "code_departement = :code" in sql


async def test_zone_resolve_caps_deep_offset() -> None:
    session = _CaptureSession(_Result(scalar=0), _Result(rows=[]))

    await zone_resolve_dvf(
        session,  # type: ignore[arg-type]
        zone_type="departement",
        code="44",
        page=10**9,
        per_page=100,
    )

    assert all(params["offset"] == MAX_OFFSET for _, params in session.calls)


async def test_zone_stats_keeps_house_and_apartment_effectifs_separate() -> None:
    session = _CaptureSession(_Result(mapping=_stats_row()))

    result = await zone_stats_dvf(session, "departement", "85")  # type: ignore[arg-type]

    assert result["nb_transactions_maison"] == 7
    assert result["nb_transactions_appartement"] == 5
    sql, _ = session.calls[0]
    assert "residentiel_maison" in sql
    assert "residentiel_appartement" in sql


@pytest.mark.parametrize(
    ("zone_type", "code"), [("commune", "44109"), ("departement", "44")]
)
async def test_zone_resolve_lists_and_counts_the_positioned_mutations_only(
    zone_type: str, code: str
) -> None:
    """Phase 04.4, D-03: the rows and the total of a zone list."""
    session = _CaptureSession(_Result(scalar=0), _Result(rows=[]))

    await zone_resolve_dvf(session, zone_type=zone_type, code=code)  # type: ignore[arg-type]

    assert len(session.calls) == 2
    assert all("position IS NOT NULL" in sql for sql, _ in session.calls)


async def test_zone_stats_count_the_mutations_of_unknown_position() -> None:
    """Phase 04.4, D-03: a statistic reads no position."""
    session = _CaptureSession(_Result(mapping=_stats_row()))

    await zone_stats_dvf(session, "commune", "44109")  # type: ignore[arg-type]

    sql, _ = session.calls[0]
    assert "position" not in sql


async def test_zone_stats_never_count_a_price_of_zero() -> None:
    """A5a-01: like every other DVF projection, a 0 euro amount is no price."""
    session = _CaptureSession(_Result(mapping=_stats_row()))

    await zone_stats_dvf(session, "departement", "85")  # type: ignore[arg-type]

    sql, _ = session.calls[0]
    assert "AND valeur_fonciere > 0" in sql
    assert "valeur_fonciere IS NOT NULL" not in sql
