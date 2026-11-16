"""Synthetic v2 receipts must bind one delivery to the measured projection."""

import copy
import gzip
import hashlib
import json

import pytest

from scripts import mesure_commune_indicators as measure
from scripts.commune_delivery_qualification import ProjectionDigest

pytestmark = pytest.mark.unit
pytest_plugins = ["tests.test_commune_indicator_measurement"]


@pytest.mark.parametrize("payload", [[], None, "not-an-object"])
def test_manifest_dispatch_rejects_nonobjects_safely(tmp_path, payload):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(measure.MeasurementError):
        measure.read_manifest(path)


def put(directory, name, payload):
    content = json.dumps(payload).encode()
    (directory / name).write_bytes(content)
    return {"path": name, "sha256": hashlib.sha256(content).hexdigest()}


@pytest.fixture
def bundle(manifest_file):
    return build_bundle(manifest_file)


@pytest.fixture
def bundle_v3(manifest_file):
    """Contract v3 (plan 04.1-10): an explicit lot of hosted departements."""
    return build_bundle(manifest_file, ("44", "75", "85"))


def build_bundle(path, departments=None):
    directory = path.parent
    payload = json.loads(path.read_text())
    payload.pop("watermarks")
    payload.update(
        schema_version="commune-measure-source.v2", window_end_exclusive="2026-01-01"
    )
    if departments is not None:
        payload.update(
            schema_version="commune-measure-source.v3", departments=list(departments)
        )
    scope = departments or ("44", "85")
    columns = [
        "id_mutation",
        "date_mutation",
        "code_departement",
        "code_commune",
        "valeur_fonciere",
        "type_local",
        "surface_reelle_bati",
    ]
    parts = []
    for year in (2024, 2025):
        for dept in scope:
            raw = (
                ",".join(columns)
                + f"\nsynthetic,{year}-01-01,{dept},{dept}001,100,Maison,10\n"
            ).encode()
            compressed = gzip.compress(raw, mtime=0)
            name = f"synthetic-{year}-{dept}.csv.gz"
            (directory / name).write_bytes(compressed)
            parts.append(
                {
                    "release_id": "synthetic-release",
                    "year": year,
                    "department": dept,
                    "compressed": {
                        "path": name,
                        "sha256": hashlib.sha256(compressed).hexdigest(),
                    },
                    "decompressed_sha256": hashlib.sha256(raw).hexdigest(),
                    "schema_sha256": measure.digest(
                        json.dumps(columns, separators=(",", ":")).encode()
                    ),
                    "rows": 1,
                }
            )
    hashes = [p["compressed"]["sha256"] for p in parts]
    base = "https://files.data.gouv.fr/geo-dvf/2025-12/csv/"
    indexes = []
    for relative, links in [
        ("", ["2024/", "2025/"]),
        ("2024/", ["departements/"]),
        ("2025/", ["departements/"]),
        ("2024/departements/", [f"{dept}.csv.gz" for dept in scope]),
        ("2025/departements/", [f"{dept}.csv.gz" for dept in scope]),
    ]:
        name = f"index-{len(indexes)}.html"
        content = "".join(f'<a href="{link}">synthetic</a>' for link in links).encode()
        (directory / name).write_bytes(content)
        indexes.append(
            {
                "url": base + relative,
                "content": {
                    "path": name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            }
        )
    observer = put(
        directory,
        "observer.json",
        {
            "schema_version": "dvf-observer-binding.v2",
            "release_id": "synthetic-release",
            "period_start": "2024-01-01",
            "period_end_exclusive": "2026-01-01",
            "partition_sha256": hashes,
            "official_directory": base,
            "supplier_generation_code": None,
            "index_documents": indexes,
            "reviewer": "synthetic-reviewer",
            "reviewed_at": "2026-09-08T12:00:00Z",
            "decision": "official_scope_observed",
        },
    )
    projected = ProjectionDigest()
    projected.add({"department": "44", "synthetic": True})
    stock = put(
        directory,
        "stock.json",
        {
            "schema_version": "dvf-stock-binding.v2",
            "release_id": "synthetic-release",
            "partition_sha256": hashes,
            "reconstruction_run_id": "synthetic-run",
            "transformation_git_sha": "d" * 40,
            "transformation_config": put(directory, "config.json", {"synthetic": True}),
            "source_snapshot_sha256": payload["sources"]["dvf"]["snapshot_sha256"],
            "restore_receipt_sha256": payload["target"]["restore_receipt"]["sha256"],
            "cog_sha256": payload["cog_sha256"],
            "generation": None,
            "projected_rows": 1,
            "projected_content_sha256": projected.hexdigest(),
            "projection_method": "sha256-sorted-row-digests-json.v1",
        },
    )
    payload["delivery_qualification"] = {
        "schema_version": "dvf-delivery-qualification.v2",
        "release_id": "synthetic-release",
        "period_start": "2024-01-01",
        "period_end_exclusive": "2026-01-01",
        "acquisition_complete": True,
        "late_registrations_possible": True,
        "all_mutations_before_b_complete": "unknown",
        "partitions": parts,
        "observer_binding": observer,
        "stock_binding": stock,
    }
    if departments is not None:
        payload["delivery_qualification"].update(
            schema_version="dvf-delivery-qualification.v3",
            departments=list(departments),
        )
    path.write_text(json.dumps(payload))
    return path, projected


def mutate(path, change):
    payload = json.loads(path.read_text())
    change(payload)
    path.write_text(json.dumps(payload))


def mutate_receipt(path, key, change):
    payload = json.loads(path.read_text())
    receipt = payload["delivery_qualification"][key]
    value = json.loads((path.parent / receipt["path"]).read_text())
    change(value)
    payload["delivery_qualification"][key] = put(path.parent, receipt["path"], value)
    path.write_text(json.dumps(payload))


def test_v2_receipts_and_actual_projection_required_before_coverage(bundle):
    path, projected = bundle
    manifest = measure.read_manifest(path)
    assert measure.window_coverage(manifest) == {"44": "unknown", "85": "unknown"}
    measure.verify_measured_projection(manifest, projected)
    assert measure.window_coverage(manifest) == {
        "44": "delivery_received_and_stock_bound",
        "85": "delivery_received_and_stock_bound",
    }
    assert "watermarks" not in manifest.model_dump()
    assert manifest.delivery_qualification.all_mutations_before_b_complete == "unknown"


@pytest.mark.parametrize(
    "reviewed_at,accepted",
    [
        ("2022-01-01T00:00:00Z", False),
        ("2025-12-31T23:59:59Z", False),
        ("2026-01-01T00:00:00Z", True),
        ("2026-09-08T12:00:00Z", True),
    ],
)
def test_received_annual_period_does_not_follow_observation(
    bundle, reviewed_at, accepted
):
    path, projected = bundle
    # Rehash the altered receipt: its declared chronology, not file integrity,
    # must prevent a future annual period from being called fully received.
    mutate_receipt(
        path,
        "observer_binding",
        lambda receipt: receipt.update(reviewed_at=reviewed_at),
    )
    if not accepted:
        with pytest.raises(measure.MeasurementError):
            measure.read_manifest(path)
    else:
        manifest = measure.read_manifest(path)
        measure.verify_measured_projection(manifest, projected)
        assert measure.window_coverage(manifest) == {
            "44": "delivery_received_and_stock_bound",
            "85": "delivery_received_and_stock_bound",
        }


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["delivery_qualification"].update(acquisition_complete=False),
        lambda p: p["delivery_qualification"].update(acquisition_complete=1),
        lambda p: p["delivery_qualification"].update(late_registrations_possible=False),
        lambda p: p["delivery_qualification"].update(
            all_mutations_before_b_complete=True
        ),
        lambda p: p["delivery_qualification"]["partitions"].pop(),
        lambda p: p["delivery_qualification"]["partitions"].append(
            copy.deepcopy(p["delivery_qualification"]["partitions"][0])
        ),
        lambda p: p["delivery_qualification"]["partitions"][0].update(
            release_id="different-release"
        ),
        lambda p: p.update(watermarks={"44": None, "85": None}),
        lambda p: p["delivery_qualification"].update(complete_before="2026-01-01"),
        lambda p: p.update(window_end_exclusive="2027-01-01"),
        lambda p: p["delivery_qualification"]["partitions"][0].update(rows=2),
        lambda p: p["delivery_qualification"]["partitions"][0].update(
            decompressed_sha256="f" * 64
        ),
        lambda p: p["delivery_qualification"]["partitions"][0].update(
            schema_sha256="f" * 64
        ),
    ],
)
def test_invalid_or_mixed_delivery_is_refused(bundle, change):
    path, _ = bundle
    mutate(path, change)
    with pytest.raises(measure.MeasurementError):
        measure.read_manifest(path)


