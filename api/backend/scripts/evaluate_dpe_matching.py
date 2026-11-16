"""Offline evaluation of DPE matching against a closed private corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dpe.matching import compare_inventory
from app.dpe.matching_schemas import DpeMatchTarget
from app.dpe.schemas import DpeInventoryPage

_CLOSED = ConfigDict(extra="forbid", hide_input_in_errors=True)
_ERROR = "evaluation failed"
_CODE_FILES = (
    Path(__file__),
    Path(__file__).parents[1] / "app/dpe/matching.py",
    Path(__file__).parents[1] / "app/dpe/matching_schemas.py",
    Path(__file__).parents[1] / "app/dpe/schemas.py",
    Path(__file__).parents[1] / "uv.lock",
)


class CorpusError(ValueError):
    """Constant public error for invalid private material."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CorpusError(_ERROR)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PrivateProof(BaseModel):
    model_config = _CLOSED

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin: Literal[
        "synthetic_observation", "source_observation", "independent_observation"
    ]
    independently_reviewed: bool = Field(strict=True)


class EvaluationCase(BaseModel):
    model_config = _CLOSED

    case_id: str = Field(min_length=1)
    department: Literal["44", "85"]
    property_type: Literal["maison", "appartement"]
    partition: Literal["calibration", "control"]
    address_group: str = Field(min_length=1)
    # Optional (v1-compatible): None means the building is unknown, so the
    # independence of dwellings in that building is not demonstrated.
    building_group: str | None = Field(default=None, min_length=1)
    chain_groups: list[str] = Field(min_length=1)
    target: DpeMatchTarget
    inventory: DpeInventoryPage
    truth: Literal["document", "none", "unknown"]
    true_document_id: str | None = Field(default=None, min_length=1)
    target_proof: str
    truth_proof: str | None = None
    document_id_by_ref: dict[str, str]

    @model_validator(mode="after")
    def validate_oracle(self) -> EvaluationCase:
        if (self.truth == "document") != (self.true_document_id is not None):
            raise ValueError("document truth identity mismatch")
        if (self.truth != "unknown") != (self.truth_proof is not None):
            raise ValueError("truth proof mismatch")
        refs = [item.document_ref for item in self.inventory.items]
        if set(self.document_id_by_ref) != set(refs):
            raise ValueError("document identity mapping is incomplete")
        values = list(self.document_id_by_ref.values())
        if not all(values) or len(values) != len(set(values)):
            raise ValueError("document identity mapping collides")
        if len(self.chain_groups) != len(set(self.chain_groups)) or not all(
            self.chain_groups
        ):
            raise ValueError("chain groups must be unique")
        if self.target.type_bien not in {None, self.property_type}:
            raise ValueError("target type and stratum disagree")
        return self


class PrivateCorpus(BaseModel):
    model_config = _CLOSED

    corpus_version: Literal[1]
    corpus_id: str = Field(min_length=1)
    provenance: Literal["synthetic", "source_observed", "independent_ground_truth"]
    proofs: dict[str, PrivateProof]
    cases: list[EvaluationCase] = Field(min_length=1)


def _validate_proofs(corpus: PrivateCorpus, directory: Path) -> None:  # noqa: C901
    root = directory.resolve()
    for proof in corpus.proofs.values():
        raw_path = Path(proof.path)
        if raw_path.is_absolute() or ".." in raw_path.parts:
            raise ValueError("proof path is invalid")
        path = (root / proof.path).resolve()
        if path == root or root not in path.parents or not path.is_file():
            raise ValueError("proof path is invalid")
        if sha256(path) != proof.sha256:
            raise ValueError("proof integrity is invalid")
        # The flag records a traced human review; only an independent
        # observation needs one, other origins declare it honestly.
        if proof.origin == "independent_observation" and not (
            proof.independently_reviewed
        ):
            raise ValueError("independent proof is not reviewed")
    for case in corpus.cases:
        target = corpus.proofs.get(case.target_proof)
        truth = corpus.proofs.get(case.truth_proof) if case.truth_proof else None
        if target is None or (case.truth != "unknown" and truth is None):
            raise ValueError("case proof is missing")
        if (
            corpus.provenance == "independent_ground_truth"
            and case.truth != "unknown"
            and truth.origin != "independent_observation"
        ):
            raise ValueError("ground truth is not independent")
        if (
            corpus.provenance == "independent_ground_truth"
            and case.truth != "unknown"
            and target.origin != "independent_observation"
        ):
            raise ValueError("ground truth target is not independent")
        if corpus.provenance == "source_observed" and case.truth != "unknown":
            raise ValueError("source observations cannot establish identity truth")


