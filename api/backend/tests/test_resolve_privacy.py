"""Anti-leak gate for the consolidated resolve telemetry (Phase 45-01, PRIV-01).

Consolidates the e5a662d5/WR-01 redaction into a FORMAL allowlist contract
(``app.resolve.telemetry``) reusable by the Phase 46 probe (D-13):

  - every resolve event carries ONLY allowlisted fields (closed set);
  - ``correlation_id`` is random per request, never derived from the input,
    and stays correlatable across router and multi events of one request;
  - dataset names are validated against the closed vocabulary before logging;
  - exceptions map to a bounded ``error_code`` (class name), never str(exc).

Sentinels are injected into q, lat/lon, BAN params and exception messages,
then message, args, extra fields and tracebacks of EVERY captured record are
scanned — zero occurrence allowed, on success as on error paths (D-11/D-12).

Scope note (2026-08-07): this gate also covers ``app.logements``. The PRIV-01
contract bans "parcel identifiers" from logs by construction, but the module
was written after the gate and logged ``id_parcelle`` and the BAN key in
clear on two diagnostic paths -- a breach the gate could not see because it
scanned neither that module nor those two sentinel shapes.
"""

import logging
import re
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response
from sqlalchemy import text

from app.ban.queries import _detect_city, _execute_query
from app.common.exceptions import ErreurApiError
from app.dpe import inventory_service
from app.dpe.queries import (
    DpeInventoryAggregate,
    DpeInventoryRecord,
    DpeInventoryTarget,
    DpeRelationGenerations,
)
from app.logements.queries import cles_ban_batiment_rnb
from app.logements.router import get_logements
from app.resolve import telemetry
from app.resolve.multi import DATASETS_DISPONIBLES, _query_one_dataset
from app.resolve.router import resolve
from app.resolve.schemas import ResultatResolution
from tests.scope_doubles import FULL_SCOPE, route_hosting

pytestmark = pytest.mark.asyncio

# --- Sensitive sentinels (synthetic, easy to detect — D-12) ------------------
_SENTINEL_ADDR = "27 impasse sentinelle-privee 44000 VillePrivee"
_SENTINEL_LAT = 47.998877
_SENTINEL_LON = -1.887766
_SENTINEL_PARAM = "sentinelle-parametre-ban"
_SENTINEL_EXC = "upstream leaked sentinelle-exception secret"
# Parcel id and BAN key are BOTH banned from logs by the PRIV-01 contract
# ("parcel identifiers", "BAN query/params"). Shapes are realistic so the
# production call sites accept them unchanged: 14 chars [0-9A-Z] for the
# parcel, INSEE_FANTOIR_NUMERO for the key.
_SENTINEL_PARCELLE = "SENTINELLEPARC"
_SENTINEL_CLE_BAN = "99999_SENTI_00042"
_SENTINEL_DPE = "SENTINELLE-ADEME-PRIVEE-58108"
_SENTINEL_DPE_REPLACE = "SENTINELLE-ADEME-REMPLACEE-58108"
_SENTINELS = (
    "sentinelle-privee",
    "VillePrivee",
    str(_SENTINEL_LAT),
    str(_SENTINEL_LON),
    _SENTINEL_PARAM,
    "sentinelle-exception",
    _SENTINEL_PARCELLE,
    _SENTINEL_CLE_BAN,
    _SENTINEL_DPE,
    _SENTINEL_DPE_REPLACE,
)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")

# Standard LogRecord attributes: anything else on a record is a custom field.
_STANDARD_ATTRS = set(
    logging.LogRecord("x", logging.INFO, "p", 0, "m", None, None).__dict__
) | {"message", "asctime", "taskName"}


class _InventoryTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _InventorySession:
    async def __aenter__(self) -> "_InventorySession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> _InventoryTransaction:
        return _InventoryTransaction()

    async def execute(self, _statement: object) -> None:
        return None


class _InventorySessionFactory:
    def __call__(self) -> _InventorySession:
        return _InventorySession()


def _custom_fields(record: logging.LogRecord) -> dict:
    return {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}


