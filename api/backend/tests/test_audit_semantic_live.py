"""Pure part of scripts/audit_semantic_live.py: families, self-check, report."""

import json

import httpx
import pytest

from app.common.schemas import OutOfCoverageReason, ResolutionOutcome
from app.resolve.schemas import out_of_coverage_state, resolution_state
from scripts import audit_semantic_live as live
from tests.test_semantic_values_contract import _commune_payload

pytestmark = pytest.mark.unit

FAMILIES = {
    "valeurs_premier_niveau",
    "provenance_source",
    "coverage_resolve",
    "origine_periode",
}


def _resolve_body() -> dict:
    states = {
        "dvf": resolution_state("dvf", [{"valeur_fonciere": 1}], is_error=False),
        "cadastre": out_of_coverage_state(
            "cadastre", OutOfCoverageReason.DATASET_NON_HEBERGE
        ),
    }
    return {
        "statut": "ok",
        "donnees": [
            {
                "dataset_states": {
                    k: v.model_dump(mode="json") for k, v in states.items()
                }
            }
        ],
        "meta": {"resolution": ResolutionOutcome(issue="exact").model_dump()},
    }


# ------------------------------------------------------------- families --


def test_family_names_are_the_four_the_gate_requires():
    assert set(live.FAMILLES_CONTROLEES) == FAMILIES
    assert not set(live.FAMILLES_CONTROLEES) & set(live.FAMILLES_NON_AUDITEES)


def test_commune_errors_are_split_between_values_and_provenance():
    payload = _commune_payload()
    payload["donnees"][0]["nb_licornes"] = 3
    del payload["meta"]["source"][0]["licence"]
    errors = live.audit_commune(payload)
    families = {error["famille"] for error in errors}
    assert families == {"valeurs_premier_niveau", "provenance_source"}


def test_producer_payloads_pass_the_live_audit():
    assert live.audit_commune(_commune_payload()) == []
    assert live.audit_resolve(_resolve_body()) == []


def test_resolve_errors_belong_to_the_coverage_family():
    body = _resolve_body()
    del body["donnees"][0]["dataset_states"]["cadastre"]["motif"]
    errors = live.audit_resolve(body)
    assert errors and {e["famille"] for e in errors} == {"coverage_resolve"}


# ----------------------------------------------------------- self-check --


def test_self_check_refuses_every_family_and_accepts_the_legitimate_period():
    result = live.self_check()
    assert result["refus"] == dict.fromkeys(FAMILIES, True)
    assert result["acceptations"] == {"origine_periode": True}


def test_an_origin_check_refusing_everything_does_not_hold_its_family(monkeypatch):
    monkeypatch.setattr(
        live, "audit_provenance_origin", lambda facts, provenance: ["always"]
    )
    result = live.self_check()
    assert result["acceptations"]["origine_periode"] is False
    assert result["refus"]["origine_periode"] is False


def test_an_origin_check_accepting_everything_does_not_hold_its_family(monkeypatch):
    monkeypatch.setattr(live, "audit_provenance_origin", lambda facts, provenance: [])
    result = live.self_check()
    assert result["acceptations"]["origine_periode"] is True
    assert result["refus"]["origine_periode"] is False


def test_a_blind_payload_audit_does_not_hold_its_families(monkeypatch):
    monkeypatch.setattr(live, "audit_commune_payload", lambda payload: [])
    monkeypatch.setattr(live, "audit_resolve_payload", lambda payload: [])
    refus = live.self_check()["refus"]
    assert refus["valeurs_premier_niveau"] is False
    assert refus["provenance_source"] is False
    assert refus["coverage_resolve"] is False


# ------------------------------------------------------------- run part --


