"""Resolve DPE V2 contract: inventory first, no dwelling attribution."""

import asyncio
import inspect
import json
import logging
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.auth.dependencies import check_quota, check_quota_hybrid
from app.auth.models import ApiKeyInfo
from app.database import get_db
from app.dpe.inventory_service import DpeInventoryServiceResult
from app.dpe.schemas import (
    DpeAttribution,
    DpeBuildingContext,
    DpeInventoryDistribution,
    DpeInventoryItem,
    DpeInventoryPage,
)
from app.main import app
from app.resolve.multi import query_multiple_datasets
from app.resolve.schemas import (
    RESOLUTION_RAISONS,
    DatasetResolutionState,
    ResultatMultiResolution,
)
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope
from tests.scope_doubles import SCOPE_44_85

pytestmark = pytest.mark.asyncio

_LAT = 46.000001
_LON = -1.000001
_BAN_KEY = "85047_5100_00001"
_BAN_KEY_AUTRE = "85047_5100_00002"
_P1 = {
    "id_parcelle": "85047000AD0123",
    "commune": "85047",
    "prefixe": "000",
    "section": "AD",
    "numero": "0123",
    "contenance": 500,
    "centroide_lat": _LAT,
    "centroide_lon": _LON,
    "geometrie_geojson": "{}",
}
_BAN_ROW = {
    "ban_id": _BAN_KEY,
    "numero": "1",
    "rep": None,
    "nom_voie": "rue sentinelle-synthetique",
    "code_postal": "85300",
    "code_commune": "85047",
    "nom_commune": "Commune-Test",
    "latitude": _LAT,
    "longitude": _LON,
    "score": 0.98,
    "distance_m": 4.0,
}
_URL_ADRESSE = "/v1/resolve?type=adresse&q=1+rue+sentinelle-synthetique+85300"


def _auth_client() -> AsyncClient:
    fake = ApiKeyInfo(
        id=1,
        name="test-key",
        quota_monthly=999999,
        revoked=False,
        created_at=datetime.now(UTC),
    )
    from app.common.publication_readiness import require_publication

    app.dependency_overrides[require_publication] = lambda: None
    app.dependency_overrides[check_quota] = lambda: fake
    app.dependency_overrides[check_quota_hybrid] = lambda: fake
    app.dependency_overrides[get_instance_scope] = lambda: SCOPE_44_85
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _scope_44_85(monkeypatch: pytest.MonkeyPatch):
    # Scope 44/85 is served by ``_auth_client`` (get_instance_scope override).
    # DB-free attribution doubles exclude catalog I/O; PostGIS tests keep it real.
    monkeypatch.setattr("app.resolve.multi.guard_publication", AsyncMock())
    monkeypatch.setattr(
        "app.middleware.RequestLoggingMiddleware._log_to_db", AsyncMock()
    )
    yield
    app.dependency_overrides.clear()


def _inventory(
    *,
    state: str = "ready",
    types: tuple[str | None, ...] = ("appartement",),
    labels: tuple[str | None, ...] | None = None,
    total: int | None = None,
    has_more: bool = False,
) -> DpeInventoryServiceResult:
    if state in {"identity_unavailable", "unavailable"}:
        page = DpeInventoryPage(
            state=state,
            total=None,
            distribution=DpeInventoryDistribution(),
            page_size=20,
            has_more=False,
            read_state="partial" if state == "unavailable" else "complete",
        )
        return DpeInventoryServiceResult(page=page, apartment_document_count=None)

    if state == "absent":
        types = ()
    labels = labels or tuple("D" for _ in types)
    confirmed_total = len(types) if total is None else total
    counts = {letter: 0 for letter in "ABCDEFG"}
    unknown = 0
    for label in labels:
        if label in counts:
            counts[label] += 1
        else:
            unknown += 1
    unknown += confirmed_total - len(labels)
    items = [
        DpeInventoryItem(
            document_ref=f"opaque-document-{index}",
            type_batiment=type_batiment,
            etiquette_dpe=label,
        )
        for index, (type_batiment, label) in enumerate(zip(types, labels, strict=True))
    ]
    page = DpeInventoryPage(
        state="ready" if confirmed_total else "absent",
        context_ref="opaque-context",
        items=items,
        total=confirmed_total,
        distribution=DpeInventoryDistribution.model_validate(
            {**counts, "unknown": unknown}
        ),
        page_size=20,
        has_more=has_more,
        next_cursor="opaque-cursor" if has_more else None,
        read_state="complete",
    )
    return DpeInventoryServiceResult(
        page=page,
        apartment_document_count=sum(t == "appartement" for t in types),
    )


