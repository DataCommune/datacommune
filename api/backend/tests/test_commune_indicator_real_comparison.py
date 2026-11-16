"""Independent comparison contract for real public commune indicator rows."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[1] / "scripts" / "compare_commune_indicator_measurements.py"
)
DVF = "11111111-1111-4111-8111-111111111111"
DPE = "291f9c13-ed87-4d00-9dfa-2aa7613a20f7"


def module():
    spec = importlib.util.spec_from_file_location("comparison", SCRIPT)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def measurements():
    dvf = []
    dpe = []
    for code in ("44001", "85084", "85165", "85212"):
        for kind in ("residentiel_maison", "residentiel_appartement"):
            for period, n, median in (
                ("previous", 4, "1000.123456789"),
                ("current", 5, "1100.987654321"),
            ):
                dvf.append(
                    {
                        "commune": code,
                        "type": kind,
                        "window": period,
                        "n": n,
                        "quantiles": {
                            q: {"value": median} for q in ("p10", "p50", "p90")
                        },
                    }
                )
        dpe.append(
            {
                "commune": code,
                "n_documentary": 10,
                "classes": {"A": 1, "B": 1, "C": 1, "D": 2, "E": 1, "F": 2, "G": 2},
                "n_unknown_class": 0,
                "n_known_classes": 10,
                "modes": ["D", "F", "G"],
                "ordinal_median": "E",
                "fg_pct": "40",
                "axes": {
                    "validity": {"not_expired_by_date": 9, "expired": 1},
                    "replacement": {"no_successor_known": 9, "replaced": 1},
                    "source_state": {"present": 10},
                    "regimes_overlapping": {"existant": 8, "neuf": 2},
                    "type": {"maison": 7, "appartement": 3},
                    "revision": {"unverified": 10},
                },
                "n_unknown_establishment": 0,
                "observed_establishment_min": "2021-01-01",
                "observed_establishment_max": "2026-08-01",
            }
        )
    return {
        "schema_version": "commune-measurements.v2",
        "source": {
            "cog_sha256": (
                "6f5a73cf350e68241abae99ec17cb4b7d81a6247a063feaae68228d5356ee2f0"
            ),
            "sources": {"dvf": {"generation": None}, "dpe": {"generation": DPE}},
            "dpe_as_of": "2026-09-09",
        },
        "windows": {
            "previous": ["2024-01-01", "2025-01-01"],
            "current": ["2025-01-01", "2026-01-01"],
        },
        "dvf": {"matrix": dvf},
        "dpe": {"matrix": dpe},
    }


def expected(mod):
    return mod.build_expected(
        measurements(),
        expected_codes={"44001", "85084", "85165", "85212"},
        expected_dvf_generation=DVF,
        expected_dpe_generation=DPE,
    )


def test_real_public_shape_states_territory_and_trend():
    mod = module()
    result = expected(mod)
    block = result["44001"]["indicateurs_dvf"]["residentiel_maison"]
    assert block["precedente"]["mediane"]["etat"] == "insufficient_sample"
    assert block["courante"]["quantiles"]["etat"] == "not_qualified"
    assert (
        result["85084"]["indicateurs_dvf"]["motif"]
        == "territorial_attribution_unqualified"
    )
    assert result["85084"]["indicateurs_dpe"]["generation"] == DPE
    data = measurements()
    for cell in data["dvf"]["matrix"]:
        cell["n"] = 5
    value = mod.build_expected(
        data,
        expected_codes={"44001", "85084", "85165", "85212"},
        expected_dvf_generation=DVF,
        expected_dpe_generation=DPE,
    )["44001"]["indicateurs_dvf"]["residentiel_maison"]
    assert value["variation_pct"]["etat"] == "value"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v["44001"]["indicateurs_dpe"]["resultat"]["population"].update(
            total=11
        ),
        lambda v: v["44001"]["indicateurs_dpe"].update(
            generation="22222222-2222-4222-8222-222222222222"
        ),
        lambda v: v.pop("44001"),
        lambda v: v.update({"99999": copy.deepcopy(v["44001"])}),
    ],
)
def test_refuses_value_generation_missing_and_extra_mutants(mutate):
    mod = module()
    wanted = expected(mod)
    actual = copy.deepcopy(wanted)
    mutate(actual)
    with pytest.raises(mod.ComparisonRefused):
        mod.compare(wanted, list(actual.values()))


def test_duplicate_float_tolerance_and_material_difference():
    mod = module()
    wanted = expected(mod)
    rows = json.loads(json.dumps(list(wanted.values()), default=float))
    mod.compare(wanted, rows)
    with pytest.raises(mod.ComparisonRefused):
        mod.compare(wanted, [*rows, copy.deepcopy(rows[0])])
    rows[0]["indicateurs_dpe"]["resultat"]["part_fg_pct"]["valeur"] += 0.01
    with pytest.raises(mod.ComparisonRefused):
        mod.compare(wanted, rows)


def test_zero_is_not_reported_and_nonpublic_margins_are_absent():
    mod = module()
    data = measurements()
    data["dvf"]["matrix"][0]["n"] = 0
    result = mod.build_expected(
        data,
        expected_codes={"44001", "85084", "85165", "85212"},
        expected_dvf_generation=DVF,
        expected_dpe_generation=DPE,
    )
    median = result["44001"]["indicateurs_dvf"]["residentiel_maison"]["precedente"][
        "mediane"
    ]
    assert median == {"etat": "not_reported", "motif": "empty_sample"}
    public = result["44001"]["indicateurs_dpe"]["resultat"]
    assert "construction" not in public and "restricted" not in public


@pytest.mark.parametrize("count", [4, 459, 461])
def test_cli_refuses_every_scope_other_than_460(tmp_path, capsys, count):
    mod = module()
    source = tmp_path / "measurements.json"
    actual = tmp_path / "actual.json"
    codes = tmp_path / "codes.json"
    source.write_text("{}")
    actual.write_text("[]")
    codes.write_text(json.dumps([f"44{i:03d}" for i in range(count)]))
    result = mod.main(
        [
            "--measurements",
            str(source),
            "--measurements-sha256",
            mod.sha256_file(source),
            "--actual",
            str(actual),
            "--codes",
            str(codes),
            "--dvf-generation",
            DVF,
            "--dpe-generation",
            DPE,
        ]
    )
    assert result == 2 and json.loads(capsys.readouterr().out)["status"] == "refused"


def test_complete_public_schema_is_required_before_comparison():
    mod = module()
    with pytest.raises(mod.ComparisonRefused, match="schema"):
        mod.validate_public_rows(
            [
                {
                    "code_insee": "44001",
                    "indicateurs_dvf": {"generation": DVF},
                    "indicateurs_dpe": {"generation": DPE},
                }
            ]
        )


def test_script_starts_without_pythonpath():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=SCRIPT.parent,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0


def test_cli_preserves_utf8_json_under_non_utf8_locale(tmp_path, monkeypatch, capsys):
    mod = module()
    source = tmp_path / "measurements.json"
    actual = tmp_path / "actual.json"
    codes = tmp_path / "codes.json"
    source.write_text('{"libellé":"évolution"}', encoding="utf-8")
    actual.write_text('[{"qualité_tendance":"élevée"}]', encoding="utf-8")
    codes.write_text(json.dumps([f"44{i:03d}" for i in range(460)]), encoding="utf-8")
    original = Path.read_text

    def locale_sensitive_read(path, encoding=None, *args, **kwargs):
        selected = "cp1252" if encoding is None else encoding
        return original(path, *args, encoding=selected, **kwargs)

    def build_expected(measures, **kwargs):
        if measures != {"libellé": "évolution"}:
            raise mod.ComparisonRefused("mojibake_measurements")
        assert len(kwargs["expected_codes"]) == 460
        return {"oracle": "préservé"}

    def validate_public_rows(rows):
        if rows != [{"qualité_tendance": "élevée"}]:
            raise mod.ComparisonRefused("mojibake_actual")

    def compare(expected_rows, actual_rows):
        assert expected_rows == {"oracle": "préservé"}
        assert actual_rows == [{"qualité_tendance": "élevée"}]

    monkeypatch.setattr(Path, "read_text", locale_sensitive_read)
    monkeypatch.setattr(mod, "build_expected", build_expected)
    monkeypatch.setattr(mod, "validate_public_rows", validate_public_rows)
    monkeypatch.setattr(mod, "compare", compare)
    assert (
        mod.main(
            [
                "--measurements",
                str(source),
                "--measurements-sha256",
                mod.sha256_file(source),
                "--actual",
                str(actual),
                "--codes",
                str(codes),
                "--dvf-generation",
                DVF,
                "--dpe-generation",
                DPE,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "matched"
