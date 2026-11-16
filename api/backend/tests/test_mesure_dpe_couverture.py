"""Offline coverage contracts: raw source, SQL census and textual HTTP replay."""

from __future__ import annotations

import copy
import csv
import hashlib
import importlib
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit
STAMP = "2026-09-05T10:00:00+00:00"
BAN = "44001_private_00001"
SECRET = "PRIVATE-SENTINEL-DO-NOT-PUBLISH"


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("real connections are forbidden")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
def m() -> Any:
    return importlib.import_module("scripts.mesure_dpe_couverture")


def diagnostic(number: str = SECRET, **extra: Any) -> dict:
    return {
        "numero_dpe": number,
        "numero_dpe_remplace": None,
        "identifiant_ban": BAN,
        "code_departement": "44",
        "regime_dpe": "existant",
        "type_batiment": "maison",
        "longitude": -1.5,
        "latitude": 47.2,
        "surface_habitable": 80,
        "conso_5_usages_ep": 100,
        "emission_ges_5_usages": 10,
        **extra,
    }


def address(ban: str = BAN, **extra: Any) -> dict:
    return {
        "ban_id": ban,
        "code_departement": ban[:2],
        "numero": "1",
        "rep": None,
        "nom_voie": SECRET,
        "code_postal": "44000",
        "nom_commune": "Commune fixture",
        **extra,
    }


def inventory(total: int = 1, **extra: Any) -> dict:
    return {
        "version": 2,
        "state": "ready" if total else "absent",
        "context_ref": "opaque-private" if total else None,
        "items": [{"document_ref": "opaque-document"}] if total else [],
        "total": total,
        "distribution": {**dict.fromkeys("ABCDEFG", 0), "unknown": total},
        "page_size": 20,
        "has_more": False,
        "next_cursor": None,
        "read_state": "complete",
        **extra,
    }


def resolved(**extra: Any) -> dict:
    return {
        "type_detecte": "adresse",
        "latitude": 47.2,
        "longitude": -1.5,
        "ban_id": BAN,
        "dpe_inventory": inventory(),
        **extra,
    }


class Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload, self.status_code = payload, status

    def json(self) -> dict:
        return self.payload


class Http:
    def __init__(self, targets: dict | None = None) -> None:
        self.calls: list = []
        self.targets = targets or {}

    def __call__(self, path: str, *, params: dict) -> Response:
        self.calls.append((path, params))
        assert path == "/v1/resolve"
        assert params["type"] == "adresse" and params["datasets"] == "dpe"
        selected = self.targets.get(params["q"], BAN)
        if isinstance(selected, Exception):
            raise selected
        if isinstance(selected, Response):
            return selected
        return Response(
            {
                "donnees": [
                    {
                        "type_detecte": "adresse",
                        "latitude": 47.2,
                        "longitude": -1.5,
                        "ban_id": selected,
                        "dpe_inventory": inventory(),
                    }
                ]
            }
        )


class Rows:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def fetchall(self) -> list:
        return copy.deepcopy(self.rows)

    def fetchone(self) -> dict | None:
        return self.rows[0] if self.rows else None


class Database:
    def __init__(self, module: Any, diagnostics: list, addresses: list) -> None:
        self.module, self.diagnostics, self.addresses = module, diagnostics, addresses
        self.calls: list = []
        self.closed = False

    def __call__(self, *args: Any, **kwargs: Any) -> Database:
        assert "readonly" in args[0]
        return self

    def execute(self, sql: str, params: Any = None) -> Rows:
        self.calls.append((sql, params))
        data = {
            self.module.READ_ONLY_SQL: [{"state": "on"}],
            self.module.DPE_SQL: self.diagnostics,
            self.module.ADDRESS_SQL: self.addresses,
            self.module.METADATA_SQL: [
                {"dataset": name, "last_updated_at": STAMP, "source_updated_at": STAMP}
                for name in ("ban", "cadastre", "dpe")
            ],
            self.module.GENERATIONS_SQL: [{"addresses": 123, "diagnostics": 456}],
        }
        assert sql in data, "unexpected SQL is forbidden"
        return Rows(data[sql])

    def close(self) -> None:
        self.closed = True


