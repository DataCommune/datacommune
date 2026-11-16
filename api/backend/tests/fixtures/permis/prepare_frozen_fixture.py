"""Deterministically remove identities from the bounded COR-07 audit witness.

Run with the private CSV path as the sole argument. Never print source rows.
Only explicitly allowed date/count columns survive; categorical identifiers are
replaced by first-occurrence tokens, preserving equality and disagreement.
"""

import csv
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

ORIGINAL_SHA256 = "9bcda940fa5b9449932a00ecec5308822144d57547e6e679722c01d3833e4881"
PRESERVED = (
    "departement",
    "date_commencement",
    "date_decision",
    "annee",
    "nb_logements",
    "sdp_creee",
)
TOKENIZED = (
    "nom_dossier",
    "type_dossier",
    "n_commune",
    "nom_commune",
    "nature_construction",
    "designation",
)


def redact(source: Path) -> tuple[bytes, dict]:
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("Unexpected private source hash")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if len(rows) != 753:
        raise ValueError("Unexpected private source row count")
    tokens = {column: {} for column in TOKENIZED}
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output, fieldnames=[*PRESERVED, *TOKENIZED], lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        cleaned = {column: row[column] for column in PRESERVED}
        for column in TOKENIZED:
            value = row[column]
            mapping = tokens[column]
            if value not in mapping:
                mapping[value] = len(mapping) + 1
            number = mapping[value]
            cleaned[column] = (
                f"93{900 + number:03d}"
                if column == "n_commune"
                else f"COR07-{column}-{number:04d}"
            )
        writer.writerow(cleaned)
    public = output.getvalue().encode("utf-8")
    preserved_stream = json.dumps(
        [[row[column] for column in PRESERVED] for row in rows],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest = {
        "original_sha256": ORIGINAL_SHA256,
        "original_bytes": len(raw),
        "redacted_sha256": hashlib.sha256(public).hexdigest(),
        "redacted_bytes": len(public),
        "rows": len(rows),
        "distinct_dossiers": len(tokens["nom_dossier"]),
        "departments": dict(Counter(row["departement"] for row in rows)),
        "preserved_columns": list(PRESERVED),
        "preserved_values_sha256": hashlib.sha256(preserved_stream).hexdigest(),
        "tokenized_columns": list(TOKENIZED),
        "removed_columns": [
            column for column in rows[0] if column not in PRESERVED + TOKENIZED
        ],
        "method": (
            "v1: source order unchanged; first-occurrence tokens per column; "
            "UTF-8/LF; only allow-listed columns emitted"
        ),
        "expected_duplicate_groups": 1,
        "expected_differing_transformed_columns": ["surface_plancher", "destination"],
        # The duplicate dossier carries one row per destination (key
        # numero_dossier, destination since migration 109): both publish.
        "expected_outcome": "published",
        "raw_identities_versioned": False,
        "scope": (
            "Complete bounded audit witness, not national coverage; "
            "dates/counts preserved, all identities removed"
        ),
    }
    return public, manifest


if __name__ == "__main__":
    public, manifest = redact(Path(sys.argv[1]))
    directory = Path(__file__).parent
    (directory / "audit-generation-redacted.csv").write_bytes(public)
    (directory / "audit-generation-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: manifest[key]
                for key in ("rows", "distinct_dossiers", "redacted_sha256")
            }
        )
    )