def _building_row(label: str = "D") -> dict:
    return {
        "numero_dpe": f"private-{label}",
        "identifiant_ban": _BAN_KEY_AUTRE,
        "etiquette_dpe": label,
    }


async def _appel_resolve(
    *,
    service: AsyncMock | None = None,
    cles: AsyncMock | None = None,
    autres: AsyncMock | None = None,
    datasets: str = "dpe",
    reverse: AsyncMock | None = None,
    scope=None,
):
    service = service or AsyncMock(return_value=_inventory())
    cles = cles or AsyncMock(return_value=())
    autres = autres or AsyncMock(return_value=([], False))
    reverse = reverse or AsyncMock(return_value=[_BAN_ROW])
    async with _auth_client() as client:
        if scope is not None:
            app.dependency_overrides[get_instance_scope] = lambda: scope
        with (
            patch("app.resolve.router.geocoder", AsyncMock(return_value=[_BAN_ROW])),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
            patch("app.resolve.multi.reverse_geocoder", reverse),
            patch(
                "app.resolve.multi.parcelle_par_reference",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(return_value=[]),
            ),
            patch("app.resolve.multi.load_dpe_inventory_result", service),
            patch("app.resolve.multi.dpe_par_identifiants_ban", autres),
            patch("app.resolve.multi.cles_ban_batiment_rnb", cles),
        ):
            return await client.get(f"{_URL_ADRESSE}&datasets={datasets}")


def _dpe_surface(result: dict) -> str:
    return json.dumps(
        {
            "inventory": result.get("dpe_inventory"),
            "attribution": result.get("dpe_attribution"),
            "dataset": (result.get("datasets") or {}).get("dpe"),
            "errors": result.get("erreurs"),
        }
    )


async def test_resolve_uses_only_the_canonical_ban_for_inventory():
    service = AsyncMock(return_value=_inventory())
    reverse = AsyncMock(return_value=[{**_BAN_ROW, "ban_id": "foreign-first"}])
    response = await _appel_resolve(service=service, reverse=reverse)

    assert response.status_code == 200
    service.assert_awaited_once()
    assert service.await_args.kwargs["ban_id"] == _BAN_KEY
    assert service.await_args.kwargs["limit"] == 20
    reverse.assert_not_called()
    from app.resolve import multi

    assert not hasattr(multi, "dpe_par_point")
    assert not hasattr(multi, "dpe_par_identifiant_ban")


@pytest.mark.parametrize("state", ["ready", "absent", "identity_unavailable"])
async def test_midnight_during_proxy_discards_completed_dpe_inventory(
    monkeypatch, state
):
    from app.dpe import references
    from app.resolve import multi

    today = [date(2026, 9, 7)]
    monkeypatch.setattr(references, "paris_today", lambda: today[0])
    service_result = _inventory(state=state)
    service_result.page.as_of_date = today[0]

    async def proxy(*args, **kwargs):
        today[0] = date(2026, 9, 8)
        return {"safe": "other dataset remains available"}

    monkeypatch.setattr(multi, "_dispatch_http", proxy)
    response = await _appel_resolve(
        service=AsyncMock(return_value=service_result), datasets="dpe,risques"
    )
    assert response.status_code == 200
    result = response.json()["donnees"][0]
    assert result["dpe_inventory"]["state"] == "unavailable"
    assert result["dpe_inventory"]["as_of_date"] == "2026-09-08"
    assert result["dpe_inventory"]["items"] == []
    assert result["dpe_inventory"]["context_ref"] is None
    assert result["dpe_attribution"]["status"] == "error"
    assert "opaque-document" not in _dpe_surface(result)


async def test_router_reconstruction_keeps_inventory_identity_and_no_store():
    response = await _appel_resolve()
    result = response.json()["donnees"][0]

    assert result["ban_id"] == _BAN_KEY
    assert result["dpe_inventory"]["version"] == 2
    assert response.headers["cache-control"] == "no-store"
    router = __import__("app.resolve.router", fromlist=["x"])
    source = inspect.getsource(router._resolve_request)
    assert 'dpe_inventory=multi.get("dpe_inventory")' in source