def _assert_no_sentinel(records: list[logging.LogRecord]) -> None:
    """Scan message, args, custom fields and traceback of every record."""
    for record in records:
        blob = " ".join(
            [
                record.getMessage(),
                repr(record.args),
                repr(_custom_fields(record)),
                repr(record.exc_info),
                repr(record.exc_text),
            ]
        )
        for sentinel in _SENTINELS:
            assert sentinel not in blob, (
                f"sentinel {sentinel!r} leaked in record {record.name}:"
                f"{record.getMessage()!r}"
            )


# ---------------------------------------------------------------------------
# Allowlist contract (formal, Phase 46 reuse)
# ---------------------------------------------------------------------------


def test_emit_rejects_non_allowlisted_fields() -> None:
    """The emitter fails closed on any field outside the allowlist."""
    logger = logging.getLogger("test.telemetry.allowlist")
    with pytest.raises(ValueError):
        telemetry.emit_resolve_event(
            logger,
            "resolve_request",
            correlation_id=telemetry.new_correlation_id(),
            q="should-never-be-loggable",  # type: ignore[call-arg]
        )


def test_sanitize_datasets_keeps_only_the_closed_vocabulary() -> None:
    """User-controlled dataset names never reach logs unvalidated."""
    validated = telemetry.sanitize_datasets(
        "dvf, sentinel-dataset-x,dpe,DVF,zonage-abc", DATASETS_DISPONIBLES
    )
    assert validated == ["dvf", "dpe", "zonage_abc"]
    assert telemetry.sanitize_datasets(None, DATASETS_DISPONIBLES) == []


def test_correlation_id_is_random_and_input_independent() -> None:
    """Same input twice -> two different opaque ids (no derivation)."""
    first = telemetry.new_correlation_id()
    second = telemetry.new_correlation_id()
    assert _HEX32.match(first) and _HEX32.match(second)
    assert first != second


# ---------------------------------------------------------------------------
# Router paths (success + failure) — full record inspection
# ---------------------------------------------------------------------------


