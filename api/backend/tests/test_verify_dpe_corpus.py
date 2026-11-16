"""Offline contracts for the standalone ADEME evidence collector."""

import json

import httpx
import pytest

from scripts import verify_dpe_corpus as collector

pytestmark = pytest.mark.unit

FIRST = "2344E0000000A"
SECOND = "2344E0000000B"
THIRD = "2344E0000000C"
STAMP = "2026-09-03T12:00:00+00:00"


def cases(*numbers: str) -> list[dict]:
    return [
        {"case_id": f"C{index:02}", "numero_dpe": [number]}
        for index, number in enumerate(numbers, start=1)
    ]


def body(*rows: dict, total: int | None = None) -> dict:
    return {"total": len(rows) if total is None else total, "results": list(rows)}


def proof(number: str, payload: dict) -> dict:
    return {
        "source": collector.SOURCE,
        "request_url": collector.request_url(number),
        "observed_at": STAMP,
        "status": 200,
        "body": payload,
    }


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)


def test_deduplicates_requests_preserves_occurrences_and_reuses_cache(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.url.host == "data.ademe.fr"
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        return httpx.Response(200, json=body({"numero_dpe": FIRST}))

    with client(handler) as http:
        first = collector.collect(cases(FIRST, FIRST), tmp_path, http)
        second = collector.collect(cases(FIRST, FIRST), tmp_path, http)
    assert len(seen) == 1
    assert first["summary"]["occurrences"] == 2
    assert first["summary"]["distinct_diagnostics"] == 1
    assert second["summary"]["http_requests"] == 0
    assert second["summary"]["reused"] == 1
    assert (
        first["diagnostics"][0]["observed_at"]
        == second["diagnostics"][0]["observed_at"]
    )
    assert "numero_dpe_remplace" in seen[0].url.params["qs"]


@pytest.mark.parametrize(
    "status,payload,state",
    [
        (200, body(), "absent"),
        (404, {"error": "private server message"}, "inaccessible"),
        (403, {}, "inaccessible"),
        (429, {}, "inaccessible"),
        (503, {}, "inaccessible"),
        (200, {"unexpected": []}, "inaccessible"),
        (200, body(total=5), "inaccessible"),
    ],
)
def test_absence_requires_complete_success_response(tmp_path, status, payload, state):
    with client(lambda _: httpx.Response(status, json=payload)) as http:
        report = collector.collect(cases(FIRST), tmp_path, http)
    assert report["diagnostics"][0]["state"] == state
    assert "private server message" not in json.dumps(report)


def test_successor_does_not_turn_missing_primary_into_found(tmp_path):
    row = {"numero_dpe": SECOND, "numero_dpe_remplace": FIRST}
    with client(lambda _: httpx.Response(200, json=body(row))) as http:
        report = collector.collect(cases(FIRST), tmp_path, http)
    observed = report["diagnostics"][0]
    assert observed["state"] == "absent"
    assert observed["direct_successor_records"] == [row]
    assert observed["primary_records"] == []
    assert report["identity_adjudication"] == "not_performed"


def test_truncated_found_response_preserves_incomplete_scope(tmp_path):
    with client(
        lambda _: httpx.Response(200, json=body({"numero_dpe": FIRST}, total=2))
    ) as http:
        report = collector.collect(cases(FIRST), tmp_path, http)
    assert report["diagnostics"][0]["state"] == "found"
    assert report["diagnostics"][0]["query_complete"] is False
    assert report["summary"]["incomplete_queries"] == 1


def test_unavailable_cache_is_retried_and_errors_are_sanitized(tmp_path):
    def failing(request):
        raise httpx.ReadTimeout("password=never-print-this", request=request)

    with client(failing) as http:
        failed = collector.collect(cases(FIRST), tmp_path, http)
    with client(lambda _: httpx.Response(200, json=body())) as http:
        retried = collector.collect(cases(FIRST), tmp_path, http)
    assert failed["diagnostics"][0]["error_code"] == "timeout"
    assert retried["summary"]["http_requests"] == 1
    assert "never-print-this" not in json.dumps(failed)
    assert "never-print-this" not in (tmp_path / "cache" / f"{FIRST}.json").read_text()


def test_redirect_is_not_followed(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            302, headers={"location": "https://api.datacommune.fr/private"}
        )

    with client(handler) as http:
        report = collector.collect(cases(FIRST), tmp_path, http)
    assert len(seen) == 1
    assert report["diagnostics"][0]["state"] == "inaccessible"


def test_offline_seed_preserves_date_and_whitelists_fields(tmp_path):
    payload = body(
        {
            "numero_dpe": FIRST,
            "adresse_complete_brut": "Source address",
            "password": "never-export",
            "surface_habitable_logement": 100,
            "conso_5_usages_ep": 20000,
            "conso_5_usages_par_m2_ep": 200,
            "date_fin_validite_dpe": "2030-01-01",
        }
    )
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(proof(FIRST, payload)), encoding="utf-8")
    seeds = collector.load_seeds([seed_path], [FIRST])
    with client(lambda _: pytest.fail("offline mode made an HTTP call")) as http:
        report = collector.collect(
            cases(FIRST, SECOND), tmp_path, http, seeds=seeds, offline=True
        )
    observed = report["diagnostics"][0]
    assert observed["observed_at"] == STAMP
    assert (
        observed["factual_checks"][0]["computed_ep_per_m2_from_total_and_area"] == 200
    )
    assert observed["factual_checks"][0]["end_date_before_observation_date"] is False
    assert "never-export" not in json.dumps(report)
    assert report["summary"]["http_requests"] == 0
    assert report["summary"]["inaccessible"] == 1


