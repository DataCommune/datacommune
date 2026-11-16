"""Validate an explicitly pinned local qualification; never write to a database.

This command neither creates a review nor signs a publication decision. The
context and manifest hashes must come from a separately trusted caller.
"""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Never


class RedactedParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError("invalid_arguments")


def main() -> int:
    try:
        parser = RedactedParser(description=__doc__)
        parser.add_argument("--manifest", required=True, type=Path)
        parser.add_argument("--manifest-sha256", required=True)
        parser.add_argument("--context", required=True, type=Path)
        parser.add_argument("--context-sha256", required=True)
        parser.add_argument("--evidence-root", required=True, type=Path)
        args = parser.parse_args()
        # Keep runtime imports inside the redaction boundary.
        from app.commune.indicator_manifest import (
            MAX_MANIFEST_BYTES,
            QualificationContext,
            parse_document,
            read_bounded,
            validate_manifest,
        )

        context_raw = read_bounded(
            args.context, root=args.evidence_root, limit=MAX_MANIFEST_BYTES
        )
        if hashlib.sha256(context_raw).hexdigest() != args.context_sha256:
            raise ValueError("context_hash")
        context = QualificationContext.model_validate(parse_document(context_raw))
        raw = read_bounded(
            args.manifest, root=args.evidence_root, limit=MAX_MANIFEST_BYTES
        )
        result = validate_manifest(
            raw,
            expected_manifest_sha256=args.manifest_sha256,
            context=context,
            evidence_root=args.evidence_root,
        )
        counts = {
            state: sum(d.status == state for d in result.indicators.values())
            for state in ("unknown", "pending", "no_go", "qualified")
        }
    except Exception:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "status": "refused",
                    "code": "manifest_validation_failed",
                    "database_written": False,
                }
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "validated",
                "scope": result.scope,
                "indicator_states": counts,
                "database_written": False,
                "publication_executed": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