async def test_success_path_logs_only_allowlisted_fields_and_no_sentinel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nominal resolve: events carry correlation_id/input_type/datasets/
    outcome/duration, and none of the injected sentinels."""
    caplog.set_level(logging.DEBUG)
    with patch("app.resolve.router._resolve_adresse", AsyncMock(return_value=[])):
        await resolve(
            db=AsyncMock(),
            scope=FULL_SCOPE,
            response=Response(),
            q=_SENTINEL_ADDR,
            type="adresse",
            datasets="dvf,sentinel-dataset-x",
        )

    _assert_no_sentinel(caplog.records)

    events = {r.getMessage(): r for r in caplog.records}
    assert "resolve_request" in events, "start event missing"
    assert "resolve_completed" in events, "completion event missing"

    request_fields = _custom_fields(events["resolve_request"])
    assert set(request_fields) <= telemetry.ALLOWED_EVENT_FIELDS, request_fields
    assert _HEX32.match(request_fields["correlation_id"]), "opaque correlation id"
    assert request_fields["input_type"] == "adresse"
    assert request_fields["datasets"] == ["dvf"], "validated names only"

    completed_fields = _custom_fields(events["resolve_completed"])
    assert set(completed_fields) <= telemetry.ALLOWED_EVENT_FIELDS, completed_fields
    assert completed_fields["outcome"] in {
        "exact",
        "ambiguous",
        "not_reported",
        "out_of_coverage",
    }
    assert isinstance(completed_fields["duration_ms"], int)
    assert completed_fields["correlation_id"] == request_fields["correlation_id"], (
        "one request = one correlatable id"
    )


async def test_http_boundary_and_middleware_keep_only_the_request_path(
    client,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real enriched resolve response never exposes source data in its logs."""
    caplog.set_level(logging.INFO)
    target = DpeInventoryTarget(
        address_id=58_108,
        department="99",
        ban_id=_SENTINEL_CLE_BAN,
    )
    generations = DpeRelationGenerations(addresses=58_101, diagnostics=58_102)
    source_record = DpeInventoryRecord(
        document_id=58_108,
        dpe_number=_SENTINEL_DPE,
        replaced_dpe_number=_SENTINEL_DPE_REPLACE,
        type_batiment="appartement",
        surface_habitable=Decimal("61.40"),
        etiquette_dpe="D",
        etiquette_ges="C",
        date_etablissement_dpe=date(2023, 5, 1),
        date_fin_validite_dpe=date(2033, 5, 1),
        annee_construction=1988,
        periode_construction="1975-1988",
        type_installation_chauffage="individuel",
        type_energie_principale_chauffage="electricite",
        qualite_isolation_enveloppe="moyenne",
        conso_5_usages_ep=Decimal("12000.20"),
        emission_ges_5_usages=Decimal("500.30"),
        cout_total_5_usages=Decimal("900.40"),
    )
    lock = AsyncMock(return_value=generations)
    # Admit only this synthetic inventory; execute the real privacy projection.
    publication_admission = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.common.publication_readiness.guard_snapshot_publication",
        publication_admission,
    )
    target_by_ban = AsyncMock(return_value=target)
    page = AsyncMock(return_value=([source_record], False))
    aggregate = AsyncMock(
        return_value=DpeInventoryAggregate(
            total=1,
            apartment_document_count=1,
            distribution={
                "A": 0,
                "B": 0,
                "C": 0,
                "D": 1,
                "E": 0,
                "F": 0,
                "G": 0,
                "unknown": 0,
            },
        )
    )
    monkeypatch.setattr(inventory_service.queries, "lock_dpe_inventory_relations", lock)
    monkeypatch.setattr(
        inventory_service.queries,
        "dpe_inventory_target_by_ban",
        target_by_ban,
    )
    monkeypatch.setattr(inventory_service.queries, "dpe_inventory_page", page)
    monkeypatch.setattr(inventory_service.queries, "dpe_inventory_aggregate", aggregate)
    factory = _InventorySessionFactory()
    canonical = ResultatResolution(
        type_detecte="adresse",
        latitude=47.2184,
        longitude=-1.5536,
        adresse="1 rue Publique, 99999 Commune-Test",
        id_parcelle=_SENTINEL_PARCELLE,
        commune="Commune-Test",
        code_postal="99999",
        code_insee="99999",
        score=0.99,
        ban_id=_SENTINEL_CLE_BAN,
    )

    with (
        patch(
            "app.resolve.router._resolve_adresse",
            AsyncMock(return_value=[canonical]),
        ),
        patch("app.resolve.router.async_session", factory),
        patch(
            "app.resolve.multi.load_dpe_inventory_result",
            wraps=inventory_service.load_dpe_inventory_result,
        ) as inventory_loader,
        patch(
            "app.resolve.multi.cles_ban_batiment_rnb",
            AsyncMock(return_value=()),
        ),
        patch("app.resolve.router.get_provenance_facts", AsyncMock(return_value={})),
    ):
        response = await client.get(
            "/v1/resolve",
            params={"q": _SENTINEL_ADDR, "type": "adresse", "datasets": "dpe"},
        )

    assert response.status_code == 200
    result = response.json()["donnees"][0]
    assert result["ban_id"] == _SENTINEL_CLE_BAN
    assert result["dpe_inventory"]["state"] == "ready"
    assert result["dpe_inventory"]["total"] == 1
    assert len(result["dpe_inventory"]["items"]) == 1
    assert result["dataset_states"]["dpe"]["presence"] == "value"
    inventory_loader.assert_awaited_once()
    publication_admission.assert_awaited_once()
    assert inventory_loader.await_args.kwargs["ban_id"] == _SENTINEL_CLE_BAN
    page.assert_awaited_once()
    assert _SENTINEL_ADDR not in response.text
    assert _SENTINEL_DPE not in response.text
    assert _SENTINEL_DPE_REPLACE not in response.text
    application_records = [
        record for record in caplog.records if record.name.startswith("app.")
    ]
    _assert_no_sentinel(application_records)
    assert {record.getMessage() for record in application_records} >= {
        "resolve_request",
        "resolve_completed",
        "http_request",
    }
    request_records = [
        record
        for record in application_records
        if record.getMessage() == "http_request"
    ]
    assert request_records
    assert {record.__dict__.get("path") for record in request_records} == {
        "/v1/resolve"
    }


