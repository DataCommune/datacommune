"""Drift gate: generated OpenAPI vs the committed snapshot (OWD-03 C1).

Generalizes to the WHOLE public surface the principle first proven on the
public dataset pages (T5, a gate removed in 03-11 with the frontend catalogue
it parsed): contracts/openapi.v1.json is the committed
public contract, and any code change that alters ``app.openapi()`` fails here
until the snapshot is regenerated and the diff is reviewed. The gate does not
block the change — it blocks the change nobody noticed, turning an accidental
break into a conscious, dated decision (see api/docs/api-deprecation-policy.md).

To update the snapshot after an intended contract change (from api/backend):
    uv run python -m scripts.export_openapi
then review the diff of contracts/openapi.v1.json in the commit.

Runs in-process (no DB, no network). Canonicalization lives in ONE place —
scripts/export_openapi.py — so writer and checker can never disagree.
"""

import json

import pytest

from scripts.export_openapi import SNAPSHOT_PATH, canonical_spec

pytestmark = pytest.mark.unit

_REGEN_HINT = (
    "Regenerate with `uv run python -m scripts.export_openapi` (from api/backend) "
    "and review the diff of contracts/openapi.v1.json in your commit."
)


def test_snapshot_file_is_committed() -> None:
    assert SNAPSHOT_PATH.is_file(), (
        f"Missing committed OpenAPI snapshot: {SNAPSHOT_PATH}. {_REGEN_HINT}"
    )


def test_generated_spec_matches_committed_snapshot() -> None:
    generated = canonical_spec()
    committed = SNAPSHOT_PATH.read_text(encoding="utf-8")
    if generated == committed:
        return

    # Full 480KB diffs are unreadable in CI logs: summarize path-level drift
    # first, fall back to a generic message for edits inside existing paths.
    gen_paths = set(json.loads(generated)["paths"])
    com_paths = set(json.loads(committed)["paths"])
    added = sorted(gen_paths - com_paths)
    removed = sorted(com_paths - gen_paths)
    drift = (
        f"paths added={added or 'none'}, removed={removed or 'none'}"
        if added or removed
        else "no path added/removed — an operation, parameter, schema or "
        "description changed inside an existing path"
    )
    pytest.fail(
        f"Public OpenAPI contract drifted from the committed snapshot ({drift}). "
        f"{_REGEN_HINT}"
    )
