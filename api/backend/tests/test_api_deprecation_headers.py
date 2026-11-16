"""HTTP proof of the deprecation of the two legacy ``/v1/bien`` routes (04-13).

``/v1/bien/logements`` and ``/v1/bien/dpe/document`` are announced deprecated
(api/docs/api-deprecation-policy.md § 3): ``deprecated: true`` in OpenAPI is not
enough, the responses themselves must carry ``Deprecation`` (RFC 9745,
structured date ``@<epoch>``) and ``Sunset`` (RFC 8594, IMF-fixdate). These
tests read the headers on the mounted application, success and errors alike —
including the publication refusal raised by a dependency before the handler —
parse them, compare the instants and check the beta minimum delay. Neighbour
routes (inventory, administrative comparison, ``/v1/resolve``) stay
undeprecated. No route is removed: the routes keep answering.

Counter-proof (04-13): removing the header emission while keeping
``deprecated=True`` turns the header tests red.
"""

import re
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from unittest.mock import AsyncMock

import pytest

from app.common.publication_readiness import require_publication
from app.database import get_db
from app.dpe.inventory_service import DpeInventoryServiceResult
from app.dpe.references import InvalidReferenceError, RefreshRequiredError
from app.dpe.schemas import (
    DpeConsultedDocument,
    DpeInventoryDistribution,
    DpeInventoryPage,
)
from app.main import app

pytestmark = pytest.mark.smoke

# Constants of the decision, pinned literally: a header recomputed per request
# (e.g. "now + 30 days") would drift away from these values.
DEPRECATION_HEADER = "@1791244800"  # 2026-10-06T00:00:00Z
SUNSET_HEADER = "Wed, 06 Jan 2027 00:00:00 GMT"
BETA_MINIMUM_DELAY = timedelta(days=30)

DEPRECATED_PATHS = ("/v1/bien/logements", "/v1/bien/dpe/document")
CONTEXT = "opaque-context"
DOCUMENT = "opaque-document"
PARCELLE = "85047000AB0042"
_STRUCTURED_DATE = re.compile(r"^@(-?\d{1,15})$")


def _parse_deprecation(value: str) -> datetime:
    """RFC 9745: an RFC 9651 Date item, ``@`` followed by integer epoch seconds."""
    match = _STRUCTURED_DATE.fullmatch(value)
    assert match, f"Deprecation is not a structured-field date: {value!r}"
    return datetime.fromtimestamp(int(match.group(1)), tz=UTC)


def _parse_sunset(value: str) -> datetime:
    """RFC 8594: an HTTP-date, which senders must emit in IMF-fixdate form."""
    instant = parsedate_to_datetime(value)
    assert instant.tzinfo is not None, f"Sunset has no zone: {value!r}"
    assert format_datetime(instant.astimezone(UTC), usegmt=True) == value, (
        f"Sunset is not an IMF-fixdate: {value!r}"
    )
    return instant


def assert_deprecated(response) -> None:
    deprecation = response.headers.get("deprecation")
    sunset = response.headers.get("sunset")
    assert deprecation is not None, "Deprecation header missing"
    assert sunset is not None, "Sunset header missing"
    deprecated_at = _parse_deprecation(deprecation)
    sunset_at = _parse_sunset(sunset)
    assert sunset_at - deprecated_at >= BETA_MINIMUM_DELAY
    assert (deprecation, sunset) == (DEPRECATION_HEADER, SUNSET_HEADER)


def assert_not_deprecated(response) -> None:
    assert "deprecation" not in response.headers
    assert "sunset" not in response.headers


def _document() -> DpeConsultedDocument:
    return DpeConsultedDocument(
        context_ref=CONTEXT,
        document_ref=DOCUMENT,
        type_batiment="appartement",
        etiquette_dpe="D",
        validity_by_date="unknown",
    )