async def test_two_requests_get_two_correlation_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The id depends on the request instance, never on the input value."""
    caplog.set_level(logging.INFO, logger="app.resolve.router")
    with patch("app.resolve.router._resolve_adresse", AsyncMock(return_value=[])):
        await resolve(
            db=AsyncMock(),
            scope=FULL_SCOPE,
            response=Response(),
            q=_SENTINEL_ADDR,
            type="adresse",
            datasets=None,
        )
        await resolve(
            db=AsyncMock(),
            scope=FULL_SCOPE,
            response=Response(),
            q=_SENTINEL_ADDR,
            type="adresse",
            datasets=None,
        )

    ids = [
        _custom_fields(r)["correlation_id"]
        for r in caplog.records
        if r.getMessage() == "resolve_request"
    ]
    assert len(ids) == 2 and ids[0] != ids[1], "same input must not reuse an id"


async def test_error_path_logs_bounded_code_without_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing resolver logs resolve_failed with a bounded error_code —
    no str(exc), no logger.exception, no traceback on this path."""
    caplog.set_level(logging.DEBUG)
    failure = RuntimeError(_SENTINEL_EXC + " " + _SENTINEL_ADDR)

    with (
        patch("app.resolve.router._resolve_adresse", AsyncMock(side_effect=failure)),
        pytest.raises(ErreurApiError),
    ):
        await resolve(
            db=AsyncMock(),
            scope=FULL_SCOPE,
            response=Response(),
            q=_SENTINEL_ADDR,
            type="adresse",
            datasets=None,
        )

    _assert_no_sentinel(caplog.records)
    failed = [r for r in caplog.records if r.getMessage() == "resolve_failed"]
    assert failed, "resolve_failed event missing"
    fields = _custom_fields(failed[0])
    assert set(fields) <= telemetry.ALLOWED_EVENT_FIELDS, fields
    assert fields["error_code"] == "RuntimeError", "bounded class name only"
    assert failed[0].exc_info is None, "no traceback on the resolve path"


# ---------------------------------------------------------------------------
# Multi-dataset path — correlation propagation without coordinates
# ---------------------------------------------------------------------------