def write_json(path: Path, value: Any) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setenv("COVERAGE_TEST_DSN", "postgresql://readonly:fixture@invalid/db")
    config = write_json(
        private / "config.json",
        {
            "dsn_env": "COVERAGE_TEST_DSN",
            "api_url": "https://fixture.invalid",
            "environment": "fixture",
        },
    )
    fields = [
        "numero_dpe",
        "code_departement_ban",
        "identifiant_ban",
        "type_batiment",
        "coordonnee_cartographique_x_ban",
        "coordonnee_cartographique_y_ban",
        "surface_habitable_logement",
        "conso_5_usages_ep",
        "emission_ges_5_usages",
        "numero_dpe_remplace",
    ]
    partitions = []
    for source in ("dpe03existant", "dpe02neuf"):
        for department in ("44", "85"):
            path = private / f"{source}-{department}.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                if source == "dpe03existant" and department == "44":
                    writer.writerow(
                        dict(
                            zip(
                                fields,
                                [
                                    SECRET,
                                    "44",
                                    BAN,
                                    "maison",
                                    "355000",
                                    "6680000",
                                    "80",
                                    "100",
                                    "10",
                                    "",
                                ],
                                strict=True,
                            )
                        )
                    )
            partitions.append(
                {
                    "source": source,
                    "department": department,
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "rows": int(source == "dpe03existant" and department == "44"),
                    "complete": True,
                    "version_start": "fixture-v1",
                    "version_end": "fixture-v1",
                    "observed_at": STAMP,
                    "units": {
                        "surface_habitable": "m2",
                        "conso_5_usages_ep": "kWhEP/year",
                        "emission_ges_5_usages": "kgCO2eq/year",
                    },
                }
            )
    manifest = write_json(
        private / "sources.json", {"version": 1, "partitions": partitions}
    )
    return {
        "private": private,
        "config": config,
        "manifest": manifest,
        "root": tmp_path,
    }


def run_before(m: Any, inputs: dict, **kwargs: Any) -> tuple[dict, Path, Database]:
    report = inputs["root"] / "before-report.json"
    db = kwargs.pop("connect", Database(m, [diagnostic()], [address()]))
    args = [
        "--mode",
        "before",
        "--private-dir",
        str(inputs["private"]),
        "--report",
        str(report),
        "--config",
        str(inputs["config"]),
        "--source-manifest",
        str(inputs["manifest"]),
    ]
    assert m.main(args, connect=db, get=kwargs.pop("get", Http()), **kwargs) == 0
    public = json.loads(report.read_text())
    evidence = inputs["private"] / f"{public['evidence_sha256']}.json"
    return public, evidence, db


def test_units_union_overlaps_and_strata(m: Any) -> None:
    rows = [
        diagnostic(),
        diagnostic("second", regime_dpe="neuf", type_batiment="appartement"),
    ]
    result = m.census(rows, [address(), address()])
    assert result["totals"]["rows"] == 2
    assert result["totals"]["distinct_dpe"] == 2
    assert result["totals"]["distinct_ban"] == 1
    assert result["totals"]["joinable_ban"] == 1
    assert result["reference_ban"] == 1
    assert result["regime_overlap_ban"] == 1
    assert len(result["strata"]) == 16
    assert sum(s["distinct_ban"] for s in result["strata"]) == 2


@pytest.mark.parametrize(
    "change,cause",
    [
        ({"ban_id": "44001_private_00002"}, "wrong_target"),
        ({"ban_id": None}, "missing_ban"),
        ({"type_detecte": "gps"}, "wrong_grain"),
        ({"dpe_inventory": None}, "inventory_unavailable"),
        ({"dpe_inventory": inventory(0)}, "inventory_empty"),
        ({"dpe_inventory": inventory(total=True)}, "inventory_unavailable"),
        ({"dpe_inventory": inventory(context_ref=None)}, "inventory_unavailable"),
        ({"dpe_inventory": inventory(read_state="partial")}, "inventory_unavailable"),
    ],
)
def test_http_200_never_proves_wrong_target(m: Any, change: dict, cause: str) -> None:
    case = m.freeze_corpus([address()], sample_size=None)[0]
    payload = {
        "type_detecte": "adresse",
        "latitude": 47.2,
        "longitude": -1.5,
        "ban_id": BAN,
        "dpe_inventory": inventory(),
        **change,
    }
    get = Http({case["query"]: Response({"donnees": [payload]})})
    outcome = m.replay([case], get)[0]
    assert outcome["cause"] == cause
    assert outcome["covered"] is (False if cause == "inventory_empty" else None)


@pytest.mark.parametrize(
    "response,cause",
    [
        (Response({"donnees": []}), "resolution_absent"),
        (Response({"donnees": [resolved(), resolved()]}), "resolution_ambiguous"),
        (Response({}, 500), "http_error"),
        (RuntimeError(SECRET), "transport_error"),
    ],
)
def test_http_absence_error_are_unavailable(m: Any, response: Any, cause: str) -> None:
    case = m.freeze_corpus([address()], sample_size=None)[0]
    result = m.replay([case], Http({case["query"]: response}))[0]
    assert result["covered"] is None and result["cause"] == cause