async def test_missing_ban_returns_identity_unavailable_without_reconstruction():
    service = AsyncMock(return_value=_inventory(state="identity_unavailable"))
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.parcelle_par_reference",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch("app.resolve.multi.load_dpe_inventory_result", service),
        ):
            response = await client.get(
                f"/v1/resolve?q={_P1['id_parcelle']}&datasets=dpe"
            )

    result = response.json()["donnees"][0]
    assert service.await_args.kwargs["ban_id"] is None
    assert result["dpe_inventory"]["state"] == "identity_unavailable"
    assert result["dpe_attribution"]["status"] == "absent"
    assert result["dpe_attribution"]["reason"] == "identity_unavailable"
    assert result["datasets"]["dpe"] is None


async def test_gps_reverse_ban_is_display_only_for_dpe_inventory():
    seen_ban: list[str | None] = []

    async def service(*_args, ban_id=None, **_kwargs):
        seen_ban.append(ban_id)
        return _inventory(state="identity_unavailable")

    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.multi.load_dpe_inventory_result",
                AsyncMock(side_effect=service),
            ),
        ):
            response = await client.get(
                "/v1/resolve",
                params={"type": "gps", "q": f"{_LAT},{_LON}", "datasets": "dpe"},
            )

    assert response.status_code == 200
    assert seen_ban == [None]
    result = response.json()["donnees"][0]
    assert result["ban_id"] == _BAN_KEY
    assert result["dpe_inventory"]["state"] == "identity_unavailable"


@pytest.mark.parametrize(
    "query",
    ["rue sentinelle-synthetique 85300", "Commune-Test 85300"],
)
async def test_imprecise_address_never_promotes_the_first_ban(query: str):
    service = AsyncMock(return_value=_inventory())
    async with _auth_client() as client:
        with (
            patch("app.resolve.router.geocoder", AsyncMock(return_value=[_BAN_ROW])),
            patch("app.resolve.multi.load_dpe_inventory_result", service),
        ):
            response = await client.get(
                "/v1/resolve",
                params={"type": "adresse", "q": query, "datasets": "dpe"},
            )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    service.assert_not_called()
    assert _BAN_KEY not in response.text


async def test_unique_unnumbered_place_keeps_its_exact_ban_inventory():
    row = {**_BAN_ROW, "numero": None, "nom_voie": "Les Etoiles"}
    seen_ban: list[str | None] = []

    async def service(*_args, ban_id=None, **_kwargs):
        seen_ban.append(ban_id)
        return _inventory()

    async with _auth_client() as client:
        with (
            patch("app.resolve.router.geocoder", AsyncMock(return_value=[row])),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.multi.load_dpe_inventory_result",
                AsyncMock(side_effect=service),
            ),
        ):
            response = await client.get(
                "/v1/resolve",
                params={
                    "type": "adresse",
                    "q": "Les Etoiles 85300 Commune-Test",
                    "datasets": "dpe",
                },
            )

    assert response.status_code == 200
    assert seen_ban == [_BAN_KEY]
    result = response.json()["donnees"][0]
    assert result["ban_id"] == _BAN_KEY
    assert result["dpe_inventory"]["state"] == "ready"


async def test_single_terminal_is_inventory_not_an_exact_attribution():
    response = await _appel_resolve(service=AsyncMock(return_value=_inventory()))
    result = response.json()["donnees"][0]

    assert result["datasets"]["dpe"] is None
    assert result["dpe_inventory"]["total"] == 1
    assert result["dpe_inventory"]["items"][0]["document_ref"].startswith("opaque-")
    assert result["dpe_attribution"]["status"] == "unassigned"
    assert result["dpe_attribution"]["reason"] == "dwelling_identity_unproven"
    assert result["dpe_attribution"]["diagnostic"] is None
    assert result["dpe_attribution"]["candidates"] == []
    assert result["dataset_states"]["dpe"]["presence"] == "value"


async def test_mixed_inventory_and_pagination_are_forwarded_unchanged():
    inventory = _inventory(
        types=("maison", "appartement", None),
        labels=("C", "F", None),
        total=4,
        has_more=True,
    )
    response = await _appel_resolve(service=AsyncMock(return_value=inventory))
    result = response.json()["donnees"][0]

    assert result["dpe_inventory"] == inventory.page.model_dump(
        mode="json", by_alias=True
    )
    assert result["dpe_attribution"]["status"] == "unassigned"
    assert result["datasets"]["dpe"] is None