@pytest.mark.parametrize(
    "key,change",
    [
        ("observer_binding", lambda r: r.update(decision="pending")),
        ("observer_binding", lambda r: r.update(release_id="another-release")),
        ("observer_binding", lambda r: r["partition_sha256"].pop()),
        ("observer_binding", lambda r: r.update(reviewed_at="2026-09-08T12:00:00")),
        ("observer_binding", lambda r: r["index_documents"].pop()),
        (
            "observer_binding",
            lambda r: r["index_documents"].append(r["index_documents"][0]),
        ),
        (
            "observer_binding",
            lambda r: r.update(official_directory="https://example.com/"),
        ),
        ("observer_binding", lambda r: r.update(supplier_generation_code="unattested")),
        ("stock_binding", lambda r: r.update(source_snapshot_sha256="f" * 64)),
        ("stock_binding", lambda r: r.update(restore_receipt_sha256="f" * 64)),
        ("stock_binding", lambda r: r.update(cog_sha256="f" * 64)),
        (
            "stock_binding",
            lambda r: r.update(generation="11111111-1111-4111-8111-111111111111"),
        ),
        ("stock_binding", lambda r: r["partition_sha256"].pop()),
    ],
)
def test_receipt_hash_is_not_enough_when_binding_is_wrong(bundle, key, change):
    path, _ = bundle
    mutate_receipt(path, key, change)
    with pytest.raises(measure.MeasurementError):
        measure.read_manifest(path)


