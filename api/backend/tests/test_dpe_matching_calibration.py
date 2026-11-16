import json
from pathlib import Path

import pytest

from scripts.calibrate_dpe_matching import (
    MANIFEST_PIN,
    build_evaluation_metrics,
    load_annotations,
    measure_sources,
    sha256,
    source_filename,
    summarize_annotations,
    validate_annotations,
    validate_manifest,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dpe_matching_synthetic.json"


def test_windows_manifest_path_has_portable_bounded_filename():
    entry = {
        "path": r"C:\tmp\private\dpe03existant-44.csv",
        "source_slug": "dpe03existant",
        "department": "44",
    }
    assert source_filename(entry) == "dpe03existant-44.csv"
    with pytest.raises(ValueError, match="unexpected source filename"):
        source_filename(entry | {"path": r"C:\tmp\private\other.csv"})


def test_synthetic_fixture_keeps_unknown_truth_and_partition_groups_separate():
    cases = load_annotations(FIXTURE)
    validate_annotations(cases)
    assert {case["truth"] for case in cases} == {"unknown"}
    assert len({case["archetype"] for case in cases}) == 8


def test_circular_positive_label_is_refused():
    case = {
        "id": "leak",
        "provenance": "source_observed",
        "partition": "calibration",
        "address_group": "a",
        "chain_group": "c",
        "target_origin": "evaluated_dpe_candidate",
        "truth": "positive",
    }
    with pytest.raises(ValueError, match="circular positive"):
        validate_annotations([case])


@pytest.mark.parametrize("group_key", ["address_group", "chain_group"])
def test_group_cannot_cross_calibration_and_control(group_key):
    base = {
        "provenance": "independent_ground_truth",
        "target_origin": "independent_observation",
        "truth": "unknown",
        "address_group": "a1",
        "chain_group": "c1",
    }
    cases = [
        base | {"id": "a", "partition": "calibration"},
        base | {"id": "b", "partition": "control"},
    ]
    cases[1][group_key] = cases[0][group_key]
    cases[1]["chain_group" if group_key == "address_group" else "address_group"] += (
        "-other"
    )
    with pytest.raises(ValueError, match=group_key):
        validate_annotations(cases)


def test_metrics_are_distinct_and_null_without_denominator():
    metrics = build_evaluation_metrics([])
    assert metrics == {
        "precision": {"numerator": 0, "denominator": 0, "value": None},
        "consultation": {"numerator": 0, "denominator": 0, "value": None},
        "attribution": {"numerator": 0, "denominator": 0, "value": None},
        "abstention": {"numerator": 0, "denominator": 0, "value": None},
        "indeterminate": {"numerator": 0, "denominator": 0, "value": None},
    }


def test_manifest_rejects_wrong_pin_incomplete_pair_and_duplicate(tmp_path):
    source = tmp_path / "dpe03existant-44.csv"
    source.write_text("type_batiment\nMaison\n", encoding="utf-8")
    manifest = {
        "import_id": "wrong",
        "files": [
            {
                "path": str(source),
                "department": "44",
                "source_slug": "dpe03existant",
                "rows": 1,
                "sha256": "x",
                "columns": ["type_batiment"],
            }
        ],
    }
    with pytest.raises(ValueError, match="incomplete"):
        validate_manifest(manifest, tmp_path, "expected")
    manifest["import_id"] = "expected"
    manifest["files"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        validate_manifest(manifest, tmp_path, MANIFEST_PIN)


def test_fixture_contains_no_real_identifiers():
    raw = FIXTURE.read_text(encoding="utf-8").lower()
    assert "numero_dpe" not in raw
    assert "adresse_ban" not in raw
    assert "complement_adresse" not in raw
    summary = summarize_annotations(load_annotations(FIXTURE))
    assert set(summary) == {
        "cases",
        "provenance",
        "partition",
        "truth",
        "metrics",
        "metrics_by_provenance_partition",
        "evaluation_status",
    }


def _mini_manifest(tmp_path):
    columns = [
        "numero_dpe",
        "type_batiment",
        "surface_habitable_logement",
        "numero_etage_appartement",
        "complement_adresse_logement",
        "identifiant_ban",
        "id_rnb",
        "etiquette_dpe",
        "etiquette_ges",
        "type_energie_principale_chauffage",
    ]
    files = []
    for source in ("dpe02neuf", "dpe03existant"):
        for department in ("44", "85"):
            path = tmp_path / f"{source}-{department}.csv"
            path.write_text(",".join(columns) + "\n", encoding="utf-8")
            files.append(
                {
                    "path": str(path),
                    "source_slug": source,
                    "department": department,
                    "rows": 0,
                    "columns": columns,
                    "sha256": sha256(path),
                }
            )
    manifest = tmp_path / "import-manifest.json"
    manifest.write_text(json.dumps({"files": files}), encoding="utf-8")
    return manifest, files


def test_real_mini_csv_hashes_rows_headers_and_pin(tmp_path):
    manifest, _ = _mini_manifest(tmp_path)

    def pinned_hash(path):
        return MANIFEST_PIN if path == manifest else sha256(path)

    report = measure_sources(manifest, hash_fn=pinned_hash)
    assert report["integrity"]["rows"] == 0
    with pytest.raises(ValueError, match="manifest hash"):
        measure_sources(manifest)


def test_duplicate_number_bad_row_count_and_header_are_rejected(tmp_path):
    manifest, files = _mini_manifest(tmp_path)
    header = ",".join(files[0]["columns"])
    row = "same,Maison,80.0,,,,,,,"
    for entry in files[:2]:
        path = Path(entry["path"])
        path.write_text(f"{header}\n{row}\n", encoding="utf-8")
        entry["rows"] = 1
        entry["sha256"] = sha256(path)
    manifest.write_text(json.dumps({"files": files}), encoding="utf-8")

    def pinned(path):
        return MANIFEST_PIN if path == manifest else sha256(path)

    with pytest.raises(ValueError, match="duplicate numero_dpe"):
        measure_sources(manifest, hash_fn=pinned)
    files[0]["rows"] = 2
    manifest.write_text(json.dumps({"files": files}), encoding="utf-8")
    with pytest.raises(ValueError, match="row count"):
        measure_sources(manifest, hash_fn=pinned)
    files[0]["columns"] = ["wrong"]
    manifest.write_text(json.dumps({"files": files}), encoding="utf-8")
    with pytest.raises(ValueError, match="headers"):
        measure_sources(manifest, hash_fn=pinned)


def test_source_mutation_during_measurement_is_rejected(tmp_path, monkeypatch):
    import scripts.calibrate_dpe_matching as calibration

    manifest, files = _mini_manifest(tmp_path)
    original = calibration._read_csv

    def mutate_after_read(path, entry, all_dpe):
        result = original(path, entry, all_dpe)
        if path == Path(files[0]["path"]):
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return result

    monkeypatch.setattr(calibration, "_read_csv", mutate_after_read)

    def pinned_hash(path):
        if path == manifest:
            return MANIFEST_PIN
        return sha256(path)

    with pytest.raises(ValueError, match="mutated during read"):
        measure_sources(manifest, hash_fn=pinned_hash)


def test_closed_aggregate_normalizes_surface_and_drops_private_canary(tmp_path):
    manifest, files = _mini_manifest(tmp_path)
    entry = files[0]
    path = Path(entry["path"])
    header = ",".join(entry["columns"])
    rows = [
        "one,Maison,80.0,1,PRIVATE_CANARY,ban-x,,A,A,Bois",
        "two,Maison,80,1,PRIVATE_CANARY,ban-x,,B,A,Bois",
    ]
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    entry["rows"] = 2
    entry["sha256"] = sha256(path)
    manifest.write_text(json.dumps({"files": files}), encoding="utf-8")

    def pinned(path):
        return MANIFEST_PIN if path == manifest else sha256(path)

    report = measure_sources(manifest, hash_fn=pinned)
    stratum = report["strata"]["dpe02neuf-44"]
    assert stratum["availability_by_type"]["house"]["surface"]["present"] == 2
    assert (
        stratum["ban_groups"]["complete_same_surface_type_floor_collision_groups"] == 1
    )
    assert stratum["ban_groups"]["conflicting_class_or_heating_groups"] == 1
    assert "PRIVATE_CANARY" not in json.dumps(report)


@pytest.mark.parametrize("raw_type", ["", " "])
def test_missing_source_type_is_an_incomplete_discriminant(tmp_path, raw_type):
    report = _measure_two_colliding_rows(tmp_path, raw_type, "80", "80.0")
    groups = report["strata"]["dpe02neuf-44"]["ban_groups"]
    assert groups["complete_same_surface_type_floor_collision_groups"] == 0
    assert groups["incomplete_discriminant_collision_groups"] == 1
    assert groups["conflicting_class_or_heating_groups"] == 0


@pytest.mark.parametrize("surface", ["NaN", "Infinity", "0", "-1", "free text"])
def test_invalid_surface_is_an_incomplete_discriminant(tmp_path, surface):
    report = _measure_two_colliding_rows(tmp_path, "Maison", surface, surface)
    groups = report["strata"]["dpe02neuf-44"]["ban_groups"]
    assert groups["complete_same_surface_type_floor_collision_groups"] == 0
    assert groups["incomplete_discriminant_collision_groups"] == 1
    assert groups["conflicting_class_or_heating_groups"] == 0


def test_known_free_type_remains_a_complete_discriminant(tmp_path):
    report = _measure_two_colliding_rows(tmp_path, "Immeuble", "80", "80.0")
    groups = report["strata"]["dpe02neuf-44"]["ban_groups"]
    assert groups["complete_same_surface_type_floor_collision_groups"] == 1
    assert groups["incomplete_discriminant_collision_groups"] == 0
    assert groups["conflicting_class_or_heating_groups"] == 1


def test_free_text_containing_maison_is_not_stratified_as_house(tmp_path):
    report = _measure_two_colliding_rows(tmp_path, "Pas une maison", "80", "80")
    types = report["strata"]["dpe02neuf-44"]["types"]
    assert types["other_or_unknown"] == 2
    assert "house" not in types


def _measure_two_colliding_rows(tmp_path, raw_type, first_surface, second_surface):
    manifest, files = _mini_manifest(tmp_path)
    entry = files[0]
    path = Path(entry["path"])
    header = ",".join(entry["columns"])
    rows = [
        f"one,{raw_type},{first_surface},1,,ban-x,,A,A,Bois",
        f"two,{raw_type},{second_surface},1,,ban-x,,B,A,Bois",
    ]
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    entry["rows"] = 2
    entry["sha256"] = sha256(path)
    manifest.write_text(json.dumps({"files": files}), encoding="utf-8")

    def pinned(path):
        return MANIFEST_PIN if path == manifest else sha256(path)

    return measure_sources(manifest, hash_fn=pinned)


def test_ground_truth_requires_independent_target_and_decision_vocabulary():
    base = {
        "id": "truth",
        "provenance": "independent_ground_truth",
        "partition": "control",
        "address_group": "a",
        "chain_group": "c",
        "truth": "positive",
        "target_origin": "source_observation",
    }
    with pytest.raises(ValueError, match="independent target_origin"):
        validate_annotations([base])
    with pytest.raises(ValueError, match="invalid decision"):
        validate_annotations(
            [base | {"target_origin": "independent_observation", "decision": "maybe"}]
        )