async def test_absence_and_building_context_remain_distinct():
    cles = AsyncMock(return_value=(_BAN_KEY_AUTRE,))
    autres = AsyncMock(return_value=([_building_row("D"), _building_row("F")], False))
    response = await _appel_resolve(
        service=AsyncMock(return_value=_inventory(state="absent")),
        cles=cles,
        autres=autres,
    )
    result = response.json()["donnees"][0]

    assert result["dpe_inventory"]["state"] == "absent"
    assert result["dpe_attribution"]["status"] == "absent"
    assert result["dpe_attribution"]["reason"] == "not_reported"
    assert result["dpe_attribution"]["building_context"] == {
        "count": 2,
        "distribution": {"D": 1, "F": 1},
        "truncated": False,
    }
    assert result["dataset_states"]["dpe"]["presence"] == "absent"
    assert _BAN_KEY_AUTRE not in _dpe_surface(result)


async def test_rnb_links_are_never_read_while_rnb_is_not_hosted():
    # rnb_liens is the table of the rnb source (D-23, 2026-09-22).
    without_rnb = InstanceScope(
        SCOPE_44_85.datasets - {"rnb"}, SCOPE_44_85.departements
    )
    cles = AsyncMock(return_value=(_BAN_KEY_AUTRE,))
    autres = AsyncMock(return_value=([_building_row("D")], False))
    response = await _appel_resolve(
        service=AsyncMock(return_value=_inventory(state="absent")),
        cles=cles,
        autres=autres,
        scope=without_rnb,
    )
    result = response.json()["donnees"][0]
    cles.assert_not_called()
    assert result["dpe_attribution"]["building_context"] is None
    # Witness: test_absence_and_building_context_remain_distinct, rnb hosted.


async def test_rnb_is_skipped_for_unusable_inventory_states():
    for state in ("identity_unavailable", "unavailable"):
        cles = AsyncMock(return_value=(_BAN_KEY_AUTRE,))
        response = await _appel_resolve(
            service=AsyncMock(return_value=_inventory(state=state)), cles=cles
        )
        result = response.json()["donnees"][0]
        cles.assert_not_called()
        assert result["dpe_attribution"]["building_context"] is None


async def test_rnb_failure_warns_without_degrading_inventory(
    caplog: pytest.LogCaptureFixture,
):
    private = "private-rnb-detail"
    with caplog.at_level(logging.WARNING):
        response = await _appel_resolve(
            service=AsyncMock(return_value=_inventory()),
            cles=AsyncMock(side_effect=RuntimeError(private)),
        )
    result = response.json()["donnees"][0]

    assert response.status_code == 200
    assert result["dpe_inventory"]["state"] == "ready"
    assert result["dpe_attribution"]["status"] == "unassigned"
    assert result["dpe_attribution"]["building_context"] is None
    assert not any(error["dataset"] == "dpe" for error in result["erreurs"] or [])
    assert result["avertissements"] == [
        {
            "dataset": "dpe",
            "code": "CONTEXTE_BATIMENT_INDISPONIBLE",
            "message": "Contexte bâtiment DPE temporairement indisponible.",
        }
    ]
    assert private not in response.text + caplog.text


async def test_inventory_failure_is_local_and_leaks_no_detail(
    caplog: pytest.LogCaptureFixture,
):
    private = f"SELECT diagnostics_dpe WHERE identifiant_ban='{_BAN_KEY}'"
    with caplog.at_level(logging.ERROR):
        response = await _appel_resolve(
            service=AsyncMock(side_effect=RuntimeError(private)),
            datasets="cadastre,dpe",
        )
    result = response.json()["donnees"][0]

    assert response.status_code == 200
    assert result["datasets"]["cadastre"]["id_parcelle"] == _P1["id_parcelle"]
    assert result["dpe_inventory"]["state"] == "unavailable"
    assert result["dpe_attribution"]["status"] == "error"
    assert result["dataset_states"]["dpe"]["presence"] == "error"
    assert any(error["dataset"] == "dpe" for error in result["erreurs"])
    assert private not in response.text + caplog.text
    assert _BAN_KEY not in _dpe_surface(result)


async def test_inventory_closes_before_parallel_rnb_and_proxy_tasks():
    events: list[str] = []
    proxy_started = asyncio.Event()
    rnb_started = asyncio.Event()

    async def inventory_service(*_args, **_kwargs):
        events.extend(("inventory-open", "inventory-closed"))
        return _inventory()

    async def proxy(*_args, **_kwargs):
        events.append("proxy-start")
        proxy_started.set()
        await asyncio.wait_for(rnb_started.wait(), timeout=1.0)
        return {"ok": True}

    async def rnb(*_args, **_kwargs):
        events.append("rnb-start")
        rnb_started.set()
        await asyncio.wait_for(proxy_started.wait(), timeout=1.0)
        return ()

    with patch("app.resolve.multi._dispatch_http", proxy):
        response = await _appel_resolve(
            service=AsyncMock(side_effect=inventory_service),
            cles=AsyncMock(side_effect=rnb),
            datasets="dpe,risques",
        )

    assert response.status_code == 200
    assert events[:2] == ["inventory-open", "inventory-closed"]
    assert set(events[2:]) == {"rnb-start", "proxy-start"}
    result = response.json()["donnees"][0]
    assert result["dpe_inventory"]["state"] == "ready"
    assert result["datasets"]["risques"] == {"ok": True}