def test_tampered_file_is_refused(bundle):
    path, _ = bundle
    payload = json.loads(path.read_text())
    artifact = payload["delivery_qualification"]["partitions"][0]["compressed"]
    (path.parent / artifact["path"]).write_bytes(b"different source")
    with pytest.raises(measure.MeasurementError):
        measure.read_manifest(path)


def test_rehashed_official_index_missing_one_partition_is_refused(bundle):
    path, _ = bundle

    def alter_index(receipt):
        index = receipt["index_documents"][-1]["content"]
        target = path.parent / index["path"]
        content = target.read_bytes().replace(b"85.csv.gz", b"86.csv.gz")
        target.write_bytes(content)
        index["sha256"] = hashlib.sha256(content).hexdigest()

    mutate_receipt(path, "observer_binding", alter_index)
    with pytest.raises(measure.MeasurementError):
        measure.read_manifest(path)


def test_missing_reconstruction_receipt_is_refused(bundle):
    path, _ = bundle
    mutate(path, lambda p: p["delivery_qualification"].pop("stock_binding"))
    with pytest.raises(measure.MeasurementError):
        measure.read_manifest(path)


def test_equal_count_but_different_actual_stock_is_refused(bundle):
    path, projected = bundle
    manifest = measure.read_manifest(path)
    changed = ProjectionDigest()
    changed.add({"department": "85", "synthetic": True})
    with pytest.raises(measure.MeasurementError):
        measure.verify_measured_projection(manifest, changed)
    assert measure.window_coverage(manifest)["44"] == "unknown"
    measure.verify_measured_projection(manifest, projected)


def test_unknown_candidate_can_be_measured_without_becoming_qualified(bundle):
    path, projected = bundle
    mutate(path, lambda p: p.update(delivery_qualification=None))
    manifest = measure.read_manifest(path)
    measure.verify_measured_projection(manifest, projected)
    assert measure.window_coverage(manifest) == {"44": "unknown", "85": "unknown"}


def test_projection_identity_preserves_duplicates_and_ignores_order():
    one, two, reversed_rows = ProjectionDigest(), ProjectionDigest(), ProjectionDigest()
    one.add({"a": 1})
    for value in (1, 2, 1):
        two.add({"a": value})
    for value in (1, 1, 2):
        reversed_rows.add({"a": value})
    assert two.hexdigest() == reversed_rows.hexdigest()
    assert one.hexdigest() != two.hexdigest()


