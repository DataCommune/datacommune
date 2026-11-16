#!/usr/bin/env python3
"""Deterministic sync of the semantic contract registry (Phase 43, D-03/D-06).

Copies `contracts/fiche-fields.v1.json` (the single source of authority)
byte-for-byte into every packaged location. Manual edits to the packaged
copies are forbidden — this script is the only writer; both backend
(`app/common/semantic_contract.py`) and frontend read their local packaged
copy, never the repo-root source, so builds/images stay self-contained.

Usage (from `api/backend/`, module form so `app` resolves — same convention as
`python -m scripts.provision_internal_account`):
    uv run python -m scripts.sync_semantic_contract           # sync (write)
    uv run python -m scripts.sync_semantic_contract --check   # verify only

Fails (non-zero exit) if:
    - the source registry fails D-03/D-04/D-06 validation — an incomplete or
      malformed field contract is never propagated ("champ non couvert"),
    - `--check` finds a packaged copy that has drifted from the source
      (manual edit or a sync that was never re-run),
    - a post-write hash comparison finds a copy did not land byte-identical
      to the source (T-43-05 defense-in-depth against partial writes).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from app.common.semantic_contract import validate_registry

# scripts/sync_semantic_contract.py -> parents[1] is api/backend, parents[2] is api/,
# parents[3] is the repo root (holds contracts/ and frontend/).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SOURCE = _REPO_ROOT / "contracts" / "fiche-fields.v1.json"
_TARGETS = (
    _REPO_ROOT / "api" / "backend" / "app" / "common" / "fiche-fields.v1.json",
    _REPO_ROOT / "frontend" / "src" / "lib" / "data" / "fiche-fields.v1.json",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_and_validate_source() -> bytes:
    """Read the source registry and refuse to propagate an invalid one."""
    if not _SOURCE.exists():
        raise SystemExit(f"source registry not found: {_SOURCE}")

    raw = _SOURCE.read_bytes()
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"source registry is not valid JSON: {exc}") from exc

    fields = document.get("fields")
    if not isinstance(fields, list) or not fields:
        raise SystemExit("source registry has no non-empty 'fields' array")

    errors = validate_registry(fields)
    if errors:
        raise SystemExit(
            "source registry failed semantic contract validation, refusing to "
            "sync (a field is not fully covered by D-03):\n  " + "\n  ".join(errors)
        )
    return raw


def check() -> int:
    """Verify packaged copies are byte-identical to the source. No writes."""
    source_bytes = _load_and_validate_source()
    source_hash = _sha256(source_bytes)

    drifted: list[Path] = []
    for target in _TARGETS:
        if not target.exists() or _sha256(target.read_bytes()) != source_hash:
            drifted.append(target)  # noqa: PERF401

    if drifted:
        for target in drifted:
            print(f"DRIFT: {target} does not match {_SOURCE}", file=sys.stderr)
        return 1

    short_hash = source_hash[:12]
    print(f"OK: {len(_TARGETS)} packaged copies match {_SOURCE} (sha256={short_hash})")
    return 0


def sync() -> int:
    """Write byte-identical copies of the source into every packaged target."""
    source_bytes = _load_and_validate_source()
    source_hash = _sha256(source_bytes)

    for target in _TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source_bytes)
        written_hash = _sha256(target.read_bytes())
        if written_hash != source_hash:
            raise SystemExit(f"post-write hash mismatch for {target} — sync aborted")
        print(f"synced: {target} (sha256={written_hash[:12]})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify packaged copies without writing (exit 1 on drift)",
    )
    args = parser.parse_args()
    return check() if args.check else sync()


if __name__ == "__main__":
    raise SystemExit(main())
