"""Compare real measurements directly with public commune indicator rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import TypeAdapter, ValidationError

from app.commune.indicator_schemas import (
    IndicateurAbsent,
    IndicateursDpe,
    IndicateursDvf,
)

UNQUALIFIED = {"85084", "85165", "85212"}
KINDS = ("residentiel_maison", "residentiel_appartement")
WINDOWS = {
    "previous": {"debut_inclus": "2024-01-01", "fin_exclue": "2025-01-01"},
    "current": {"debut_inclus": "2025-01-01", "fin_exclue": "2026-01-01"},
}
VALIDITIES = (
    "unknown",
    "expired",
    "not_expired_by_date",
    "inconsistent_dates",
    "future_establishment",
)
REPLACEMENTS = ("not_checked", "no_successor_known", "replaced", "ambiguous")
TREND = (
    "Évolution descriptive des médianes observées ; ce n'est pas un indice "
    "à qualité constante. La composition des ventes peut varier entre les "
    "fenêtres et les données peuvent être révisées."
)


class ComparisonRefused(ValueError):  # noqa: N818
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dec(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ComparisonRefused("invalid_decimal") from None
    if not result.is_finite():
        raise ComparisonRefused("invalid_decimal")
    return result


def absent(n: int) -> dict[str, str]:
    return (
        {"etat": "not_reported", "motif": "empty_sample"}
        if n == 0
        else {"etat": "insufficient_sample", "motif": "insufficient_sample"}
    )


def window(cell: dict[str, Any]) -> dict[str, Any]:
    n = cell.get("n")
    value = cell.get("quantiles", {}).get("p50", {}).get("value")
    if type(n) is not int or n < 0 or (n >= 5 and value is None):
        raise ComparisonRefused("invalid_dvf_cell")
    median = {"etat": "value", "valeur": dec(value)} if n >= 5 else absent(n)
    return {
        "fenetre": WINDOWS[cell["window"]],
        "motif_periode": None,
        "effectif": n,
        "mediane": median,
        "quantiles": {"etat": "not_qualified", "motif": "indicator_policy_unqualified"},
    }


def dvf(
    code: str, cells: dict[tuple[str, str], dict[str, Any]], generation: str
) -> dict[str, Any]:
    if code in UNQUALIFIED:
        return {"etat": "not_qualified", "motif": "territorial_attribution_unqualified"}
    out: dict[str, Any] = {"generation": generation}
    for kind in KINDS:
        before, after = window(cells[kind, "previous"]), window(cells[kind, "current"])
        bm, am = before["mediane"], after["mediane"]
        if bm["etat"] == am["etat"] == "value":
            variation = {
                "etat": "value",
                "valeur": Decimal(100) * (am["valeur"] / bm["valeur"] - 1),
            }
            qualification = TREND
        else:
            variation = bm if bm["etat"] != "value" else am
            qualification = None
        out[kind] = {
            "precedente": before,
            "courante": after,
            "variation_pct": variation,
            "qualification_tendance": qualification,
        }
    return out


def complete(source: dict[str, int], keys: tuple[str, ...]) -> dict[str, int]:
    return {k: source.get(k, 0) for k in keys}


def dpe(row: dict[str, Any], generation: str) -> dict[str, Any]:
    axes = row["axes"]
    raw = axes.get("type", {})
    known = row["n_known_classes"]
    types = {
        "maison": raw.get("maison", 0),
        "appartement": raw.get("appartement", 0),
        "autre": sum(
            v for k, v in raw.items() if k not in {"maison", "appartement", "inconnu"}
        ),
        "inconnu": raw.get("inconnu", 0),
    }
    fg = (
        {"etat": "value", "valeur": dec(row["fg_pct"])}
        if known
        else {"etat": "zero_denominator", "motif": "known_class_denominator_zero"}
    )
    population = {
        "total": row["n_documentary"],
        "classes_connues": known,
        "types_batiment": types,
        "validite": complete(axes.get("validity", {}), VALIDITIES),
        "remplacement": complete(axes.get("replacement", {}), REPLACEMENTS),
        "regimes": complete(
            axes.get("regimes_overlapping", {}), ("existant", "neuf", "inconnu")
        ),
        "source_inconnue": axes.get("source_state", {}).get("unknown", 0),
        "revision_non_verifiee": axes.get("revision", {}).get("unverified", 0),
    }
    result = {
        "etat": "value",
        "population": population,
        "distribution": {
            "classes": complete(row["classes"], tuple("ABCDEFG")),
            "inconnue": row["n_unknown_class"],
        },
        "modes": row["modes"],
        "mediane_ordinale": row["ordinal_median"],
        "part_fg_pct": fg,
        "date_etablissement_min": row["observed_establishment_min"],
        "date_etablissement_max": row["observed_establishment_max"],
        "dates_etablissement_inconnues": row["n_unknown_establishment"],
    }
    return {
        "generation": generation,
        "date_evaluation": "2026-09-09",
        "resultat": result,
    }


def build_expected(
    document: dict[str, Any],
    *,
    expected_codes: set[str],
    expected_dvf_generation: str,
    expected_dpe_generation: str,
) -> dict[str, dict[str, Any]]:
    source = document.get("source", {})
    expected_windows = {
        k: [v["debut_inclus"], v["fin_exclue"]] for k, v in WINDOWS.items()
    }
    if (
        document.get("schema_version") != "commune-measurements.v2"
        or document.get("windows") != expected_windows
        or source.get("cog_sha256")
        != "6f5a73cf350e68241abae99ec17cb4b7d81a6247a063feaae68228d5356ee2f0"
        or source.get("dpe_as_of") != "2026-09-09"
        or source.get("sources", {}).get("dpe", {}).get("generation")
        != expected_dpe_generation
    ):
        raise ComparisonRefused("measurement_context_mismatch")
    measured = source.get("sources", {}).get("dvf", {}).get("generation")
    if measured not in {None, expected_dvf_generation}:
        raise ComparisonRefused("dvf_generation_mismatch")
    cells = {code: {} for code in expected_codes}
    for cell in document.get("dvf", {}).get("matrix", []):
        code = cell.get("commune")
        key = (cell.get("type"), cell.get("window"))
        if (
            code not in cells
            or key in cells[code]
            or key[0] not in KINDS
            or key[1] not in WINDOWS
        ):
            raise ComparisonRefused("invalid_dvf_matrix")
        cells[code][key] = cell
    required = {(kind, period) for kind in KINDS for period in WINDOWS}
    if any(set(value) != required for value in cells.values()):
        raise ComparisonRefused("missing_dvf_cell")
    dpe_rows = {}
    for row in document.get("dpe", {}).get("matrix", []):
        code = row.get("commune")
        if code not in expected_codes or code in dpe_rows:
            raise ComparisonRefused("invalid_dpe_matrix")
        dpe_rows[code] = row
    if set(dpe_rows) != expected_codes:
        raise ComparisonRefused("missing_dpe_cell")
    return {
        code: {
            "code_insee": code,
            "indicateurs_dvf": dvf(code, cells[code], expected_dvf_generation),
            "indicateurs_dpe": dpe(dpe_rows[code], expected_dpe_generation),
        }
        for code in sorted(expected_codes)
    }


def data_view(row: dict[str, Any]) -> dict[str, Any]:
    try:
        dv = row["indicateurs_dvf"]
        dp = row["indicateurs_dpe"]
        if dv.get("etat") != "not_qualified":
            dv = {"generation": dv["generation"], **{k: dv[k] for k in KINDS}}
        if dp.get("etat") != "not_qualified":
            dp = {
                "generation": dp["generation"],
                "date_evaluation": dp["date_evaluation"],
                "resultat": dp["resultat"],
            }
        return {
            "code_insee": row["code_insee"],
            "indicateurs_dvf": dv,
            "indicateurs_dpe": dp,
        }
    except (KeyError, TypeError, AttributeError):
        raise ComparisonRefused("invalid_public_row") from None


def equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, Decimal):
        try:
            observed = dec(actual)
        except ComparisonRefused:
            return False
        return abs(expected - observed) <= Decimal("1e-9") * max(
            abs(expected), abs(observed), Decimal(1)
        )
    if isinstance(expected, dict) and isinstance(actual, dict):
        return set(expected) == set(actual) and all(
            equal(expected[k], actual[k]) for k in expected
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            equal(a, b) for a, b in zip(expected, actual, strict=True)
        )
    return expected == actual


def compare(expected: dict[str, dict[str, Any]], actual_rows: list[Any]) -> None:
    """Compare normalized views; the CLI schema-validates public rows first."""
    actual = {}
    for raw in actual_rows:
        if not isinstance(raw, dict):
            raise ComparisonRefused("invalid_public_row")
        row = data_view(raw)
        code = row["code_insee"]
        if code in actual:
            raise ComparisonRefused("duplicate_actual_commune")
        actual[code] = row
    if set(actual) != set(expected):
        raise ComparisonRefused("actual_commune_scope_mismatch")
    if any(not equal(expected[c], actual[c]) for c in expected):
        raise ComparisonRefused("indicator_value_mismatch")


def validate_public_rows(actual_rows: list[Any]) -> None:
    """Validate complete wire blocks before the arithmetic projection."""
    dvf_adapter = TypeAdapter(IndicateursDvf | IndicateurAbsent)
    dpe_adapter = TypeAdapter(IndicateursDpe | IndicateurAbsent)
    try:
        for row in actual_rows:
            if not isinstance(row, dict):
                raise TypeError
            dvf_adapter.validate_python(row["indicateurs_dvf"])
            dpe_adapter.validate_python(row["indicateurs_dpe"])
    except (KeyError, TypeError, ValidationError):
        raise ComparisonRefused("invalid_public_schema") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("measurements", "actual", "codes"):
        parser.add_argument(f"--{flag}", required=True, type=Path)
    for flag in ("measurements-sha256", "dvf-generation", "dpe-generation"):
        parser.add_argument(f"--{flag}", required=True)
    args = parser.parse_args(argv)
    try:
        if sha256_file(args.measurements) != args.measurements_sha256:
            raise ComparisonRefused("measurement_hash_mismatch")
        measures = json.loads(args.measurements.read_text(encoding="utf-8"))
        rows = json.loads(args.actual.read_text(encoding="utf-8"))
        codes = json.loads(args.codes.read_text(encoding="utf-8"))
        expected_codes = set(codes)
        if (
            not isinstance(rows, list)
            or not isinstance(codes, list)
            or len(codes) != 460
            or len(expected_codes) != 460
        ):
            raise ComparisonRefused("commune_scope_mismatch")
        expected = build_expected(
            measures,
            expected_codes=expected_codes,
            expected_dvf_generation=args.dvf_generation,
            expected_dpe_generation=args.dpe_generation,
        )
        validate_public_rows(rows)
        compare(expected, rows)
    except (ComparisonRefused, OSError, UnicodeError, json.JSONDecodeError):
        print(json.dumps({"status": "refused", "raw_values_emitted": False}))
        return 2
    print(
        json.dumps(
            {
                "status": "matched",
                "communes_compared": 460,
                "dvf_cells_compared": 1840,
                "dpe_cells_compared": 460,
                "territories_unqualified": sorted(UNQUALIFIED),
                "measurements_sha256": args.measurements_sha256,
                "raw_values_emitted": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
