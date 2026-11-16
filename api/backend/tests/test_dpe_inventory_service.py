"""Transactional service contract for DPE inventory and document consultation."""

import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.dpe import inventory_service
from app.dpe.queries import (
    DpeInventoryAggregate,
    DpeInventoryRecord,
    DpeInventoryTarget,
    DpeRelationGenerations,
)
from app.dpe.references import (
    InvalidReferenceError,
    RefreshRequiredError,
    issue_context,
    issue_cursor,
    issue_document,
    verify_context,
    verify_document,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _unit_publication_admission(monkeypatch):
    # Unit session doubles have no physical engine; the two-connection protocol
    # runs without overrides in test_dpe_inventory_generation and COR20 PostGIS.
    monkeypatch.setattr(
        "app.common.publication_readiness.guard_snapshot_publication", AsyncMock()
    )


_BAN_ID = "44109_zz01_00002"
_TARGET = DpeInventoryTarget(address_id=731, department="44", ban_id=_BAN_ID)
_GENERATIONS = DpeRelationGenerations(addresses=16543, diagnostics=16544)


class _Transaction:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def __aenter__(self) -> None:
        self.events.append("transaction-enter")

    async def __aexit__(self, *args: object) -> None:
        self.events.append("transaction-exit")


class _Session:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.closed = False

    async def __aenter__(self) -> "_Session":
        self.events.append("session-enter")
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True
        self.events.append("session-exit")

    def begin(self) -> _Transaction:
        return _Transaction(self.events)

    async def execute(self, statement: object, parameters=None):
        self.events.append("sql:" + " ".join(str(statement).split()))
        return SimpleNamespace(scalar_one=lambda: True)


class _SessionFactory:
    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    def __call__(self) -> _Session:
        session = _Session()
        self.sessions.append(session)
        return session


def _record(index: int, **overrides: object) -> DpeInventoryRecord:
    values: dict[str, Any] = {
        "document_id": index,
        "dpe_number": f"PRIVATE-DPE-{index:04d}",
        "replaced_dpe_number": None,
        "type_batiment": "appartement",
        "surface_habitable": Decimal("61.40"),
        "etiquette_dpe": "D",
        "etiquette_ges": "C",
        "date_etablissement_dpe": date(2023, 5, 1),
        "date_fin_validite_dpe": date(2033, 5, 1),
        "annee_construction": 1988,
        "periode_construction": "1975-1988",
        "type_installation_chauffage": "individuel",
        "type_energie_principale_chauffage": "électricité",
        "qualite_isolation_enveloppe": "moyenne",
        "conso_5_usages_ep": Decimal("12000.20"),
        "emission_ges_5_usages": Decimal("500.30"),
        "cout_total_5_usages": Decimal("900.40"),
    }
    values.update(overrides)
    return DpeInventoryRecord(**values)


def _context_reference(
    *,
    ban_id: str = _BAN_ID,
    addresses: int = _GENERATIONS.addresses,
    diagnostics: int = _GENERATIONS.diagnostics,
) -> str:
    return issue_context(
        address_id=_TARGET.address_id,
        department=_TARGET.department,
        ban_id=ban_id,
        addresses_generation=addresses,
        diagnostics_generation=diagnostics,
    )


def _install_source(
    monkeypatch: pytest.MonkeyPatch,
    records: list[DpeInventoryRecord],
    *,
    target: DpeInventoryTarget | None = _TARGET,
    generations: DpeRelationGenerations = _GENERATIONS,
) -> None:
    async def lock(_session: object) -> DpeRelationGenerations:
        return generations

    async def by_ban(_session: object, **_kwargs: object) -> DpeInventoryTarget | None:
        return target

    async def by_id(_session: object, **_kwargs: object) -> DpeInventoryTarget | None:
        return target

    async def record_by_id(
        _session: object,
        *,
        document_id: int,
        **_kwargs: object,
    ) -> DpeInventoryRecord | None:
        return next(
            (record for record in records if record.document_id == document_id),
            None,
        )

    async def page(
        _session: object,
        *,
        limit: int,
        anchor: DpeInventoryRecord | None,
        **_kwargs: object,
    ) -> tuple[list[DpeInventoryRecord], bool]:
        offset = 0 if anchor is None else records.index(anchor) + 1
        selected = records[offset : offset + limit]
        return selected, offset + limit < len(records)

    counts = {label: 0 for label in "ABCDEFG"}
    unknown = 0
    for record in records:
        label = (record.etiquette_dpe or "").strip().upper()
        if label in counts:
            counts[label] += 1
        else:
            unknown += 1

    async def aggregate(_session: object, **_kwargs: object) -> DpeInventoryAggregate:
        return DpeInventoryAggregate(
            total=len(records),
            apartment_document_count=sum(
                (record.type_batiment or "").strip().lower() == "appartement"
                for record in records
            ),
            distribution={**counts, "unknown": unknown},
        )

    monkeypatch.setattr(inventory_service.queries, "lock_dpe_inventory_relations", lock)
    monkeypatch.setattr(
        inventory_service.queries, "dpe_inventory_target_by_ban", by_ban
    )
    monkeypatch.setattr(inventory_service.queries, "dpe_inventory_target_by_id", by_id)
    monkeypatch.setattr(
        inventory_service.queries, "dpe_inventory_record_by_id", record_by_id
    )
    monkeypatch.setattr(inventory_service.queries, "dpe_inventory_page", page)
    monkeypatch.setattr(inventory_service.queries, "dpe_inventory_aggregate", aggregate)


@pytest.mark.asyncio
async def test_137_rows_are_visited_once_with_stable_total_and_distribution(
    monkeypatch: pytest.MonkeyPatch,
):
    records = [_record(index) for index in range(1, 138)]
    records[0] = _record(
        1,
        type_batiment="type source inconnu",
        etiquette_dpe=None,
        etiquette_ges="opaque",
        date_etablissement_dpe=None,
        date_fin_validite_dpe=None,
    )
    records[1] = _record(2, replaced_dpe_number="PRIVATE-DPE-0001")
    _install_source(monkeypatch, records)
    factory = _SessionFactory()

    page = await inventory_service.load_dpe_inventory(
        factory,  # type: ignore[arg-type]
        ban_id=_BAN_ID,
        limit=20,
    )
    seen_ids: list[int] = []
    totals: list[int | None] = []
    distributions: list[dict[str, int]] = []
    while True:
        context = verify_context(page.context_ref or "")
        seen_ids.extend(
            verify_document(item.document_ref, context=context).document_id
            for item in page.items
        )
        totals.append(page.total)
        distributions.append(page.distribution.model_dump(by_alias=True))
        if page.next_cursor is None:
            break
        page = await inventory_service.load_dpe_inventory(
            factory,  # type: ignore[arg-type]
            context_ref=page.context_ref,
            cursor=page.next_cursor,
            limit=20,
        )

    assert seen_ids == list(range(1, 138))
    assert totals == [137] * 7
    assert all(distribution == distributions[0] for distribution in distributions)
    assert distributions[0]["unknown"] == 1
    assert all(session.closed for session in factory.sessions)
    assert all(
        session.events[2]
        == "sql:SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        for session in factory.sessions
    )


@pytest.mark.asyncio
async def test_snapshot_setup_precedes_all_source_reads_and_uses_one_session(
    monkeypatch: pytest.MonkeyPatch,
):
    records = [_record(1)]
    observed: list[tuple[str, object]] = []

    async def lock(session: object) -> DpeRelationGenerations:
        observed.append(("lock", session))
        return _GENERATIONS

    async def by_ban(session: object, **_kwargs: object) -> DpeInventoryTarget:
        observed.append(("target", session))
        return _TARGET

    async def page(session: object, **_kwargs: object):
        observed.append(("page", session))
        return records, False

    async def aggregate(session: object, **_kwargs: object):
        observed.append(("aggregate", session))
        return DpeInventoryAggregate(
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

    monkeypatch.setattr(inventory_service.queries, "lock_dpe_inventory_relations", lock)
    monkeypatch.setattr(
        inventory_service.queries, "dpe_inventory_target_by_ban", by_ban
    )
    monkeypatch.setattr(inventory_service.queries, "dpe_inventory_page", page)
    monkeypatch.setattr(inventory_service.queries, "dpe_inventory_aggregate", aggregate)
    factory = _SessionFactory()

    result = await inventory_service.load_dpe_inventory(
        factory,  # type: ignore[arg-type]
        ban_id=_BAN_ID,
    )

    session = factory.sessions[0]
    assert session.events[:3] == [
        "session-enter",
        "transaction-enter",
        "sql:SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
    ]
    assert [name for name, _ in observed] == ["lock", "target", "page", "aggregate"]
    assert all(source_session is session for _, source_session in observed)
    assert session.events[-2:] == ["transaction-exit", "session-exit"]
    assert session.closed and result.total == 1


@pytest.mark.asyncio
async def test_apartment_count_stays_internal_and_names_documents(
    monkeypatch: pytest.MonkeyPatch,
):
    records = [
        _record(1, type_batiment="Appartement"),
        _record(2, type_batiment=" appartement "),
        _record(3, type_batiment="maison"),
        _record(4, type_batiment=None),
    ]
    _install_source(monkeypatch, records)

    result = await inventory_service.load_dpe_inventory_result(
        _SessionFactory(),  # type: ignore[arg-type]
        ban_id=_BAN_ID,
    )

    assert result.apartment_document_count == 2
    public_payload = result.page.model_dump(mode="json")
    assert "apartment_document_count" not in public_payload
    assert "logement" not in public_payload


@pytest.mark.asyncio
async def test_missing_identity_returns_no_context_without_opening_a_session():
    factory = _SessionFactory()

    page = await inventory_service.load_dpe_inventory(
        factory,  # type: ignore[arg-type]
        ban_id=None,
    )

    assert page.state == "identity_unavailable"
    assert page.context_ref is None
    assert page.total is None
    assert page.distribution.total_count() == 0
    assert factory.sessions == []


@pytest.mark.asyncio
async def test_context_generation_or_ban_change_requires_a_refresh(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_source(monkeypatch, [_record(1)])
    factory = _SessionFactory()

    for context_ref in (
        _context_reference(addresses=_GENERATIONS.addresses - 1),
        _context_reference(ban_id="44109_zz01_99999"),
    ):
        with pytest.raises(RefreshRequiredError):
            await inventory_service.load_dpe_inventory(
                factory,  # type: ignore[arg-type]
                context_ref=context_ref,
            )


@pytest.mark.asyncio
async def test_reused_cursor_row_id_cannot_change_the_keyset_anchor(
    monkeypatch: pytest.MonkeyPatch,
):
    current = _record(1, dpe_number="PRIVATE-DPE-REPLACEMENT")
    _install_source(monkeypatch, [current])
    context_ref = _context_reference()
    cursor = issue_cursor(
        context_ref,
        anchor_id=current.document_id,
        dpe_number="PRIVATE-DPE-ORIGINAL",
    )

    with pytest.raises(RefreshRequiredError):
        await inventory_service.load_dpe_inventory(
            _SessionFactory(),  # type: ignore[arg-type]
            context_ref=context_ref,
            cursor=cursor,
        )


@pytest.mark.asyncio
async def test_document_projection_is_closed_and_checked_after_session_close(
    monkeypatch: pytest.MonkeyPatch,
):
    record = _record(
        90210,
        etiquette_dpe="f",
        etiquette_ges="unknown class",
        date_fin_validite_dpe=None,
    )
    _install_source(monkeypatch, [record])
    context_ref = _context_reference()
    document_ref = issue_document(
        context_ref,
        document_id=record.document_id,
        dpe_number=record.dpe_number,
    )
    factory = _SessionFactory()

    document = await inventory_service.load_dpe_document(
        factory,  # type: ignore[arg-type]
        context_ref=context_ref,
        document_ref=document_ref,
    )

    assert factory.sessions[0].closed
    assert document.etiquette_dpe == "F"
    assert document.etiquette_ges is None
    assert document.validity_by_date == "unknown"
    payload = document.model_dump(mode="json")
    assert set(payload) == {
        "version",
        "context_ref",
        "document_ref",
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
        "validity_by_date",
        "lifecycle",
        "association",
        "address_check",
        "source",
        "source_coverage",
        "graph_state",
        "imported_at",
    }
    serialized = json.dumps(payload)
    assert record.dpe_number not in serialized
    assert _BAN_ID not in serialized


@pytest.mark.asyncio
async def test_document_row_reuse_and_cross_context_inputs_are_refused(
    monkeypatch: pytest.MonkeyPatch,
):
    current = _record(90210, dpe_number="PRIVATE-DPE-REPLACEMENT")
    _install_source(monkeypatch, [current])
    context_ref = _context_reference()
    stale_document = issue_document(
        context_ref,
        document_id=current.document_id,
        dpe_number="PRIVATE-DPE-ORIGINAL",
    )
    with pytest.raises(RefreshRequiredError):
        await inventory_service.load_dpe_document(
            _SessionFactory(),  # type: ignore[arg-type]
            context_ref=context_ref,
            document_ref=stale_document,
        )

    with pytest.raises(InvalidReferenceError):
        await inventory_service.load_dpe_inventory(
            _SessionFactory(),  # type: ignore[arg-type]
            ban_id=_BAN_ID,
            context_ref=context_ref,
        )


@pytest.mark.asyncio
async def test_page_size_is_strictly_bounded_before_database_access():
    for invalid in (0, 51, -1, True):
        factory = _SessionFactory()
        with pytest.raises(ValueError, match="invalid_page_size"):
            await inventory_service.load_dpe_inventory(
                factory,  # type: ignore[arg-type]
                ban_id=_BAN_ID,
                limit=invalid,
            )
        assert factory.sessions == []


def _lifecycle_record(**overrides: object) -> SimpleNamespace:
    values = asdict(_record(1))
    values.update(
        regime_dpe="existant",
        source_observations=[
            {
                "source_slug": slug,
                "department": "44",
                "source_version": "a" * 64,
                "observed_at": "2026-09-05T22:30:00Z",
                "last_seen_at": "2026-09-04T10:00:00Z",
                "state": state,
                "import_id": "PRIVATE-IMPORT",
            }
            for slug, state in [
                ("dpe02neuf", "absent_from_complete_scope"),
                ("dpe03existant", "present"),
            ]
        ],
        source_state="present",
        source_snapshot_at=datetime(2026, 9, 5, 22, 30, tzinfo=UTC),
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
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_lifecycle_shared_date_historical_regimes_and_expired_replacement(
    monkeypatch,
):
    from app.dpe import references

    monkeypatch.setattr(references, "paris_today", lambda: date(2026, 9, 7))
    record = _lifecycle_record(date_fin_validite_dpe=date(2025, 5, 1))
    _install_source(monkeypatch, [record])
    page = await inventory_service.load_dpe_inventory(_SessionFactory(), ban_id=_BAN_ID)
    assert page.as_of_date == date(2026, 9, 7)
    lifecycle = page.items[0].lifecycle
    assert lifecycle is not None
    assert lifecycle.source_snapshot_date == date(2026, 9, 6)
    assert lifecycle.regimes == ["existant", "neuf"]
    assert lifecycle.replacement_state == "replaced"
    assert lifecycle.validity == "expired"
    assert lifecycle.revision_status == "unverified"
    document = await inventory_service.load_dpe_document(
        _SessionFactory(),
        context_ref=page.context_ref,
        document_ref=page.items[0].document_ref,
    )
    assert document.lifecycle == lifecycle
    assert document.validity_by_date == "expired"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"source_state": "PRIVATE-SENTINEL"},
        {"source_observations": [{"source_slug": "PRIVATE-SENTINEL"}]},
        {
            "lifecycle_facts": {
                "replacement_state": "PRIVATE-SENTINEL",
                "graph_issues": [],
                "date_issues": [],
            }
        },
        {
            "lifecycle_facts": {
                "replacement_state": "replaced",
                "graph_issues": [],
                "date_issues": ["PRIVATE-SENTINEL"],
            }
        },
        {"revision_evidence": {"status": "verified_attestation", "kind": "2026"}},
    ],
)
async def test_malformed_private_facts_close_entire_lifecycle(monkeypatch, changed):
    record = _lifecycle_record(**changed)
    _install_source(monkeypatch, [record])
    page = await inventory_service.load_dpe_inventory(_SessionFactory(), ban_id=_BAN_ID)
    document = await inventory_service.load_dpe_document(
        _SessionFactory(),
        context_ref=page.context_ref,
        document_ref=page.items[0].document_ref,
    )
    assert page.items[0].lifecycle is None
    assert document.lifecycle is None
    assert "PRIVATE-SENTINEL" not in page.model_dump_json() + document.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "established,end,issues,expected",
    [
        (date(2030, 1, 1), date(2040, 1, 1), [], "future_establishment"),
        (date(2024, 1, 1), date(2023, 1, 1), [], "inconsistent_dates"),
        (None, date(2033, 1, 1), [], "unknown"),
        (None, date(2033, 1, 1), ["invalid_establishment_date"], "inconsistent_dates"),
    ],
)
async def test_reader_projects_conservative_calendar_validity(
    monkeypatch, established, end, issues, expected
):
    from app.dpe import references

    monkeypatch.setattr(references, "paris_today", lambda: date(2026, 9, 7))
    record = _lifecycle_record(
        date_etablissement_dpe=established,
        date_fin_validite_dpe=end,
        lifecycle_facts={
            "replacement_state": "no_successor_known",
            "graph_issues": [],
            "date_issues": issues,
        },
    )
    _install_source(monkeypatch, [record])
    page = await inventory_service.load_dpe_inventory(_SessionFactory(), ban_id=_BAN_ID)
    document = await inventory_service.load_dpe_document(
        _SessionFactory(),
        context_ref=page.context_ref,
        document_ref=page.items[0].document_ref,
    )
    assert document.lifecycle.validity == expected
    assert document.validity_by_date == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "population", ["ready", "absent", "identity_unavailable", "no_ban"]
)
async def test_midnight_before_return_requires_refresh_for_every_page(
    monkeypatch, population
):
    from app.dpe import references

    today = [date(2026, 9, 7)]
    monkeypatch.setattr(references, "paris_today", lambda: today[0])
    _install_source(
        monkeypatch,
        [_record(1)] if population == "ready" else [],
        target=None if population == "identity_unavailable" else _TARGET,
    )
    original_exit = _Session.__aexit__

    async def midnight_exit(self, *args):
        await original_exit(self, *args)
        today[0] = date(2026, 9, 8)

    monkeypatch.setattr(_Session, "__aexit__", midnight_exit)
    if population == "no_ban":
        ticks = iter([date(2026, 9, 7), date(2026, 9, 8)])
        monkeypatch.setattr(references, "paris_today", lambda: next(ticks))
    with pytest.raises(RefreshRequiredError):
        await inventory_service.load_dpe_inventory(
            _SessionFactory(), ban_id=None if population == "no_ban" else _BAN_ID
        )