@pytest.mark.parametrize("corruption", ["year", "department", "columns", "crc"])
def test_rehashed_bad_artifact_still_fails_content_validation(bundle, corruption):
    path, _ = bundle
    payload = json.loads(path.read_text())
    qualification = payload["delivery_qualification"]
    part = qualification["partitions"][0]
    artifact = path.parent / part["compressed"]["path"]
    old_hash = part["compressed"]["sha256"]
    raw = gzip.decompress(artifact.read_bytes())
    if corruption == "year":
        raw = raw.replace(b"2024-01-01", b"2023-01-01")
    elif corruption == "department":
        raw = raw.replace(b",44,44001,", b",85,85001,")
    elif corruption == "columns":
        raw = raw.replace(b",100,Maison,10", b",100,Maison,10,extra")
    compressed = gzip.compress(raw, mtime=0)
    if corruption == "crc":
        compressed = compressed[:-8] + bytes([compressed[-8] ^ 1]) + compressed[-7:]
    artifact.write_bytes(compressed)
    new_hash = hashlib.sha256(compressed).hexdigest()
    part["compressed"]["sha256"] = new_hash
    part["decompressed_sha256"] = hashlib.sha256(raw).hexdigest()
    for key in ("observer_binding", "stock_binding"):
        receipt = qualification[key]
        value = json.loads((path.parent / receipt["path"]).read_text())
        value["partition_sha256"] = [
            new_hash if item == old_hash else item for item in value["partition_sha256"]
        ]
        qualification[key] = put(path.parent, receipt["path"], value)
    path.write_text(json.dumps(payload))
    with pytest.raises(measure.MeasurementError):
        measure.read_manifest(path)


def test_changed_content_invalidates_a_previous_success(bundle):
    path, projected = bundle
    manifest = measure.read_manifest(path)
    measure.verify_measured_projection(manifest, projected)
    assert (
        measure.window_coverage(manifest)["44"] == "delivery_received_and_stock_bound"
    )
    with pytest.raises(measure.MeasurementError):
        measure.verify_measured_projection(manifest, ProjectionDigest())
    assert measure.window_coverage(manifest)["44"] == "unknown"


def test_v2_measurement_output_still_waits_for_publication_decision(
    bundle, monkeypatch
):
    path, projected = bundle
    monkeypatch.setenv(
        "DC_MEASURE_DATABASE_URL",
        "host=127.0.0.1 port=55491 dbname=dc_measure_fixture user=test",
    )

    def measured(manifest, params):
        measure.verify_measured_projection(manifest, projected)
        return {"dvf": {}, "dpe": {}}

    monkeypatch.setattr(measure, "run", measured)
    output = path.parent / "output-v2"
    assert (
        measure.main(
            [
                "--dsn-env",
                "DC_MEASURE_DATABASE_URL",
                "--source-manifest",
                str(path),
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads((output / "59.1-MEASURES.json").read_text())
    assert report["schema_version"] == "commune-measurements.v2"
    assert report["publication_decision"] == "pending_independent_review"
    assert (
        report["source"]["delivery_qualification"]["all_mutations_before_b_complete"]
        == "unknown"
    )


def test_v3_lot_of_hosted_departements_binds_its_receipts(bundle_v3):
    path, projected = bundle_v3
    manifest = measure.read_manifest(path)
    assert isinstance(manifest, measure.ManifestV3)
    assert manifest.departments == ("44", "75", "85")
    measure.verify_measured_projection(manifest, projected)
    assert measure.window_coverage(manifest) == {
        dept: "delivery_received_and_stock_bound" for dept in ("44", "75", "85")
    }


@pytest.mark.parametrize(
    "change",
    [
        # A departement outside the hostable list, or a collectivity (D-123).
        lambda p: p.update(departments=["44", "75", "85", "99"]),
        lambda p: p["delivery_qualification"].update(departments=["44", "75", "975"]),
        # The delivery must cover exactly the measured departements.
        lambda p: p["delivery_qualification"].update(departments=["44", "85"]),
        # Departements once and in canonical order.
        lambda p: p.update(departments=["75", "44", "85"]),
        # Versions 1 and 2 keep their reading rule: exactly 44 then 85.
        lambda p: p.update(schema_version="commune-measure-source.v2"),
        lambda p: p["delivery_qualification"].update(
            schema_version="dvf-delivery-qualification.v2"
        ),
    ],
)
def test_v3_scope_is_refused_outside_its_rule(bundle_v3, change):
    path, _ = bundle_v3
    mutate(path, change)
    with pytest.raises(measure.MeasurementError):
        measure.read_manifest(path)


def test_v3_missing_partition_of_a_lot_departement_is_refused(bundle_v3):
    path, _ = bundle_v3
    mutate(
        path,
        lambda p: p["delivery_qualification"].update(
            partitions=[
                part
                for part in p["delivery_qualification"]["partitions"]
                if part["department"] != "75"
            ]
        ),
    )
    with pytest.raises(measure.MeasurementError):
        measure.read_manifest(path)
