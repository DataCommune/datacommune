"""Local result storage boundaries without a database."""

import pytest

from app.commune.indicator_results import ResultStoreRefused, read_build


@pytest.mark.parametrize("build_id", [None, True, 0, -1, "1"])
def test_build_identifier_is_explicit_positive_integer(build_id):
    with pytest.raises(ResultStoreRefused):
        read_build(None, build_id=build_id, context=None)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), object()])
def test_canonical_result_rejects_nonfinite_or_opaque_values(value):
    from app.commune.indicator_results import _canonical

    with pytest.raises(ResultStoreRefused):
        _canonical({"value": value})


def test_canonical_hash_orders_keys_preserves_types_and_bounds_bytes():
    from app.commune.indicator_results import _canonical, _digest

    assert _digest({"b": 1, "a": "é"}) == _digest({"a": "é", "b": 1})
    assert _digest({"a": True}) != _digest({"a": 1})
    assert _digest({"a": None}) != _digest({"a": 0})
    with pytest.raises(ResultStoreRefused):
        _canonical({"a": "é"}, limit=8)


def test_publication_scope_is_refused_before_sql():
    from unittest.mock import Mock

    from app.commune.indicator_context import IndicatorBuildContext
    from app.commune.indicator_projector import ProjectionRequest
    from app.commune.indicator_results import build_and_store
    from tests.test_commune_indicator_projector import NOW

    scope = "publication"
    context = IndicatorBuildContext(
        (), "a" * 64, "synthetic", (), NOW, scope, None, (), None, None
    )
    connection = Mock()
    with pytest.raises(ResultStoreRefused):
        build_and_store(
            connection, context=context, request=ProjectionRequest(NOW, scope)
        )
    connection.assert_not_called()
    assert connection.method_calls == []


def test_read_result_contains_no_private_binding_and_returns_independent_copies():
    from app.commune.indicator_results import PublicResultBuild, _canonical

    data = [
        {
            "code_insee": "44001",
            "indicateurs_dpe": {
                "etat": "not_qualified",
                "motif": "source_generation_unqualified",
            },
        }
    ]
    result = PublicResultBuild(_canonical(data))
    output = result.public_dump()
    output[0]["code_insee"] = "changed"
    assert result.public_dump() == data
    assert not hasattr(result, "bindings") and not hasattr(result, "header")