@pytest.mark.parametrize(
    "rows,cause",
    [
        ([], "resolution_absent"),
        ([resolved(), resolved()], "resolution_ambiguous"),
        ([resolved(ban_id="44001_private_00002")], "wrong_target"),
        ([resolved(type_detecte="gps")], "wrong_grain"),
        ([resolved(ban_id=None)], "missing_ban"),
    ],
)
def test_valid_resolution_failure_is_proof_without_coverage(
    m: Any, inputs: dict, rows: list, cause: str
) -> None:
    query = m.freeze_corpus([address()], None)[0]["query"]
    report, path, _ = run_before(
        m, inputs, get=Http({query: Response({"donnees": rows})})
    )
    outcome = json.loads(path.read_text())["outcomes"][0]
    assert outcome["cause"] == cause and outcome["covered"] is None
    assert report["replay"]["covered"] == report["replay"]["confirmed_empty"] == 0
    assert report["replay"]["unavailable"] == 1
    assert m.check_report(report, inputs["private"])
    assert report["replay"]["observed_resolution_failures"] == 1
    assert report["replay"]["unusable_evidence"] == 0


@pytest.mark.parametrize(
    "rows",
    [
        [{}],
        [{}, {}],
        [resolved(), {}],
        [resolved(type_detecte="unexpected")],
        [resolved(ban_id=42)],
        [resolved(ban_id="malformed")],
        [resolved(latitude="not-a-number")],
        [resolved(latitude=float("nan"))],
        [{k: v for k, v in resolved().items() if k != "ban_id"}],
        [{k: v for k, v in resolved().items() if k != "longitude"}],
    ],
)
def test_malformed_resolution_never_proves_functional_failure(
    m: Any, inputs: dict, rows: list
) -> None:
    query = m.freeze_corpus([address()], None)[0]["query"]
    report, path, _ = run_before(
        m, inputs, get=Http({query: Response({"donnees": rows})})
    )
    outcome = json.loads(path.read_text())["outcomes"][0]
    assert outcome["cause"] == "invalid_response"
    assert outcome["covered"] is None
    assert not m.check_report(report, inputs["private"])


@pytest.mark.parametrize("reverse", [False, True])
def test_resolution_transition_keeps_documentary_gains_unknown(
    m: Any, inputs: dict, reverse: bool
) -> None:
    query = m.freeze_corpus([address()], None)[0]["query"]
    negative = Http({query: Response({"donnees": [resolved(ban_id=None)]})})
    before, baseline, _ = run_before(m, inputs, get=Http() if reverse else negative)
    baseline_bytes = baseline.read_bytes()
    output = inputs["root"] / "after.json"
    assert (
        m.main(
            [
                "--mode",
                "after",
                "--private-dir",
                str(inputs["private"]),
                "--report",
                str(output),
                "--baseline",
                str(baseline),
                "--config",
                str(inputs["config"]),
                "--source-manifest",
                str(inputs["manifest"]),
            ],
            connect=Database(m, [diagnostic()], [address()]),
            get=negative if reverse else Http(),
        )
        == 0
    )
    after = json.loads(output.read_text())
    comparison = after["comparison"]
    assert baseline.read_bytes() == baseline_bytes
    assert before["corpus_sha256"] == after["corpus_sha256"]
    assert comparison["common_denominator"] == comparison["unresolved_pairs"] == 1
    assert comparison["gains"] == comparison["losses"] == 0
    assert m.check_report(after, inputs["private"])
    assert comparison["resolution_paths_restored"] == int(not reverse)
    assert comparison["resolution_paths_lost"] == int(reverse)
    assert sum(row["count"] for row in comparison["transitions"]) == 1
    assert sum(row["resolution_paths_restored"] for row in comparison["strata"]) == int(
        not reverse
    )
    candidate = inputs["private"] / f"{after['evidence_sha256']}.json"
    comparison_path = inputs["root"] / "compare.json"
    assert (
        m.main(
            [
                "--mode",
                "compare",
                "--private-dir",
                str(inputs["private"]),
                "--report",
                str(comparison_path),
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
            ]
        )
        == 0
    )
    comparison_report = json.loads(comparison_path.read_text())
    assert comparison_report["comparison"] == comparison
    assert m.check_report(comparison_report, inputs["private"])


