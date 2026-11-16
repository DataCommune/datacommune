"""Reading of a BDNB departemental export, without database (plan 04.7-19).

The exports are built in code by ``tests/etl/bdnb_fixture`` with the headers
of the 2026-02.b dictionary (quoted departement code, multiline version
history). What is checked: the rows handed to COPY, the departement of the
file, the label of the version member, the accents kept and the BOM dropped,
and every refusal (absent member, missing column, cap, non-UTF-8 bytes,
non-integer year).
"""

import csv
import zipfile
from datetime import date

import pytest

from app.etl_bdnb import transform
from app.etl_bdnb.config import MEMBERS_READ, OUTPUT_COLUMNS
from app.etl_bdnb.transform import (
    ComptesExport,
    VersionExport,
    departement_du_fichier,
    label_de_millesime,
    read_version,
    transform_export,
    validate_export,
)
from tests.etl.bdnb_fixture import (
    FFO,
    RELATIONS,
    VERSION,
    Export,
    build_export,
    export_name,
    mini_exports,
    with_spaced_headers,
)

pytestmark = pytest.mark.unit


def _rows(path):
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        assert next(reader) == OUTPUT_COLUMNS
        return list(reader)


def test_the_departement_is_the_one_of_the_file(tmp_path):
    assert departement_du_fichier(tmp_path / export_name("85")) == "85"
    assert departement_du_fichier(tmp_path / export_name("2A")) == "2A"
    assert departement_du_fichier(tmp_path / "dep2b.zip") == "2B"
    assert departement_du_fichier(tmp_path / "dep01.zip") == "01"
    with pytest.raises(ValueError, match="département illisible"):
        departement_du_fichier(tmp_path / "dep971.zip")


def test_the_label_of_the_url_and_of_the_version_member():
    assert label_de_millesime("2026-02-b") == "2026-02.b"
    for malforme in ("2026-02.b", "2026-13-b", "latest", "2026-02-B"):
        with pytest.raises(ValueError, match="mal formé"):
            label_de_millesime(malforme)


def test_the_newest_line_of_the_multiline_history_is_the_label(tmp_path):
    path = build_export(tmp_path, "85", mini_exports()["85"])
    assert read_version(path) == VersionExport("2026-02.b", date(2026, 10, 7))
    assert validate_export(path) == VersionExport("2026-02.b", date(2026, 10, 7))


def test_rows_of_both_natures_with_the_departement_of_the_file(tmp_path):
    path = build_export(tmp_path, "85", mini_exports()["85"])
    out = tmp_path / "out.csv"

    comptes = transform_export(path, out, "85")

    assert comptes == ComptesExport(19, 1, 17, 0)
    rows = _rows(out)
    relations = [r for r in rows if r[0] == "relation"]
    ffo = [r for r in rows if r[0] == "ffo"]
    assert len(relations) == 18 and len(ffo) == 17
    assert relations[0] == [
        "relation",
        "bdnb-bg-85A0-0000-0001",
        "85191000RA0001",
        "85",
        "t",
        "",
        "",
        "",
    ]
    assert [
        "relation",
        "bdnb-bg-85B0-0000-0002",
        "85191000RB0001",
        "85",
        "f",
        "",
        "",
        "",
    ] in relations
    # Empty stays empty (NULL at COPY); the accent is kept as is.
    assert ["ffo", "bdnb-bg-85C0-0000-0001", "", "85", "", "", "PIERRE", ""] in ffo
    assert [
        "ffo",
        "bdnb-bg-DUP0-0000-0001",
        "",
        "85",
        "",
        "1930",
        "MEULIÈRE",
        "TUILES",
    ] in ffo


def test_a_bom_is_dropped_and_corsica_is_upper_case(tmp_path):
    exports = mini_exports()
    dep44 = build_export(tmp_path, "44", exports["44"])
    out = tmp_path / "44.csv"
    transform_export(dep44, out, departement_du_fichier(dep44))
    assert _rows(out)[0][1] == "bdnb-bg-44A0-0000-0001"

    dep2a = build_export(tmp_path, "2A", exports["2A"])
    out = tmp_path / "2a.csv"
    transform_export(dep2a, out, departement_du_fichier(dep2a))
    assert {row[3] for row in _rows(out)} == {"2A"}