async def test_multi_failure_carries_correlation_id_and_no_location(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per-dataset degradation events stay correlatable and location-free."""
    caplog.set_level(logging.DEBUG, logger="app.resolve.multi")
    correlation_id = telemetry.new_correlation_id()
    failure = RuntimeError(f"boom ({_SENTINEL_LAT}, {_SENTINEL_LON})")

    with patch("app.resolve.multi._dispatch_http", AsyncMock(side_effect=failure)):
        await _query_one_dataset(
            "rnb",
            AsyncMock(),
            _SENTINEL_LAT,
            _SENTINEL_LON,
            None,
            correlation_id=correlation_id,
        )

    _assert_no_sentinel(caplog.records)
    failed = [r for r in caplog.records if r.getMessage() == "dataset_query_failed"]
    assert failed, "dataset_query_failed event missing"
    fields = _custom_fields(failed[0])
    assert set(fields) <= telemetry.ALLOWED_EVENT_FIELDS, fields
    assert fields["dataset"] == "rnb"
    assert fields["error_code"] == "RuntimeError"
    assert fields["correlation_id"] == correlation_id, "propagated without q"


async def test_full_request_propagates_one_id_from_router_to_multi(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """router -> multi: the same opaque id links all events of one request."""
    caplog.set_level(logging.DEBUG)
    failure = RuntimeError(_SENTINEL_EXC)

    with (
        patch(
            "app.resolve.router._resolve_gps",
            AsyncMock(
                return_value=[
                    ResultatResolution(
                        type_detecte="gps",
                        latitude=_SENTINEL_LAT,
                        longitude=_SENTINEL_LON,
                    )
                ]
            ),
        ),
        patch("app.resolve.multi._dispatch_http", AsyncMock(side_effect=failure)),
        patch("app.resolve.router.get_provenance_facts", AsyncMock(return_value={})),
    ):
        await resolve(
            db=AsyncMock(),
            scope=FULL_SCOPE,
            response=Response(),
            q=f"{_SENTINEL_LAT},{_SENTINEL_LON}",
            type="gps",
            datasets="rnb",
        )

    _assert_no_sentinel(caplog.records)
    by_event: dict[str, logging.LogRecord] = {}
    for record in caplog.records:
        by_event.setdefault(record.getMessage(), record)
    request_id = _custom_fields(by_event["resolve_request"])["correlation_id"]
    multi_id = _custom_fields(by_event["dataset_query_failed"])["correlation_id"]
    assert request_id == multi_id, "router and multi must share the request id"


# ---------------------------------------------------------------------------
# BAN paths — bounded codes, no query/params
# ---------------------------------------------------------------------------


class _FailingSession:
    def begin_nested(self) -> "_FailingSession":
        return self

    async def __aenter__(self) -> "_FailingSession":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError(f"db failure carrying {_SENTINEL_PARAM}")


async def test_ban_failures_emit_bounded_codes_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """BAN degradation events carry error_code only — never query or params."""
    caplog.set_level(logging.DEBUG, logger="app.ban.queries")

    assert await _detect_city(_FailingSession(), _SENTINEL_ADDR) is None
    assert (
        await _execute_query(
            _FailingSession(),
            text("SELECT 1"),
            {"query": _SENTINEL_PARAM, "lat": _SENTINEL_LAT},
            "",
        )
        == []
    )

    _assert_no_sentinel(caplog.records)
    events = {r.getMessage() for r in caplog.records}
    assert "city_detection_failed" in events
    assert "geocode_query_failed" in events
    for record in caplog.records:
        fields = _custom_fields(record)
        assert set(fields) <= telemetry.ALLOWED_EVENT_FIELDS, fields
        assert fields.get("error_code") == "RuntimeError"


# ---------------------------------------------------------------------------
# Logements paths — diagnostic counters without the identifiers they describe
# ---------------------------------------------------------------------------


async def test_logements_inventory_failure_log_drops_private_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Inventory degradation logs a constant event, never inputs or errors."""
    caplog.set_level(logging.DEBUG, logger="app.logements.router")
    response = Response()

    with (
        patch(
            "app.logements.router.load_dpe_inventory_result",
            AsyncMock(side_effect=RuntimeError(_SENTINEL_EXC)),
        ),
        patch(
            "app.logements.router.cles_ban_batiment_rnb",
            AsyncMock(return_value=()),
        ),
        patch(
            "app.logements.router.copropriete_par_parcelle",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.logements.router.dvf_appartements_par_parcelle",
            AsyncMock(return_value=[]),
        ),
    ):
        reponse = await get_logements(
            hosting=route_hosting("dpe", "dvf", "copropriete", "rnb", "ban"),
            db=AsyncMock(),
            response=response,
            id_parcelle=_SENTINEL_PARCELLE,
            ban_id=_SENTINEL_CLE_BAN,
            adresse=None,
            limite=120,
        )

    assert reponse.donnees.dpe_inventory.state == "unavailable"
    assert reponse.donnees.diagnostics_exclus_adresse == 0
    assert [
        r for r in caplog.records if "inventaire DPE indisponible" in r.getMessage()
    ], "the degradation trace itself must survive the redaction"
    assert response.headers["cache-control"] == "no-store"
    _assert_no_sentinel(caplog.records)


async def test_logements_ceiling_log_drops_the_parcel_identifier(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The width-ceiling trace names the width that decided, not the parcel."""
    caplog.set_level(logging.DEBUG, logger="app.logements.queries")
    monkeypatch.setattr(
        "app.logements.queries.publication_actee", AsyncMock(return_value=True)
    )
    session = AsyncMock()
    # Width 16 > PLAFOND_LARGEUR_PORTEUR (9): the ceiling fires and logs.
    session.execute = AsyncMock(return_value=[("99999_SENTI_00007", 16)])

    cles = await cles_ban_batiment_rnb(session, _SENTINEL_PARCELLE, _SENTINEL_CLE_BAN)

    assert cles == (), "width ceiling must have cancelled the widening"
    assert [r for r in caplog.records if "plafond" in r.getMessage()], (
        "the diagnostic trace itself must survive the redaction"
    )
    _assert_no_sentinel(caplog.records)