async def test_proxy_failure_after_inventory_does_not_erase_dpe():
    with patch(
        "app.resolve.multi._dispatch_http",
        AsyncMock(side_effect=RuntimeError("private-proxy")),
    ):
        response = await _appel_resolve(datasets="dpe,risques")
    result = response.json()["donnees"][0]

    assert response.status_code == 200
    assert result["dpe_inventory"]["state"] == "ready"
    assert result["dpe_attribution"]["status"] == "unassigned"
    assert result["dataset_states"]["risques"]["presence"] == "error"


def _raw_multi() -> dict:
    inventory = _inventory().page.model_dump(mode="json", by_alias=True)
    attribution = DpeAttribution(
        status="unassigned", reason="dwelling_identity_unproven"
    ).model_dump(mode="json")
    return {
        "type_detecte": "adresse",
        "latitude": _LAT,
        "longitude": _LON,
        "datasets": {"dpe": None},
        "dataset_states": {
            "dpe": DatasetResolutionState(dataset="dpe", presence="value").model_dump()
        },
        "dpe_inventory": inventory,
        "dpe_attribution": attribution,
    }


@pytest.mark.parametrize(
    ("mutation", "private"),
    [
        (lambda payload: payload["dpe_attribution"].pop("version"), False),
        (
            lambda payload: payload["dpe_attribution"].update(
                status="exact",
                reason=None,
                diagnostic={"numero_dpe": "private-ademe", "perime": False},
                candidates=[],
                building_context=None,
            ),
            True,
        ),
        (
            lambda payload: payload["datasets"].update(
                dpe=[{"numero_dpe": "private-ademe"}]
            ),
            True,
        ),
        (lambda payload: payload.update(private_ban="private-ban"), True),
    ],
)
def test_resolve_schema_rejects_legacy_or_open_dpe_payloads(mutation, private):
    payload = _raw_multi()
    mutation(payload)
    with pytest.raises(ValidationError) as raised:
        ResultatMultiResolution.model_validate(payload)
    if private:
        assert "private-" not in str(raised.value)


def test_legacy_dataset_is_rejected_even_without_both_v2_fields():
    payload = _raw_multi()
    payload.pop("dpe_inventory")
    payload.pop("dpe_attribution")
    payload["dataset_states"].pop("dpe")
    payload["datasets"]["dpe"] = [{"numero_dpe": "private-legacy-ademe"}]

    with pytest.raises(ValidationError) as raised:
        ResultatMultiResolution.model_validate(payload)

    assert "datasets.dpe must be empty in V2" in str(raised.value)
    assert "private-legacy-ademe" not in str(raised.value)


@pytest.mark.parametrize("marker", ["dataset", "state"])
def test_dpe_marker_without_v2_pair_is_rejected(marker: str):
    payload = _raw_multi()
    payload.pop("dpe_inventory")
    payload.pop("dpe_attribution")
    if marker == "dataset":
        payload["dataset_states"].pop("dpe")
    else:
        payload["datasets"].pop("dpe")

    with pytest.raises(ValidationError, match="DPE markers require"):
        ResultatMultiResolution.model_validate(payload)


@pytest.mark.parametrize("field", ["dpe_inventory", "dpe_attribution"])
def test_constructed_nested_model_cannot_bypass_v2_version(field: str):
    payload = _raw_multi()
    payload["dpe_inventory"] = _inventory().page
    payload["dpe_attribution"] = DpeAttribution(
        status="unassigned", reason="dwelling_identity_unproven"
    )
    object.__setattr__(payload[field], "version", 1)

    with pytest.raises(ValidationError, match="must explicitly declare version 2"):
        ResultatMultiResolution.model_validate(payload)


def test_valid_preconstructed_dpe_models_are_revalidated_and_preserved():
    payload = _raw_multi()
    payload["dpe_inventory"] = _inventory().page
    payload["dpe_attribution"] = DpeAttribution(
        status="unassigned", reason="dwelling_identity_unproven"
    )
    payload["dataset_states"]["dpe"] = DatasetResolutionState(
        dataset="dpe", presence="value"
    )

    result = ResultatMultiResolution.model_validate(payload)

    assert result.dpe_inventory == _inventory().page
    assert result.dpe_attribution.status == "unassigned"
    assert result.dataset_states is not None
    assert result.dataset_states["dpe"].dataset == "dpe"


