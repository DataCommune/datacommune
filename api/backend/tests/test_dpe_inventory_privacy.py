"""End-to-end privacy boundaries for the DPE address inventory."""

from __future__ import annotations

import base64
import json
import logging
import zlib
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import itsdangerous.timed as dangerous_timed
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import check_quota_hybrid
from app.common.exceptions import ErreurApiError
from app.dpe import inventory_service
from app.dpe.queries import (
    DpeInventoryAggregate,
    DpeInventoryTarget,
    DpeRelationGenerations,
)
from app.dpe.references import issue_context, issue_document, verify_context
from app.main import app

pytestmark = pytest.mark.smoke

_BAN = "44991_SENTINELLE_BAN_00001"
_FOREIGN_BAN = "44991_SENTINELLE_BAN_99999"
_DPE = "SENTINELLE-ADEME-PRIVEE-0001"
_REPLACED = "SENTINELLE-REMPLACEMENT-PRIVE-0000"
_ADDRESS = "27 impasse sentinelle privée"
_COMPLEMENT = "Bâtiment secret, étage 7, porte gauche"
_FLOOR = "938571"
_DOOR = "PORTE-SENTINELLE-PRIVEE-58108"
_LATITUDE = "47.998877"
_LONGITUDE = "-1.887766"
_IMPORT = "SENTINELLE-IMPORT-PRIVE-582"
_SOURCE_VERSION = "abcdef1234567890" * 4
_PRIVATE_VALUES = (
    _BAN,
    _FOREIGN_BAN,
    _DPE,
    _REPLACED,
    _ADDRESS,
    _COMPLEMENT,
    _FLOOR,
    _DOOR,
    _LATITUDE,
    _LONGITUDE,
    _IMPORT,
    _SOURCE_VERSION,
)
_GENERATIONS = DpeRelationGenerations(addresses=81_001, diagnostics=81_002)
_TARGET = DpeInventoryTarget(address_id=7001, department="44", ban_id=_BAN)


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(self, _statement: object) -> None:
        return None


class _SessionFactory:
    def __call__(self) -> _Session:
        return _Session()


def _record(document_id: int, dpe_number: str, energy_class: str) -> SimpleNamespace:
    """Private query row with every forbidden source family injected."""
    return SimpleNamespace(
        document_id=document_id,
        dpe_number=dpe_number,
        replaced_dpe_number=_REPLACED,
        identifiant_ban=_BAN,
        adresse_brute=_ADDRESS,
        complement_adresse_logement=_COMPLEMENT,
        numero_etage_appartement=int(_FLOOR),
        porte_appartement=_DOOR,
        longitude=float(_LONGITUDE),
        latitude=float(_LATITUDE),
        type_batiment="appartement",
        surface_habitable=Decimal("61.40"),
        etiquette_dpe=energy_class,
        etiquette_ges="C",
        date_etablissement_dpe=date(2023, 5, 1),
        date_fin_validite_dpe=date(2033, 5, 1),
        annee_construction=1988,
        periode_construction="1975-1988",
        type_installation_chauffage="individuel",
        type_energie_principale_chauffage="électricité",
        qualite_isolation_enveloppe="moyenne",
        conso_5_usages_ep=Decimal("12000.20"),
        emission_ges_5_usages=Decimal("500.30"),
        cout_total_5_usages=Decimal("900.40"),
        regime_dpe="existant",
        source_observations=[
            {
                "source_slug": "dpe03existant",
                "department": "44",
                "source_version": _SOURCE_VERSION,
                "observed_at": "2026-09-05T12:00:00Z",
                "last_seen_at": "2026-09-05T12:00:00Z",
                "state": "present",
                "import_id": _IMPORT,
            }
        ],
        source_state="present",
        source_snapshot_at=datetime(2026, 9, 5, 12, tzinfo=UTC),
        is_tombstone=False,
        lifecycle_facts={
            "replacement_state": "replaced",
            "graph_issues": [],
            "date_issues": [],
        },
        revision_evidence={
            "status": "unverified",
            "kind": None,
            "revision_date": None,
            "verified_at": None,
            "evidence_sha256": None,
            "official_source": None,
        },
        successor={
            "identifiant_ban": _FOREIGN_BAN,
            "numero_dpe": _REPLACED,
            "adresse": _ADDRESS,
            "latitude": _LATITUDE,
            "longitude": _LONGITUDE,
        },
    )


