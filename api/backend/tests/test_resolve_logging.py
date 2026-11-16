"""Privacy regression tests for the polymorphic resolve logging boundary."""

import logging
import logging.config
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import structlog
import yaml
from fastapi import Response
from sqlalchemy import text

from app.ban.queries import _detect_city, _execute_query
from app.bdnb.client import query_batiment_by_adresse
from app.common.exceptions import ErreurApiError, UpstreamUnavailableError
from app.errial.client import query_avis
from app.georisques.client import _safe_json, query_polluted_sites
from app.logging_config import configure_logging
from app.resolve.multi import _query_one_dataset
from app.resolve.router import _parse_forced_type, resolve
from app.rnb.client import query_by_address
from app.sirene.client import entreprises_a_proximite
from app.urbanisme.client import _safe_result
from tests.scope_doubles import FULL_SCOPE
from tests.test_stabilisation_ci_gate import push_triggers

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _workflow(name: str) -> dict:
    path = _REPO_ROOT / ".github/workflows" / name
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("type_force", "private_q", "resolver"),
    [
        ("adresse", "27 avenue sentinelle 44000 Nantes", "_resolve_adresse"),
        ("gps", "47.123456,-1.987654", "_resolve_gps"),
        ("cadastre", "44109000AB0123", "_resolve_cadastre"),
    ],
)
async def test_resolve_logs_only_safe_metadata(
    caplog: pytest.LogCaptureFixture,
    type_force: str,
    private_q: str,
    resolver: str,
) -> None:
    """Address, coordinates, parcel and raw dataset input never reach logs."""
    private_datasets = "dvf,private-dataset-sentinel"
    with (
        caplog.at_level(logging.INFO, logger="app.resolve.router"),
        patch(f"app.resolve.router.{resolver}", AsyncMock(return_value=[])),
    ):
        await resolve(
            db=AsyncMock(),
            scope=FULL_SCOPE,
            response=Response(),
            q=private_q,
            type=type_force,
            datasets=private_datasets,
        )

    assert "resolve_request" in caplog.text
    assert private_q not in caplog.text
    assert private_datasets not in caplog.text
    assert "private-dataset-sentinel" not in caplog.text


@pytest.mark.asyncio
async def test_resolve_error_drops_sensitive_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unhandled resolver errors become generic before the global catch-all."""
    private_q = "8 rue erreur-sentinelle 85000 La Roche-sur-Yon"
    failure = RuntimeError(f"database parameters contained {private_q}")

    with (
        caplog.at_level(logging.ERROR, logger="app.resolve.router"),
        patch("app.resolve.router._resolve_adresse", AsyncMock(side_effect=failure)),
        pytest.raises(ErreurApiError) as exc_info,
    ):
        await resolve(
            db=AsyncMock(),
            scope=FULL_SCOPE,
            response=Response(),
            q=private_q,
            type="adresse",
            datasets=None,
        )

    assert exc_info.value.code == 500
    assert exc_info.value.detail == "La resolution n'a pas pu aboutir."
    assert "resolve_failed" in caplog.text
    assert private_q not in caplog.text
    assert "database parameters" not in caplog.text


@pytest.mark.parametrize(
    "private_q",
    ["coordonnees-privees-invalides", "secret,not-a-number", "91,181"],
)
def test_gps_validation_errors_do_not_echo_private_input(private_q: str) -> None:
    """Validation details describe the contract without reflecting input."""
    with pytest.raises(ErreurApiError) as exc_info:
        _parse_forced_type("gps", private_q)

    assert private_q not in (exc_info.value.detail or "")
    for fragment in private_q.replace(",", " ").split():
        assert fragment not in (exc_info.value.detail or "")


class _FailingSession:
    def begin_nested(self) -> "_FailingSession":
        return self

    async def __aenter__(self) -> "_FailingSession":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("database failure carrying private SQL parameters")


@pytest.mark.asyncio
async def test_ban_failures_do_not_log_query_or_params(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """BAN fallback warnings contain only an error type."""
    private_query = "14 rue ban-sentinelle ville-privee"
    private_param = "private-param-sentinel"

    with caplog.at_level(logging.WARNING, logger="app.ban.queries"):
        assert await _detect_city(_FailingSession(), private_query) is None
        assert (
            await _execute_query(
                _FailingSession(),
                text("SELECT 1"),
                {"query": private_param},
                "",
            )
            == []
        )

    assert "city_detection_failed" in caplog.text
    assert "geocode_query_failed" in caplog.text
    assert private_query not in caplog.text
    assert private_param not in caplog.text
    assert "private SQL parameters" not in caplog.text


@pytest.mark.asyncio
async def test_multi_dataset_failure_drops_coordinates_and_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per-dataset degradation logs neither location nor exception message."""
    lat = 47.123456
    lon = -1.987654
    failure = RuntimeError(f"upstream leaked ({lat}, {lon})")

    with (
        caplog.at_level(logging.ERROR, logger="app.resolve.multi"),
        patch("app.resolve.multi._dispatch_http", AsyncMock(side_effect=failure)),
    ):
        await _query_one_dataset("rnb", AsyncMock(), lat, lon, None)

    assert "dataset_query_failed" in caplog.text
    assert str(lat) not in caplog.text
    assert str(lon) not in caplog.text
    assert "upstream leaked" not in caplog.text