def test_legacy_functional_outcome_without_validation_is_not_proof(
    m: Any, inputs: dict
) -> None:
    query = m.freeze_corpus([address()], None)[0]["query"]
    _, path, _ = run_before(m, inputs, get=Http({query: Response({"donnees": []})}))
    evidence = json.loads(path.read_text())
    del evidence["outcomes"][0]["functional_observation"]
    public = m.project(evidence, inputs["private"])
    write_json(inputs["private"] / f"{m.digest(evidence)}.json", evidence)
    assert public["replay"]["unavailable"] == public["replay"]["unusable_evidence"] == 1
    assert public["replay"]["observed_resolution_failures"] == 0
    assert not m.check_report(public, inputs["private"])


@pytest.mark.parametrize("kind", ["functional", "transport", "invalid_inventory"])
def test_entrant_evidence_does_not_change_frozen_comparison(
    m: Any, inputs: dict, kind: str
) -> None:
    _, baseline, _ = run_before(m, inputs)
    entrant = address("85001_private_00002", numero="2", code_postal="85000")
    query = m.freeze_corpus([entrant], None)[0]["query"]
    responses = {
        "functional": Response({"donnees": []}),
        "transport": RuntimeError(SECRET),
        "invalid_inventory": Response(
            {"donnees": [resolved(ban_id=entrant["ban_id"], dpe_inventory=None)]}
        ),
    }
    output = inputs["root"] / "after.json"
    assert (
        m.main(
            [
                "--mode",
                "after",
                "--private-dir",
                str(inputs["private"]),
                "--report",
                str(output),
                "--baseline",
                str(baseline),
                "--config",
                str(inputs["config"]),
                "--source-manifest",
                str(inputs["manifest"]),
            ],
            connect=Database(m, [diagnostic()], [address(), entrant]),
            get=Http({query: responses[kind]}),
        )
        == 0
    )
    report = json.loads(output.read_text())
    assert report["comparison"]["common_denominator"] == 1
    assert report["comparison"]["entrants"] == 1
    assert report["comparison"]["resolution_paths_restored"] == 0
    assert report["entrants_replay"]["unavailable"] == 1
    assert report["entrants_replay"]["covered"] == 0
    assert m.check_report(report, inputs["private"]) is (kind == "functional")


def test_before_raw_files_sql_http_and_public_privacy(m: Any, inputs: dict) -> None:
    public, evidence, db = run_before(m, inputs)
    assert public["source"]["totals"]["rows"] == 1
    assert public["source"]["rejections"]["accepted"] == 1
    assert public["sql"]["totals"]["rows"] == 1
    assert public["replay"]["covered"] == 1
    assert public["replay"]["attempted"] == 1
    assert public["replay"]["population"] == 1
    assert public["replay"]["sampling"] == "census"
    assert db.closed and db.read_only is True
    for sql, _ in db.calls:
        from scripts.verify_golden_cases import assert_select_only

        assert_select_only(sql, m.ALLOWED_SQL)
    assert SECRET not in json.dumps(public) and BAN not in json.dumps(public)
    assert SECRET in evidence.read_text()
    assert (
        m.main(
            [
                "--check-report",
                "--report",
                str(inputs["root"] / "before-report.json"),
                "--private-dir",
                str(inputs["private"]),
            ]
        )
        == 0
    )


def test_freeze_deduplicates_and_rejects_conflicting_address(m: Any) -> None:
    assert len(m.freeze_corpus([address(), address()], sample_size=None)) == 1
    with pytest.raises(m.CoverageError):
        m.freeze_corpus([address(), address(numero="2")], sample_size=None)


def test_sample_is_deterministic_and_never_extrapolated(m: Any, inputs: dict) -> None:
    rows = [address(), address("85001_private_00002"), address("44001_private_00003")]
    assert m.freeze_corpus(rows, sample_size=1) == m.freeze_corpus(
        rows[::-1], sample_size=1
    )
    db = Database(m, [diagnostic()], rows)
    report = inputs["root"] / "sample.json"
    assert (
        m.main(
            [
                "--mode",
                "before",
                "--private-dir",
                str(inputs["private"]),
                "--report",
                str(report),
                "--config",
                str(inputs["config"]),
                "--source-manifest",
                str(inputs["manifest"]),
                "--sample-size",
                "1",
            ],
            connect=db,
            get=Http(),
        )
        == 0
    )
    replay = json.loads(report.read_text())["replay"]
    assert replay["population"] == 3 and replay["attempted"] == 1
    assert replay["sampling"] == "sample" and replay["extrapolated"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("complete", False),
        ("rows", 2),
        ("version_end", "changed"),
        ("sha256", "0" * 64),
    ],
)
def test_incomplete_source_never_becomes_zero(
    m: Any, inputs: dict, field: str, value: Any
) -> None:
    data = json.loads(inputs["manifest"].read_text())
    data["partitions"][0][field] = value
    write_json(inputs["manifest"], data)
    public, _, _ = run_before(m, inputs)
    assert public["source"]["status"] == "unavailable"
    assert public["source"]["totals"] is None
    assert m.check_report(public, inputs["private"]) is False


