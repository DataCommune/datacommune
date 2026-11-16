"""scripts/measure_route_latencies.py: percentiles, failures apart, content check."""

import json

import httpx
import pytest

from scripts.measure_route_latencies import (
    Observation,
    functional_check,
    main,
    measure_route,
    percentile,
    require_loopback,
    summarize_route,
)

OK_BODY = {"donnees": [{"code_insee": "44001"}]}


def _obs(status, ms, payload=OK_BODY):
    return Observation(status=status, elapsed_ms=ms, payload=payload)


# --------------------------------------------------------------- percentiles --


def test_nearest_rank_percentiles_on_one_hundred_values():
    values = [float(v) for v in range(100, 0, -1)]  # unsorted on purpose
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0
    assert percentile(values, 99) == 99.0
    assert percentile(values, 100) == 100.0


def test_nearest_rank_percentiles_on_few_values_are_observed_values():
    assert percentile([4.0, 1.0, 3.0, 2.0], 50) == 2.0
    assert percentile([4.0, 1.0, 3.0, 2.0], 95) == 4.0
    assert percentile([7.5], 99) == 7.5


def test_percentile_of_nothing_is_refused():
    with pytest.raises(ValueError):
        percentile([], 50)


# ------------------------------------------------------ failures kept apart --


def test_failures_are_counted_apart_and_never_mixed_into_the_budget():
    observations = [
        _obs(200, 10.0),
        _obs(200, 20.0),
        _obs(500, 900.0, {"title": "boom"}),
        _obs(429, 1.0, {"title": "quota"}),
        _obs(200, 30.0),
    ]
    result = summarize_route(observations, cle_attendue="donnees", non_vide=True)
    assert result["samples"] == 3
    assert result["http_status"] == 200
    assert result["p50_ms"] == 20.0
    assert result["max_ms"] == 30.0
    assert result["errors"]["count"] == 2
    assert result["errors"]["statuses"] == {"429": 1, "500": 1}
    assert result["errors"]["max_ms"] == 900.0
    assert result["no_budget"] is False


def test_no_error_gives_a_structured_empty_error_block():
    result = summarize_route([_obs(200, 5.0)], cle_attendue="donnees", non_vide=True)
    assert result["errors"] == {
        "count": 0,
        "statuses": {},
        "p50_ms": None,
        "p95_ms": None,
        "p99_ms": None,
        "max_ms": None,
    }


def test_only_failures_leave_the_route_without_budget():
    result = summarize_route(
        [_obs(429, 1.0, {}), _obs(302, 2.0, None)],
        cle_attendue="donnees",
        non_vide=True,
    )
    assert result["no_budget"] is True
    assert result["samples"] == 0
    assert result["p95_ms"] is None
    assert result["http_status"] == 429 or result["http_status"] == 302
    assert result["errors"]["count"] == 2


def test_a_redirect_is_not_a_functional_response():
    result = summarize_route(
        [_obs(302, 2.0, OK_BODY)], cle_attendue="donnees", non_vide=True
    )
    assert result["no_budget"] is True
    assert result["errors"]["statuses"] == {"302": 1}


# ------------------------------------------------------------ content check --


def test_a_200_without_the_expected_key_is_refused():
    result = summarize_route(
        [_obs(200, 5.0, {"meta": {}})], cle_attendue="donnees", non_vide=True
    )
    assert result["no_budget"] is True
    assert result["samples"] == 0
    assert result["functional"] == {
        "cle_attendue": "donnees",
        "presente": False,
        "non_vide": False,
    }
    assert result["errors"]["statuses"] == {"200:non_fonctionnel": 1}


def test_a_200_with_an_empty_collection_is_refused_when_content_is_required():
    result = summarize_route(
        [_obs(200, 5.0, {"donnees": []})], cle_attendue="donnees", non_vide=True
    )
    assert result["no_budget"] is True
    assert result["functional"]["presente"] is True
    assert result["functional"]["non_vide"] is False


def test_an_empty_collection_is_accepted_when_content_is_not_required():
    result = summarize_route(
        [_obs(200, 5.0, {"donnees": []})], cle_attendue="donnees", non_vide=False
    )
    assert result["no_budget"] is False
    assert result["samples"] == 1
    assert result["functional"] == {
        "cle_attendue": "donnees",
        "presente": True,
        "non_vide": False,
    }


