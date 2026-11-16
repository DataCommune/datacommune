"""L08 independent oracle: frozen source bytes, stdlib arithmetic, closed fields.

No application/ETL/schema import is permitted in this module. The committed
manifest is a reviewable expectation, never generated from an API response.
"""

import csv
import hashlib
import json
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "api/backend/tests/fixtures"
AUDIT = ROOT / "docs/audit-stabilisation-2026-09-08"
MANIFEST = FIXTURES / "l08_oracles/manifest.json"
KINDS = ("appartement", "maison", "1-2_pieces", "3+_pieces")
# Independently checked against both the frozen CRLF bytes normalized to LF
# and the existing Git blobs (387ae9fc); historical download hashes stay intact.
PREFIX_SHA256_LF = {
    "appartement": "ea19bb9f0172e1469b706a5385f36a36d4264d692434f90362faac6b48c7fb8b",
    "maison": "45499586f68b51a05a51ad772f65312d8394280ef5a1f33e31ad9c77177963c4",
    "1-2_pieces": "16a6547409c79aad27ea8aed983837e8bb1367a2af2a3efbf3805fbca93abcba",
    "3+_pieces": "d409d937f22c27f1cf719906ddf67be44ca29f7341e8a9ecffee8934a96bbb15",
}
SURFACES = dict(
    zip(KINDS, [(52, 22.2), (92, 22.4), (37, 22.9), (72, 21.3)], strict=True)
)
REVENUS_COLUMNS = {
    "nb_menages_fiscaux": "NBMEN21",
    "revenu_median": "Q221",
    "revenu_q1": "Q121",
    "revenu_q3": "Q321",
    "revenu_d1": "D121",
    "revenu_d9": "D921",
    "indice_gini": "GI21",
    "part_imposee": "PMIMP21",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path, *, lf=False):
    data = path.read_bytes()
    return hashlib.sha256(data.replace(b"\r\n", b"\n") if lf else data).hexdigest()


def rows(path, encoding="utf-8-sig"):
    with path.open(encoding=encoding, newline="") as stream:
        return list(csv.DictReader(stream, delimiter=";"))


def number(value, places=None):
    if value == "s":
        return None
    value = Decimal(value.replace(",", "."))
    if places is not None:
        value = value.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_EVEN)
    return int(value) if value == value.to_integral_value() else float(value)


def _fact(value, columns, unit, method="exact source value"):
    return {"value": value, "columns": columns, "unit": unit, "method": method}


def _case(dataset, key, sources, facts, commune=None):
    return {
        "id": dataset + ":" + ":".join(key.values()),
        "dataset": dataset,
        "key": key,
        "sources": sources,
        "fields": facts,
        "commune_fields": commune or {},
    }


def _source(path, **extra):
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "sha256_lf": sha(path, lf=True),
        **extra,
    }


