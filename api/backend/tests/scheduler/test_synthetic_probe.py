"""Unit tests for the private synthetic golden probe (HEALTH-02, D-01..D-04).

The probe replays an opaque golden ``case_id`` against the FINAL contract of
``app/resolve/router.py`` and asserts the D-03 critical block:

  - resolution identity is valid (non-empty, exact),
  - expected ``code_insee``,
  - expected ``id_parcelle``,
  - the ``cadastre`` dataset block is present,
  - the ``dvf`` block is present ONLY for a ``with_dvf`` case.

Every degradation (timeout, 4xx/5xx, false invariant, invalid fixture, or a
failed Apprise delivery) must raise / exit non-zero. Only D-02 fields
(``case_id``, duration, invariant booleans, normalized error codes) may reach a
log record — never the raw query, address, coordinates, INSEE or parcel value.

All HTTP is simulated with ``httpx.MockTransport`` — no network, no live API.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.scheduler import synthetic_probe as sp

# A deliberately recognisable private sentinel: it appears in the fixture's
# exact query AND in the expected identity, so any leak into logs/stdout is
# detectable by substring search.
SENTINEL_QUERY = "SENTINEL-12-rue-des-secrets-Nantes"
SENTINEL_INSEE = "SENTINEL44109"
SENTINEL_PARCELLE = "SENTINEL44109000AB0001"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _write_fixture(tmp_path: Path, cases: list[dict], version: int = 1) -> str:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"version": version, "cases": cases}), encoding="utf-8")
    return str(path)


def _case(
    *,
    case_id: str = "gc-0001",
    query: str = SENTINEL_QUERY,
    datasets: list[str] | None = None,
    expected_code_insee: str = SENTINEL_INSEE,
    expected_id_parcelle: str = SENTINEL_PARCELLE,
    with_dvf: bool = False,
    type_: str | None = None,
) -> dict:
    case: dict = {
        "case_id": case_id,
        "query": query,
        "datasets": datasets if datasets is not None else ["cadastre"],
        "expected_code_insee": expected_code_insee,
        "expected_id_parcelle": expected_id_parcelle,
        "with_dvf": with_dvf,
    }
    if type_ is not None:
        case["type"] = type_
    return case


def _resolve_body(
    *,
    code_insee: str = SENTINEL_INSEE,
    id_parcelle: str = SENTINEL_PARCELLE,
    resolution: str = "exact",
    datasets_block: dict | None = None,
    empty: bool = False,
) -> dict:
    if empty:
        return {"donnees": [], "meta": {}}
    return {
        "donnees": [
            {
                "type_detecte": "cadastre",
                "latitude": 47.21,
                "longitude": -1.55,
                "id_parcelle": id_parcelle,
                "code_insee": code_insee,
                "resolution": resolution,
                "datasets": datasets_block
                if datasets_block is not None
                else {"cadastre": [{"id_parcelle": id_parcelle}]},
            }
        ],
        "meta": {},
    }


def _client(responses: list[httpx.Response]) -> httpx.Client:
    """Build an httpx.Client whose transport replays *responses* in order."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/resolve"
        assert request.headers.get("x-api-key")  # key always sent
        return queue.pop(0)

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.example.test",
        headers={"X-API-Key": "dc_probe_test"},
    )


def _ok(alert_calls: list) -> sp.AlertFn:
    def _fn(failures) -> bool:
        alert_calls.append(list(failures))
        return True

    return _fn


# --------------------------------------------------------------------------- #
# D-03 nominal — each invariant covered by a distinct passing case
# --------------------------------------------------------------------------- #
def test_nominal_identity_insee_parcelle_cadastre_pass(tmp_path):
    fixture = _write_fixture(tmp_path, [_case()])
    client = _client([httpx.Response(200, json=_resolve_body())])
    report = sp.run_probe(fixture_path=fixture, client=client, api_key="dc_probe")
    assert report.cases_total == 1
    assert report.cases_passed == 1
    assert report.failures == []


def test_nominal_dvf_present_when_with_dvf(tmp_path):
    fixture = _write_fixture(
        tmp_path,
        [_case(datasets=["cadastre", "dvf"], with_dvf=True)],
    )
    body = _resolve_body(
        datasets_block={
            "cadastre": [{"id_parcelle": SENTINEL_PARCELLE}],
            "dvf": [{"id_parcelle": SENTINEL_PARCELLE, "valeur_fonciere": 150000.0}],
        }
    )
    client = _client([httpx.Response(200, json=body)])
    report = sp.run_probe(fixture_path=fixture, client=client, api_key="dc_probe")
    assert report.cases_passed == 1


def test_dvf_absence_ignored_when_not_with_dvf(tmp_path):
    # A case NOT flagged with_dvf must pass even though dvf is absent.
    fixture = _write_fixture(tmp_path, [_case(with_dvf=False)])
    client = _client([httpx.Response(200, json=_resolve_body())])
    report = sp.run_probe(fixture_path=fixture, client=client, api_key="dc_probe")
    assert report.cases_passed == 1