def test_a_failed_last_response_leaves_the_route_without_budget():
    result = summarize_route(
        [_obs(200, 5.0), _obs(429, 1.0, {"title": "quota"})],
        cle_attendue="donnees",
        non_vide=True,
    )
    assert result["samples"] == 1
    assert result["functional"]["presente"] is False
    assert result["no_budget"] is True


def test_functional_block_describes_the_last_measured_response():
    result = summarize_route(
        [_obs(200, 5.0), _obs(200, 6.0, {"donnees": []})],
        cle_attendue="donnees",
        non_vide=True,
    )
    assert result["functional"]["non_vide"] is False
    assert result["no_budget"] is True


def test_indicator_object_is_non_empty_only_with_a_non_null_value():
    assert functional_check({"i": {"a": None, "b": None}}, "i")["non_vide"] is False
    assert functional_check({"i": {"a": None, "b": 0}}, "i")["non_vide"] is True


def test_dotted_expected_key_reaches_nested_values_and_list_items():
    body = {"donnees": [{"dpe_inventory": {"context_ref": "x"}}]}
    check = functional_check(body, "donnees.0.dpe_inventory.context_ref")
    assert check == {
        "cle_attendue": "donnees.0.dpe_inventory.context_ref",
        "presente": True,
        "non_vide": True,
    }
    assert functional_check(body, "donnees.1.dpe_inventory")["presente"] is False


def test_a_non_json_body_is_not_functional():
    assert functional_check(None, "donnees")["presente"] is False


# ----------------------------------------------------------- loopback only --


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8000", "http://localhost:8811", "http://[::1]:8000"],
)
def test_loopback_urls_are_accepted(url):
    require_loopback(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://192.0.2.78:8000",
        "https://api.datacommune.fr",
        "http://127.0.0.1.nip.io:8000",
        "ftp://127.0.0.1",
        "",
    ],
)
def test_non_local_urls_are_refused(url):
    with pytest.raises(ValueError):
        require_loopback(url)


def test_main_refuses_a_non_local_url_before_any_request(tmp_path, monkeypatch):
    routes = tmp_path / "routes.json"
    routes.write_text(json.dumps({"routes": []}), encoding="utf-8")
    monkeypatch.setenv("DC_API_KEY", "dc_live_" + "k" * 43)
    code = main(
        [
            "--base-url",
            "https://api.datacommune.fr",
            "--api-key-env",
            "DC_API_KEY",
            "--routes-json",
            str(routes),
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert code == 2
    assert not (tmp_path / "out.json").exists()


# ------------------------------------------------------ measurement driver --


def _client(handler):
    return httpx.Client(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler)
    )


def test_warmup_requests_are_sent_but_never_measured():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=OK_BODY)

    route = {
        "name": "commune",
        "path": "/v1/commune",
        "params": {"code_insee": "44001"},
    }
    with _client(handler) as client:
        observations = measure_route(client, route, {}, warmup=2, samples=5)
    assert len(seen) == 7
    assert len(observations) == 5


def test_pacing_waits_between_requests_outside_the_timed_window():
    waits = []

    def handler(request):
        return httpx.Response(200, json=OK_BODY)

    route = {"name": "commune", "path": "/v1/commune", "params": {}}
    with _client(handler) as client:
        observations = measure_route(
            client,
            route,
            {},
            warmup=1,
            samples=3,
            min_interval_s=2.5,
            sleep=waits.append,
        )
    assert len(observations) == 3
    assert waits and all(w <= 2.5 for w in waits)
    assert all(o.elapsed_ms < 2500 for o in observations)


def test_params_derived_from_an_earlier_route_are_resolved_at_run_time():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"items": [1]})

    route = {
        "name": "inventaire",
        "path": "/v1/bien/dpe/inventaire",
        "params": {"limite": "1"},
        "params_from": {"contexte": {"route": "resolve", "pointer": "donnees.0.ref"}},
    }
    previous = {"resolve": {"donnees": [{"ref": "opaque-ctx"}]}}
    with _client(handler) as client:
        measure_route(client, route, previous, warmup=0, samples=1)
    assert seen == [{"limite": "1", "contexte": "opaque-ctx"}]