def build_manifest():  # noqa: C901
    """Recompute expectations from fixed columns, never production constants."""
    sources, cases = {}, []
    guide = FIXTURES / "l08_oracles/loyers-guide-2025.pdf"
    guide_hash = "620db206f562be7696e7abe2074dc4c8cd53392ff29dda16aae498df2e28a224"
    assert sha(guide) == guide_hash
    sources["guide p1"] = {
        "path": str(guide.relative_to(ROOT)).replace("\\", "/"),
        "sha256": guide_hash,
        "version": "Carte des loyers 2025, guide figé COR06",
        "pages": [1, 3],
        "method": "Documentary reading recorded in COR06; byte integrity checked",
    }
    folder = FIXTURES / "cor05_revenus"
    provenance = read_json(folder / "manifest.json")
    for archive in provenance["archives"]:
        for member in archive["members"]:
            path = folder / member["name"]
            assert sha(path, lf=True) == member["fixture_sha256_lf"]
            sources[member["name"]] = _source(
                path,
                encoding="utf-8-sig",
                upstream_archive_sha256=archive["sha256"],
                upstream_member_sha256=member["original_sha256"],
                url=archive["url"],
            )
    declared_file, poverty_file = (
        "FILO2021_DEC_COM.csv",
        "FILO2021_DISP_PAUVRES_COM.csv",
    )
    poverty = {r["CODGEO"]: r for r in rows(folder / poverty_file)}
    for row in rows(folder / declared_file):
        code = row["CODGEO"]
        facts = {
            "annee": _fact(2021, ["DEC2021 / DISP_PAUVRES2021"], "year"),
        }
        for field, column in REVENUS_COLUMNS.items():
            unit = (
                "EUR/UC/year declared"
                if field.startswith("revenu_")
                else "households"
                if field == "nb_menages_fiscaux"
                else "ratio 0..1"
                if field == "indice_gini"
                else "percentage points"
            )
            facts[field] = _fact(
                number(row[column]),
                [declared_file + ":" + column],
                unit,
                "decimal source; secret s -> JSON null",
            )
        facts["taux_pauvrete"] = _fact(
            number(poverty[code]["TP6021"]),
            [poverty_file + ":TP6021"],
            "percentage points, disposable income",
            "join CODGEO; threshold 60% of metropolitan median",
        )
        commune = {
            field: field
            for field in (
                "revenu_median",
                "indice_gini",
                "part_imposee",
                "taux_pauvrete",
            )
        }
        commune["annee_revenus"] = "annee"
        cases.append(
            _case(
                "revenus",
                {"code_insee": code},
                [declared_file, poverty_file],
                facts,
                commune,
            )
        )

    folder = AUDIT / "lot-02/cor06-sources"
    original = read_json(FIXTURES / "loyers/official-2025.json")
    for provenance in read_json(folder / "manifest.json"):
        kind = provenance["type_habitat"]
        path = folder / provenance["local_file"]
        assert sha(path, lf=True) == PREFIX_SHA256_LF[kind]
        source_id = "loyers:" + kind
        sources[source_id] = _source(
            path,
            encoding="latin-1",
            upstream_prefix_sha256=provenance["sha256"],
            normalization="CRLF -> LF only; original download hash retained",
            url=provenance["resolved_url"],
        )
        extracted = rows(FIXTURES / "loyers" / (kind + ".csv"), "latin-1")
        witnesses = [s for s in original if s["type_habitat"] == kind]
        assert extracted == [s["values"] for s in witnesses]
        extracted_id = source_id + ":extracted"
        sources[extracted_id] = _source(
            FIXTURES / "loyers" / (kind + ".csv"), encoding="latin-1"
        )
        full_prefix = rows(path, "latin-1")
        for source in witnesses:
            row = full_prefix[source["source_line"] - 2]
            assert row == source["values"]
            assert row in extracted
            facts = {
                "annee": _fact(2025, ["guide p1"], "year"),
                "trimestre_reference": _fact(3, ["guide p1"], "quarter"),
                "charges_comprises": _fact(True, ["guide p1"], "boolean"),
                "meuble": _fact(False, ["guide p1"], "boolean"),
                "surface_reference_m2": _fact(SURFACES[kind][0], ["guide p1"], "m2"),
                "surface_piece_reference_m2": _fact(
                    SURFACES[kind][1], ["guide p1"], "m2/piece"
                ),
                "id_zone": _fact(row["id_zone"], ["id_zone"], "source estimation zone"),
                "type_prediction": _fact(
                    row["TYPPRED"], ["TYPPRED"], "prediction geography"
                ),
            }
            for field, column in {
                "loyer_m2": "loypredm2",
                "loyer_m2_bas": "lwr.IPm2",
                "loyer_m2_haut": "upr.IPm2",
            }.items():
                facts[field] = _fact(
                    number(row[column], 2),
                    [column],
                    "EUR/m2/month predicted charges included",
                    "decimal half-even to 2 places; "
                    "bounds are prediction 95%, not quartiles (guide p3)",
                )
            for field, column in {
                "nb_observations": "nbobs_com",
                "nb_observations_commune": "nbobs_com",
                "nb_observations_maille": "nbobs_mail",
            }.items():
                facts[field] = _fact(int(row[column]), [column], "observations")
            facts["r2"] = _fact(
                number(row["R2_adj"], 3),
                ["R2_adj"],
                "ratio",
                "legacy precision 3 places",
            )
            facts["r2_ajuste"] = _fact(
                number(row["R2_adj"]), ["R2_adj"], "adjusted R2", "full source decimal"
            )
            case = _case(
                "loyers",
                {"code_insee": row["INSEE_C"], "type_habitat": kind},
                [source_id, extracted_id, "guide p1"],
                facts,
            )
            case["source_line"] = source["source_line"]
            case["commune_object"] = "loyers_" + kind if kind in KINDS[:2] else None
            cases.append(case)

    folder = FIXTURES / "cor08_lovac"
    provenance = read_json(folder / "manifest.json")
    path = folder / "official-communes26.csv"
    assert sha(path) == provenance["fixture_sha256"]
    sources["lovac"] = _source(
        path,
        encoding="latin-1",
        upstream_sha256=provenance["raw_sha256"],
        url=provenance["url"],
    )
    for row in rows(path, "latin-1"):
        total, vacant, long = [
            number(row[c])
            for c in ("ff_pp_total_25", "pp_vacant_26", "pp_vacant_plus_2ans_26")
        ]
        short = None if vacant is None or long is None else vacant - long
        facts = {
            "annee": _fact(2026, ["CODGEO_26 / pp_vacant_26"], "numerator year"),
            "nb_logements_prives": _fact(
                total, ["ff_pp_total_25"], "private housing, denominator 2025"
            ),
        }
        for field, value, columns in [
            ("nb_logements_vacants", vacant, ["pp_vacant_26"]),
            (
                "nb_vacants_moins_2ans",
                short,
                ["pp_vacant_26", "pp_vacant_plus_2ans_26"],
            ),
            ("nb_vacants_plus_2ans", long, ["pp_vacant_plus_2ans_26"]),
        ]:
            facts[field] = _fact(
                value,
                columns,
                "private housing",
                "difference for short only if both counts known; secret s -> null",
            )
        for field, value, numerator in zip(
            ("taux_vacance", "taux_vacance_moins_2ans", "taux_vacance_plus_2ans"),
            (vacant, short, long),
            ("nb_logements_vacants", "nb_vacants_moins_2ans", "nb_vacants_plus_2ans"),
            strict=True,
        ):
            rate = (
                None
                if total is None or total <= 0 or value is None or value < 0
                else number(str(Decimal(value) * 100 / total), 2)
            )
            facts[field] = _fact(
                rate,
                [*facts[numerator]["columns"], "ff_pp_total_25"],
                "percentage points",
                "known nonnegative numerator / known positive private "
                "denominator *100; half-even 2 places",
            )
        cases.append(
            _case(
                "vacance",
                {"code_commune": row["CODGEO_26"]},
                ["lovac"],
                facts,
                {
                    "annee_vacance": "annee",
                    "taux_vacance": "taux_vacance",
                    "nb_vacants_plus_2ans": "nb_vacants_plus_2ans",
                },
            )
        )
    return {
        "version": 1,
        "sources": sources,
        "cases": cases,
        "guide_evidence": {
            "historical_sha256": guide_hash,
            "report": "docs/audit-stabilisation-2026-09-08/lot-02/cor06-result.md",
            "locally_rehashed": True,
        },
        "limits": [
            "22 selected public source rows, no national exhaustiveness",
            "guide p1/p3 interpretation reused from COR06; frozen PDF shipped in CI",
            "no frontend renderer, authentication, acquisition network "
            "or runner-wide gate certified",
            "legacy metadata and synthetic corruptions "
            "are not official source observations",
        ],
    }


