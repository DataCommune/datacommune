"""Independent CSV/Decimal oracle for the exact frozen LOVAC generation."""

import csv
import gzip
import hashlib
import io
import json
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

FIXTURE = Path(__file__).parents[1] / "fixtures/l09_lovac"
FIELDS = (
    "code_commune",
    "annee",
    "nb_logements_prives",
    "nb_logements_vacants",
    "nb_vacants_moins_2ans",
    "nb_vacants_plus_2ans",
    "taux_vacance",
    "taux_vacance_moins_2ans",
    "taux_vacance_plus_2ans",
)


def frozen_bytes():
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    packed = (FIXTURE / "official-communes26.csv.gz").read_bytes()
    assert len(packed) == manifest["gzip_bytes"]
    assert hashlib.sha256(packed).hexdigest() == manifest["gzip_sha256"]
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
        raw = stream.read(manifest["raw_bytes"] + 1)
    assert len(raw) == manifest["raw_bytes"] == 7628201
    assert hashlib.sha256(raw).hexdigest() == manifest["raw_sha256"]
    return raw, manifest


def expected_rows(raw):
    rows = list(csv.DictReader(io.StringIO(raw.decode("latin-1")), delimiter=";"))
    assert len(rows) == 34912
    assert len({row["CODGEO_26"] for row in rows}) == len(rows)
    assert sum(row["ff_pp_total_25"] == "s" for row in rows) == 18321

    def number(value):
        return None if value == "s" else int(value)

    result = {}
    for row in rows:
        total, vacant, long = (
            number(row[key])
            for key in ("ff_pp_total_25", "pp_vacant_26", "pp_vacant_plus_2ans_26")
        )
        short = None if vacant is None or long is None else vacant - long
        values = [row["CODGEO_26"], 2026, total, vacant, short, long]
        for numerator in (vacant, short, long):
            values.append(  # noqa: PERF401
                None
                if total is None or total <= 0 or numerator is None or numerator < 0
                else (Decimal(numerator) * 100 / total).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                )
            )
        result[row["CODGEO_26"]] = tuple(values)
    return result