@pytest.mark.parametrize(
    "target",
    [
        "inventory_items",
        "inventory_state",
        "attribution_diagnostic",
        "attribution_candidates",
        "attribution_building_context",
        "dataset_state",
        "dataset_state_absence_reason",
    ],
)
def test_mutated_preconstructed_dpe_models_are_rejected_without_leak(target: str):
    private = f"private-{target}"
    inventory = _inventory().page
    attribution = DpeAttribution(
        status="unassigned", reason="dwelling_identity_unproven"
    )
    coarse_state = DatasetResolutionState(dataset="dpe", presence="value")
    payload = _raw_multi()
    payload["dpe_inventory"] = inventory
    payload["dpe_attribution"] = attribution
    payload["dataset_states"]["dpe"] = coarse_state

    if target == "inventory_items":
        object.__setattr__(
            inventory,
            "items",
            [{"document_ref": "opaque-document", "numero_dpe": private}],
        )
    elif target == "inventory_state":
        object.__setattr__(inventory, "state", private)
    elif target == "attribution_diagnostic":
        object.__setattr__(
            attribution, "diagnostic", {"numero_dpe": private, "perime": False}
        )
    elif target == "attribution_candidates":
        object.__setattr__(
            attribution,
            "candidates",
            [
                {"numero_dpe": private, "perime": False},
                {"numero_dpe": "opaque-document", "perime": False},
            ],
        )
    elif target == "attribution_building_context":
        object.__setattr__(
            attribution,
            "building_context",
            {"count": 1, "distribution": {private: 1}},
        )
    elif target == "dataset_state":
        object.__setattr__(coarse_state, "dataset", private)
    else:
        object.__setattr__(coarse_state, "absence_reason", private)

    with pytest.raises(ValidationError) as raised:
        ResultatMultiResolution.model_validate(payload)

    assert private not in str(raised.value)


@pytest.mark.parametrize(
    ("state", "status", "reason", "presence"),
    [
        ("ready", "unassigned", "dwelling_identity_unproven", "value"),
        ("absent", "absent", "not_reported", "absent"),
        ("identity_unavailable", "absent", "identity_unavailable", "absent"),
        ("unavailable", "error", "temporary_error", "error"),
    ],
)
def test_graph_state_must_match_for_every_inventory_state(
    state: str,
    status: str,
    reason: str,
    presence: str,
):
    payload = _raw_multi()
    payload["dpe_inventory"] = _inventory(state=state).page.model_copy(
        update={"graph_state": "incoherent"}
    )
    payload["dpe_attribution"] = DpeAttribution(status=status, reason=reason)
    payload["dataset_states"]["dpe"] = DatasetResolutionState(
        dataset="dpe", presence=presence
    )

    with pytest.raises(ValidationError, match="DPE graph states must agree"):
        ResultatMultiResolution.model_validate(payload)


def test_identity_unavailable_cannot_carry_a_building_aggregate():
    payload = _raw_multi()
    payload["dpe_inventory"] = _inventory(state="identity_unavailable").page
    payload["dpe_attribution"] = DpeAttribution(
        status="absent",
        reason="identity_unavailable",
        building_context=DpeBuildingContext(count=2, distribution={"D": 1, "F": 1}),
    )
    payload["dataset_states"]["dpe"] = DatasetResolutionState(
        dataset="dpe", presence="absent"
    )

    with pytest.raises(ValidationError, match="cannot carry building context"):
        ResultatMultiResolution.model_validate(payload)


def test_dpe_state_key_cannot_carry_another_dataset_identity():
    payload = _raw_multi()
    payload["dataset_states"]["dpe"] = DatasetResolutionState(
        dataset="dvf", presence="value"
    )

    with pytest.raises(
        ValidationError, match="DPE coarse state does not match the inventory"
    ):
        ResultatMultiResolution.model_validate(payload)