def test_run_writes_budgets_without_key_nor_private_values(tmp_path, monkeypatch):
    key = "dc_live_" + "s" * 43
    secret_address = "12 rue Privee 44000 Nulle-Part"
    seen_headers = []

    def handler(request):
        seen_headers.append(request.headers.get("X-API-Key"))
        return httpx.Response(200, json={"donnees": [{"x": 1}]})

    routes = tmp_path / "routes.json"
    routes.write_text(
        json.dumps(
            {
                "routes": [
                    {
                        "name": "resolve",
                        "path": "/v1/resolve",
                        "params": {"type": "adresse"},
                        "private_params": {
                            "q": {"case_id": "golden-address-001", "field": "query"}
                        },
                        "cle_attendue": "donnees",
                        "non_vide": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DC_API_KEY", key)
    monkeypatch.setattr(
        "scripts.measure_route_latencies._read_private_cases",
        lambda path: {"golden-address-001": {"query": secret_address}},
    )
    monkeypatch.setattr(
        "scripts.measure_route_latencies._make_client",
        lambda base_url, api_key: httpx.Client(
            base_url=base_url,
            headers={"X-API-Key": api_key},
            transport=httpx.MockTransport(handler),
        ),
    )
    out = tmp_path / "out.json"
    code = main(
        [
            "--base-url",
            "http://127.0.0.1:8000",
            "--api-key-env",
            "DC_API_KEY",
            "--routes-json",
            str(routes),
            "--private-input",
            "contracts/golden-cases/v1/private/golden-selection.json",
            "--warmup",
            "1",
            "--samples",
            "4",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert seen_headers == [key] * 5
    text = out.read_text(encoding="utf-8")
    assert key not in text and secret_address not in text
    result = json.loads(text)["latencies"]["/v1/resolve"]
    assert result["samples"] == 4
    assert result["functional"]["presente"] is True
    assert result["params"]["q"] == "<prive:golden-address-001.query>"


def test_run_exits_1_when_a_route_has_no_budget(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"donnees": []})

    routes = tmp_path / "routes.json"
    routes.write_text(
        json.dumps(
            {
                "routes": [
                    {
                        "name": "dvf",
                        "path": "/v1/dvf",
                        "params": {},
                        "cle_attendue": "donnees",
                        "non_vide": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DC_API_KEY", "dc_live_" + "k" * 43)
    monkeypatch.setattr(
        "scripts.measure_route_latencies._make_client",
        lambda base_url, api_key: httpx.Client(
            base_url=base_url, transport=httpx.MockTransport(handler)
        ),
    )
    out = tmp_path / "out.json"
    code = main(
        [
            "--base-url",
            "http://127.0.0.1:8000",
            "--routes-json",
            str(routes),
            "--warmup",
            "0",
            "--samples",
            "2",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    assert (
        json.loads(out.read_text(encoding="utf-8"))["latencies"]["/v1/dvf"]["no_budget"]
        is True
    )


def test_route_file_requires_expected_key_and_content_flag(tmp_path, monkeypatch):
    routes = tmp_path / "routes.json"
    routes.write_text(
        json.dumps({"routes": [{"name": "dvf", "path": "/v1/dvf", "params": {}}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DC_API_KEY", "dc_live_" + "k" * 43)
    code = main(
        [
            "--base-url",
            "http://127.0.0.1:8000",
            "--routes-json",
            str(routes),
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert code == 2


def test_pacing_carries_over_from_one_route_to_the_next():
    # 02-18: the rhythm restarted at every route, so the first warmup request
    # of a route followed the last measure of the previous one at once.
    waits = []

    def handler(request):
        return httpx.Response(200, json=OK_BODY)

    pace = {}
    route = {"name": "commune", "path": "/v1/commune", "params": {}}
    with _client(handler) as client:
        for _ in range(2):
            measure_route(
                client,
                route,
                {},
                warmup=0,
                samples=1,
                min_interval_s=2.5,
                sleep=waits.append,
                pace=pace,
            )
    assert len(waits) == 1 and 0 < waits[0] <= 2.5