@pytest.mark.parametrize(
    "mutation", ["missing_partition", "extra_partition", "wrong_scope"]
)
def test_four_partitions_and_scope_are_mandatory(
    m: Any, inputs: dict, mutation: str
) -> None:
    data = json.loads(inputs["manifest"].read_text())
    if mutation == "missing_partition":
        data["partitions"].pop()
    elif mutation == "extra_partition":
        data["partitions"].append(data["partitions"][0])
    else:
        data["partitions"][0]["department"] = "75"
    write_json(inputs["manifest"], data)
    public, _, _ = run_before(m, inputs)
    assert public["source"]["status"] == "unavailable"


def test_pretransform_exclusive_reasons_and_collisions(m: Any) -> None:
    rows = [
        diagnostic(),
        diagnostic(),
        diagnostic("conflict"),
        diagnostic("conflict", surface_habitable=90),
        diagnostic("", longitude=None),
        diagnostic("outside", code_departement="75"),
        diagnostic("no-geometry", longitude=None),
        diagnostic("invalid-geometry", longitude=float("nan")),
    ]
    result = m.source_census(rows, [address()])
    reasons = result["rejections"]
    assert sum(reasons.values()) == len(rows)
    assert reasons["duplicate_exact"] == 1
    assert reasons["collision_divergent"] == 2
    assert reasons["identity_missing"] == 1
    assert reasons["outside_scope"] == 1
    assert reasons["geometry_missing"] == 1
    assert reasons["geometry_invalid"] == 1
    assert result["totals"]["rows"] == 8


def test_ban_grains_orphans_links_and_fill(m: Any) -> None:
    rows = [
        diagnostic("a"),
        diagnostic("b", identifiant_ban="44001_road"),
        diagnostic("c", identifiant_ban="44001", numero_dpe_remplace="absent"),
        diagnostic("d", identifiant_ban=None, surface_habitable=None),
    ]
    result = m.census(rows, [address()])
    assert result["grains"] == {
        "address": 1,
        "street": 1,
        "commune": 1,
        "missing": 1,
        "invalid": 0,
    }
    assert result["orphan_ban"] == 2
    assert result["links"]["declared"] == 1 and result["links"]["missing_target"] == 1
    assert result["fill"]["surface_habitable"] == {"filled": 3, "denominator": 4}


def test_after_compare_freezes_common_population_and_separates_entries(
    m: Any, inputs: dict
) -> None:
    before, baseline, _ = run_before(m, inputs)
    baseline_bytes = baseline.read_bytes()
    report = inputs["root"] / "after-report.json"
    newcomer = address("85001_private_00002", numero="2", code_postal="85000")
    db = Database(m, [diagnostic()], [newcomer])
    query = m.freeze_corpus([newcomer], sample_size=None)[0]["query"]
    get = Http({query: newcomer["ban_id"]})
    assert (
        m.main(
            [
                "--mode",
                "after",
                "--private-dir",
                str(inputs["private"]),
                "--report",
                str(report),
                "--baseline",
                str(baseline),
                "--config",
                str(inputs["config"]),
                "--source-manifest",
                str(inputs["manifest"]),
            ],
            connect=db,
            get=get,
        )
        == 0
    )
    after = json.loads(report.read_text())
    assert after["corpus_sha256"] == before["corpus_sha256"]
    assert baseline.read_bytes() == baseline_bytes
    assert len(get.calls) == 2
    candidate = inputs["private"] / f"{after['evidence_sha256']}.json"
    output = inputs["root"] / "comparison.json"
    assert (
        m.main(
            [
                "--mode",
                "compare",
                "--private-dir",
                str(inputs["private"]),
                "--report",
                str(output),
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
            ]
        )
        == 0
    )
    comparison = json.loads(output.read_text())["comparison"]
    assert comparison["common_denominator"] == 1
    assert comparison["entrants"] == 1 and comparison["exits"] == 1
    assert comparison["gains"] == 0 and comparison["losses"] == 0
    assert comparison["unresolved_pairs"] == 0
    assert m.check_report(json.loads(output.read_text()), inputs["private"])