def _validate_groups(cases: list[EvaluationCase]) -> int:
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        if parent[node] != node:
            parent[node] = find(parent[node])
        return parent[node]

    def union(left: str, right: str) -> None:
        parent[find(left)] = find(right)

    for case in cases:
        nodes = [f"address:{case.address_group}"] + [
            f"chain:{group}" for group in case.chain_groups
        ]
        if case.building_group is not None:
            nodes.append(f"building:{case.building_group}")
        document_ids = set(case.document_id_by_ref.values())
        if case.true_document_id is not None:
            document_ids.add(case.true_document_id)
        nodes.extend(f"document:{document_id}" for document_id in document_ids)
        for node in nodes[1:]:
            union(nodes[0], node)
    partitions: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        partitions[find(f"address:{case.address_group}")].add(case.partition)
    if any(len(values) != 1 for values in partitions.values()):
        raise ValueError("linked groups cross partitions")
    return len(partitions)


def load_corpus(path: Path, expected_sha256: str) -> PrivateCorpus:
    try:
        resolved = path.resolve(strict=True)
        if len(expected_sha256) != 64 or sha256(resolved) != expected_sha256:
            raise ValueError("corpus digest mismatch")
        corpus = PrivateCorpus.model_validate_json(resolved.read_text(encoding="utf-8"))
        ids = [case.case_id for case in corpus.cases]
        refs = [
            item.document_ref for case in corpus.cases for item in case.inventory.items
        ]
        if len(ids) != len(set(ids)) or len(refs) != len(set(refs)):
            raise ValueError("duplicate private identity")
        _validate_proofs(corpus, resolved.parent)
        _validate_groups(corpus.cases)
        return corpus
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CorpusError("invalid private corpus") from exc


