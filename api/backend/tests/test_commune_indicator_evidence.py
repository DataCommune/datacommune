import hashlib
import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_commune_indicator_evidence.py"
SPEC = importlib.util.spec_from_file_location("indicator_evidence", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
SHA = "a" * 40
SCHEMA = "096_commune_indicator_build_activation"


def make_test_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\0" + b"\0\0\0" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sketch_manifest(tmp_path: Path) -> Path:
    payload = write_json(
        tmp_path / "commune.json",
        {
            "donnees": [
                {
                    "code_insee": "85047",
                    "indicateurs_dvf": {"version_contrat": "1.1", "generation": "g1"},
                    "indicateurs_dpe": {"version_contrat": "1.1", "generation": "g2"},
                }
            ]
        },
    )
    png = tmp_path / "375.png"
    png.write_bytes(make_test_png(375, 800))
    return write_json(
        tmp_path / "sketch.json",
        {
            "version": 1,
            "scope": "preproduction",
            "candidate_sha": SHA,
            "generations": {"dvf": "g1", "dpe": "g2"},
            "provenance": {
                "kind": "real-http",
                "operator": "ci",
                "captured_at": "2026-09-10T12:00:00Z",
                "base_url": "https://preprod.invalid",
            },
            "payloads": [{"path": str(payload), "sha256": digest(payload)}],
            "screenshots": [
                {"path": str(png), "sha256": digest(png), "width": 375, "height": 800}
            ],
        },
    )


def test_sketch_accepts_bound_real_preproduction_artifacts(tmp_path):
    module.verify_sketch(sketch_manifest(tmp_path))


@pytest.mark.parametrize("mutation", ["synthetic", "sha", "generation", "tamper"])
def test_sketch_fails_closed(tmp_path, mutation):
    path = sketch_manifest(tmp_path)
    data = json.loads(path.read_text())
    if mutation == "synthetic":
        data["scope"] = "synthetic"
    elif mutation == "sha":
        data["candidate_sha"] = "b" * 40
    elif mutation == "generation":
        data["generations"]["dvf"] = "other"
    else:
        Path(data["payloads"][0]["path"]).write_text("tampered")
    if mutation != "tamper":
        write_json(path, data)
    with pytest.raises(module.EvidenceError):
        module.verify_sketch(path, expected_sha=SHA)


def requirements(tmp_path: Path) -> Path:
    return write_json(
        tmp_path / "runs.json",
        {
            "version": 1,
            "workflows": [
                {"name": name, "required_jobs": ["required"]}
                for name in module.REQUIRED_WORKFLOWS
            ],
        },
    )


def fake_gh(name: str, *, conclusion="success", attempt=2, job_conclusion="success"):
    return {
        "databaseId": hash(name) % 10000 + 1,
        "headSha": SHA,
        "status": "completed",
        "conclusion": conclusion,
        "workflowName": name,
        "runAttempt": attempt,
        "jobs": [
            {"name": "required", "status": "completed", "conclusion": job_conclusion}
        ],
    }


def test_release_runs_checks_list_and_jobs_for_four_workflows(tmp_path, monkeypatch):
    runs = [fake_gh(name) for name in module.REQUIRED_WORKFLOWS]
    monkeypatch.setattr(module, "gh_run_list", lambda *_: runs)
    monkeypatch.setattr(
        module,
        "gh_run_view",
        lambda _repo, run_id: next(run for run in runs if run["databaseId"] == run_id),
    )
    module.verify_release_runs(SHA, "owner/repo", requirements(tmp_path))


@pytest.mark.parametrize(
    "defect", ["missing", "skipped", "failure", "sha", "older_green"]
)
def test_release_runs_refuses_incomplete_or_fallback_evidence(
    tmp_path, monkeypatch, defect
):
    runs = [fake_gh(name) for name in module.REQUIRED_WORKFLOWS]
    if defect == "missing":
        runs.pop()
    elif defect == "skipped":
        runs[0]["jobs"][0]["conclusion"] = "skipped"
    elif defect == "failure":
        runs[0]["conclusion"] = "failure"
    elif defect == "sha":
        runs[0]["headSha"] = "b" * 40
    else:
        runs[0]["databaseId"] = 999
        runs.append(
            {
                **fake_gh(module.REQUIRED_WORKFLOWS[0], conclusion="failure"),
                "databaseId": 1000,
            }
        )
    monkeypatch.setattr(module, "gh_run_list", lambda *_: runs)
    monkeypatch.setattr(
        module,
        "gh_run_view",
        lambda _repo, run_id: next(run for run in runs if run["databaseId"] == run_id),
    )
    with pytest.raises(module.EvidenceError):
        module.verify_release_runs(SHA, "owner/repo", requirements(tmp_path))


def served_files(tmp_path: Path):
    receipt = write_json(
        tmp_path / "receipt.json",
        {
            "version": 1,
            "candidate_sha": SHA,
            "image_id": "sha256:" + "c" * 64,
            "container_image_id": "sha256:" + "c" * 64,
            "image_label_or_tag": f"datacommune-preprod-api:sha-{SHA}",
            "deployment_run": {"head_sha": SHA, "conclusion": "success"},
            "schema": SCHEMA,
        },
    )
    expected = write_json(
        tmp_path / "expected.json",
        {
            "code_insee": "85047",
            "indicateurs_dvf": {
                "generation": "g1",
                "residentiel_maison": {"courante": {"effectif": 4, "periode": "2025"}},
            },
            "indicateurs_dpe": {
                "generation": "g2",
                "part_fg": {"valeur": 12.5, "periode": "2024"},
            },
        },
    )
    response = {
        "donnees": [
            {
                "code_insee": "85047",
                "indicateurs_dvf": {
                    "generation": "g1",
                    "residentiel_maison": {
                        "courante": {"effectif": 4, "periode": "2025"}
                    },
                },
                "indicateurs_dpe": {
                    "generation": "g2",
                    "part_fg": {"valeur": 12.5, "periode": "2024"},
                },
            }
        ]
    }
    frontend = {"environment": "preproduction", "deployment_sha": SHA}
    api = {
        "environment": "preproduction",
        "deployment_sha": SHA,
        "schema_actual": SCHEMA,
        "schema_expected": SCHEMA,
    }
    return receipt, expected, response, frontend, api


def test_served_binds_runtime_versions_and_real_commune(tmp_path, monkeypatch):
    receipt, expected, response, frontend, api = served_files(tmp_path)
    monkeypatch.setattr(
        module,
        "read_json",
        lambda url, *_: (
            response
            if "/v1/commune" in url
            else (frontend if url.endswith("/__version") else api)
        ),
    )
    module.verify_served(SHA, receipt, "https://preprod.invalid", expected, "secret")


@pytest.mark.parametrize(
    "defect", ["image", "container_image", "tag", "run", "schema", "generation"]
)
def test_served_refuses_runtime_or_generation_mismatch(tmp_path, monkeypatch, defect):
    receipt, expected, response, frontend, api = served_files(tmp_path)
    data = json.loads(receipt.read_text())
    if defect == "image":
        data["image_id"] = "not-an-image-id"
    elif defect == "container_image":
        data["container_image_id"] = "sha256:" + "d" * 64
    elif defect == "tag":
        data["image_label_or_tag"] = "datacommune-preprod-api:latest"
    elif defect == "run":
        data["deployment_run"]["conclusion"] = "failure"
    elif defect == "schema":
        data["schema"] = "095_old"
    else:
        response["donnees"][0]["indicateurs_dvf"]["generation"] = "other"
    write_json(receipt, data)
    monkeypatch.setattr(
        module,
        "read_json",
        lambda url, *_: (
            response
            if "/v1/commune" in url
            else (frontend if url.endswith("/__version") else api)
        ),
    )
    with pytest.raises(module.EvidenceError):
        module.verify_served(
            SHA, receipt, "https://preprod.invalid", expected, "secret"
        )


@pytest.mark.parametrize("missing", ["indicateurs_dvf", "indicateurs_dpe"])
def test_served_requires_independent_dvf_and_dpe_expectations(
    tmp_path, monkeypatch, missing
):
    receipt, expected, response, frontend, api = served_files(tmp_path)
    data = json.loads(expected.read_text())
    del data[missing]
    write_json(expected, data)
    monkeypatch.setattr(
        module,
        "read_json",
        lambda url, *_: (
            response
            if "/v1/commune" in url
            else (frontend if url.endswith("/__version") else api)
        ),
    )
    with pytest.raises(module.EvidenceError):
        module.verify_served(
            SHA, receipt, "https://preprod.invalid", expected, "secret"
        )


def test_cli_requires_named_environment_variables(monkeypatch):
    monkeypatch.delenv("MISSING", raising=False)
    assert module.main(["sketch", "--manifest-env", "MISSING"]) == 1