@pytest.mark.parametrize(
    "mutation", ["sentinel", "bool", "denominator", "fingerprint", "missing"]
)
def test_check_report_rejects_tampering(m: Any, inputs: dict, mutation: str) -> None:
    report, _, _ = run_before(m, inputs)
    if mutation == "sentinel":
        report["private"] = SECRET
    elif mutation == "bool":
        report["replay"]["covered"] = True
    elif mutation == "denominator":
        report["replay"]["attempted"] = 100
    elif mutation == "fingerprint":
        report["corpus_sha256"] = "0" * 64
    else:
        del report["sql"]
    assert m.check_report(report, inputs["private"]) is False


@pytest.mark.parametrize("missing_import", [None, "ban", "cadastre", "dpe"])
def test_unknown_source_freshness_keeps_import_provenance_required(
    m: Any, inputs: dict, missing_import: str | None
) -> None:
    class UnknownFreshnessDatabase(Database):
        def execute(self, sql: str, params: Any = None) -> Rows:
            result = super().execute(sql, params)
            if sql == m.METADATA_SQL:
                for row in result.rows:
                    row["source_updated_at"] = None
                    if row["dataset"] == missing_import:
                        row["last_updated_at"] = None
            return result

    report, _, _ = run_before(
        m, inputs, connect=UnknownFreshnessDatabase(m, [diagnostic()], [address()])
    )

    for dataset in ("ban", "cadastre", "dpe"):
        assert report["sql"]["freshness"][dataset] == {
            "last_updated_at": None if dataset == missing_import else STAMP,
            "source_updated_at": None,
        }
    assert m.check_report(report, inputs["private"]) is (missing_import is None)


def test_private_evidence_tampering_rejected(m: Any, inputs: dict) -> None:
    report, evidence, _ = run_before(m, inputs)
    data = json.loads(evidence.read_text())
    data["corpus"][0]["query"] = "Changed private address"
    write_json(evidence, data)
    assert m.check_report(report, inputs["private"]) is False


def test_no_overwrite_and_no_private_report(m: Any, inputs: dict) -> None:
    report, evidence, _ = run_before(m, inputs)
    previous = evidence.read_bytes()
    for target in (evidence, inputs["private"] / "public.json"):
        assert (
            m.main(
                [
                    "--mode",
                    "compare",
                    "--private-dir",
                    str(inputs["private"]),
                    "--report",
                    str(target),
                    "--baseline",
                    str(evidence),
                    "--candidate",
                    str(evidence),
                ]
            )
            != 0
        )
    assert evidence.read_bytes() == previous
    assert m.check_report(report, inputs["private"])