@pytest.mark.asyncio
async def test_midnight_after_document_transaction_requires_refresh(monkeypatch):
    from app.dpe import references

    today = [date(2026, 9, 7)]
    monkeypatch.setattr(references, "paris_today", lambda: today[0])
    _install_source(monkeypatch, [_record(1)])
    page = await inventory_service.load_dpe_inventory(_SessionFactory(), ban_id=_BAN_ID)

    async def midnight_exit(self, *args):
        today[0] = date(2026, 9, 8)

    monkeypatch.setattr(_Session, "__aexit__", midnight_exit)
    with pytest.raises(RefreshRequiredError):
        await inventory_service.load_dpe_document(
            _SessionFactory(),
            context_ref=page.context_ref,
            document_ref=page.items[0].document_ref,
        )


@pytest.mark.asyncio
async def test_contradictory_private_date_flags_fail_closed_without_validation_error(
    monkeypatch,
):
    record = _lifecycle_record(
        lifecycle_facts={
            "replacement_state": "replaced",
            "graph_issues": [],
            "date_issues": ["invalid_establishment_date"],
        }
    )
    _install_source(monkeypatch, [record])
    page = await inventory_service.load_dpe_inventory(_SessionFactory(), ban_id=_BAN_ID)
    document = await inventory_service.load_dpe_document(
        _SessionFactory(),
        context_ref=page.context_ref,
        document_ref=page.items[0].document_ref,
    )
    assert page.items[0].lifecycle is None
    assert document.lifecycle is None
    assert document.validity_by_date == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "departements,expected",
    [
        (("44", "44"), "44/85"),
        (("85", "85"), "44/85"),
        (("971", "971"), "971"),
        (("75", "971"), "971"),
        (("971", "972"), None),
    ],
)
async def test_served_source_scope_names_the_founding_departement(
    monkeypatch, departements, expected
):
    """Plan 04.1-08: a 971 lifecycle is served under 971, never under 44/85.

    The fixture holds an absent neuf observation and a present existant one:
    the present one founds the state. Two founding departements observed at
    the same instant close the group (manque), never a guessed scope.
    """
    record = _lifecycle_record()
    for entry, department in zip(record.source_observations, departements, strict=True):
        entry["department"] = department
    if len(set(departements)) == 2 and expected is None:
        for entry in record.source_observations:
            entry["state"] = "present"
    _install_source(monkeypatch, [record])
    page = await inventory_service.load_dpe_inventory(_SessionFactory(), ban_id=_BAN_ID)
    lifecycle = page.items[0].lifecycle
    if expected is None:
        assert lifecycle is None
    else:
        assert lifecycle is not None
        assert lifecycle.source_scope == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    [
        "present_without_observation",
        "present_without_presence",
        "absent_with_presence",
        "unknown_with_observations",
        "unknown_with_snapshot",
        "snapshot_before_observation",
        "whitespace_import",
        "unproven_deactivation",
    ],
)
async def test_incoherent_source_facts_close_entire_group(monkeypatch, variant):
    record = _lifecycle_record()
    if variant == "present_without_observation":
        record.source_observations = []
    elif variant == "present_without_presence":
        for entry in record.source_observations:
            entry["state"] = "absent_from_complete_scope"
    elif variant == "absent_with_presence":
        record.source_state, record.is_tombstone = "absent_from_complete_scope", True
    elif variant == "unknown_with_observations":
        record.source_state, record.source_snapshot_at = "unknown", None
    elif variant == "unknown_with_snapshot":
        record.source_state, record.source_observations = "unknown", []
    elif variant == "snapshot_before_observation":
        record.source_snapshot_at = datetime(2026, 9, 5, 12, tzinfo=UTC)
    elif variant == "whitespace_import":
        record.source_observations[0]["import_id"] = "   "
    else:
        record.source_state, record.is_tombstone = "explicitly_deactivated", True
    _install_source(monkeypatch, [record])
    page = await inventory_service.load_dpe_inventory(_SessionFactory(), ban_id=_BAN_ID)
    detail = await inventory_service.load_dpe_document(
        _SessionFactory(),
        context_ref=page.context_ref,
        document_ref=page.items[0].document_ref,
    )
    assert detail.lifecycle is page.items[0].lifecycle is None