def _metric(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _is_complete(page: DpeInventoryPage) -> bool:
    return (
        page.state in {"ready", "absent"}
        and page.read_state == "complete"
        and not page.has_more
        and page.total is not None
        and page.total == len(page.items)
    )


def _hashes() -> dict[str, str]:
    return {path.name: sha256(path) for path in _CODE_FILES}


def _segment_summary(rows: list[tuple[str, str, str | None]]) -> dict[str, object]:
    matrix = {
        truth: {status: 0 for status in ("unassigned", "absent", "error")}
        for truth in ("document", "none", "unknown")
    }
    retrieval = Counter(
        document_truth=0, eligible=0, found=0, not_found_complete=0, indeterminate=0
    )
    results: Counter[str] = Counter()
    for truth, status, recovery in rows:
        matrix[truth][status] += 1
        results[status] += 1
        if truth == "document":
            retrieval["document_truth"] += 1
            retrieval[recovery] += 1
            if recovery != "indeterminate":
                retrieval["eligible"] += 1
    count = len(rows)
    return {
        "cases": count,
        "matrix": matrix,
        "retrieval": dict(retrieval),
        "metrics": {
            "precision": _metric(0, 0),
            "consultation": _metric(0, 0),
            "attribution": _metric(0, count),
            "abstention": _metric(results["unassigned"], count),
            "error": _metric(results["error"], count),
            "indeterminate": _metric(sum(row[0] == "unknown" for row in rows), count),
        },
    }


def evaluate_corpus(path: Path, expected_sha256: str) -> dict[str, object]:
    corpus_hash_before = sha256(path)
    corpus = load_corpus(path, expected_sha256)
    proof_hashes_before = {
        name: sha256((path.parent / proof.path).resolve())
        for name, proof in corpus.proofs.items()
    }
    code_before = _hashes()
    matrix = {
        truth: {status: 0 for status in ("unassigned", "absent", "error")}
        for truth in ("document", "none", "unknown")
    }
    result_counts: Counter[str] = Counter()
    retrieval = Counter(
        document_truth=0, eligible=0, found=0, not_found_complete=0, indeterminate=0
    )
    strata: dict[str, Counter[str]] = defaultdict(Counter)
    evaluated_rows: list[tuple[EvaluationCase, str, str | None]] = []
    rule_versions: set[str] = set()
    for case in corpus.cases:
        result = compare_inventory(case.inventory, case.target)
        status = result.attribution.status
        if status not in {"unassigned", "absent", "error"}:
            raise CorpusError(_ERROR)
        if result.attribution.diagnostic is not None or result.attribution.candidates:
            raise CorpusError(_ERROR)
        rule_versions.add(result.rule_version)
        matrix[case.truth][status] += 1
        result_counts[status] += 1
        strata[f"{case.department}/{case.property_type}"][status] += 1
        recovery = None
        if case.truth == "document":
            retrieval["document_truth"] += 1
            mapped = set(case.document_id_by_ref.values())
            if case.true_document_id in mapped:
                recovery = "found"
                retrieval["found"] += 1
                retrieval["eligible"] += 1
            elif _is_complete(case.inventory):
                recovery = "not_found_complete"
                retrieval["not_found_complete"] += 1
                retrieval["eligible"] += 1
            else:
                recovery = "indeterminate"
                retrieval["indeterminate"] += 1
        evaluated_rows.append((case, status, recovery))
    corpus_hash_after = sha256(path)
    proof_hashes_after = {
        name: sha256((path.parent / proof.path).resolve())
        for name, proof in corpus.proofs.items()
    }
    code_after = _hashes()
    expected_proofs = {name: proof.sha256 for name, proof in corpus.proofs.items()}
    if (
        corpus_hash_before != expected_sha256
        or corpus_hash_after != expected_sha256
        or proof_hashes_before != expected_proofs
        or proof_hashes_after != expected_proofs
        or code_before != code_after
    ):
        raise CorpusError(_ERROR)
    executed = len(corpus.cases)
    independent_groups = _validate_groups(corpus.cases)
    independent = sum(
        corpus.provenance == "independent_ground_truth" and case.truth != "unknown"
        for case in corpus.cases
    )
    segments: dict[str, list[tuple[str, str, str | None]]] = defaultdict(list)
    for case, status, recovery in evaluated_rows:
        row = (case.truth, status, recovery)
        segments[f"provenance/{corpus.provenance}"].append(row)
        segments[f"partition/{case.partition}"].append(row)
        segments[f"stratum/{case.department}/{case.property_type}"].append(row)
    return {
        "report_version": 1,
        "scope": corpus.provenance,
        "counts": {
            "executed": executed,
            "independent_truth": independent,
            "unknown_truth": sum(case.truth == "unknown" for case in corpus.cases),
            "building_group_unknown": sum(
                case.building_group is None for case in corpus.cases
            ),
            "independent_groups": independent_groups,
            "proofs": {
                "declared": len(corpus.proofs),
                "independently_reviewed": sum(
                    proof.independently_reviewed for proof in corpus.proofs.values()
                ),
            },
        },
        # The engine never attributes a dwelling, so identity precision cannot
        # be measured, even when an independent truth exists.
        "identity": {
            "status": "non_evaluable",
            "reasons": (["no_independent_truth"] if independent == 0 else [])
            + ["no_positive_attribution_policy"],
        },
        "by_provenance": {corpus.provenance: executed},
        "by_partition": dict(
            sorted(Counter(case.partition for case in corpus.cases).items())
        ),
        "by_stratum": {
            key: dict(sorted(value.items())) for key, value in sorted(strata.items())
        },
        "segments": {
            name: _segment_summary(rows) for name, rows in sorted(segments.items())
        },
        "matrix": matrix,
        "retrieval": dict(retrieval),
        "metrics": {
            "precision": _metric(0, 0),
            "consultation": _metric(0, 0),
            "attribution": _metric(0, executed),
            "abstention": _metric(result_counts["unassigned"], executed),
            "error": _metric(result_counts["error"], executed),
            "indeterminate": _metric(
                matrix["unknown"]["unassigned"]
                + matrix["unknown"]["absent"]
                + matrix["unknown"]["error"],
                executed,
            ),
        },
        "rule_version": sorted(rule_versions),
        "integrity": {
            "corpus_sha256": corpus_hash_after,
            "evaluator_sha256": code_after[Path(__file__).name],
            "engine_sha256": code_after["matching.py"],
            "schemas_sha256": code_after["matching_schemas.py"],
            "inventory_schemas_sha256": code_after["schemas.py"],
            "lock_sha256": code_after["uv.lock"],
        },
        "limits": [
            "no_document_consultation",
            "no_positive_attribution_policy",
            "synthetic_cases_do_not_measure_field_precision",
        ],
    }


def _safe_output(corpus_path: Path, corpus: PrivateCorpus, output: Path) -> Path:
    private = {corpus_path.resolve()}
    private.update(
        (corpus_path.parent / proof.path).resolve() for proof in corpus.proofs.values()
    )
    resolved = output.resolve()
    same_private_file = resolved.exists() and any(
        private_path.exists() and os.path.samefile(resolved, private_path)
        for private_path in private
    )
    if resolved in private or same_private_file:
        raise CorpusError(_ERROR)
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="Evaluate DPE matching offline")
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--corpus-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        corpus = load_corpus(args.corpus, args.corpus_sha256)
        output = _safe_output(args.corpus, corpus, args.output)
        report = evaluate_corpus(args.corpus, args.corpus_sha256)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=output.parent,
            prefix=".dpe-evaluation-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
            temporary = Path(stream.name)
        temporary.replace(output)
        return 0
    except (CorpusError, OSError):
        print(_ERROR, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
