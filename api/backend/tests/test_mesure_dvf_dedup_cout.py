"""The D-02 cost measurement script (plan 04.4-01, task 3), offline.

The ``sql`` variant B must be the served aggregation, the ``national`` mode
must run on a small synthetic archive (D-31: ``SYN-`` identifiers), and the
script carries no SQL write statement nor any non-stdlib import outside the
``sql`` mode.
"""

import ast
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.etl_dvf import reconstruction
from scripts import mesure_dvf_dedup_cout as mesure

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "mesure_dvf_dedup_cout.py"
QUALIFICATION = BACKEND / "app" / "etl_dvf" / "qualification.py"
STDLIB = set(sys.stdlib_module_names)


def test_variant_b_is_the_served_aggregation():
    assert mesure.AGGREGATION_B_SQL == reconstruction._AGGREGATION_SQL


def test_variant_avant_counts_locals_per_line_and_a_in_sql():
    assert "count(*) FILTER (WHERE code_type_local = '1')" in (
        mesure.AGGREGATION_AVANT_SQL
    )
    assert "max(n) AS m" in mesure.AGGREGATION_A_SQL
    # Variant V in A as in B: the lines are grouped by parcel AND culture.
    assert "id_parcelle, code_nature_culture" in mesure.AGGREGATION_A_SQL
    assert set(mesure.VARIANTES_SQL) == {"AVANT", "A", "B"}


def test_script_has_no_sql_write_statement():
    source = SCRIPT.read_text(encoding="utf-8")
    ecritures = re.findall(
        r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|COPY|GRANT|MERGE)\b",
        source,
    )
    assert ecritures == []


def test_script_is_concatenable_and_stdlib_outside_sql_mode():
    """No ``from __future__`` (it would break after qualification.py); module
    level imports from the standard library only, besides the guarded
    package import replaced by the concatenation."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert node.module != "__future__"
            assert node.module.split(".")[0] in STDLIB
        elif isinstance(node, ast.Import):
            assert {a.name.split(".")[0] for a in node.names} <= STDLIB


def _archive(racine: Path) -> str:
    """Three synthetic mutations: a house repeated on two parcels, a land, a
    house repeated on the S and J culture lines of its parcel (variant V)."""
    entetes = [
        "id_mutation",
        "numero_disposition",
        "nature_mutation",
        "valeur_fonciere",
        "code_commune",
        "id_parcelle",
        "code_type_local",
        "surface_reelle_bati",
        "nombre_pieces_principales",
        "surface_terrain",
        "code_nature_culture",
    ]
    lignes = [
        [
            "SYN-1",
            "000002",
            "Vente",
            "262000",
            "44999",
            "SYN-P1",
            "",
            "",
            "",
            "675",
            "S",
        ],
        [
            "SYN-1",
            "000002",
            "Vente",
            "262000",
            "44999",
            "SYN-P2",
            "1",
            "131",
            "5",
            "",
            "",
        ],
        [
            "SYN-1",
            "000002",
            "Vente",
            "262000",
            "44999",
            "SYN-P3",
            "1",
            "131",
            "5",
            "",
            "",
        ],
        [
            "SYN-2",
            "000001",
            "Vente",
            "50000",
            "44999",
            "SYN-P4",
            "",
            "",
            "",
            "500",
            "T",
        ],
        [
            "SYN-3",
            "000001",
            "Vente",
            "192000",
            "44999",
            "SYN-P5",
            "1",
            "96",
            "4",
            "412",
            "S",
        ],
        [
            "SYN-3",
            "000001",
            "Vente",
            "192000",
            "44999",
            "SYN-P5",
            "1",
            "96",
            "4",
            "230",
            "J",
        ],
    ]
    (racine / "raw").mkdir(parents=True)
    with open(racine / "raw" / "44.csv", "w", encoding="utf-8", newline="") as h:
        writer = csv.writer(h)
        writer.writerow(entetes)
        writer.writerows(lignes)
    (racine / "runs").mkdir()
    record = {"files": [{"relpath": "raw/2021/44.csv", "object": "raw/44.csv"}]}
    (racine / "runs" / "RUN-TEST.json").write_text(json.dumps(record), "utf-8")
    return "RUN-TEST"


def test_national_mode_runs_on_a_synthetic_archive(tmp_path):
    run = _archive(tmp_path)

    rapport = mesure.mode_national(str(tmp_path), run, cible=10)

    assert (rapport["lignes"], rapport["mutations"]) == (6, 3)
    temoin = rapport["mesures"]["temoin_naif"]
    b = rapport["mesures"]["B_compte_lignes"]
    assert temoin["passes"] == b["passes"] == 4
    assert b["mutations_evaluees"] == 12
    # The two repeated houses (by parcel, by culture): two locals each for the
    # naive count, one each with D-02 variant V.
    assert temoin["classes_une_passe"]["ensemble_residentiel"] == 2
    assert b["classes_une_passe"]["residentiel_maison"] == 2
    assert "ensemble_residentiel" not in b["classes_une_passe"]
    assert "SYN-" not in json.dumps(rapport)


def test_national_mode_restores_the_logging_of_its_caller(tmp_path):
    """Regression: a global ``logging.disable`` left behind silenced the log
    assertions of every later test of the suite (uvicorn redaction, scope)."""
    import logging

    run = _archive(tmp_path)
    avant = logging.root.manager.disable

    mesure.mode_national(str(tmp_path), run, cible=2)

    assert logging.root.manager.disable == avant


def test_national_mode_runs_concatenated_after_qualification(tmp_path):
    """The exact shape of the hypervisor run: qualification.py + script on stdin."""
    run = _archive(tmp_path / "archive")
    source = QUALIFICATION.read_text("utf-8") + "\n" + SCRIPT.read_text("utf-8")

    sortie = subprocess.run(
        [
            sys.executable,
            "-I",
            "-",
            "national",
            "--run",
            run,
            "--cible",
            "6",
            "--racine",
            str(tmp_path / "archive"),
        ],
        input=source,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
        check=True,
    )

    rapport = json.loads(sortie.stdout)
    assert rapport["mode"] == "national"
    assert rapport["mesures"]["B_compte_lignes"]["mutations_evaluees"] == 6
    assert rapport["mesures"]["B_compte_lignes"]["classes_une_passe"] == {
        "residentiel_maison": 2,
        "terrain_seul": 1,
    }