# --------------------------------------------------------------------------- #
# D-03 negatives — each degradation must raise (non-zero)
# --------------------------------------------------------------------------- #
def test_identity_absent_raises(tmp_path):
    fixture = _write_fixture(tmp_path, [_case()])
    client = _client([httpx.Response(200, json=_resolve_body(empty=True))])
    alerts: list = []
    with pytest.raises(sp.ProbeError):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=_ok(alerts)
        )
    assert alerts and alerts[0][0].error_code == "identity_absent"


def test_malformed_donnees_object_raises_with_alert(tmp_path):
    # A 200 whose "donnees" is a dict (not a list) must fail closed via the alert
    # path — regression: donnees[0] used to KeyError and bypass the alert.
    fixture = _write_fixture(tmp_path, [_case()])
    client = _client(
        [httpx.Response(200, json={"donnees": {"unexpected": True}, "meta": {}})]
    )
    alerts: list = []
    with pytest.raises(sp.ProbeError):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=_ok(alerts)
        )
    assert alerts and alerts[0][0].error_code == "identity_absent"


def test_malformed_result_not_object_raises_with_alert(tmp_path):
    # "donnees" is a non-empty list but its element is not an object.
    fixture = _write_fixture(tmp_path, [_case()])
    client = _client([httpx.Response(200, json={"donnees": ["oops"], "meta": {}})])
    alerts: list = []
    with pytest.raises(sp.ProbeError):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=_ok(alerts)
        )
    assert alerts and alerts[0][0].error_code == "result_malformed"


def test_unexpected_crash_in_check_still_alerts(tmp_path, monkeypatch):
    # Any unexpected exception inside a case check must be converted to a
    # ProbeFailure and routed through the alert path, never bypass it.
    fixture = _write_fixture(tmp_path, [_case()])

    def boom(client, case):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(sp, "_check_case", boom)
    client = _client([httpx.Response(200, json=_resolve_body())])
    alerts: list = []
    with pytest.raises(sp.ProbeError):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=_ok(alerts)
        )
    assert alerts and alerts[0][0].error_code == "unexpected_error"


def test_resolution_not_exact_raises(tmp_path):
    fixture = _write_fixture(tmp_path, [_case()])
    body = _resolve_body(resolution="ambiguous")
    client = _client([httpx.Response(200, json=body)])
    with pytest.raises(sp.ProbeError):
        sp.run_probe(fixture_path=fixture, client=client, api_key="k", alert=_ok([]))


def test_code_insee_mismatch_raises(tmp_path):
    fixture = _write_fixture(tmp_path, [_case()])
    body = _resolve_body(code_insee="WRONG00000")
    client = _client([httpx.Response(200, json=body)])
    alerts: list = []
    with pytest.raises(sp.ProbeError):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=_ok(alerts)
        )
    assert alerts[0][0].error_code == "code_insee_mismatch"


def test_id_parcelle_mismatch_raises(tmp_path):
    fixture = _write_fixture(tmp_path, [_case()])
    body = _resolve_body(id_parcelle="WRONG000AB9999")
    client = _client([httpx.Response(200, json=body)])
    alerts: list = []
    with pytest.raises(sp.ProbeError):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=_ok(alerts)
        )
    assert alerts[0][0].error_code == "id_parcelle_mismatch"


def test_cadastre_absent_raises(tmp_path):
    fixture = _write_fixture(tmp_path, [_case()])
    body = _resolve_body(datasets_block={})  # no cadastre block
    client = _client([httpx.Response(200, json=body)])
    alerts: list = []
    with pytest.raises(sp.ProbeError):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=_ok(alerts)
        )
    assert alerts[0][0].error_code == "cadastre_absent"


def test_dvf_absent_raises_when_with_dvf(tmp_path):
    fixture = _write_fixture(
        tmp_path, [_case(datasets=["cadastre", "dvf"], with_dvf=True)]
    )
    body = _resolve_body()  # cadastre only, no dvf
    client = _client([httpx.Response(200, json=body)])
    alerts: list = []
    with pytest.raises(sp.ProbeError):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=_ok(alerts)
        )
    assert alerts[0][0].error_code == "dvf_absent"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503])
def test_http_error_status_raises(tmp_path, status):
    fixture = _write_fixture(tmp_path, [_case()])
    client = _client([httpx.Response(status, json={"detail": "nope"})])
    alerts: list = []
    with pytest.raises(sp.ProbeError):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=_ok(alerts)
        )
    assert alerts[0][0].error_code == f"http_{status}"


def test_timeout_raises():
    # A transport that raises a timeout for every request.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.example.test",
    )
    # fixture built inline via a temp file is unnecessary — use in-memory path
    # by passing cases directly is not supported; write a tiny fixture instead.
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        fixture = _write_fixture(Path(d), [_case()])
        alerts: list = []
        with pytest.raises(sp.ProbeError):
            sp.run_probe(
                fixture_path=fixture, client=client, api_key="k", alert=_ok(alerts)
            )
        assert alerts[0][0].error_code == "timeout"