def _install_source(
    monkeypatch: pytest.MonkeyPatch,
    records: list[SimpleNamespace],
    *,
    target: DpeInventoryTarget | None = _TARGET,
    generations: DpeRelationGenerations = _GENERATIONS,
) -> None:
    async def lock(_session: object) -> DpeRelationGenerations:
        return generations

    # The privacy fixture supplies admitted stock, not an AsyncEngine/SQL journal.
    monkeypatch.setattr(
        "app.common.publication_readiness.guard_snapshot_publication",
        AsyncMock(return_value=None),
    )

    async def by_ban(_session: object, **_kwargs: object):
        return target

    async def by_id(_session: object, **_kwargs: object):
        return target

    async def by_document(
        _session: object, *, document_id: int, **_kwargs: object
    ) -> SimpleNamespace | None:
        return next(
            (record for record in records if record.document_id == document_id),
            None,
        )

    async def page(
        _session: object,
        *,
        limit: int,
        anchor: SimpleNamespace | None,
        **_kwargs: object,
    ) -> tuple[list[SimpleNamespace], bool]:
        offset = 0 if anchor is None else records.index(anchor) + 1
        selected = records[offset : offset + limit]
        return selected, offset + limit < len(records)

    counts = {label: 0 for label in "ABCDEFG"}
    for record in records:
        if not record.is_tombstone:
            counts[record.etiquette_dpe] += 1

    async def aggregate(_session: object, **_kwargs: object) -> DpeInventoryAggregate:
        return DpeInventoryAggregate(
            total=len(records),
            apartment_document_count=sum(not row.is_tombstone for row in records),
            distribution={
                **counts,
                "unknown": sum(row.is_tombstone for row in records),
            },
        )

    monkeypatch.setattr(inventory_service.queries, "lock_dpe_inventory_relations", lock)
    monkeypatch.setattr(
        inventory_service.queries, "dpe_inventory_target_by_ban", by_ban
    )
    monkeypatch.setattr(inventory_service.queries, "dpe_inventory_target_by_id", by_id)
    monkeypatch.setattr(
        inventory_service.queries,
        "dpe_inventory_record_by_id",
        by_document,
    )
    monkeypatch.setattr(inventory_service.queries, "dpe_inventory_page", page)
    monkeypatch.setattr(inventory_service.queries, "dpe_inventory_aggregate", aggregate)


def _decode_unsigned_payload(reference: str) -> dict[str, object]:
    value = reference.rsplit(".", 2)[0]
    compressed = value.startswith(".")
    encoded = value[1:] if compressed else value
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    if compressed:
        raw = zlib.decompress(raw)
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    return payload


def _assert_private_absent(value: object) -> None:
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    for sentinel in _PRIVATE_VALUES:
        assert sentinel not in rendered


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for nested in value.values() for key in _nested_keys(nested)
        }
    if isinstance(value, list):
        return {key for nested in value for key in _nested_keys(nested)}
    return set()


def _records_blob(records: list[logging.LogRecord]) -> str:
    return "\n".join(
        f"{record.getMessage()} {record.args!r} {record.__dict__!r} "
        f"{record.exc_info!r} {record.exc_text!r}"
        for record in records
    )


@pytest.fixture()
async def public_client():
    app.dependency_overrides.clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


