"""Export the public OpenAPI contract to contracts/openapi.v1.json (OWD-03 C1).

Single writer for the committed snapshot, same governance as
sync_semantic_contract.py: the snapshot is never edited by hand, this script
is the only way to update it. The drift gate is tests/test_openapi_snapshot.py:
any code change that alters the public contract turns CI red until the snapshot
is regenerated here and the diff is reviewed — the change stays possible, only
the UNNOTICED change is blocked.

Generation is in-process via ``app.openapi()`` (no DB, no network — the app
factory never touches the database at import time). Canonical form: sorted
keys, 2-space indent, UTF-8, LF, trailing newline — so drift always shows as a
minimal reviewable diff.

Usage (from `api/backend/`, module form so `app` resolves — same convention as
`python -m scripts.sync_semantic_contract`):
    uv run python -m scripts.export_openapi
"""

import json
from pathlib import Path

from app.main import app

# scripts/... -> parents[1] is api/backend, parents[3] is the repo root.
# Relative to the repo, never to the CWD (same convention as the tests).
_REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT_PATH = _REPO_ROOT / "contracts" / "openapi.v1.json"


def canonical_spec() -> str:
    """Render the live OpenAPI spec in its canonical committed form."""
    return (
        json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )


def main() -> None:
    content = canonical_spec()
    SNAPSHOT_PATH.write_text(content, encoding="utf-8", newline="\n")
    n_paths = len(app.openapi()["paths"])
    print(f"Snapshot OpenAPI ecrit: {SNAPSHOT_PATH}")
    print(f"  {n_paths} chemins, {len(content)} caracteres")


if __name__ == "__main__":
    main()
