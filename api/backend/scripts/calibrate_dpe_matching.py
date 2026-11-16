"""Offline, privacy-preserving calibration measurements for frozen DPE CSV files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

MANIFEST_PIN = "75f2172565e064348d2cfd898c0cb70be0b04740a699c1f03a035a841839f685"
PROVENANCES = {"synthetic", "source_observed", "independent_ground_truth"}
PARTITIONS = {"calibration", "control"}
TRUTHS = {"positive", "negative", "unknown"}
DECISIONS = {"attributed", "abstained", "not_evaluated"}
TARGET_ORIGINS = {
    "synthetic_invariant",
    "source_observation",
    "independent_observation",
}
REQUIRED_PAIRS = {
    (source, department)
    for source in ("dpe02neuf", "dpe03existant")
    for department in ("44", "85")
}
AVAILABILITY = {
    "type": "type_batiment",
    "surface": "surface_habitable_logement",
    "floor": "numero_etage_appartement",
    "complement": "complement_adresse_logement",
    "ban": "identifiant_ban",
    "rnb": "id_rnb",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def load_annotations(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def source_filename(entry: dict[str, Any]) -> str:
    raw_path = str(entry.get("path", ""))
    filename = (
        PureWindowsPath(raw_path).name
        if "\\" in raw_path
        else PurePosixPath(raw_path).name
    )
    expected = f"{entry.get('source_slug')}-{entry.get('department')}.csv"
    if filename != expected:
        raise ValueError("unexpected source filename")
    return filename


def validate_annotations(cases: Iterable[dict[str, Any]]) -> None:
    seen: dict[str, dict[str, str]] = {"address_group": {}, "chain_group": {}}
    for case in cases:
        if (
            case.get("provenance") not in PROVENANCES
            or case.get("partition") not in PARTITIONS
            or case.get("truth") not in TRUTHS
        ):
            raise ValueError("invalid annotation vocabulary")
        if (
            case.get("truth") == "positive"
            and case.get("target_origin") == "evaluated_dpe_candidate"
        ):
            raise ValueError("circular positive label")
        if case.get("target_origin") not in TARGET_ORIGINS:
            raise ValueError("invalid target_origin")
        if case.get("decision", "not_evaluated") not in DECISIONS:
            raise ValueError("invalid decision")
        if (
            case["provenance"] == "independent_ground_truth"
            and case["target_origin"] != "independent_observation"
        ):
            raise ValueError("ground truth requires independent target_origin")
        for group_key in seen:
            group = case.get(group_key)
            if not group:
                raise ValueError(f"missing {group_key}")
            previous = seen[group_key].setdefault(group, case["partition"])
            if previous != case["partition"]:
                raise ValueError(f"{group_key} crosses partitions")


def _metric(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": safe_ratio(numerator, denominator),
    }


def build_evaluation_metrics(
    cases: Iterable[dict[str, Any]],
) -> dict[str, dict[str, int | float | None]]:
    rows = list(cases)
    independent = [
        row
        for row in rows
        if row.get("provenance") == "independent_ground_truth"
        and row.get("truth") != "unknown"
    ]
    promoted = [row for row in independent if row.get("decision") == "attributed"]
    correct = sum(row.get("truth") == "positive" for row in promoted)
    consultation_rows = [row for row in rows if isinstance(row.get("consulted"), bool)]
    decision_rows = [
        row for row in rows if row.get("decision") in {"attributed", "abstained"}
    ]
    consulted = sum(row["consulted"] for row in consultation_rows)
    attributed = sum(row.get("decision") == "attributed" for row in decision_rows)
    abstained = sum(row.get("decision") == "abstained" for row in decision_rows)
    unknown = sum(row.get("truth") == "unknown" for row in rows)
    return {
        "precision": _metric(correct, len(promoted)),
        "consultation": _metric(consulted, len(consultation_rows)),
        "attribution": _metric(attributed, len(decision_rows)),
        "abstention": _metric(abstained, len(decision_rows)),
        "indeterminate": _metric(unknown, len(rows)),
    }


def summarize_annotations(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Return only closed aggregate fields; private grouping keys never escape."""
    validate_annotations(cases)
    segments = {}
    for provenance in sorted(PROVENANCES):
        for partition in sorted(PARTITIONS):
            segment = [
                row
                for row in cases
                if row["provenance"] == provenance and row["partition"] == partition
            ]
            if segment:
                segments[f"{provenance}/{partition}"] = build_evaluation_metrics(
                    segment
                )
    return {
        "cases": len(cases),
        "provenance": dict(sorted(Counter(row["provenance"] for row in cases).items())),
        "partition": dict(sorted(Counter(row["partition"] for row in cases).items())),
        "truth": dict(sorted(Counter(row["truth"] for row in cases).items())),
        "evaluation_status": {
            "evaluated": sum(
                row.get("decision") in {"attributed", "abstained"} for row in cases
            ),
            "not_evaluated": sum(
                row.get("decision", "not_evaluated") == "not_evaluated" for row in cases
            ),
        },
        "metrics": build_evaluation_metrics(cases),
        "metrics_by_provenance_partition": segments,
    }