def test_seed_primary_only_does_not_claim_successor_coverage(tmp_path):
    seed = proof(FIRST, body({"numero_dpe": FIRST}))
    seed["request_url"] = str(
        httpx.URL(collector.ENDPOINT, params={"qs": f'numero_dpe:"{FIRST}"'})
    )
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    assert collector.load_seeds([path], [FIRST]) == {}


def test_wrong_requested_number_rejects_cache(tmp_path):
    path = tmp_path / "cache" / f"{FIRST}.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(proof(SECOND, body({"numero_dpe": SECOND}))), encoding="utf-8"
    )
    with client(lambda _: httpx.Response(200, json=body())) as http:
        report = collector.collect(cases(FIRST), tmp_path, http)
    assert report["summary"]["http_requests"] == 1
    assert report["summary"]["absent"] == 1


def test_normalization_ignores_private_context_and_rejects_query_injection():
    normalized = collector.normalize_cases(
        {
            "cases": [
                {
                    "case_id": "C01",
                    "numero_dpe": [FIRST.lower(), FIRST],
                    "url": "https://private.example/?token=never-print",
                    "signal": "private text",
                }
            ]
        }
    )
    assert normalized == [{"case_id": "C01", "numero_dpe": [FIRST, FIRST]}]
    with pytest.raises(collector.CorpusError):
        collector.normalize_cases(
            {"cases": [{"case_id": "C01", "numero_dpe": ['x" OR *']}]}
        )
    with pytest.raises(collector.CorpusError, match="diagnostic_limit_exceeded"):
        collector.normalize_cases({"cases": cases(FIRST, SECOND)}, limit=1)


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--token", "never-print-me"],
        ["--offline", "--refresh"],
        ["--timeout", "NaN"],
        ["--interval", "-1"],
        ["--max-diagnostics", "101"],
    ],
)
def test_argument_errors_do_not_echo_values(tmp_path, capsys, arguments):
    base = (
        []
        if not arguments
        else ["--input", "unused.json", "--output-dir", str(tmp_path)]
    )
    assert collector.main(base + arguments) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "never-print-me" not in output.err
    assert "Traceback" not in output.err
    assert json.loads(output.err)["error"]


def test_cli_console_only_contains_counts_and_disables_environment(
    tmp_path, capsys, monkeypatch
):
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({"cases": cases(FIRST)}), encoding="utf-8")
    real_client = httpx.Client
    options = {}

    def factory(**kwargs):
        options.update(kwargs)
        return real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body())),
            **kwargs,
        )

    monkeypatch.setattr(collector.httpx, "Client", factory)
    rc = collector.main(
        ["--input", str(input_path), "--output-dir", str(tmp_path / "out")]
    )
    assert rc == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["absent"] == 1
    assert FIRST not in output.out and "C01" not in output.out
    assert not output.err
    assert options["trust_env"] is False
    assert options["follow_redirects"] is False


def test_repository_output_is_rejected(tmp_path):
    with pytest.raises(
        collector.CorpusError, match="output_must_be_outside_repository"
    ):
        collector.parse_args(
            [
                "--input",
                str(tmp_path / "input.json"),
                "--output-dir",
                str(collector.REPO_ROOT / "reports"),
            ]
        )


@pytest.mark.parametrize(
    "payload",
    [
        body({"numero_dpe": SECOND}),
        body({"numero_dpe": FIRST, "numero_dpe_remplace": {"unexpected": "value"}}),
        body({"numero_dpe": FIRST, "surface_habitable_logement": float("nan")}),
    ],
)
def test_invalid_source_rows_are_unavailable_not_evidence(tmp_path, payload):
    with client(lambda _: httpx.Response(200, content=json.dumps(payload))) as http:
        report = collector.collect(cases(FIRST), tmp_path, http)
    assert report["diagnostics"][0]["state"] == "inaccessible"
    assert report["diagnostics"][0]["error_code"] == "invalid_response"


def test_oversized_or_non_json_response_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "MAX_BYTES", 20)
    with client(lambda _: httpx.Response(200, content="sensitive" * 10)) as http:
        report = collector.collect(cases(FIRST), tmp_path, http)
    assert report["diagnostics"][0]["error_code"] == "response_too_large"
    with client(lambda _: httpx.Response(200, content="not-json")) as http:
        report = collector.collect(cases(FIRST), tmp_path, http)
    assert report["diagnostics"][0]["error_code"] == "invalid_json"
    assert "sensitive" not in json.dumps(report)


def test_batch_seed_covers_numbers_without_relabeling_date(tmp_path):
    seed = proof(FIRST, body({"numero_dpe": FIRST}, {"numero_dpe": SECOND}))
    queries = [
        collector.query_scope(collector.request_url(number))
        for number in (FIRST, SECOND)
    ]
    terms = [
        f'{field}:"{number}"'
        for query in queries
        for field, numbers in query.items()
        for number in numbers
    ]
    seed["request_url"] = str(
        httpx.URL(collector.ENDPOINT, params={"qs": " OR ".join(terms)})
    )
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    seeds = collector.load_seeds([path], [FIRST, SECOND, THIRD])
    assert set(seeds) == {FIRST, SECOND}
    assert seeds[FIRST]["observed_at"] == STAMP


def test_refresh_replaces_complete_cache_with_new_observation(tmp_path):
    with client(lambda _: httpx.Response(200, json=body())) as http:
        collector.collect(cases(FIRST), tmp_path, http)
    with client(
        lambda _: httpx.Response(200, json=body({"numero_dpe": FIRST}))
    ) as http:
        refreshed = collector.collect(cases(FIRST), tmp_path, http, refresh=True)
    assert refreshed["summary"]["http_requests"] == 1
    assert refreshed["summary"]["found"] == 1
