"""ARCEP rows take the departement of their file (plan 04.1-21, Ianis decision).

A departemental ARCEP file can name a building whose address is in another
departement (2 of 337,006 buildings of base_imb_67 are addressed in 57). The
row keeps the INSEE code of its address and is filed under the departement of
the file, like BD TOPO.
"""

import csv
import gzip

import pytest

from app.config import settings
from app.etl_arcep.pipeline import ArcepPipeline

pytestmark = pytest.mark.unit


def _gz(path, header, rows):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)


def test_row_addressed_elsewhere_keeps_its_insee_under_the_file_departement(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl_tmp"))
    directory = tmp_path / "67"
    directory.mkdir()
    _gz(
        directory / "base_imb_67.csv.gz",
        ["imb_id", "addr_code_insee", "addr_nom_voie", "addr_numero", "addr_rep"],
        [
            ["IMB-A", "67482", "rue du Dôme", "1", ""],
            ["IMB-B", "57463", "route de Sarrebourg", "2", ""],
        ],
    )
    _gz(
        directory / "actuel_67.csv.gz",
        ["imb_id", "code_operateur", "code_techno", "classe_debit_descendant"],
        [["IMB-A", "OP", "FO", "1G"], ["IMB-B", "OP", "FO", "1G"]],
    )
    output = ArcepPipeline.__new__(ArcepPipeline).transform(directory)
    with output.open(encoding="utf-8", newline="") as fh:
        rows = {row["id_imb"]: row for row in csv.DictReader(fh)}
    assert rows["IMB-A"]["code_insee"] == "67482"
    assert rows["IMB-B"]["code_insee"] == "57463"
    assert {row["code_departement"] for row in rows.values()} == {"67"}
