"""SQL boundaries for the address DPE inventory."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.dpe.queries import (
    DpeInventoryRecord,
    dpe_inventory_aggregate,
    dpe_inventory_page,
    dpe_inventory_record_by_id,
    dpe_inventory_target_by_ban,
    dpe_inventory_target_by_id,
    lock_dpe_inventory_relations,
)

pytestmark = pytest.mark.unit

_BAN_ID = "44109_zz01_00002"


class _MappingsResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_MappingsResult":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict[str, Any]:
        assert len(self._rows) == 1
        return self._rows[0]

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _CaptureSession:
    def __init__(self, *responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(
        self,
        statement: object,
        params: dict[str, Any] | None = None,
    ) -> _MappingsResult:
        self.calls.append((str(statement), params))
        rows = self.responses.pop(0) if self.responses else []
        return _MappingsResult(rows)


def _row(index: int, **overrides: object) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": index,
        "numero_dpe": f"DPE-{index:04d}",
        "numero_dpe_remplace": None,
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
    return values


def _squash(sql: str) -> str:
    return " ".join(sql.split())


@pytest.mark.asyncio
async def test_relation_parents_are_locked_in_order_before_qualified_oid_reads():
    session = _CaptureSession(
        [],
        [],
        [{"addresses_generation": 901, "diagnostics_generation": 902}],
    )

    generations = await lock_dpe_inventory_relations(session)  # type: ignore[arg-type]

    statements = [_squash(call[0]) for call in session.calls]
    assert statements == [
        "LOCK TABLE ONLY public.adresses IN ACCESS SHARE MODE",
        "LOCK TABLE ONLY public.diagnostics_dpe IN ACCESS SHARE MODE",
        "SELECT to_regclass('public.adresses')::oid::bigint AS "
        "addresses_generation, "
        "to_regclass('public.diagnostics_dpe')::oid::bigint AS "
        "diagnostics_generation",
    ]
    assert generations.addresses == 901
    assert generations.diagnostics == 902


@pytest.mark.asyncio
async def test_target_queries_bind_identity_department_and_technical_id():
    by_ban = _CaptureSession([{"id": 73, "code_departement": "44", "ban_id": _BAN_ID}])
    target = await dpe_inventory_target_by_ban(
        by_ban,  # type: ignore[arg-type]
        ban_id=_BAN_ID,
        department="44",
    )
    sql, params = by_ban.calls[0]
    assert "FROM public.adresses" in sql
    assert "a.ban_id = :ban_id" in sql
    assert "a.code_departement = :department" in sql
    assert _BAN_ID not in sql
    assert params == {"ban_id": _BAN_ID, "department": "44"}
    assert target is not None and target.address_id == 73

    by_id = _CaptureSession([{"id": 73, "code_departement": "44", "ban_id": _BAN_ID}])
    await dpe_inventory_target_by_id(
        by_id,  # type: ignore[arg-type]
        address_id=73,
        department="44",
    )
    sql, params = by_id.calls[0]
    assert "a.id = :address_id" in sql
    assert params == {"address_id": 73, "department": "44"}


@pytest.mark.asyncio
async def test_page_uses_a_bound_keyset_and_keeps_unknown_historical_rows():
    rows = [
        _row(2, numero_dpe="DPE-0002", type_batiment="maison"),
        _row(3, numero_dpe="DPE-0003", etiquette_dpe=None),
        _row(4, numero_dpe="DPE-0004", type_batiment="type opaque"),
    ]
    anchor = DpeInventoryRecord(
        **{
            "document_id": 1,
            "dpe_number": "DPE-0001",
            "replaced_dpe_number": None,
            **{
                key: value
                for key, value in _row(1).items()
                if key not in {"id", "numero_dpe", "numero_dpe_remplace"}
            },
        }
    )
    session = _CaptureSession(rows)

    page, has_more = await dpe_inventory_page(
        session,  # type: ignore[arg-type]
        ban_id=_BAN_ID,
        department="44",
        limit=2,
        anchor=anchor,
    )

    sql, params = session.calls[0]
    compact = _squash(sql)
    assert "FROM public.diagnostics_dpe AS d" in compact
    assert "d.identifiant_ban = :ban_id" in compact
    assert "d.code_departement = :department" in compact
    assert "(d.numero_dpe, d.id) > (:anchor_number, :anchor_id)" in compact
    assert "ORDER BY d.numero_dpe, d.id" in compact
    assert "type_batiment =" not in compact
    assert _BAN_ID not in compact
    assert params == {
        "ban_id": _BAN_ID,
        "department": "44",
        "fetch_limit": 3,
        "anchor_number": "DPE-0001",
        "anchor_id": 1,
    }
    assert [record.document_id for record in page] == [2, 3]
    assert page[1].etiquette_dpe is None
    assert has_more is True


@pytest.mark.asyncio
async def test_aggregate_counts_every_class_and_the_unknown_bucket():
    aggregate_row = {
        "total": 137,
        "apartment_document_count": 91,
        "class_a": 1,
        "class_b": 2,
        "class_c": 3,
        "class_d": 4,
        "class_e": 5,
        "class_f": 6,
        "class_g": 7,
        "class_unknown": 109,
    }
    session = _CaptureSession([aggregate_row])

    aggregate = await dpe_inventory_aggregate(
        session,  # type: ignore[arg-type]
        ban_id=_BAN_ID,
        department="44",
    )

    sql, params = session.calls[0]
    compact = _squash(sql)
    assert "count(*)::bigint AS total" in compact
    assert "AS apartment_document_count" in compact
    assert "lower(btrim(d.type_batiment)) = 'appartement'" in compact
    assert "AS class_unknown" in compact
    assert "NOT IN ('A', 'B', 'C', 'D', 'E', 'F', 'G')" in compact
    assert "d.identifiant_ban = :ban_id" in compact
    assert "d.code_departement = :department" in compact
    assert params == {"ban_id": _BAN_ID, "department": "44"}
    assert aggregate.total == 137
    assert aggregate.apartment_document_count == 91
    assert sum(aggregate.distribution.values()) == 137


@pytest.mark.asyncio
async def test_document_lookup_requires_id_ban_and_department():
    session = _CaptureSession([_row(90210)])

    record = await dpe_inventory_record_by_id(
        session,  # type: ignore[arg-type]
        document_id=90210,
        ban_id=_BAN_ID,
        department="44",
    )

    sql, params = session.calls[0]
    compact = _squash(sql)
    assert "d.id = :document_id" in compact
    assert "d.identifiant_ban = :ban_id" in compact
    assert "d.code_departement = :department" in compact
    assert "SELECT *" not in compact
    assert params == {
        "document_id": 90210,
        "ban_id": _BAN_ID,
        "department": "44",
    }
    assert record is not None and record.dpe_number == "DPE-90210"


@pytest.mark.asyncio
async def test_private_lifecycle_columns_are_read_without_filtering_tombstones():
    from datetime import UTC, datetime

    facts = {
        "regime_dpe": "neuf",
        "source_observations": [],
        "source_state": "absent_from_complete_scope",
        "source_snapshot_at": datetime(2026, 9, 5, 12, tzinfo=UTC),
        "is_tombstone": True,
        "lifecycle_facts": {
            "replacement_state": "replaced",
            "graph_issues": [],
            "date_issues": [],
        },
        "revision_evidence": {
            "status": "unverified",
            "kind": None,
            "revision_date": None,
            "verified_at": None,
            "evidence_sha256": None,
            "official_source": None,
        },
    }
    session = _CaptureSession([_row(1, **facts)])
    record = await dpe_inventory_record_by_id(
        session, document_id=1, ban_id=_BAN_ID, department="44"
    )
    assert record is not None
    assert all(getattr(record, key) == value for key, value in facts.items())
    sql = _squash(session.calls[0][0])
    assert all(f"d.{key}" in sql for key in facts)
    assert "NOT d.is_tombstone" not in sql
    assert "lifecycle_facts" not in sql.split("WHERE", 1)[1]