@pytest.mark.parametrize(
    ("inventory_state", "status", "attribution_reason", "absence_reason"),
    [
        ("ready", "unassigned", "dwelling_identity_unproven", None),
        ("absent", "absent", "not_reported", "not_reported"),
        ("absent", "absent", "not_reported", "source_delayed"),
        ("absent", "absent", "not_reported", "out_of_coverage"),
        (
            "identity_unavailable",
            "absent",
            "identity_unavailable",
            "not_reported",
        ),
        ("unavailable", "error", "temporary_error", "temporary_error"),
    ],
)
def test_dpe_coarse_state_accepts_only_closed_public_reasons(
    inventory_state: str,
    status: str,
    attribution_reason: str,
    absence_reason: str | None,
):
    payload = _raw_multi()
    payload["dpe_inventory"] = _inventory(state=inventory_state).page
    payload["dpe_attribution"] = DpeAttribution(
        status=status, reason=attribution_reason
    )
    presence = {
        "ready": "value",
        "absent": "absent",
        "identity_unavailable": "absent",
        "unavailable": "error",
    }[inventory_state]
    payload["dataset_states"]["dpe"] = DatasetResolutionState(
        dataset="dpe",
        presence=presence,
        absence_reason=absence_reason,
        raison=(
            RESOLUTION_RAISONS[absence_reason] if absence_reason is not None else None
        ),
    )

    result = ResultatMultiResolution.model_validate(payload)

    assert result.dataset_states is not None
    assert result.dataset_states["dpe"].absence_reason == absence_reason


@pytest.mark.parametrize(
    ("inventory_state", "status", "attribution_reason", "presence", "reason"),
    [
        ("ready", "unassigned", "dwelling_identity_unproven", "value", None),
        ("absent", "absent", "not_reported", "absent", "not_reported"),
        (
            "unavailable",
            "error",
            "temporary_error",
            "error",
            "temporary_error",
        ),
    ],
)
def test_dpe_coarse_state_rejects_arbitrary_reason_without_leak(
    inventory_state: str,
    status: str,
    attribution_reason: str,
    presence: str,
    reason: str | None,
):
    private = "private-coarse-state-reason"
    payload = _raw_multi()
    payload["dpe_inventory"] = _inventory(state=inventory_state).page
    payload["dpe_attribution"] = DpeAttribution(
        status=status, reason=attribution_reason
    )
    payload["dataset_states"]["dpe"] = DatasetResolutionState(
        dataset="dpe",
        presence=presence,
        absence_reason=reason,
        raison=private,
    )

    with pytest.raises(
        ValidationError, match="DPE coarse state carries an invalid public reason"
    ) as raised:
        ResultatMultiResolution.model_validate(payload)

    assert private not in str(raised.value)


@pytest.mark.parametrize(
    ("total", "items"),
    [
        (0, []),
        (None, None),
        (1, []),
    ],
)
def test_ready_inventory_requires_positive_total_and_non_empty_first_page(
    total: int | None,
    items: list[dict] | None,
):
    payload = _raw_multi()
    inventory = payload["dpe_inventory"]
    inventory["context_ref"] = "private-ready-context"
    inventory["total"] = total
    if items is not None:
        inventory["items"] = items
    if total == 0:
        inventory["distribution"] = {
            **{letter: 0 for letter in "ABCDEFG"},
            "unknown": 0,
        }

    with pytest.raises(ValidationError) as raised:
        ResultatMultiResolution.model_validate(payload)

    assert "a ready DPE inventory requires a non-empty first page" in str(raised.value)
    assert "private-ready-context" not in str(raised.value)


async def test_parallel_dpe_snapshots_share_the_global_dataset_bound():
    state = {"active": 0, "peak": 0, "started": 0}
    first_wave_full = asyncio.Event()
    all_callers_started = asyncio.Event()
    release = asyncio.Event()

    async def inventory_service(*_args, **_kwargs):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        if state["active"] == 2:
            first_wave_full.set()
        try:
            await asyncio.wait_for(release.wait(), timeout=1.0)
            return _inventory()
        finally:
            state["active"] -= 1

    async def run_one():
        state["started"] += 1
        if state["started"] == 4:
            all_callers_started.set()
        return await query_multiple_datasets(
            AsyncMock(),
            ["dpe"],
            lat=_LAT,
            lon=_LON,
            canonical_ban_id=_BAN_KEY,
        )

    bounded = asyncio.Semaphore(2)
    with (
        patch("app.resolve.multi._dataset_semaphore", bounded),
        patch(
            "app.resolve.multi.load_dpe_inventory_result",
            AsyncMock(side_effect=inventory_service),
        ),
    ):
        tasks = [asyncio.create_task(run_one()) for _ in range(4)]
        try:
            await asyncio.wait_for(all_callers_started.wait(), timeout=1.0)
            await asyncio.wait_for(first_wave_full.wait(), timeout=1.0)
            assert state["peak"] == 2
        finally:
            release.set()
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=1.0
                )
            except TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

    assert state["peak"] == 2
    assert not any(isinstance(result, Exception) for result in results)
    assert all(result["dpe_inventory"].state == "ready" for result in results)


