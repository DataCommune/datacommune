"""Synthetic qualification fixtures only; no real decision or database access."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.commune import indicator_manifest as im

pytestmark = pytest.mark.unit
G1 = UUID("11111111-1111-4111-8111-111111111111")
G2 = UUID("22222222-2222-4222-8222-222222222222")
STAMP = "2026-09-09T12:00:00Z"


def fixture(tmp_path, generation=G1, dataset="dpe"):
    tmp_path.mkdir(exist_ok=True)
    decisions = {
        name: {
            "status": "pending",
            "rule_version": None,
            "reason_code": "synthetic_review_due",
        }
        for name in im.INDICATORS[dataset]
    }
    first = next(iter(decisions))
    decisions[first] = {
        "status": "qualified",
        "rule_version": "synthetic-rule-v1",
        "reason_code": "synthetic_only",
    }
    policy = {
        "schema_version": "commune-indicator-policy.v1",
        "dataset_name": dataset,
        "policy_version": "synthetic-policy-v1",
        "scope": "synthetic",
        "indicators": decisions,
    }
    policy_raw = json.dumps(policy).encode()
    policy_hash = hashlib.sha256(policy_raw).hexdigest()
    evidence = {
        "schema_version": "commune-indicator-review.v1",
        "dataset_name": dataset,
        "source_generation": str(generation),
        "source_snapshot_sha256": "a" * 64,
        "cog_sha256": "b" * 64,
        "cog_version": "synthetic-cog-v1",
        "policy_sha256": policy_hash,
        "scope": "synthetic",
        "producer": "synthetic-producer",
        "reviewer": "synthetic-reviewer",
        "qualified_at": STAMP,
        "indicators": decisions,
    }
    evidence_raw = json.dumps(evidence).encode()
    evidence_hash = hashlib.sha256(evidence_raw).hexdigest()
    (tmp_path / "policy.json").write_bytes(policy_raw)
    (tmp_path / "review.json").write_bytes(evidence_raw)
    payload = {
        "schema_version": "commune-indicators-manifest.v1",
        "dataset_name": dataset,
        "source_generation": str(generation),
        "source_snapshot_sha256": "a" * 64,
        "cog_version": "synthetic-cog-v1",
        "cog_sha256": "b" * 64,
        "source_observed_at": "2026-09-09T00:00:00Z",
        "scope": "synthetic",
        "producer": "synthetic-producer",
        "reviewer": "synthetic-reviewer",
        "qualified_at": STAMP,
        "policy_version": "synthetic-policy-v1",
        "policy": {"path": "policy.json", "sha256": policy_hash},
        "evidence": {"path": "review.json", "sha256": evidence_hash},
        "indicators": decisions,
        "dpe_as_of": "2026-09-09" if dataset == "dpe" else None,
        "dvf_anchor": None
        if dataset == "dpe"
        else {"etat": "unknown", "motif": "synthetic_unknown"},
    }
    if dataset == "dvf":
        for decision in decisions.values():
            decision.update(
                status="unknown",
                rule_version=None,
                reason_code="synthetic_anchor_unknown",
            )
        policy_raw = json.dumps(policy).encode()
        policy_hash = hashlib.sha256(policy_raw).hexdigest()
        evidence["policy_sha256"] = policy_hash
        evidence_raw = json.dumps(evidence).encode()
        evidence_hash = hashlib.sha256(evidence_raw).hexdigest()
        (tmp_path / "policy.json").write_bytes(policy_raw)
        (tmp_path / "review.json").write_bytes(evidence_raw)
        payload["policy"]["sha256"] = policy_hash
        payload["evidence"]["sha256"] = evidence_hash
    context = im.QualificationContext(
        dataset_name=dataset,
        source_generation=generation,
        source_snapshot_sha256="a" * 64,
        cog_sha256="b" * 64,
        cog_version="synthetic-cog-v1",
        policy_version="synthetic-policy-v1",
        policy_sha256=policy_hash,
        evidence_sha256=evidence_hash,
        scope="synthetic",
        checked_at="2026-09-09T13:00:00Z",
    )
    return payload, context


def validate(payload, context, directory):
    return im.validate_manifest(
        json.dumps(payload).encode(),
        expected_manifest_sha256=im.manifest_sha256(payload),
        context=context,
        evidence_root=directory,
    )


def test_g1_refused_for_g2_then_explicit_g2_fixture_qualifies(tmp_path):
    old, context = fixture(tmp_path / "g1")
    with pytest.raises(im.ManifestRefused, match="source_generation_unqualified"):
        validate(
            old, context.model_copy(update={"source_generation": G2}), tmp_path / "g1"
        )
    new, current = fixture(tmp_path / "g2", G2)
    result = validate(new, current, tmp_path / "g2")
    assert result.source_generation == G2
    result.require_indicator(next(iter(im.INDICATORS["dpe"])))


@pytest.mark.parametrize("state", ["unknown", "pending", "no_go"])
def test_distinct_unqualified_states_cannot_authorize_values(tmp_path, state):
    payload, context = fixture(tmp_path)
    result = validate(payload, context, tmp_path)
    field = next(
        name
        for name, decision in result.indicators.items()
        if decision.status != "qualified"
    )
    decision = im.IndicatorDecision(
        status=state, rule_version=None, reason_code="synthetic"
    )
    with pytest.raises(im.IndicatorNotQualified) as error:
        decision.require_qualified(field)
    assert error.value.state == state


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_generation", None),
        ("value", 12),
        ("watermark", "2026-01-01"),
        ("extra", "private"),
    ],
)
def test_closed_manifest_rejects_invented_fields_and_missing_generation(
    tmp_path, field, value
):
    payload, context = fixture(tmp_path)
    payload[field] = value
    with pytest.raises(im.ManifestRefused):
        validate(payload, context, tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_snapshot_sha256", "c" * 64),
        ("cog_sha256", "c" * 64),
        ("policy_sha256", "c" * 64),
        ("evidence_sha256", "c" * 64),
        ("scope", "publication"),
        ("policy_version", "other"),
        ("cog_version", "other"),
    ],
)
def test_external_trust_pins_and_scope_are_required(tmp_path, field, value):
    payload, context = fixture(tmp_path)
    with pytest.raises(im.ManifestRefused):
        validate(payload, context.model_copy(update={field: value}), tmp_path)


def test_manifest_hash_is_canonical_for_jsonb_and_duplicates_rejected(tmp_path):
    payload, context = fixture(tmp_path)
    raw = json.dumps(payload, indent=4, sort_keys=True).encode()
    result = im.validate_manifest(
        raw,
        expected_manifest_sha256=im.manifest_sha256(payload),
        context=context,
        evidence_root=tmp_path,
    )
    assert result.dataset_name == "dpe"
    raw = raw.replace(
        b'"dataset_name": "dpe"', b'"dataset_name": "dvf", "dataset_name": "dpe"'
    )
    with pytest.raises(im.ManifestRefused):
        im.validate_manifest(
            raw,
            expected_manifest_sha256=im.manifest_sha256(payload),
            context=context,
            evidence_root=tmp_path,
        )


@pytest.mark.parametrize("name", ["policy.json", "review.json"])
def test_tampered_receipts_refused_without_echo(tmp_path, name):
    payload, context = fixture(tmp_path)
    (tmp_path / name).write_text("PRIVATE_SENTINEL")
    with pytest.raises(im.ManifestRefused) as error:
        validate(payload, context, tmp_path)
    assert "PRIVATE_SENTINEL" not in str(error.value)


@pytest.mark.parametrize(
    "path",
    [
        "../policy.json",
        "/tmp/policy.json",
        "C:/private/policy.json",
        "\\\\host\\private",
        "a/../../policy.json",
        "policy.json:alternate_stream",
    ],
)
def test_evidence_paths_are_confined_before_read(tmp_path, path):
    payload, context = fixture(tmp_path)
    payload["policy"]["path"] = path
    with pytest.raises(im.ManifestRefused):
        validate(payload, context, tmp_path)


def test_symlink_escape_is_refused(tmp_path):
    payload, context = fixture(tmp_path)
    target = tmp_path / "link.json"
    try:
        target.symlink_to(tmp_path / "policy.json")
    except OSError:
        pytest.fail("Real filesystem symlink canary unavailable")
    payload["policy"]["path"] = "link.json"
    with pytest.raises(im.ManifestRefused):
        validate(payload, context, tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("qualified_at", "2026-09-08T12:00:00Z"),
        ("qualified_at", "2026-09-10T12:00:00Z"),
        ("source_observed_at", "2026-09-10T00:00:00Z"),
        ("qualified_at", "2026-09-09T12:00:00"),
        ("reviewer", "synthetic-producer"),
        ("qualified_at", 1788955200),
        ("dpe_as_of", 1788912000),
        ("dpe_as_of", "2026-09-09T00:00:00Z"),
    ],
)
def test_chronology_and_reviewer_are_constraints_not_authority(tmp_path, field, value):
    payload, context = fixture(tmp_path)
    payload[field] = value
    with pytest.raises(im.ManifestRefused):
        validate(payload, context, tmp_path)


def test_missing_manifest_is_typed_absence_and_storage_envelope_is_checked(tmp_path):
    payload, context = fixture(tmp_path)
    with pytest.raises(im.ManifestRefused) as error:
        im.load_qualified_manifest(None, context=context, evidence_root=tmp_path)
    assert error.value.code == "source_generation_unqualified"
    row = im.StoredManifest(
        dataset_name="dpe",
        source_generation=G1,
        manifest=payload,
        manifest_sha256=im.manifest_sha256(payload),
        qualified_at=STAMP,
    )
    assert (
        im.load_qualified_manifest(
            row, context=context, evidence_root=tmp_path
        ).source_generation
        == G1
    )
    with pytest.raises(im.ManifestRefused):
        im.load_qualified_manifest(
            row.model_copy(update={"source_generation": G2}),
            context=context,
            evidence_root=tmp_path,
        )


def test_unknown_dvf_anchor_has_no_values_or_invented_watermark(tmp_path):
    payload, context = fixture(tmp_path, dataset="dvf")
    assert validate(payload, context, tmp_path).dvf_anchor.etat == "unknown"
    payload["indicators"]["dvf.count"] = {
        "status": "qualified",
        "rule_version": "synthetic",
        "reason_code": "synthetic",
    }
    with pytest.raises(im.ManifestRefused):
        validate(payload, context, tmp_path)


def received(payload):
    return {
        "etat": "delivery_received_and_stock_bound",
        "version_qualification": "dvf-delivery-qualification.v2",
        "lot_id": "synthetic-lot-2024-2025",
        "periode_debut_inclus": "2024-01-01",
        "periode_fin_exclue": "2026-01-01",
        "departements": ["44", "85"],
        "acquisition_complete": True,
        "revisions_possibles": True,
        "exhaustivite_historique": "unknown",
        "generation_fournisseur": None,
        "date_observation": "2026-09-09T00:00:00Z",
        "preuve_qualification_sha256": payload["evidence"]["sha256"],
        "preuve_observation_sha256": "c" * 64,
        "preuve_reconstruction_sha256": "d" * 64,
        "snapshot_sha256": payload["source_snapshot_sha256"],
        "contenu_projete_sha256": "e" * 64,
        "cog_sha256": payload["cog_sha256"],
        "methode_projection": "sha256-sorted-row-digests-json.v1",
        "generation_stock": payload["source_generation"],
    }


def test_received_lot_is_not_complete_or_automatically_qualified(tmp_path):
    payload, context = fixture(tmp_path, dataset="dvf")
    payload["dvf_anchor"] = received(payload)
    result = validate(payload, context, tmp_path)
    assert result.dvf_anchor.exhaustivite_historique == "unknown"
    with pytest.raises(im.IndicatorNotQualified):
        result.require_indicator("dvf.count")
    payload["dvf_anchor"]["exhaustivite_historique"] = "complete"
    with pytest.raises(im.ManifestRefused):
        validate(payload, context, tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation_stock", str(G2)),
        ("snapshot_sha256", "f" * 64),
        ("cog_sha256", "f" * 64),
        ("date_observation", "2025-01-01T00:00:00Z"),
        ("preuve_qualification_sha256", "f" * 64),
    ],
)
def test_received_anchor_rejects_other_stock_or_impossible_period(
    tmp_path, field, value
):
    payload, context = fixture(tmp_path, dataset="dvf")
    payload["dvf_anchor"] = received(payload)
    payload["dvf_anchor"][field] = value
    with pytest.raises(im.ManifestRefused):
        validate(payload, context, tmp_path)


def test_qualification_cannot_be_changed_in_memory_to_admit_unknown(tmp_path):
    payload, context = fixture(tmp_path)
    result = validate(payload, context, tmp_path)
    with pytest.raises(TypeError):
        result.indicators["dpe.fg_share"] = result.indicators["dpe.document_count"]
    with pytest.raises(im.IndicatorNotQualified):
        result.require_indicator("dpe.fg_share")


@pytest.mark.parametrize("value", [datetime(2026, 9, 9, tzinfo=UTC), object()])
def test_non_json_stored_input_has_a_redacted_typed_refusal(tmp_path, value):
    payload, context = fixture(tmp_path)
    record = im.StoredManifest(
        dataset_name="dpe",
        source_generation=G1,
        manifest={**payload, "private_value": value},
        manifest_sha256=im.manifest_sha256(payload),
        qualified_at=STAMP,
    )
    with pytest.raises(im.ManifestRefused) as refusal:
        im.load_qualified_manifest(record, context=context, evidence_root=tmp_path)
    assert str(refusal.value) == "manifest_validation_failed"


def test_received_anchor_remains_immutable_and_serializes_the_public_contract(tmp_path):
    payload, context = fixture(tmp_path, dataset="dvf")
    payload["dvf_anchor"] = received(payload)
    result = validate(payload, context, tmp_path)
    anchor = result.dvf_anchor
    before = result.model_dump(mode="json")["dvf_anchor"]
    for field, value in (
        ("generation_stock", G2),
        ("date_observation", datetime(2030, 1, 1, tzinfo=UTC)),
        ("departements", ["44"]),
    ):
        with pytest.raises(ValueError, match="frozen"):
            setattr(anchor, field, value)
    with pytest.raises(TypeError):
        anchor.departements[0] = "85"
    with pytest.raises(AttributeError):
        anchor.departements.append("44")
    payload["dvf_anchor"]["departements"].clear()
    assert anchor.generation_stock == G1
    assert result.model_dump(mode="json")["dvf_anchor"] == before
    assert before == im.LivraisonDvfRecue.model_validate(received(payload)).model_dump(
        mode="json"
    )


def test_unknown_anchor_is_also_immutable(tmp_path):
    payload, context = fixture(tmp_path, dataset="dvf")
    result = validate(payload, context, tmp_path)
    with pytest.raises(ValueError, match="frozen"):
        result.dvf_anchor.motif = "synthetic_replacement"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":{"a":1,"a":2}}',
        b'{"x":NaN}',
        b"[]",
        b"\xff",
        b" " * (im.MAX_MANIFEST_BYTES + 1),
    ],
    ids=["duplicate", "nonfinite", "array", "encoding", "oversized"],
)
def test_duplicate_nested_keys_nonfinite_and_oversized_documents_fail(raw):
    with pytest.raises(im.ManifestRefused):
        im.parse_document(raw)


def test_unknown_indicator_cannot_carry_numeric_value(tmp_path):
    payload, context = fixture(tmp_path)
    payload["indicators"]["dpe.fg_share"]["value"] = 4
    with pytest.raises(im.ManifestRefused):
        validate(payload, context, tmp_path)


def test_oversized_receipt_is_refused(tmp_path):
    payload, context = fixture(tmp_path)
    with (tmp_path / "policy.json").open("wb") as stream:
        stream.write(b"x" * (im.MAX_EVIDENCE_BYTES + 1))
    with pytest.raises(im.ManifestRefused):
        validate(payload, context, tmp_path)


def test_actual_cli_validates_without_database_or_settings_and_redacts_errors(tmp_path):
    payload, context = fixture(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps(payload))
    (tmp_path / "context.json").write_text(context.model_dump_json())
    args = [
        sys.executable,
        "-m",
        "scripts.qualify_commune_indicators",
        "--manifest",
        str(tmp_path / "manifest.json"),
        "--manifest-sha256",
        im.manifest_sha256(payload),
        "--context",
        str(tmp_path / "context.json"),
        "--context-sha256",
        hashlib.sha256((tmp_path / "context.json").read_bytes()).hexdigest(),
        "--evidence-root",
        str(tmp_path),
    ]
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://SYNTHETIC_SECRET@127.0.0.1:1/no_db",
        "DEBUG": "PRIVATE_STARTUP_SENTINEL",
    }
    result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=15)
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout)["database_written"] is False
    result = subprocess.run(
        [*args, "--PRIVATE_SENTINEL"],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 2 and result.stderr == ""
    assert json.loads(result.stdout) == {
        "status": "refused",
        "code": "manifest_validation_failed",
        "database_written": False,
    }
    assert "PRIVATE" not in result.stdout and "SYNTHETIC_SECRET" not in result.stdout
