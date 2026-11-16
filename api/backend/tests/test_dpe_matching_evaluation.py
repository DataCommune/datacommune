import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.evaluate_dpe_matching import (
    CorpusError,
    evaluate_corpus,
    load_corpus,
    main,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def page(refs=(), complete=True, unavailable=False):
    items = [{"document_ref": ref} for ref in refs]
    if unavailable:
        return {
            "state": "unavailable",
            "distribution": {},
            "page_size": 10,
            "has_more": False,
            "read_state": "partial",
        }
    return {
        "state": "ready",
        "context_ref": "context",
        "items": items,
        "total": len(items) if complete else None,
        "distribution": {"unknown": len(items)} if complete else {},
        "page_size": 10,
        "has_more": not complete,
        "next_cursor": "next" if not complete else None,
        "read_state": "complete" if complete else "partial",
    }


def case(case_id, partition="control", truth="unknown", refs=(), complete=True):
    return {
        "case_id": case_id,
        "department": "44",
        "property_type": "maison",
        "partition": partition,
        "address_group": f"a-{case_id}",
        "chain_groups": [f"c-{case_id}"],
        "target": {"type_bien": "maison"},
        "inventory": page(refs, complete),
        "truth": truth,
        "true_document_id": "true-stable" if truth == "document" else None,
        "target_proof": "target",
        "truth_proof": "truth" if truth != "unknown" else None,
        "document_id_by_ref": {ref: f"id-{ref}" for ref in refs},
    }


def corpus(tmp_path, cases, provenance="synthetic"):
    proof_dir = tmp_path / "proofs"
    proof_dir.mkdir(parents=True)
    target, truth = proof_dir / "target.txt", proof_dir / "truth.txt"
    target.write_text("target", encoding="utf-8")
    truth.write_text("truth", encoding="utf-8")
    target_origin = (
        "independent_observation"
        if provenance == "independent_ground_truth"
        else "synthetic_observation"
    )
    data = {
        "corpus_version": 1,
        "corpus_id": "synthetic-corpus",
        "provenance": provenance,
        "proofs": {
            "target": {
                "path": "proofs/target.txt",
                "sha256": sha(target),
                "origin": target_origin,
                "independently_reviewed": True,
            },
            "truth": {
                "path": "proofs/truth.txt",
                "sha256": sha(truth),
                "origin": "independent_observation",
                "independently_reviewed": True,
            },
        },
        "cases": cases,
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "field,value",
    [("decision", "attributed"), ("consulted", True), ("unexpected", "secret")],
)
def test_closed_corpus_rejects_declarative_results(tmp_path, field, value):
    path = corpus(tmp_path, [case("one")])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cases"][0][field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


def test_document_oracle_is_independent_and_may_be_outside_inventory(tmp_path):
    path = corpus(tmp_path, [case("doc", truth="document")], "independent_ground_truth")
    assert load_corpus(path, sha(path)).cases[0].true_document_id == "true-stable"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["proofs"]["truth"]["origin"] = "evaluated_candidate"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


@pytest.mark.parametrize("origin", ["synthetic_observation", "source_observation"])
def test_ground_truth_target_must_also_be_independent(tmp_path, origin):
    path = corpus(tmp_path, [case("doc", truth="document")], "independent_ground_truth")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["proofs"]["target"]["origin"] = origin
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


def test_source_observed_cannot_claim_identity_truth(tmp_path):
    path = corpus(tmp_path, [case("doc", truth="document")], "source_observed")
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


def test_unknown_none_duplicates_and_tampering_are_rejected(tmp_path):
    for truth in ("none", "unknown"):
        item = case(truth, truth=truth)
        item["true_document_id"] = "forbidden"
        path = corpus(tmp_path / truth, [item])
        with pytest.raises(CorpusError, match="invalid private corpus"):
            load_corpus(path, sha(path))
    path = corpus(tmp_path / "duplicate", [case("same"), case("same")])
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))
    path = corpus(tmp_path / "tamper", [case("one")])
    (path.parent / "proofs/target.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


def test_transitive_groups_cannot_cross_partitions(tmp_path):
    first, middle, last = (
        case("first", "calibration"),
        case("middle", "calibration"),
        case("last"),
    )
    middle["address_group"] = first["address_group"]
    last["chain_groups"] = middle["chain_groups"]
    path = corpus(tmp_path, [first, middle, last])
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


def test_real_engine_and_retrieval_states(monkeypatch, tmp_path):
    cases = [
        case("found", truth="document", refs=("visible",)),
        case("partial", truth="document", complete=False),
        case("missing", truth="document"),
        case("unknown", "calibration"),
    ]
    cases[0]["document_id_by_ref"] = {"visible": "true-stable"}
    path = corpus(tmp_path, cases, "independent_ground_truth")
    import scripts.evaluate_dpe_matching as evaluation

    calls, original = [], evaluation.compare_inventory
    monkeypatch.setattr(
        evaluation,
        "compare_inventory",
        lambda inventory, target: (calls.append(1), original(inventory, target))[1],
    )
    report = evaluate_corpus(path, sha(path))
    assert len(calls) == 4
    assert report["matrix"]["document"] == {"unassigned": 3, "absent": 0, "error": 0}
    assert report["retrieval"] == {
        "document_truth": 3,
        "eligible": 2,
        "found": 1,
        "not_found_complete": 1,
        "indeterminate": 1,
    }
    assert report["metrics"]["precision"]["value"] is None
    assert report["metrics"]["consultation"]["value"] is None
    assert report["metrics"]["attribution"] == {
        "numerator": 0,
        "denominator": 4,
        "value": 0.0,
    }
    assert report["segments"]["partition/calibration"]["cases"] == 1
    assert report["segments"]["partition/control"]["retrieval"]["document_truth"] == 3
    assert "true-stable" not in json.dumps(report)


def test_error_is_distinct_and_output_cannot_overwrite_input(tmp_path, capsys):
    item = case("error")
    item["inventory"] = page(unavailable=True)
    path = corpus(tmp_path, [item])
    assert evaluate_corpus(path, sha(path))["matrix"]["unknown"]["error"] == 1
    assert (
        main(
            ["--corpus", str(path), "--corpus-sha256", sha(path), "--output", str(path)]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "evaluation failed\n"
    assert main([]) == 2
    assert capsys.readouterr().err == "evaluation failed\n"

    proof = path.parent / "proofs/target.txt"
    hardlink = path.parent / "hardlink-output.json"
    os.link(proof, hardlink)
    original = proof.read_bytes()
    assert (
        main(
            [
                "--corpus",
                str(path),
                "--corpus-sha256",
                sha(path),
                "--output",
                str(hardlink),
            ]
        )
        == 2
    )
    assert proof.read_bytes() == original


def test_stable_document_identity_cannot_cross_partitions(tmp_path):
    calibration = case("calibration", "calibration", refs=("first",))
    control = case("control", "control", refs=("second",))
    calibration["document_id_by_ref"] = {"first": "shared-stable-id"}
    control["document_id_by_ref"] = {"second": "shared-stable-id"}
    path = corpus(tmp_path, [calibration, control])
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


def test_direct_cli_executes_engine_without_pythonpath(tmp_path):
    private_canary = "PRIVATE-CLI-CANARY"
    path = corpus(tmp_path, [case("direct-cli")])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["corpus_id"] = private_canary
    path.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "aggregate.json"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    backend = Path(__file__).parents[1]
    command = [
        sys.executable,
        "scripts/evaluate_dpe_matching.py",
        "--corpus",
        str(path),
        "--corpus-sha256",
        sha(path),
        "--output",
        str(output),
    ]

    completed = subprocess.run(
        command,
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["counts"]["executed"] == 1
    assert report["matrix"]["unknown"]["unassigned"] == 1
    assert private_canary not in json.dumps(report)

    command[command.index(sha(path))] = "0" * 64
    failed = subprocess.run(
        command,
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode == 2
    assert failed.stdout == ""
    assert failed.stderr == "evaluation failed\n"
    assert private_canary not in failed.stderr


def apartment(case_id, partition, building_group=None):
    item = case(case_id, partition)
    item["property_type"] = "appartement"
    item["target"] = {"type_bien": "appartement"}
    if building_group is not None:
        item["building_group"] = building_group
    return item


def test_neighbour_apartments_of_one_building_cannot_cross_partitions(tmp_path):
    same_side = [
        apartment("left", "control", "building-1"),
        apartment("right", "control", "building-1"),
    ]
    path = corpus(tmp_path / "same", same_side)
    loaded = load_corpus(path, sha(path))
    assert [item.building_group for item in loaded.cases] == ["building-1"] * 2

    split = [
        apartment("left", "calibration", "building-1"),
        apartment("right", "control", "building-1"),
    ]
    path = corpus(tmp_path / "split", split)
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))

    path = corpus(tmp_path / "empty", [apartment("left", "control", "")])
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


def test_building_group_links_transitively_with_documents(tmp_path):
    # Only the building links "control" to the calibration side: control shares
    # building-1 with "bridge", which shares a document with "calibration".
    control = apartment("control", "control", "building-1")
    bridge = apartment("bridge", "calibration", "building-1")
    calibration = case("calibration", "calibration", refs=("ref-calibration",))
    bridge["inventory"] = page(("ref-bridge",))
    bridge["document_id_by_ref"] = {"ref-bridge": "shared-document"}
    calibration["document_id_by_ref"] = {"ref-calibration": "shared-document"}
    path = corpus(tmp_path / "linked", [control, bridge, calibration])
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))

    bridge["building_group"] = "building-2"
    path = corpus(tmp_path / "separate", [control, bridge, calibration])
    assert len(load_corpus(path, sha(path)).cases) == 3


def test_unknown_buildings_and_independent_groups_are_counted(tmp_path):
    cases = [
        apartment("known-a", "control", "building-1"),
        apartment("known-b", "control", "building-1"),
        apartment("unknown", "control"),
        case("house", "calibration"),
    ]
    path = corpus(tmp_path, cases)
    report = evaluate_corpus(path, sha(path))
    assert report["counts"]["building_group_unknown"] == 2
    assert report["counts"]["independent_groups"] == 3


@pytest.mark.parametrize("provenance", ["synthetic", "source_observed"])
def test_identity_is_explicitly_non_evaluable(tmp_path, provenance):
    path = corpus(tmp_path, [case("one")], provenance)
    report = evaluate_corpus(path, sha(path))
    assert report["identity"] == {
        "status": "non_evaluable",
        "reasons": ["no_independent_truth", "no_positive_attribution_policy"],
    }
    assert report["metrics"]["precision"] == {
        "numerator": 0,
        "denominator": 0,
        "value": None,
    }


def test_independent_truth_still_gives_no_identity_precision(tmp_path):
    path = corpus(tmp_path, [case("doc", truth="document")], "independent_ground_truth")
    report = evaluate_corpus(path, sha(path))
    assert report["counts"]["independent_truth"] == 1
    assert report["identity"] == {
        "status": "non_evaluable",
        "reasons": ["no_positive_attribution_policy"],
    }
    assert report["metrics"]["precision"]["value"] is None


def test_derived_ban_or_rnb_proof_stays_a_source_observation(tmp_path):
    # A proof re-reading BAN/RNB links derived from the same geocoding is a
    # source observation; it can never establish independent dwelling truth.
    path = corpus(tmp_path, [case("doc", truth="document")], "independent_ground_truth")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["proofs"]["truth"]["origin"] = "source_observation"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


def test_unknown_oracle_stays_unknown_and_is_counted(tmp_path):
    item = case("unknown")
    item["truth_proof"] = "truth"
    path = corpus(tmp_path / "with-proof", [item])
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))
    path = corpus(tmp_path / "counted", [case("unknown"), case("other")])
    report = evaluate_corpus(path, sha(path))
    assert report["counts"]["unknown_truth"] == 2
    assert report["metrics"]["indeterminate"] == {
        "numerator": 2,
        "denominator": 2,
        "value": 1.0,
    }
    assert report["retrieval"]["document_truth"] == 0