# --------------------------------------------------------------------------- #
# Fixture validation — closed schema, opaque case_id, mandatory env
# --------------------------------------------------------------------------- #
def test_missing_fixture_path_raises(monkeypatch):
    monkeypatch.delenv("SYNTHETIC_PROBE_FIXTURE_PATH", raising=False)
    with pytest.raises(sp.ProbeError):
        sp.run_probe(client=_client([]), api_key="k")


def test_nonexistent_fixture_file_raises(tmp_path):
    with pytest.raises(sp.ProbeError):
        sp.run_probe(
            fixture_path=str(tmp_path / "absent.json"),
            client=_client([]),
            api_key="k",
        )


def test_unknown_top_level_key_rejected(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps({"version": 1, "cases": [_case()], "leak": SENTINEL_QUERY}),
        encoding="utf-8",
    )
    with pytest.raises(sp.ProbeError):
        sp.run_probe(fixture_path=str(path), client=_client([]), api_key="k")


def test_unknown_case_key_rejected(tmp_path):
    bad = _case()
    bad["surprise"] = "x"
    fixture = _write_fixture(tmp_path, [bad])
    with pytest.raises(sp.ProbeError):
        sp.run_probe(fixture_path=fixture, client=_client([]), api_key="k")


def test_missing_required_case_field_rejected(tmp_path):
    bad = _case()
    del bad["expected_id_parcelle"]
    fixture = _write_fixture(tmp_path, [bad])
    with pytest.raises(sp.ProbeError):
        sp.run_probe(fixture_path=fixture, client=_client([]), api_key="k")


def test_non_opaque_case_id_rejected(tmp_path):
    # A case_id containing whitespace/PII-looking text is refused (D-01/D-02).
    fixture = _write_fixture(tmp_path, [_case(case_id="12 rue des secrets")])
    with pytest.raises(sp.ProbeError):
        sp.run_probe(fixture_path=fixture, client=_client([]), api_key="k")


def test_empty_cases_rejected(tmp_path):
    fixture = _write_fixture(tmp_path, [])
    with pytest.raises(sp.ProbeError):
        sp.run_probe(fixture_path=fixture, client=_client([]), api_key="k")


# --------------------------------------------------------------------------- #
# Alerting — a failed Apprise delivery is itself a non-zero condition
# --------------------------------------------------------------------------- #
def test_false_apprise_delivery_raises(tmp_path):
    fixture = _write_fixture(tmp_path, [_case()])
    client = _client([httpx.Response(200, json=_resolve_body(empty=True))])

    def failing_alert(failures) -> bool:
        return False

    with pytest.raises(sp.ProbeAlertError):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=failing_alert
        )


def test_successful_alert_still_nonzero(tmp_path):
    fixture = _write_fixture(tmp_path, [_case()])
    client = _client([httpx.Response(200, json=_resolve_body(empty=True))])
    with pytest.raises(sp.ProbeFailed):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=lambda f: True
        )


def test_main_returns_nonzero_on_failure(tmp_path, monkeypatch):
    fixture = _write_fixture(tmp_path, [_case()])
    client = _client([httpx.Response(200, json=_resolve_body(empty=True))])
    monkeypatch.setenv("SYNTHETIC_PROBE_FIXTURE_PATH", fixture)
    monkeypatch.setattr(sp, "_build_client", lambda **kw: client)
    monkeypatch.setattr(sp, "_send_apprise_alert", lambda failures: True)
    assert sp.main() == 1


def test_main_returns_zero_on_success(tmp_path, monkeypatch):
    fixture = _write_fixture(tmp_path, [_case()])
    client = _client([httpx.Response(200, json=_resolve_body())])
    monkeypatch.setenv("SYNTHETIC_PROBE_FIXTURE_PATH", fixture)
    monkeypatch.setattr(sp, "_build_client", lambda **kw: client)
    assert sp.main() == 0


# --------------------------------------------------------------------------- #
# Redaction — no private sentinel may reach logs or stdout/stderr
# --------------------------------------------------------------------------- #
def test_no_private_sentinel_in_logs_on_success(tmp_path, caplog, capsys):
    fixture = _write_fixture(tmp_path, [_case()])
    client = _client([httpx.Response(200, json=_resolve_body())])
    with caplog.at_level("DEBUG"):
        sp.run_probe(fixture_path=fixture, client=client, api_key="dc_probe")
    captured = capsys.readouterr()
    blob = caplog.text + captured.out + captured.err
    for sentinel in (SENTINEL_QUERY, SENTINEL_INSEE, SENTINEL_PARCELLE):
        assert sentinel not in blob


def test_no_private_sentinel_in_logs_on_failure(tmp_path, caplog, capsys):
    fixture = _write_fixture(tmp_path, [_case()])
    body = _resolve_body(code_insee="WRONG00000")
    client = _client([httpx.Response(200, json=body)])
    with caplog.at_level("DEBUG"), pytest.raises(sp.ProbeError):
        sp.run_probe(
            fixture_path=fixture, client=client, api_key="k", alert=lambda f: True
        )
    captured = capsys.readouterr()
    blob = caplog.text + captured.out + captured.err
    for sentinel in (SENTINEL_QUERY, SENTINEL_INSEE, SENTINEL_PARCELLE):
        assert sentinel not in blob