async def test_dpe_keeps_its_first_seen_position_in_public_maps():
    with patch(
        "app.resolve.multi._dispatch_db",
        AsyncMock(return_value=({"available": True}, [])),
    ):
        response = await _appel_resolve(datasets="dpe,monuments,batiments")

    assert response.status_code == 200
    result = response.json()["donnees"][0]
    assert list(result["datasets"]) == ["dpe", "monuments", "batiments"]
    assert list(result["dataset_states"]) == ["dpe", "monuments", "batiments"]


_FORBIDDEN_PROXIMITY_COPY = (
    "dpe du bien",
    "dpe du logement",
    "plus proche",
    "probable",
    "meilleur dpe",
    "correspondance cadastrale",
    "exact",
)


def _dpe_endpoint_texts() -> dict[str, str]:
    from app.dpe.router import get_dpe, router

    route = next(r for r in router.routes if getattr(r, "path", "") == "/v1/dpe")
    return {
        "summary": route.summary or "",
        "description": route.description or "",
        "docstring": get_dpe.__doc__ or "",
    }


async def test_proximity_endpoint_boundary_is_unchanged():
    from app.dpe import router as dpe_router

    signature = inspect.signature(dpe_router.get_dpe)
    assert list(signature.parameters) == [
        "hosting",  # hosted_datasets("dpe"): scope read before any query
        # C52 (B2): the scope requalifies the hosting for the departement of
        # the point (commune referential) -- a dependency, no public parameter.
        "scope",
        "db",
        "lat",
        "lon",
        "rayon_m",
        "etiquette_dpe",
        "annee_construction_min",
        "limite",
        "curseur",
    ]
    texts = _dpe_endpoint_texts()
    assert "/v1/resolve?datasets=dpe" in texts["description"]
    for text in texts.values():
        for forbidden in _FORBIDDEN_PROXIMITY_COPY:
            assert forbidden not in text.lower()
    assert "dpe_par_point(" in inspect.getsource(dpe_router.get_dpe)


async def test_proximity_neighbor_is_never_promoted_by_resolve():
    neighbor = {
        "numero_dpe": "private-neighbor",
        "date_etablissement_dpe": date(2023, 1, 1),
        "type_batiment": "maison",
        "surface_habitable": 95.5,
        "etiquette_dpe": "F",
        "etiquette_ges": "E",
        "code_postal": "85300",
        "code_insee": "85047",
        "nom_commune": "Commune-Test",
        "longitude": _LON,
        "latitude": _LAT,
    }
    async with _auth_client() as client:
        with (
            patch("app.dpe.router.dpe_par_point", AsyncMock(return_value=[neighbor])),
            # C52 (B2): the commune covering the point (referential), hosted.
            patch(
                "app.scope.dependencies.commune_at_point",
                AsyncMock(return_value="85047"),
            ),
        ):
            proximity = await client.get(f"/v1/dpe?lat={_LAT}&lon={_LON}")
    assert proximity.status_code == 200
    assert "private-neighbor" in proximity.text

    resolve = await _appel_resolve(
        service=AsyncMock(return_value=_inventory(state="absent"))
    )
    assert resolve.status_code == 200
    assert "private-neighbor" not in resolve.text


async def _appel_logements(service: AsyncMock):
    async def _fake_db():
        return AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    try:
        async with _auth_client() as client:
            with (
                patch("app.logements.router.load_dpe_inventory_result", service),
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
                return await client.get(
                    "/v1/bien/logements",
                    params={"id_parcelle": _P1["id_parcelle"], "ban_id": _BAN_KEY},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize(
    "state", ["ready", "absent", "identity_unavailable", "unavailable"]
)
async def test_resolve_and_logements_share_inventory_and_decision(state: str):
    inventory = _inventory(state=state)
    resolve = await _appel_resolve(
        service=AsyncMock(return_value=inventory),
        cles=AsyncMock(return_value=()),
    )
    logements = await _appel_logements(AsyncMock(return_value=inventory))

    assert resolve.status_code == logements.status_code == 200
    resolve_data = resolve.json()["donnees"][0]
    logements_data = logements.json()["donnees"]
    assert resolve_data["dpe_inventory"] == logements_data["dpe_inventory"]
    assert resolve_data["dpe_attribution"] == logements_data["dpe_attribution"]