def load_manifest():
    manifest = read_json(MANIFEST)
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest):
    assert manifest == build_manifest(), (
        "manifest differs from independently read frozen sources"
    )


def expected_row(case):
    return {
        **case["key"],
        **{field: fact["value"] for field, fact in case["fields"].items()},
    }


def assert_row(case, actual):
    """Check only named certified fields; extra fields are explicitly unclaimed."""
    for field, expected in expected_row(case).items():
        assert field in actual, f"{case['id']} missing {field}"
        value = actual[field]
        assert type(value) is type(expected) or (
            type(value) in (int, float, Decimal) and type(expected) in (int, float)
        ), f"{case['id']} {field} type"
        assert value == expected or (
            type(value) is Decimal and value == Decimal(str(expected))
        ), f"{case['id']} {field}: {value!r} != {expected!r}"


def mutations(case):
    """One wrong value per certified field, plus decimal unit conversions."""
    for field, value in expected_row(case).items():
        wrong = (
            0
            if value is None
            else None
            if type(value) in (int, float)
            else not value
            if type(value) is bool
            else value + "-WRONG"
        )
        yield field + ":wrong", field, wrong
    for field, fact in case["fields"].items():
        value = fact["value"]
        if type(value) in (int, float) and value != 0:
            yield field + ":shift", field, value + 1
            if fact["unit"].startswith("EUR") or "percentage" in fact["unit"]:
                yield field + ":unit", field, value / 100


if __name__ == "__main__":
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(build_manifest(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
