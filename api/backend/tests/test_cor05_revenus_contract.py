"""COR05 semantic contract: DEC income must never be described as DISP."""

from app.revenus.schemas import ResultatRevenus


def test_income_schema_declares_series_and_unit():
    props = ResultatRevenus.model_json_schema()["properties"]
    assert "déclaré" in props["revenu_median"].get("description", "")
    assert "consommation" in props["revenu_median"]["description"]
    assert "disponible" in props["taux_pauvrete"]["description"]


def test_frozen_metropolitan_benchmark_is_declared():
    import csv
    from pathlib import Path

    directory = Path(__file__).parent / "fixtures/cor05_revenus"
    values = []
    for series in ["DEC", "DISP"]:
        with (directory / f"FILO2021_{series}_METROPOLE.csv").open(
            encoding="utf-8-sig"
        ) as stream:
            row = next(csv.DictReader(stream, delimiter=";"))
            values.append(int(row["Q221"]))
    assert values == [22900, 23080]


def test_transform_keeps_suppression_distinct_from_synthetic_zero(
    tmp_path, monkeypatch
):
    import csv

    from app.etl_revenus import pipeline as module

    monkeypatch.setattr(module, "get_download_dir", lambda _: tmp_path)
    main = tmp_path / module.MAIN_CSV_NAME
    main.write_text("CODGEO;Q221;PMIMP21\n44998;0;0\n44999;s;s\n", encoding="utf-8")
    (tmp_path / module.POVERTY_CSV_NAME).write_text(
        "CODGEO;TP6021\n44998;0\n44999;s\n", encoding="utf-8"
    )
    result = module.RevenusPipeline().transform(main)
    with result.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    for field in ["revenu_median", "taux_pauvrete", "part_imposee"]:
        assert float(rows[0][field]) == 0
        assert rows[1][field] == ""
