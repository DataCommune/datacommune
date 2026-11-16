"""Administrative entry point for guarded DVF stock adoption."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.commune.indicator_adoption import (
    AdoptionRequest,
    adopt_existing_dvf_stock,
    measure_stock,
    runtime_source_sha256,
)
from app.config import settings
from app.scope.model import parse_departements


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser()
    command.add_argument("--scope", choices=("preproduction_ct277",))
    # Received departements of the lot, e.g. "44,85" for the existing adoption.
    command.add_argument("--departements", type=parse_departements)
    command.add_argument("--expected-candidate-sha")
    command.add_argument("--expected-runtime-sha256")
    command.add_argument("--expected-schema-revision")
    command.add_argument("--expected-system-identifier")
    command.add_argument("--expected-database")
    command.add_argument("--expected-server-address")
    command.add_argument("--expected-server-port", type=int)
    command.add_argument("--backup-receipt", type=Path)
    command.add_argument("--expected-backup-sha256")
    command.add_argument("--expected-source-rows", type=int)
    command.add_argument("--expected-mutation-count", type=int)
    command.add_argument("--expected-all-years-projection-sha256")
    command.add_argument("--expected-projection-count", type=int)
    command.add_argument("--expected-projection-sha256")
    command.add_argument("--expected-raw-sha256")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--print-runtime-sha256", action="store_true")
    return command


def main() -> int:
    args = parser().parse_args()
    if args.print_runtime_sha256:
        print(runtime_source_sha256())
        return 0
    # app.config resolves Docker's DATABASE_URL_FILE convention at import time.
    # Require an explicit operator configuration instead of the development default.
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL or a readable DATABASE_URL_FILE is required")
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    if args.departements is None:
        raise SystemExit("Missing adoption arguments: departements")
    departments = tuple(args.departements)
    if args.dry_run:
        print(json.dumps(measure_stock(dsn, departments).__dict__, sort_keys=True))
        return 0
    values = vars(args).copy()
    values.pop("dry_run")
    values.pop("print_runtime_sha256")
    values.pop("departements")
    missing = sorted(key for key, value in values.items() if value is None)
    if missing:
        raise SystemExit("Missing adoption arguments: " + ", ".join(missing))
    request = AdoptionRequest(**values, departments=departments)
    print(json.dumps(adopt_existing_dvf_stock(dsn, request), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
