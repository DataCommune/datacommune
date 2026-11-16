"""Regressions for bounded golden replay requests to ``/v1/resolve``."""

from typing import Any, ClassVar

import pytest

from app.config import settings
from app.resolve.multi import MAX_DATASETS_PAR_REQUETE
from scripts.verify_golden_cases import (
    _ADDRESS_RESOLVE_DATASET_BATCHES,
    _LIVE_REPLAY_CASE_INTERVAL_SECONDS,
    GoldenSecurityError,
    _api_payload,
    _merge_resolve_batch,
)

_PARCEL = "synthetic-parcel"
_BAN_ID = "synthetic-ban"
_INSEE = "44991"


class _Response:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    @staticmethod
    def raise_for_status() -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"donnees": [self._result]}


class _Client:
    calls: ClassVar[list[tuple[str, dict[str, str]]]] = []

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = iter(responses)

    def get(self, path: str, params: dict[str, str]) -> _Response:
        self.calls.append((path, params))
        return _Response(next(self._responses))


def _identity(**overrides: Any) -> dict[str, Any]:
    result = {
        "id_parcelle": None,
        "code_insee": _INSEE,
        "ban_id": _BAN_ID,
    }
    result.update(overrides)
    return result


def test_address_replay_batches_cover_contract_without_exceeding_api_cap() -> None:
    flattened = [name for batch in _ADDRESS_RESOLVE_DATASET_BATCHES for name in batch]

    assert all(
        len(batch) <= MAX_DATASETS_PAR_REQUETE
        for batch in _ADDRESS_RESOLVE_DATASET_BATCHES
    )
    assert len(flattened) == len(set(flattened))
    assert set(flattened) == {
        "cadastre",
        "dvf",
        "dpe",
        "risques",
        "urbanisme",
        "commune",
    }


def test_live_replay_interval_respects_worst_case_ip_rate_limit() -> None:
    case_starts_per_minute = 1 + (59 // _LIVE_REPLAY_CASE_INTERVAL_SECONDS)
    startup_requests = 2
    max_requests_per_address = 4

    assert (
        startup_requests + case_starts_per_minute * max_requests_per_address
        <= settings.rate_limit_per_ip
    )


def test_address_replay_merges_bounded_responses_after_identity_checks() -> None:
    _Client.calls = []
    first = _identity(
        datasets={
            name: {"state": "synthetic"} for name in _ADDRESS_RESOLVE_DATASET_BATCHES[0]
        },
        dataset_states={"dpe": {"presence": "value"}},
        erreurs=[{"dataset": "risques"}],
        dpe_inventory={"state": "available"},
    )
    first["datasets"]["cadastre"] = {"id_parcelle": _PARCEL}
    second = _identity(
        datasets={"commune": {"state": "synthetic"}},
        dataset_states={"commune": {"presence": "value"}},
        erreurs=[{"dataset": "commune"}],
    )
    client = _Client([first, second])

    payload = _api_payload(
        client,  # type: ignore[arg-type]
        {"type": "address"},
        {"query": "12 rue synthetique 44991", "id_parcelle": _PARCEL},
        resolve_logging_redacted=True,
    )

    assert [call[0] for call in _Client.calls] == ["/v1/resolve", "/v1/resolve"]
    assert [len(call[1]["datasets"].split(",")) for call in _Client.calls] == [5, 1]
    assert set(payload["datasets"]) == {
        "cadastre",
        "dvf",
        "dpe",
        "risques",
        "urbanisme",
        "commune",
    }
    assert set(payload["dataset_states"]) == {"dpe", "commune"}
    assert payload["erreurs"] == [
        {"dataset": "risques"},
        {"dataset": "commune"},
    ]
    assert payload["dpe_inventory"] == {"state": "available"}


def test_address_replay_rejects_identity_drift_between_batches() -> None:
    _Client.calls = []
    client = _Client(
        [
            _identity(datasets={"cadastre": {"id_parcelle": _PARCEL}}),
            _identity(code_insee="85991", datasets={"commune": {}}),
        ]
    )

    with pytest.raises(GoldenSecurityError, match="batch identity mismatch"):
        _api_payload(
            client,  # type: ignore[arg-type]
            {"type": "address"},
            {"query": "12 rue synthetique 44991", "id_parcelle": _PARCEL},
            resolve_logging_redacted=True,
        )


def test_address_replay_rejects_overlapping_batch_payloads() -> None:
    merged = _identity(datasets={"cadastre": {"state": "first"}})
    batch = _identity(datasets={"cadastre": {"state": "second"}})

    with pytest.raises(GoldenSecurityError, match="datasets overlaps"):
        _merge_resolve_batch(merged, batch)