def test_single_byte_proof_alteration_is_rejected(tmp_path):
    path = corpus(tmp_path, [case("one")])
    proof = path.parent / "proofs/target.txt"
    content = bytearray(proof.read_bytes())
    content[0] ^= 0x01
    proof.write_bytes(bytes(content))
    assert len(content) == len("target")
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


@pytest.mark.parametrize("reviewed", [False, "true", 1])
def test_independent_proof_requires_a_true_review_boolean(tmp_path, reviewed):
    path = corpus(tmp_path, [case("one")])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["proofs"]["truth"]["origin"] == "independent_observation"
    data["proofs"]["truth"]["independently_reviewed"] = reviewed
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


@pytest.mark.parametrize("reviewed", ["true", 1])
def test_review_flag_is_a_strict_boolean_on_every_proof(tmp_path, reviewed):
    path = corpus(tmp_path, [case("one")])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["proofs"]["target"]["independently_reviewed"] = reviewed
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CorpusError, match="invalid private corpus"):
        load_corpus(path, sha(path))


def test_unreviewed_synthetic_proof_is_declared_not_ticked(tmp_path):
    # A synthetic or source observation nobody reviewed must be loadable as
    # such, instead of forcing an automatically ticked review flag.
    path = corpus(tmp_path, [case("one")])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["proofs"]["target"]["origin"] == "synthetic_observation"
    data["proofs"]["target"]["independently_reviewed"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    report = evaluate_corpus(path, sha(path))
    assert report["counts"]["proofs"] == {"declared": 2, "independently_reviewed": 1}


def test_private_sentinels_never_reach_the_report(tmp_path, capsys):
    sentinel = "SENTINEL-0416"
    item = apartment(f"{sentinel}-case", "control", f"{sentinel}-building")
    item["address_group"] = f"{sentinel}-address"
    item["chain_groups"] = [f"{sentinel}-chain"]
    item["inventory"] = page((f"{sentinel}-ref",))
    item["document_id_by_ref"] = {f"{sentinel}-ref": f"{sentinel}-document"}
    path = corpus(tmp_path, [item])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["corpus_id"] = f"{sentinel}-corpus"
    path.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "aggregate.json"
    assert (
        main(
            [
                "--corpus",
                str(path),
                "--corpus-sha256",
                sha(path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert sentinel not in output.read_text(encoding="utf-8")
    assert sentinel not in captured.out + captured.err