async def test_private_source_fields_never_cross_http_or_readable_references(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [_record(101, _DPE, "D"), _record(102, "SECOND-PRIVATE-DPE", "E")]
    _install_source(monkeypatch, records)
    factory = _SessionFactory()
    first = await inventory_service.load_dpe_inventory(
        factory,  # type: ignore[arg-type]
        ban_id=_BAN,
        limit=1,
    )
    assert first.context_ref and first.next_cursor
    monkeypatch.setattr("app.logements.router.async_session", factory)

    inventory_response = await client.get(
        "/v1/bien/dpe/inventaire",
        params={"contexte": first.context_ref, "limite": 1},
    )
    document_response = await client.get(
        "/v1/bien/dpe/document",
        params={
            "contexte": first.context_ref,
            "reference": first.items[0].document_ref,
        },
    )

    assert inventory_response.status_code == document_response.status_code == 200
    _assert_private_absent(inventory_response.json())
    _assert_private_absent(document_response.json())
    for reference in (
        first.context_ref,
        first.items[0].document_ref,
        first.next_cursor,
    ):
        _assert_private_absent(_decode_unsigned_payload(reference))
    forbidden_keys = {
        "source_observations",
        "source_version",
        "import_id",
        "source_snapshot_at",
        "lifecycle_facts",
        "date_issues",
        "revision_evidence",
        "evidence_sha256",
        "official_source",
        "successor",
        "numero_dpe",
        "numero_dpe_remplace",
        "identifiant_ban",
        "adresse",
        "complement_adresse_logement",
        "numero_etage_appartement",
        "porte",
        "latitude",
        "longitude",
    }
    assert forbidden_keys.isdisjoint(_nested_keys(inventory_response.json()))
    assert forbidden_keys.isdisjoint(_nested_keys(document_response.json()))


@pytest.mark.parametrize("malformed", [False, True])
@pytest.mark.parametrize("legacy_observations", [False, True])
async def test_contaminated_tombstone_purges_every_old_field_and_private_fact(
    client,
    monkeypatch,
    caplog,
    malformed,
    legacy_observations,
):
    record = _record(101, _DPE, "F")
    record.is_tombstone = True
    record.source_state = "absent_from_complete_scope"
    record.source_observations[0]["state"] = "absent_from_complete_scope"
    if legacy_observations:
        record.source_observations = []
    record.type_installation_chauffage = "ANCIEN-CHAUFFAGE-RETIRE"
    if malformed:
        record.lifecycle_facts["private-edge"] = _FOREIGN_BAN
    _install_source(monkeypatch, [record])
    factory = _SessionFactory()
    page = await inventory_service.load_dpe_inventory(factory, ban_id=_BAN)
    monkeypatch.setattr("app.logements.router.async_session", factory)
    with caplog.at_level(logging.DEBUG):
        response = await client.get(
            "/v1/bien/dpe/document",
            params={
                "contexte": page.context_ref,
                "reference": page.items[0].document_ref,
            },
        )
        inventory = await client.get(
            "/v1/bien/dpe/inventaire", params={"contexte": page.context_ref}
        )
    assert response.status_code == inventory.status_code == 200
    detail = response.json()
    item = inventory.json()["items"][0]
    fields = (
        "type_batiment",
        "surface_habitable",
        "etiquette_dpe",
        "etiquette_ges",
        "date_etablissement_dpe",
        "date_fin_validite_dpe",
        "annee_construction",
        "periode_construction",
        "type_installation_chauffage",
        "type_energie_principale_chauffage",
        "qualite_isolation_enveloppe",
        "conso_5_usages_ep",
        "emission_ges_5_usages",
        "cout_total_5_usages",
    )
    assert all(detail[field] is None for field in fields)
    assert all(item[field] is None for field in fields if field in item)
    assert detail["validity_by_date"] == "unknown"
    if malformed:
        assert detail["lifecycle"] is item["lifecycle"] is None
    else:
        assert detail["lifecycle"]["detail_availability"] == "source_reconciled"
        assert detail["lifecycle"]["validity"] == "unknown"
    assert page.total == 1 and page.distribution.unknown == 1
    for value in (detail, item, _records_blob(caplog.records)):
        _assert_private_absent(value)
        assert "ANCIEN-CHAUFFAGE-RETIRE" not in str(value)


@pytest.mark.parametrize("variant", ["tampered", "wrong-purpose", "cross-target"])
async def test_invalid_finality_tampering_and_cross_target_fail_without_content(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    record = _record(101, _DPE, "D")
    _install_source(monkeypatch, [record])
    factory = _SessionFactory()
    context = issue_context(
        address_id=_TARGET.address_id,
        department="44",
        ban_id=_BAN,
        addresses_generation=_GENERATIONS.addresses,
        diagnostics_generation=_GENERATIONS.diagnostics,
    )
    document = issue_document(
        context,
        document_id=record.document_id,
        dpe_number=record.dpe_number,
    )
    if variant == "tampered":
        reference = ("A" if context[0] != "A" else "B") + context[1:]
        path = "/v1/bien/dpe/inventaire"
        params = {"contexte": reference}
    elif variant == "wrong-purpose":
        reference = document
        path = "/v1/bien/dpe/inventaire"
        params = {"contexte": reference}
    else:
        foreign_context = issue_context(
            address_id=_TARGET.address_id + 1,
            department="44",
            ban_id=_FOREIGN_BAN,
            addresses_generation=_GENERATIONS.addresses,
            diagnostics_generation=_GENERATIONS.diagnostics,
        )
        reference = issue_document(
            foreign_context,
            document_id=999,
            dpe_number="FOREIGN-PRIVATE-DPE",
        )
        path = "/v1/bien/dpe/document"
        params = {"contexte": context, "reference": reference}
    monkeypatch.setattr("app.logements.router.async_session", factory)

    response = await client.get(path, params=params)

    assert response.status_code == 400
    assert response.json()["instance"] == path
    assert reference not in response.text
    _assert_private_absent(response.json())


async def test_expired_generation_and_fingerprint_fail_as_refresh_required(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record(101, _DPE, "D")
    _install_source(monkeypatch, [record])
    factory = _SessionFactory()
    issued_at = 1_800_000_000
    monkeypatch.setattr(dangerous_timed.time, "time", lambda: issued_at)
    expired = issue_context(
        address_id=_TARGET.address_id,
        department="44",
        ban_id=_BAN,
        addresses_generation=_GENERATIONS.addresses,
        diagnostics_generation=_GENERATIONS.diagnostics,
    )
    monkeypatch.setattr(dangerous_timed.time, "time", lambda: issued_at + 1801)
    monkeypatch.setattr("app.logements.router.async_session", factory)
    expired_response = await client.get(
        "/v1/bien/dpe/inventaire", params={"contexte": expired}
    )
    assert expired_response.status_code == 409

    monkeypatch.setattr(dangerous_timed.time, "time", lambda: issued_at)
    stale_generation = issue_context(
        address_id=_TARGET.address_id,
        department="44",
        ban_id=_BAN,
        addresses_generation=_GENERATIONS.addresses,
        diagnostics_generation=_GENERATIONS.diagnostics - 1,
    )
    stale_fingerprint = issue_context(
        address_id=_TARGET.address_id,
        department="44",
        ban_id=_FOREIGN_BAN,
        addresses_generation=_GENERATIONS.addresses,
        diagnostics_generation=_GENERATIONS.diagnostics,
    )
    for reference in (stale_generation, stale_fingerprint):
        response = await client.get(
            "/v1/bien/dpe/inventaire", params={"contexte": reference}
        )
        assert response.status_code == 409
        assert reference not in response.text
        _assert_private_absent(response.json())


@pytest.mark.parametrize("status", [400, 409, 503])
async def test_document_errors_and_request_logs_never_reflect_private_values(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    status: int,
) -> None:
    caplog.set_level(logging.INFO)
    errors = {
        400: inventory_service.InvalidReferenceError(),
        409: inventory_service.RefreshRequiredError(),
        503: RuntimeError(" ".join(_PRIVATE_VALUES)),
    }
    monkeypatch.setattr(
        "app.logements.router.load_dpe_document",
        AsyncMock(side_effect=errors[status]),
    )
    context = "opaque-context"
    document = "opaque-document"

    response = await client.get(
        "/v1/bien/dpe/document",
        params={"contexte": context, "reference": document},
    )

    assert response.status_code == status
    assert response.json()["instance"] == "/v1/bien/dpe/document"
    assert context not in response.text and document not in response.text
    _assert_private_absent(response.json())
    application_records = [
        record for record in caplog.records if record.name.startswith("app.")
    ]
    logs = _records_blob(application_records)
    for sentinel in (*_PRIVATE_VALUES, context, document):
        assert sentinel not in logs
    request_records = [
        record
        for record in application_records
        if record.getMessage() == "http_request"
    ]
    assert request_records
    assert {record.__dict__.get("path") for record in request_records} == {
        "/v1/bien/dpe/document"
    }


async def test_validation_and_quota_errors_are_non_reflective(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malicious = _ADDRESS + ("x" * 600)
    validation = await client.get(
        "/v1/bien/dpe/inventaire", params={"contexte": malicious}
    )
    assert validation.status_code == 422
    assert malicious not in validation.text
    assert "input" not in validation.json()

    async def quota_exhausted() -> None:
        raise ErreurApiError(429, "Quota épuisé", headers={"Retry-After": "60"})

    app.dependency_overrides[check_quota_hybrid] = quota_exhausted
    quota = await client.get(
        "/v1/bien/dpe/inventaire", params={"contexte": malicious[:100]}
    )
    assert quota.status_code == 429
    assert quota.headers["retry-after"] == "60"
    assert malicious[:100] not in quota.text
    monkeypatch.delitem(app.dependency_overrides, check_quota_hybrid, raising=False)


async def test_auth_error_does_not_reflect_the_opaque_query(
    public_client: AsyncClient,
) -> None:
    opaque_query = "opaque-private-looking-context"
    unauthorized = await public_client.get(
        "/v1/bien/dpe/inventaire", params={"contexte": opaque_query}
    )
    assert unauthorized.status_code in {401, 403}
    assert opaque_query not in unauthorized.text


def test_reference_payload_keeps_only_technical_ids_generations_and_hmacs() -> None:
    context_ref = issue_context(
        address_id=_TARGET.address_id,
        department="44",
        ban_id=_BAN,
        addresses_generation=_GENERATIONS.addresses,
        diagnostics_generation=_GENERATIONS.diagnostics,
    )
    context = verify_context(context_ref)
    document_ref = issue_document(
        context,
        document_id=101,
        dpe_number=_DPE,
    )
    assert set(_decode_unsigned_payload(context_ref)) == {
        "v",
        "p",
        "a",
        "d",
        "ah",
        "ag",
        "dg",
        "ad",
    }
    assert set(_decode_unsigned_payload(document_ref)) == {
        "v",
        "p",
        "a",
        "d",
        "ah",
        "ag",
        "dg",
        "ad",
        "r",
        "rh",
    }
    _assert_private_absent(_decode_unsigned_payload(context_ref))
    _assert_private_absent(_decode_unsigned_payload(document_ref))