def test_private_directory_cannot_be_inside_any_git_checkout(
    m: Any, tmp_path: Path
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    (root / ".git").write_text("gitdir: elsewhere")
    private = root / "nested"
    private.mkdir()
    with pytest.raises(m.CoverageError):
        m.validate_private_dir(private)


def test_cli_errors_redacted(
    m: Any, inputs: dict, capsys: pytest.CaptureFixture
) -> None:
    assert m.main(["--unknown", SECRET]) != 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_transport_exception_not_published(
    m: Any, inputs: dict, capsys: pytest.CaptureFixture
) -> None:
    query = m.freeze_corpus([address()], sample_size=None)[0]["query"]
    report, _, _ = run_before(m, inputs, get=Http({query: RuntimeError(SECRET)}))
    assert report["replay"]["unavailable"] == 1
    assert report["replay"]["covered"] == 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err + json.dumps(report)
    assert m.check_report(report, inputs["private"]) is False


@pytest.mark.parametrize(
    "changes",
    [
        {"items": [{}]},
        {"has_more": True, "next_cursor": None},
        {"page_size": True},
        {"page_size": 0},
        {"page_size": 51},
        {"items": [{"document_ref": ""}]},
        {"next_cursor": "unpaired"},
        {
            "has_more": False,
            "total": 2,
            "distribution": {**dict.fromkeys("ABCDEFG", 0), "unknown": 2},
        },
    ],
)
def test_malformed_inventory_never_counts(m: Any, changes: dict) -> None:
    assert m.inventory_state(inventory(**changes)) == "inventory_unavailable"


def test_empty_corpus_is_missing_proof(m: Any, inputs: dict) -> None:
    report, _, _ = run_before(m, inputs, connect=Database(m, [], []))
    assert not m.check_report(report, inputs["private"])


def test_complete_candidate_cannot_hide_unavailable_baseline(
    m: Any, inputs: dict
) -> None:
    query = m.freeze_corpus([address()], sample_size=None)[0]["query"]
    _, baseline, _ = run_before(m, inputs, get=Http({query: RuntimeError(SECRET)}))
    report_path = inputs["root"] / "after.json"
    assert (
        m.main(
            [
                "--mode",
                "after",
                "--private-dir",
                str(inputs["private"]),
                "--report",
                str(report_path),
                "--baseline",
                str(baseline),
                "--config",
                str(inputs["config"]),
                "--source-manifest",
                str(inputs["manifest"]),
            ],
            connect=Database(m, [diagnostic()], [address()]),
            get=Http(),
        )
        == 0
    )
    assert not m.check_report(json.loads(report_path.read_text()), inputs["private"])


def replace_csv_partition(inputs: dict, index: int, changes: dict) -> None:
    manifest = json.loads(inputs["manifest"].read_text())
    part = manifest["partitions"][index]
    original = Path(manifest["partitions"][0]["path"])
    with original.open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    row.update(changes)
    path = Path(part["path"])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    part["rows"] = 1
    part["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(inputs["manifest"], manifest)


def test_raw_four_populations_and_insee_department_fallback(
    m: Any, inputs: dict
) -> None:
    for index, dept in ((1, "85"), (2, "44"), (3, "85")):
        replace_csv_partition(
            inputs,
            index,
            {
                "numero_dpe": f"fixture-{index}",
                "code_departement_ban": "",
                "code_insee_ban": f"{dept}001",
                "identifiant_ban": f"{dept}001_private_00001",
            },
        )
    public, _, _ = run_before(m, inputs)
    assert public["source"]["totals"]["rows"] == 4
    assert public["source"]["rejections"]["accepted"] == 4
    assert sum(s["rows"] > 0 for s in public["source"]["strata"]) == 4


def test_per_square_metre_units_are_not_annual_totals(m: Any, inputs: dict) -> None:
    manifest = json.loads(inputs["manifest"].read_text())
    manifest["partitions"][0]["units"]["conso_5_usages_ep"] = "kWh/m2/year"
    write_json(inputs["manifest"], manifest)
    public, _, _ = run_before(m, inputs)
    assert public["source"]["status"] == "unavailable"


def test_private_partition_metadata_is_also_closed(m: Any, inputs: dict) -> None:
    _, evidence_path, _ = run_before(m, inputs)
    evidence = json.loads(evidence_path.read_text())
    evidence["source"]["partitions"][0]["private"] = SECRET
    with pytest.raises(m.CoverageError):
        m.project(evidence, inputs["private"])


def test_raw_geometry_rejections_count_before_filter(m: Any, inputs: dict) -> None:
    replace_csv_partition(inputs, 0, {"coordonnee_cartographique_x_ban": ""})
    public, _, _ = run_before(m, inputs)
    assert public["source"]["totals"]["rows"] == 1
    assert public["source"]["rejections"]["geometry_missing"] == 1
    assert public["source"]["totals"]["distinct_dpe"] == 1


@pytest.mark.parametrize(
    "ban", ["44001_a_b_c_d", "44001_a_b_c_d_e", "44001__", "44001_a__b"]
)
def test_malformed_ban_is_a_countable_rejection(m: Any, ban: str) -> None:
    assert m.grain(ban) == "invalid"
    assert (
        m.census([diagnostic(identifiant_ban=ban)], [address()])["grains"]["invalid"]
        == 1
    )


def test_missing_sql_cannot_claim_zero_source_joins(m: Any, inputs: dict) -> None:
    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(SECRET)

    report, _, _ = run_before(m, inputs, connect=unavailable)
    assert report["source"]["totals"]["joinable_ban"] is None
    assert report["source"]["reference_ban"] is None
    assert report["source"]["orphan_ban"] is None
    assert not m.check_report(report, inputs["private"])


def test_forged_private_denominator_rejected_even_with_new_hash(
    m: Any, inputs: dict
) -> None:
    _, path, _ = run_before(m, inputs)
    evidence = json.loads(path.read_text())
    evidence["population"] = 100
    with pytest.raises(m.CoverageError):
        m.project(evidence, inputs["private"])


def test_sql_contract_matches_existing_tables(m: Any) -> None:
    from sqlalchemy import create_engine, text

    db = create_engine("sqlite://")
    with db.connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE diagnostics_dpe (numero_dpe TEXT, "
                "numero_dpe_remplace TEXT, identifiant_ban TEXT, "
                "code_departement TEXT, "
                "regime_dpe TEXT, type_batiment TEXT, longitude REAL, latitude REAL, "
                "surface_habitable REAL, conso_5_usages_ep REAL, "
                "emission_ges_5_usages REAL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE adresses (ban_id TEXT, code_departement TEXT, "
                "numero TEXT, rep TEXT, nom_voie TEXT, "
                "code_postal TEXT, nom_commune TEXT)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE dataset_metadata (dataset_name TEXT, "
                "last_updated_at TEXT, source_updated_at TEXT)"
            )
        )
        for sql in (m.DPE_SQL, m.ADDRESS_SQL):
            assert "code_departement = ANY(%(departements)s)" in sql
            adapted = sql.replace("= ANY(%(departements)s)", "IN ('44', '85')")
            connection.execute(text(adapted)).fetchall()
        connection.execute(text(m.METADATA_SQL)).fetchall()
    db.dispose()


def test_replay_strata_overlap_does_not_inflate_union(m: Any, inputs: dict) -> None:
    db = Database(
        m,
        [
            diagnostic(),
            diagnostic("second", regime_dpe="neuf", type_batiment="appartement"),
        ],
        [address()],
    )
    public, _, _ = run_before(m, inputs, connect=db)
    assert public["replay"]["covered"] == 1
    assert sum(s["covered"] for s in public["replay_strata"]) == 2
    assert len(public["replay_strata"]) == 17
    assert (
        public["harness_sha256"]
        == hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
    )


def test_replay_strata_reads_each_population_once(m: Any) -> None:
    class CountedPopulation(list):
        reads = 0

        def __iter__(self):
            self.reads += 1
            return super().__iter__()

    population = CountedPopulation(["covered", "empty", "unattempted"])
    evidence = {
        "memberships": [
            {
                "department": "44",
                "regime": "existant",
                "building": "maison",
                "ids": population,
            }
        ],
        "outcomes": [
            {"id": "covered", "cause": "covered", "covered": True},
            {"id": "empty", "cause": "inventory_empty", "covered": False},
            {"id": "other", "cause": "covered", "covered": True},
        ],
    }

    result = m.stratified_replay(evidence)[0]

    assert result["population"] == 3
    assert result["attempted"] == 2
    assert result["covered"] == result["confirmed_empty"] == 1
    assert result["sampling"] == "sample"
    assert population.reads == 1


def test_verified_gain_uses_frozen_stratum(m: Any, inputs: dict) -> None:
    query = m.freeze_corpus([address()], None)[0]["query"]
    empty = Response(
        {
            "donnees": [
                {
                    "type_detecte": "adresse",
                    "latitude": 47.2,
                    "longitude": -1.5,
                    "ban_id": BAN,
                    "dpe_inventory": inventory(0),
                }
            ]
        }
    )
    _, baseline, _ = run_before(m, inputs, get=Http({query: empty}))
    target = inputs["root"] / "gain.json"
    assert (
        m.main(
            [
                "--mode",
                "after",
                "--private-dir",
                str(inputs["private"]),
                "--report",
                str(target),
                "--baseline",
                str(baseline),
                "--config",
                str(inputs["config"]),
                "--source-manifest",
                str(inputs["manifest"]),
            ],
            connect=Database(m, [diagnostic(type_batiment="appartement")], [address()]),
            get=Http(),
        )
        == 0
    )
    report = json.loads(target.read_text())
    assert report["comparison"]["gains"] == 1
    gains = [s for s in report["comparison"]["strata"] if s["gains"]]
    assert len(gains) == 1 and gains[0]["building"] == "maison"
    assert m.check_report(report, inputs["private"])


def test_cli_module_and_direct_check_report(m: Any, inputs: dict) -> None:
    run_before(m, inputs)
    args = [
        "--check-report",
        "--private-dir",
        str(inputs["private"]),
        "--report",
        str(inputs["root"] / "before-report.json"),
    ]
    for command in (
        [sys.executable, "-m", "scripts.mesure_dpe_couverture"],
        [sys.executable, str(Path(m.__file__))],
    ):
        result = subprocess.run(
            command + args, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0
        assert SECRET not in result.stdout + result.stderr


@pytest.mark.parametrize("missing", ["created_at", "generations"])
def test_missing_measurement_provenance_is_rejected(
    m: Any, inputs: dict, missing: str
) -> None:
    _, path, _ = run_before(m, inputs)
    evidence = json.loads(path.read_text())
    if missing == "created_at":
        evidence["created_at"] = None
    else:
        evidence["sql"]["generations"] = []
    with pytest.raises(m.CoverageError):
        m.project(evidence, inputs["private"])
