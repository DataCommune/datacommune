"""Independent official CSV/Decimal oracle; no application configuration/helpers."""

import csv
import gzip
import hashlib
import io
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

FIXTURE = Path(__file__).parents[1] / "fixtures/frozen_loyers_2025"


def frozen_files(folder):
    folder.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        packed = (FIXTURE / item["gzip_name"]).read_bytes()
        assert len(packed) == item["gzip_bytes"]
        assert hashlib.sha256(packed).hexdigest() == item["gzip_sha256"]
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
            raw = stream.read(item["bytes"] + 1)
        assert len(raw) == item["bytes"]
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        with (folder / (item["housing_type"] + ".csv")).open("xb") as stream:
            stream.write(raw)
    return manifest


FIELDS = [
    "code_insee",
    "nom_commune",
    "code_departement",
    "code_epci",
    "code_region",
    "type_habitat",
    "loyer_m2",
    "loyer_m2_bas",
    "loyer_m2_haut",
    "r2",
    "nb_observations",
    "annee",
    "id_zone",
    "type_prediction",
    "nb_observations_commune",
    "nb_observations_maille",
    "r2_ajuste",
    "trimestre_reference",
    "charges_comprises",
    "meuble",
    "surface_reference_m2",
    "surface_piece_reference_m2",
]


def expected(folder):
    """Raw official CSV oracle, independent from pipeline config and transform."""
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    reference = {
        "appartement": (52, "22.2"),
        "maison": (92, "22.4"),
        "1-2_pieces": (37, "22.9"),
        "3+_pieces": (72, "21.3"),
    }
    rows, witnesses = [], {}
    for resource in manifest["files"]:
        kind = resource["housing_type"]
        source = folder / f"{kind}.csv"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == resource["sha256"]
        with source.open(encoding="latin-1", newline="") as stream:
            source_rows = list(csv.DictReader(stream, delimiter=";"))
        assert len(source_rows) == len({row["INSEE_C"] for row in source_rows}) == 34900
        for raw in source_rows:

            def number(key, source_row=raw):
                value = source_row[key]
                return None if value in {"", "NA"} else Decimal(value.replace(",", "."))

            rent = number("loypredm2")
            assert rent is not None and rent != 0 and rent.is_finite()
            scope = "epci" if raw["TYPPRED"] == "EPCI" else raw["TYPPRED"]
            if scope not in witnesses:
                witnesses[scope] = (raw["INSEE_C"], kind)
            values = {
                "code_insee": raw["INSEE_C"],
                "nom_commune": raw["LIBGEO"],
                "code_departement": raw["DEP"],
                "code_epci": raw["EPCI"],
                "code_region": raw["REG"],
                "type_habitat": kind,
                "loyer_m2": rent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                "loyer_m2_bas": number("lwr.IPm2").quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                "loyer_m2_haut": number("upr.IPm2").quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),
                "r2": number("R2_adj").quantize(
                    Decimal("0.001"), rounding=ROUND_HALF_UP
                ),
                "nb_observations": int(raw["nbobs_com"]),
                "annee": 2025,
                "id_zone": raw["id_zone"],
                "type_prediction": scope,
                "nb_observations_commune": int(raw["nbobs_com"]),
                "nb_observations_maille": int(raw["nbobs_mail"]),
                "r2_ajuste": number("R2_adj"),
                "trimestre_reference": 3,
                "charges_comprises": True,
                "meuble": False,
                "surface_reference_m2": Decimal(reference[kind][0]),
                "surface_piece_reference_m2": Decimal(reference[kind][1]),
            }
            rows.append(tuple(values[field] for field in FIELDS))
    assert len(rows) == 139600
    assert sum(row[FIELDS.index("type_prediction")] == "epci" for row in rows) == 671
    return manifest, rows, witnesses
