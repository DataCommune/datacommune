"""Volume floor follows the instance scope (DQ-UNIV-01, D-03, D-21, D-22).

Anti-circularity contract (D-03): the volume floor is computed from the
instance scope the caller read from the database — an intentional operator
declaration — NEVER from count(distinct code_departement) observed in the
table. A truncated ETL must not silently lower its own floor.
"""

import inspect

import pytest

from app.data_quality.profiles import PROFILES, _compute_volume_floor
from app.etl.base import DEPARTEMENTS
from app.scope import InstanceScope

pytestmark = pytest.mark.unit


def _scope(*departements: str) -> InstanceScope:
    return InstanceScope(frozenset({"equipements"}), tuple(departements))


FULL_FRANCE = _scope(*DEPARTEMENTS)


def test_volume_floor_uses_declared_scope_not_observed_dept() -> None:
    """A 2-dept scope scales full_france_min proportionally, whatever is observed."""
    full_france_min = 2_500_000
    expected_floor = int(full_france_min * 2 / len(DEPARTEMENTS))

    result = _compute_volume_floor(full_france_min, False, _scope("44", "49"), "x")

    assert result == expected_floor, (
        f"Expected floor={expected_floor} for 2-dept scope, got {result}. "
        "Floor must come from the declared scope, not observed row count."
    )


def test_volume_floor_full_france_when_scope_covers_every_departement() -> None:
    full_france_min = 2_500_000
    for is_national in (False, True):
        assert (
            _compute_volume_floor(
                full_france_min, is_national, FULL_FRANCE, "equipements"
            )
            == full_france_min
        )


def test_national_file_floor_is_the_sum_of_departement_floors() -> None:
    """is_national=True: the file is filtered to the hosted departements (D-22).

    Each hosted departement gets 80 % of its measured reference count, or
    the proportional share times the dataset margin without a reference.
    """
    full_france_min = 2_500_000
    # equipements reference: 44 = 49 211, 85 = 26 092 rows.
    result = _compute_volume_floor(
        full_france_min, True, _scope("44", "85"), "equipements"
    )
    assert result == int(49_211 * 0.8) + int(26_092 * 0.8)
    # 01: no reference -> 2 500 000 / 102 x 0.5.
    result = _compute_volume_floor(full_france_min, True, _scope("01"), "equipements")
    assert result == int(full_france_min / len(DEPARTEMENTS) * 0.5)
    assert 0 < result < full_france_min


def test_volume_floor_dept_scoped_divided() -> None:
    """is_national=False + 1-dept scope: floor divided proportionally (D-08 P29)."""
    full_france_min = 2_500_000
    expected_floor = int(full_france_min * 1 / len(DEPARTEMENTS))

    result = _compute_volume_floor(full_france_min, False, _scope("85"), "x")

    assert result == expected_floor, (
        "is_national=False with 1-dept scope must divide: "
        f"expected {expected_floor}, got {result}."
    )


def test_empty_scope_hosts_nothing_so_no_floor() -> None:
    assert _compute_volume_floor(2_500_000, False, _scope(), "x") == 0


def test_volume_floor_never_reads_observed_dept_count_nor_configuration() -> None:
    """Signature: (full_france_min, is_national, scope, dataset) — no conn/table.

    The scope is a parameter bound by the caller (D-21): no hidden default,
    no environment setting read by the profiles module.
    """
    import app.data_quality.profiles as profiles

    params = inspect.signature(_compute_volume_floor).parameters
    assert list(params) == ["full_france_min", "is_national", "scope", "dataset"]
    assert all(param.default is inspect.Parameter.empty for param in params.values())
    assert not hasattr(profiles, "settings")


def test_44_85_partitioned_profile_floors_stay_meaningful() -> None:
    """44+85 calibrated profiles must scale back to their observed scoped floors."""
    expected = {
        "ban": 800_000,
        "batiments": 1_800_000,
        "cadastre": 2_200_000,
        # Buildings since plan 04.1-16 (one row per ARCEP building).
        "connectivite": 800_000,
        "dpe": 350_000,
        "dvf": 550_000,
    }

    for dataset, floor in expected.items():
        volume = PROFILES[dataset].volume
        assert volume is not None
        result = _compute_volume_floor(
            volume.full_france_min, volume.is_national, _scope("44", "85"), dataset
        )
        assert result == floor, (
            f"{dataset}: expected 44+85 effective floor={floor}, got {result}"
        )
