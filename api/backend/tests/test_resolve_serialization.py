"""Regression tests for resolve dataset serialization.

Postgres ``numeric`` columns come back as ``Decimal`` from asyncpg and land in
the resolve ``datasets`` dict (``dict[str, Any]``). Pydantic v2 serialized them
as JSON *strings* ("150000.00"), which the frontend numeric readers dropped →
every numeric field on the Fiche Bien rendered "Donnée non disponible". A
``field_serializer`` now coerces Decimal → float so they serialize as numbers.
"""

import json
from decimal import Decimal

import pytest

from app.resolve.schemas import ResultatMultiResolution

pytestmark = pytest.mark.unit


def test_datasets_decimal_serialized_as_json_number() -> None:
    """Decimal-typed dataset columns must serialize as JSON numbers, not strings."""
    m = ResultatMultiResolution(
        type_detecte="gps",
        latitude=47.0,
        longitude=-1.5,
        datasets={
            "risques": [
                {
                    "indice_exposition": Decimal("1.50"),
                    "distance_m": Decimal("2380"),
                    "nombre_aleas": 3,  # integer stays an integer
                }
            ],
        },
    )

    payload = json.loads(m.model_dump_json())
    risques = payload["datasets"]["risques"][0]

    # Regression: Decimal values were emitted as JSON strings before the fix.
    assert isinstance(risques["indice_exposition"], (int, float))
    assert isinstance(risques["distance_m"], (int, float))
    # Values preserved through the float coercion.
    assert risques["indice_exposition"] == 1.5
    assert risques["distance_m"] == 2380.0
    # Non-Decimal columns are untouched.
    assert risques["nombre_aleas"] == 3


def test_datasets_none_serializes_to_null() -> None:
    """A resolution without datasets still serializes cleanly (datasets = null)."""
    m = ResultatMultiResolution(type_detecte="gps", latitude=47.0, longitude=-1.5)
    payload = json.loads(m.model_dump_json())
    assert payload["datasets"] is None
