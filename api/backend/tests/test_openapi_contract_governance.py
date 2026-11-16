"""Guard the /v1 governance banner in the published OpenAPI description (OWD-03 C4).

The banner is what makes the beta status *declared* instead of implicit: what is
published without a stability notice is presumed stable by its consumers. This
test fails if the banner (or the stable-error-contract statement) is dropped or
reworded out of the description, forcing that removal to be a conscious,
reviewable decision. Reference policy: api/docs/api-deprecation-policy.md.

Runs in-process via ``app.openapi()`` — no DB, no network (same approach as
test_openapi_snapshot.py).
"""

from pathlib import Path

import pytest

from app.main import app

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def description() -> str:
    return app.openapi()["info"]["description"]


def test_beta_status_banner_is_published(description: str) -> None:
    """The beta-status banner must open the contract-governance section."""
    assert "## Statut du contrat : beta publique" in description
    assert "sans garantie de compatibilite ascendante" in description


def test_deprecation_policy_is_announced(description: str) -> None:
    """Removals must be announced via RFC 8594 headers with a minimum delay."""
    assert "`Deprecation`" in description
    assert "`Sunset`" in description
    assert "RFC 8594" in description
    assert "30 jours" in description


def test_stable_error_contract_is_documented(description: str) -> None:
    """Stable error contract = HTTP status + Retry-After + problem+json status.

    The human-facing texts (title/detail) are explicitly NOT part of the
    contract — clients must be told not to parse them.
    """
    assert "`Retry-After`" in description
    assert "application/problem+json" in description
    assert "RFC 9457" in description
    assert "ne les parsez pas" in description


# 04-13: the announced deprecations. The quarantined routes were marked
# deprecated earlier (they only ever answer 503, see
# tests/test_unqualified_surfaces.py) and carry no Sunset.
_ANNOUNCED = {
    ("get", "/v1/bien/logements"): "/v1/adresse/{ban_id}",
    ("get", "/v1/bien/dpe/document"): "/v1/dpe/{numero}",
}
_QUARANTINED = {
    ("get", "/v1/encadrement-loyers"),
    ("get", "/v1/scoring/renov"),
    ("get", "/v1/scoring/renov-target"),
}
_POLICY = Path(__file__).parents[2] / "docs" / "api-deprecation-policy.md"


def test_deprecated_operations_are_exactly_the_announced_and_quarantined() -> None:
    deprecated = {
        (method, path)
        for path, operations in app.openapi()["paths"].items()
        for method, operation in operations.items()
        if isinstance(operation, dict) and operation.get("deprecated")
    }
    assert deprecated == set(_ANNOUNCED) | _QUARANTINED


def test_policy_lists_the_announced_deprecations_with_their_headers() -> None:
    from email.utils import format_datetime

    from app.logements.router import _DEPRECATED_AT, _SUNSET_AT, DEPRECATED_PATHS

    assert {path for _method, path in _ANNOUNCED} == DEPRECATED_PATHS
    policy = _POLICY.read_text(encoding="utf-8")
    assert "Aucun élément de `/v1` n'est déprécié" not in policy
    for (_method, path), replacement in _ANNOUNCED.items():
        assert f"`{path}`" in policy
        assert f"`{replacement}`" in policy
    assert f"`@{int(_DEPRECATED_AT.timestamp())}`" in policy
    assert f"`{format_datetime(_SUNSET_AT, usegmt=True)}`" in policy
    for _method, path in _QUARANTINED:
        assert f"`{path}`" in policy