def _identity_unavailable() -> DpeInventoryServiceResult:
    return DpeInventoryServiceResult(
        page=DpeInventoryPage(
            state="identity_unavailable",
            total=None,
            distribution=DpeInventoryDistribution(),
            page_size=20,
            has_more=False,
            read_state="complete",
        ),
        apartment_document_count=None,
    )


class _Result:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar_one(self) -> bool:
        return self._value


class _UnpublishedSession:
    """Read lock granted, then the readiness statement says "not published"."""

    def __init__(self) -> None:
        self._answers = iter((True, False))

    async def execute(self, *_args, **_kwargs) -> _Result:
        return _Result(next(self._answers))


@pytest.fixture()
def logements_reads(monkeypatch):
    """Patch the reads of /v1/bien/logements; no database is opened."""

    async def fake_db():
        return AsyncMock()

    app.dependency_overrides[get_db] = fake_db
    monkeypatch.setattr(
        "app.logements.router.load_dpe_inventory_result",
        AsyncMock(return_value=_identity_unavailable()),
    )
    monkeypatch.setattr(
        "app.logements.router.copropriete_par_parcelle", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.logements.router.dvf_appartements_par_parcelle",
        AsyncMock(return_value=[]),
    )
    yield
    app.dependency_overrides.pop(get_db, None)