def validate_manifest(
    manifest: dict[str, Any], directory: Path, expected_pin: str
) -> list[dict[str, Any]]:
    entries = manifest.get("files", [])
    pairs = [
        (entry.get("source_slug"), str(entry.get("department"))) for entry in entries
    ]
    if len(pairs) != len(set(pairs)):
        raise ValueError("duplicate source/department pair")
    if set(pairs) != REQUIRED_PAIRS:
        raise ValueError("incomplete source/department pairs")
    for entry in entries:
        path = directory / source_filename(entry)
        if not path.is_file():
            raise ValueError("missing source file")
        if not entry.get("columns") or int(entry.get("rows", -1)) < 0:
            raise ValueError("invalid manifest counts or headers")
    if expected_pin != MANIFEST_PIN:
        raise ValueError("manifest pin is not approved")
    return entries


def _present(value: str | None) -> bool:
    return bool(value and value.strip())


def _type(value: str | None) -> str:
    normalized = (value or "").strip().casefold()
    if normalized in {"maison", "maison individuelle"}:
        return "house"
    if normalized in {"appartement", "appartement individuel"}:
        return "apartment"
    return "other_or_unknown"


def _surface(value: str | None) -> str | None:
    if not _present(value):
        return None
    try:
        parsed = Decimal(value.strip())
        if not parsed.is_finite() or parsed <= 0:
            return None
        return format(parsed.normalize(), "f")
    except InvalidOperation:
        return None


def measure_sources(
    manifest_path: Path,
    expected_pin: str = MANIFEST_PIN,
    hash_fn: Callable[[Path], str] = sha256,
) -> dict[str, Any]:
    if hash_fn(manifest_path) != expected_pin:
        raise ValueError("manifest hash mismatch before read")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    directory = manifest_path.parent
    entries = validate_manifest(manifest, directory, expected_pin)
    strata: dict[str, Any] = {}
    source_hashes: dict[str, str] = {}
    all_dpe: set[str] = set()
    total_rows = 0
    for entry in entries:
        path = directory / source_filename(entry)
        before = hash_fn(path)
        if before != entry["sha256"]:
            raise ValueError("source hash mismatch before read")
        counters = _read_csv(path, entry, all_dpe)
        after = hash_fn(path)
        if after != before:
            raise ValueError("source mutated during read")
        if counters["rows"] != int(entry["rows"]):
            raise ValueError("source row count mismatch")
        total_rows += counters["rows"]
        stratum = f"{entry['source_slug']}-{entry['department']}"
        strata[stratum] = counters
        source_hashes[stratum] = before
    if hash_fn(manifest_path) != expected_pin:
        raise ValueError("manifest mutated during read")
    return {
        "schema_version": 1,
        "measurement_kind": "source_observed",
        "snapshot_date": "2026-09-05",
        "scope": {
            "departments": ["44", "85"],
            "regimes": ["dpe02neuf", "dpe03existant"],
        },
        "integrity": {
            "manifest_sha256": expected_pin,
            "source_hashes_verified_before_and_after": True,
            "rows": total_rows,
            "distinct_dpe": len(all_dpe),
            "source_sha256": source_hashes,
        },
        "published_import_reference": {
            "distinct_numbers": 540717,
            "provenance": "phase_58_2_local_import_measurement",
            "part_of_source_csv_measurement": False,
        },
        "strata": strata,
        "identity_truth": "unknown",
        "evaluation_metrics": build_evaluation_metrics([]),
        "limitations": [
            "Documentary discrimination is not dwelling identity.",
            "The snapshot does not describe current production.",
            "No independent ground truth was available.",
            "A floor value on a house is not evidence that it is an apartment.",
            "Before/after hashes cannot prevent adversarial rewrite-and-restore "
            "between checks.",
            "Collision groups are scoped to one regime/department stratum.",
        ],
    }


