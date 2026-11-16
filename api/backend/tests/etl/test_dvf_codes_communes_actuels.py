"""KI-D7: a sale of a commune merged or recoded recently keeps its former code in
geo-dvf, so the fiche of the commune of today did not count it (Orée d'Anjou
49069 -> 49126, Pierrefitte-sur-Seine 93059 -> 93066 Saint-Denis, measured on
the work base on 2026-11-11). The import now recodes a former code to the one
commune of today it became, in the same departement; a split keeps its former
code. The table comes from the COG of the INSEE (``codes_communes_actuels.csv``).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from app.etl_dvf.transform import (
    CODES_COMMUNES_ACTUELS_PATH,
    codes_communes_actuels,
    transform_dvf_csv,
)

_HEADER = [
    "id_mutation",
    "date_mutation",
    "valeur_fonciere",
    "code_commune",
    "nom_commune",
    "longitude",
    "latitude",
]


def _transformer(tmp_path: Path, codes: list[str]) -> pd.DataFrame:
    raw = tmp_path / "raw"
    raw.mkdir()
    source = raw / "dvf-test.csv"
    with source.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_HEADER)
        for i, code in enumerate(codes):
            writer.writerow(
                [f"SYN-{i}", "2024-05-02", "100000", code, f"Commune {code}", "", ""]
            )
    return pd.read_csv(transform_dvf_csv(source), dtype=str)


def test_the_three_measured_former_codes_are_recoded(tmp_path: Path) -> None:
    out = _transformer(tmp_path, ["49069", "93059", "69152"])
    assert out["code_commune"].tolist() == ["49126", "93066", "69149"]
    assert out["code_departement"].tolist() == ["49", "93", "69"]
    # The name stays the one the source wrote: the place of the sale.
    assert out["nom_commune"].tolist() == [
        "Commune 49069",
        "Commune 93059",
        "Commune 69152",
    ]


def test_a_current_code_an_unknown_code_and_a_missing_code_are_kept(
    tmp_path: Path,
) -> None:
    out = _transformer(tmp_path, ["44109", "2A004", "97411", "99999", ""])
    assert out["code_commune"].fillna("").tolist() == [
        "44109",
        "2A004",
        "97411",
        "99999",
        "",
    ]


def test_the_table_is_one_to_one_in_the_same_departement_without_chain() -> None:
    table = codes_communes_actuels()
    assert len(table) > 1000, (
        "the table must be read (a wrong path would pass vacuously)"
    )
    assert not set(table) & set(table.values()), (
        "a code of today is never recoded, no chain"
    )

    def dep(c: str) -> str:
        return c[:3] if c.startswith("97") else c[:2]

    assert all(dep(a) == dep(b) for a, b in table.items())


def test_the_table_names_its_source_and_vintage() -> None:
    tete = CODES_COMMUNES_ACTUELS_PATH.read_text(encoding="utf-8").splitlines()[:4]
    assert any(
        "INSEE" in ligne and "2026" in ligne for ligne in tete if ligne.startswith("#")
    )