class TestLogementsRoute:
    async def test_success_keeps_its_behaviour_and_announces_deprecation(
        self, client, logements_reads
    ):
        response = await client.get(
            "/v1/bien/logements", params={"id_parcelle": PARCELLE}
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()["donnees"]
        assert body["id_parcelle"] == PARCELLE
        assert body["dpe_attribution"]["status"] == "absent"
        assert_deprecated(response)

    async def test_validation_error_announces_deprecation(self, client):
        response = await client.get(
            "/v1/bien/logements", params={"id_parcelle": "trop-court"}
        )
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        # The detailed validation body of this route is unchanged.
        assert "id_parcelle" in response.json()["detail"]
        assert_deprecated(response)

    async def test_out_of_coverage_keeps_no_store_and_announces_deprecation(
        self, client, monkeypatch, logements_reads
    ):
        from app.scope import InstanceScope
        from app.scope.dependencies import get_instance_scope

        scope = InstanceScope(datasets=frozenset({"ban"}), departements=("85",))
        app.dependency_overrides[get_instance_scope] = lambda: scope
        response = await client.get(
            "/v1/bien/logements", params={"id_parcelle": PARCELLE}
        )
        assert response.status_code == 409
        assert response.headers["cache-control"] == "no-store"
        assert_deprecated(response)

    async def test_publication_refusal_before_handler_keeps_no_store(self, client):
        app.dependency_overrides.pop(require_publication)
        session = _UnpublishedSession()

        async def unpublished_db():
            return session

        app.dependency_overrides[get_db] = unpublished_db
        response = await client.get(
            "/v1/bien/logements", params={"id_parcelle": PARCELLE}
        )
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert_deprecated(response)


class TestDocumentRoute:
    async def test_success_keeps_its_behaviour_and_announces_deprecation(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            "app.logements.router.load_dpe_document",
            AsyncMock(return_value=_document()),
        )
        response = await client.get(
            "/v1/bien/dpe/document",
            params={"contexte": CONTEXT, "reference": DOCUMENT},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["document_ref"] == DOCUMENT
        assert_deprecated(response)

    @pytest.mark.parametrize(
        ("error", "status"),
        [(InvalidReferenceError(), 400), (RefreshRequiredError(), 409)],
    )
    async def test_unknown_document_keeps_no_store_and_announces_deprecation(
        self, client, monkeypatch, error, status
    ):
        monkeypatch.setattr(
            "app.logements.router.load_dpe_document",
            AsyncMock(side_effect=error),
        )
        response = await client.get(
            "/v1/bien/dpe/document",
            params={"contexte": CONTEXT, "reference": "unknown-document"},
        )
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"
        assert_deprecated(response)

    async def test_validation_error_keeps_no_store_and_announces_deprecation(
        self, client
    ):
        response = await client.get(
            "/v1/bien/dpe/document", params={"contexte": CONTEXT}
        )
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
        assert_deprecated(response)

    async def test_publication_refusal_before_handler_keeps_no_store(self, client):
        app.dependency_overrides.pop(require_publication)
        session = _UnpublishedSession()

        async def unpublished_db():
            return session

        app.dependency_overrides[get_db] = unpublished_db
        response = await client.get(
            "/v1/bien/dpe/document",
            params={"contexte": CONTEXT, "reference": DOCUMENT},
        )
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert_deprecated(response)

    async def test_service_unavailable_keeps_no_store(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.logements.router.load_dpe_document",
            AsyncMock(side_effect=RuntimeError("down")),
        )
        response = await client.get(
            "/v1/bien/dpe/document",
            params={"contexte": CONTEXT, "reference": DOCUMENT},
        )
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert_deprecated(response)


@pytest.mark.parametrize("path", DEPRECATED_PATHS)
async def test_auth_refusal_announces_deprecation(path):
    """Refused before any data read (no API key): still announced, no-store kept."""
    from httpx import ASGITransport, AsyncClient

    app.dependency_overrides.clear()
    params = (
        {"id_parcelle": PARCELLE}
        if path == "/v1/bien/logements"
        else {"contexte": CONTEXT, "reference": DOCUMENT}
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as public_client:
        response = await public_client.get(path, params=params)
    assert response.status_code in {401, 403}
    if path == "/v1/bien/dpe/document":
        assert response.headers["cache-control"] == "no-store"
    assert_deprecated(response)


class TestNeighboursStayUndeprecated:
    async def test_inventory_success_and_error(self, client, monkeypatch):
        page = DpeInventoryPage(
            state="absent",
            context_ref=CONTEXT,
            total=0,
            distribution=DpeInventoryDistribution(),
            page_size=20,
            has_more=False,
            read_state="complete",
        )
        monkeypatch.setattr(
            "app.logements.router.load_dpe_inventory", AsyncMock(return_value=page)
        )
        ok = await client.get("/v1/bien/dpe/inventaire", params={"contexte": CONTEXT})
        assert ok.status_code == 200
        assert_not_deprecated(ok)
        invalid = await client.get("/v1/bien/dpe/inventaire")
        assert invalid.status_code == 422
        assert_not_deprecated(invalid)

    async def test_administrative_comparison(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.logements.router.load_dpe_inventory",
            AsyncMock(side_effect=RefreshRequiredError()),
        )
        refused = await client.post(
            "/v1/bien/dpe/comparaison", json={"contexte": CONTEXT}
        )
        assert refused.status_code == 409
        assert_not_deprecated(refused)
        invalid = await client.post("/v1/bien/dpe/comparaison", json={})
        assert invalid.status_code == 422
        assert_not_deprecated(invalid)

    async def test_resolve(self, client):
        response = await client.get("/v1/resolve")
        assert response.status_code == 422
        assert_not_deprecated(response)


def test_openapi_marks_exactly_the_two_routes_with_their_replacements():
    schema = app.openapi()
    logements = schema["paths"]["/v1/bien/logements"]["get"]
    document = schema["paths"]["/v1/bien/dpe/document"]["get"]
    assert logements.get("deprecated") is True
    assert document.get("deprecated") is True
    assert "/v1/adresse/{ban_id}" in logements["description"]
    assert "/v1/dpe/{numero}" in document["description"]
    for operation in (logements, document):
        for status, response in operation["responses"].items():
            headers = response.get("headers", {})
            assert {"Deprecation", "Sunset"} <= set(headers), status
    for path, method in (
        ("/v1/bien/dpe/inventaire", "get"),
        ("/v1/bien/dpe/comparaison", "post"),
        ("/v1/resolve", "get"),
    ):
        assert not schema["paths"][path][method].get("deprecated", False), path