def test_only_the_three_named_members_are_ever_opened(tmp_path, monkeypatch):
    path = build_export(tmp_path, "85", mini_exports()["85"])
    opened = []
    real_open = zipfile.ZipFile.open

    def spy(self, name, *args, **kwargs):
        opened.append(name if isinstance(name, str) else name.filename)
        return real_open(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", spy)
    validate_export(path)
    transform_export(path, tmp_path / "out.csv", "85")

    assert set(opened) == set(MEMBERS_READ)
    assert "csv/batiment_groupe.csv" in zipfile.ZipFile(path).namelist()


def _export(**changes) -> Export:
    """The 85 export of the mini-export, with *changes*."""
    base = mini_exports()["85"]
    return Export(relations=base.relations, ffo=base.ffo, **changes)


@pytest.mark.parametrize(
    ("export", "message"),
    [
        (_export(omit=(FFO,)), "membre absent csv/batiment_groupe_ffo_bat.csv"),
        (_export(omit=(VERSION,)), "membre absent"),
        (
            _export(raw={RELATIONS: b"batiment_groupe_id;parcelle_id\nG;P\n"}),
            r"colonnes absentes .*parcelle_principale",
        ),
        (
            _export(raw={FFO: b"batiment_groupe_id;annee_construction\nG;1990\n"}),
            r"colonnes absentes .*mat_mur_txt",
        ),
    ],
)
def test_a_member_or_a_column_missing_is_refused(tmp_path, export, message):
    path = build_export(tmp_path, "85", export)
    with pytest.raises(ValueError, match=message):
        validate_export(path)


def test_bytes_that_are_not_utf8_are_refused(tmp_path):
    latin1 = (
        "batiment_groupe_id;code_departement_insee;nb_niveau;annee_construction;"
        "usage_niveau_1_txt;mat_mur_txt;mat_toit_txt;nb_log\n"
        'G;"85";1;1990;Résidentiel;MEULIÈRE;TUILES;1\n'
    ).encode("latin-1")
    path = build_export(tmp_path, "85", _export(raw={FFO: latin1}))
    with pytest.raises(ValueError, match="octets non UTF-8 dans csv/batiment_groupe"):
        transform_export(path, tmp_path / "out.csv", "85")


def test_a_year_that_is_not_an_integer_is_refused(tmp_path):
    ffo = (
        "batiment_groupe_id;code_departement_insee;nb_niveau;annee_construction;"
        "usage_niveau_1_txt;mat_mur_txt;mat_toit_txt;nb_log\n"
        'G;"85";1;1990.5;Résidentiel;BETON;TUILES;1\n'
    ).encode()
    path = build_export(tmp_path, "85", _export(raw={FFO: ffo}))
    with pytest.raises(ValueError, match=r"année non entière '1990\.5'"):
        transform_export(path, tmp_path / "out.csv", "85")


def test_a_principal_flag_outside_one_and_zero_is_refused(tmp_path):
    relations = (
        b"batiment_groupe_id;parcelle_id;code_departement_insee;parcelle_principale\n"
        b'G;85191000RA0001;"85";oui\n'
    )
    path = build_export(tmp_path, "85", _export(raw={RELATIONS: relations}))
    with pytest.raises(ValueError, match="parcelle_principale 'oui'"):
        transform_export(path, tmp_path / "out.csv", "85")


def test_a_malformed_label_in_the_version_member_is_refused(tmp_path):
    path = build_export(tmp_path, "85", _export(label="2026-02-b"))
    with pytest.raises(ValueError, match="millésime illisible"):
        read_version(path)


def test_a_member_above_the_caps_is_refused_before_it_is_read(tmp_path, monkeypatch):
    path = build_export(tmp_path, "85", mini_exports()["85"])
    size = zipfile.ZipFile(path).getinfo(RELATIONS).file_size
    monkeypatch.setattr(transform, "MAX_MEMBER_BYTES", size - 1)
    with pytest.raises(ValueError, match="au-delà du plafond"):
        transform_export(path, tmp_path / "out.csv", "85")

    monkeypatch.setattr(transform, "MAX_MEMBER_BYTES", 2 * 1024**3)
    # 20 000 identical lines compress far beyond a ratio of 50.
    bombe = ('G;P;"85";1\n' * 20_000).encode()
    header = b"batiment_groupe_id;parcelle_id;code_departement_insee;"
    header += b"parcelle_principale\n"
    path = build_export(tmp_path / "b", "85", _export(raw={RELATIONS: header + bombe}))
    with pytest.raises(ValueError, match="rapport au-delà de 50"):
        transform_export(path, tmp_path / "out.csv", "85")


def test_column_names_are_read_as_they_are_checked(tmp_path):
    """A header `` parcelle_id`` passes the check (names stripped): the rows
    are read by the same stripped names, never a KeyError that would skip the
    file instead of refusing it (review Opus 4)."""
    export = mini_exports()["85"]
    propre = build_export(tmp_path / "propre", "85", export)
    espaces = build_export(
        tmp_path / "espaces", "85", with_spaced_headers("85", export)
    )
    assert zipfile.ZipFile(espaces).read(RELATIONS).startswith(b" batiment_groupe_id ")

    assert validate_export(espaces) == validate_export(propre)
    comptes = transform_export(espaces, tmp_path / "espaces.csv", "85")
    assert comptes == transform_export(propre, tmp_path / "propre.csv", "85")
    assert _rows(tmp_path / "espaces.csv") == _rows(tmp_path / "propre.csv")
