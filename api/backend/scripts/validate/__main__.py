"""CLI: python -m scripts.validate --dataset dpe -n 10"""

from __future__ import annotations

import argparse
import sys

from .report import print_reports
from .validators import VALIDATORS


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valide les donnees BDD contre les APIs gouvernementales"
    )
    parser.add_argument(
        "--dataset",
        choices=[*VALIDATORS.keys(), "all"],
        default="all",
        help="Dataset a valider (defaut: all)",
    )
    parser.add_argument(
        "-n",
        "--sample",
        type=int,
        default=10,
        help="Nombre d'echantillons (defaut: 10)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=0.5,
        help="Delai entre appels API en secondes (defaut: 0.5)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Sortie JSON"
    )
    args = parser.parse_args()

    names = list(VALIDATORS.keys()) if args.dataset == "all" else [args.dataset]

    reports = []
    for name in names:
        validator_cls = VALIDATORS[name]
        validator = validator_cls()
        print(f"Validation {name} ({args.sample} echantillons)...")
        report = validator.run(n=args.sample, rate_limit=args.rate_limit)
        reports.append(report)

    print_reports(reports, as_json=args.as_json)

    has_issues = any(r.divergences > 0 or r.api_errors > 0 for r in reports)
    return 1 if has_issues else 0


if __name__ == "__main__":
    sys.exit(main())