def test_non_loopback_base_url_is_refused_before_any_request(tmp_path):
    code = live.main(
        [
            "--base-url",
            "http://192.0.2.78:8000",
            "--panel",
            str(tmp_path / "panel.json"),
            "--report",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == 2
    assert not (tmp_path / "r.json").exists()


def _write_inputs(tmp_path):
    panel = tmp_path / "panel.json"
    panel.write_text(json.dumps({"codes": ["44001", "85001"]}), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "cases": [
                    {"case_id": "golden-address-001", "type": "address"},
                    {"case_id": "golden-commune-001", "type": "commune"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return panel, manifest


def _patch_http(monkeypatch, handler, private):
    monkeypatch.setenv("DC_API_KEY", "dc_live_" + "k" * 43)
    monkeypatch.setattr(live, "_read_private_cases", lambda path: private)
    monkeypatch.setattr(
        live,
        "_make_client",
        lambda base_url, api_key: httpx.Client(
            base_url=base_url, transport=httpx.MockTransport(handler)
        ),
    )


def test_live_run_writes_a_structured_report_without_private_values(
    tmp_path, monkeypatch
):
    secret = "3 impasse Secrete 44000 Nulle-Part"
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/v1/commune":
            return httpx.Response(200, json=_commune_payload())
        assert request.url.params["q"] == secret
        return httpx.Response(200, json=_resolve_body())

    panel, manifest = _write_inputs(tmp_path)
    _patch_http(monkeypatch, handler, {"golden-address-001": {"query": secret}})
    report = tmp_path / "audit.json"
    code = live.main(
        [
            "--base-url",
            "http://127.0.0.1:8000",
            "--panel",
            str(panel),
            "--manifest",
            str(manifest),
            "--addresses-from-private-golden",
            "--report",
            str(report),
        ]
    )
    text = report.read_text(encoding="utf-8")
    assert secret not in text
    data = json.loads(text)
    assert code == 0
    assert data["commune"]["codes_audites"] == ["44001", "85001"]
    assert data["commune"]["payloads"] == 2
    assert data["resolve"]["case_ids_audites"] == ["golden-address-001"]
    assert data["resolve"]["payloads"] == 1
    assert data["resolve"]["reponses"] == len(live.RESOLVE_DATASET_BATCHES)
    assert data["resolve"]["erreurs"] == []
    assert data["resolve"]["blocages_declares"] == []
    assert set(data["familles_controlees"]) == FAMILIES
    assert data["self_check"]["refus"] == dict.fromkeys(FAMILIES, True)
    assert seen.count("/v1/commune") == 2


def test_a_refused_real_payload_is_reported_and_exits_1(tmp_path, monkeypatch):
    def handler(request):
        if request.url.path == "/v1/commune":
            body = _commune_payload()
            body["donnees"][0]["nb_licornes"] = 3
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=_resolve_body())

    panel, manifest = _write_inputs(tmp_path)
    _patch_http(monkeypatch, handler, {"golden-address-001": {"query": "x"}})
    report = tmp_path / "audit.json"
    code = live.main(
        [
            "--base-url",
            "http://127.0.0.1:8000",
            "--panel",
            str(panel),
            "--manifest",
            str(manifest),
            "--report",
            str(report),
        ]
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    assert code == 1
    assert {e["code_insee"] for e in data["commune"]["erreurs"]} == {"44001", "85001"}
    # Without --addresses-from-private-golden no resolve payload is examined.
    assert data["resolve"]["case_ids_audites"] == []


def test_an_http_failure_is_an_error_of_the_report(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(429, json={"title": "quota"})

    panel, manifest = _write_inputs(tmp_path)
    _patch_http(monkeypatch, handler, {})
    report = tmp_path / "audit.json"
    code = live.main(
        [
            "--base-url",
            "http://127.0.0.1:8000",
            "--panel",
            str(panel),
            "--manifest",
            str(manifest),
            "--report",
            str(report),
        ]
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    assert code == 1
    assert data["commune"]["payloads"] == 0
    assert {e["famille"] for e in data["commune"]["erreurs"]} == {"http"}


def test_self_check_mode_makes_no_request(monkeypatch, capsys):
    monkeypatch.setattr(
        live, "_make_client", lambda *a: pytest.fail("no client in self-check")
    )
    assert live.main(["--self-check"]) == 0
    assert "SELF_CHECK OK" in capsys.readouterr().out