def test_logging_config_suppresses_raw_url_loggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structured logging keeps the unsafe Uvicorn access logger silent."""
    captured: dict = {}
    monkeypatch.setattr(logging.config, "dictConfig", captured.update)
    monkeypatch.setattr(structlog, "configure", lambda **kwargs: None)

    configure_logging()

    for logger_name in ("uvicorn.access", "httpx", "httpcore"):
        assert captured["loggers"][logger_name] == {
            "handlers": [],
            "level": "CRITICAL",
            "propagate": False,
        }


def test_georisques_error_log_drops_url_and_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_url = "https://example.invalid/ssp?q=private-address-sentinel"
    private_body = "private-response-body-sentinel"
    response = httpx.Response(
        503,
        request=httpx.Request("GET", private_url),
        content=private_body,
    )

    with caplog.at_level(logging.WARNING, logger="app.georisques.client"):
        assert _safe_json(response) is None

    assert "georisques_response_error" in caplog.text
    assert "private-address-sentinel" not in caplog.text
    assert private_body not in caplog.text


@pytest.mark.parametrize(
    "result",
    [
        httpx.ConnectError(
            "request failed for https://example.invalid/?geom=private-geom-sentinel"
        ),
        httpx.Response(
            503,
            request=httpx.Request(
                "GET", "https://example.invalid/?geom=private-geom-sentinel"
            ),
            content="private-response-body-sentinel",
        ),
    ],
)
def test_apicarto_error_log_drops_exception_url_and_response_body(
    caplog: pytest.LogCaptureFixture,
    result: object,
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.urbanisme.client"):
        assert _safe_result("zone-urba", result) is None

    assert "private-geom-sentinel" not in caplog.text
    assert "private-response-body-sentinel" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("async_client_target", "invoke"),
    [
        (
            "app.bdnb.client.httpx.AsyncClient",
            lambda: query_batiment_by_adresse("private-address-key-sentinel"),
        ),
        (
            "app.errial.client.httpx.AsyncClient",
            lambda: query_avis("99999", "private-parcel-sentinel"),
        ),
        (
            "app.georisques.client.httpx.AsyncClient",
            lambda: query_polluted_sites(47.123456, -1.987654),
        ),
        (
            "app.rnb.client.httpx.AsyncClient",
            lambda: query_by_address("private-address-sentinel"),
        ),
        (
            "app.sirene.client.httpx.AsyncClient",
            lambda: entreprises_a_proximite(47.123456, -1.987654),
        ),
    ],
)
async def test_upstream_client_failures_drop_private_exception_text(
    caplog: pytest.LogCaptureFixture,
    async_client_target: str,
    invoke,
) -> None:
    """Client exceptions keep their raw request URL out of logs and errors."""
    private_fragment = "private-upstream-url-sentinel"
    failure = httpx.ConnectError(
        f"failed GET https://example.invalid/?q={private_fragment}"
    )

    with (
        patch(async_client_target) as client_class,
        caplog.at_level(logging.WARNING),
    ):
        client = AsyncMock()
        client.get.side_effect = failure
        client_class.return_value.__aenter__ = AsyncMock(return_value=client)
        client_class.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises(UpstreamUnavailableError) as exc_info:
            await invoke()

    assert private_fragment not in caplog.text
    assert private_fragment not in str(exc_info.value)


def test_launchers_and_proxy_do_not_emit_query_string_access_logs() -> None:
    """Every repository-managed HTTP layer disables raw-URI access logs."""
    launchers = [
        "api/docker/Dockerfile",
        "api/docker/Dockerfile.dev",
        "api/docker-compose.local.yml",
    ]
    for relative_path in launchers:
        content = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "--no-access-log" in content, relative_path

    proxies = ["api/Caddyfile", "api/Caddyfile.vps", "api/Caddyfile.dev"]
    for relative_path in proxies:
        content = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        directives = {
            line.strip().split(maxsplit=1)[0]
            for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        assert "log" not in directives, relative_path


# Two privacy guards lived here until plan 01-07: the Caddy reload/purge order
# of `cd-api.yml` and the privacy-safe image pin of `rollback-api.yml`. Both
# workflows were removed with the stopped `datacommune-prod` runner (D-05,
# `docs/REPO-STATE.md`), so the shell steps they asserted on no longer exist --
# a contract test goes with the workflow it describes, and only with it.
#
# What those two tests proved about the *configuration* is NOT lost: the
# repository-managed HTTP layers are still guarded above by
# `test_launchers_and_proxy_do_not_emit_query_string_access_logs`
# (`--no-access-log` on every launcher, no `log` directive in any Caddyfile).
# What IS no longer covered is the deploy-time *sequencing* of that config --
# there is no deploy in this repository to sequence.


def test_ci_path_filters_cover_deploy_and_live_workflow_contracts() -> None:
    """Workflow-only changes still execute their static regression tests.

    The deploy and live-replay halves of this guard (``cd-api.yml``,
    ``rollback-api.yml``, ``verify-golden-live.yml``) went with those workflows at
    plan 01-07. What remains is the half that still has a subject: a workflow
    file or a script that CI executes must re-run its own contract test when it
    changes, and each kept workflow must run on every push that touches it or
    a script it executes (quick 261024-f9r skips pushes touching only docs or the
    internal tracking).
    """
    ci_api = _workflow("ci-api.yml")
    ci_data = _workflow("ci-data-contracts.yml")

    assert ".github/workflows/ci-api.yml" in ci_api["on"]["pull_request"]["paths"]
    for path in (
        ".github/workflows/ci-data-contracts.yml",
        "api/backend/scripts/select_golden_cases.py",
        "api/backend/scripts/verify_golden_cases.py",
    ):
        assert path in ci_data["on"]["pull_request"]["paths"]
    for name, workflow in (("ci-api.yml", ci_api), ("ci-data-contracts.yml", ci_data)):
        push = workflow["on"]["push"]
        assert push["branches"] == ["master"]
        for changed in (
            f".github/workflows/{name}",
            "api/backend/scripts/select_golden_cases.py",
            "api/backend/scripts/verify_golden_cases.py",
        ):
            assert push_triggers(push, changed)
        assert workflow["jobs"]