def _read_csv(path: Path, entry: dict[str, Any], all_dpe: set[str]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    counts_by_type: defaultdict[str, Counter[str]] = defaultdict(Counter)
    types: Counter[str] = Counter()
    ban_groups: defaultdict[str, Counter[tuple[str, str, str]]] = defaultdict(Counter)
    class_heat: defaultdict[tuple[str, str, str, str], set[tuple[str, str, str]]] = (
        defaultdict(set)
    )
    rows = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != entry["columns"]:
            raise ValueError("source headers mismatch")
        for row in reader:
            rows += 1
            number = row["numero_dpe"].strip()
            if not number or number in all_dpe:
                raise ValueError("missing or duplicate numero_dpe")
            all_dpe.add(number)
            dwelling_type = _type(row.get("type_batiment"))
            source_type = (row.get("type_batiment") or "").strip().casefold() or None
            types[dwelling_type] += 1
            for label, column in AVAILABILITY.items():
                present = _present(row.get(column))
                counts[label] += present
                counts_by_type[dwelling_type][label] += present
            ban = row.get("identifiant_ban", "").strip()
            discriminants = (
                source_type,
                _surface(row.get("surface_habitable_logement")),
                row.get("numero_etage_appartement", "").strip(),
            )
            if ban:
                ban_groups[ban][discriminants] += 1
                class_heat[(ban, *discriminants)].add(
                    (
                        row.get("etiquette_dpe", "").strip(),
                        row.get("etiquette_ges", "").strip(),
                        row.get("type_energie_principale_chauffage", "").strip(),
                    )
                )
    complete_collisions = sum(
        1
        for groups in ban_groups.values()
        for key, value in groups.items()
        if value > 1 and all(key)
    )
    incomplete_collisions = sum(
        1
        for groups in ban_groups.values()
        for key, value in groups.items()
        if value > 1 and not all(key)
    )
    conflicting = sum(
        any(
            len({value[index] for value in values if value[index]}) > 1
            for index in range(3)
        )
        for key, values in class_heat.items()
        if all(key[1:]) and len(values) > 1
    )
    return {
        "rows": rows,
        "types": dict(sorted(types.items())),
        "availability": {
            key: {
                "present": counts[key],
                "denominator": rows,
                "ratio": safe_ratio(counts[key], rows),
            }
            for key in AVAILABILITY
        },
        "availability_by_type": {
            dwelling_type: {
                key: {
                    "present": counts_by_type[dwelling_type][key],
                    "denominator": type_rows,
                    "ratio": safe_ratio(counts_by_type[dwelling_type][key], type_rows),
                }
                for key in AVAILABILITY
            }
            for dwelling_type, type_rows in sorted(types.items())
        },
        "ban_groups": {
            "groups": len(ban_groups),
            "complete_same_surface_type_floor_collision_groups": complete_collisions,
            "incomplete_discriminant_collision_groups": incomplete_collisions,
            "conflicting_class_or_heating_groups": conflicting,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path, required=True, help="Private frozen import manifest"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Public aggregate JSON output"
    )
    parser.add_argument("--manifest-sha256", default=MANIFEST_PIN)
    parser.add_argument(
        "--annotations", type=Path, help="Optional synthetic or ground-truth corpus"
    )
    args = parser.parse_args()
    report = measure_sources(args.manifest, args.manifest_sha256)
    if args.annotations:
        report["annotation_control"] = summarize_annotations(
            load_annotations(args.annotations)
        )
    report["generated_at"] = datetime.now(UTC).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
