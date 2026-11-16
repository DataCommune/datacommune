"""scripts/fidelite_nationale.py, without network nor database (04.1-03).

Tiny fixtures stand for the producer files; what is checked is the ETL filter
each count reproduces, the four states kept apart by ``diff`` (a departement
missing on one side is never equal nor zero, a gap is explained only by a
written motive naming that exact gap), and that the counting code opens no
file for writing (it runs as root on the hypervisor).
"""

from __future__ import annotations

import ast
import csv
import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from scripts import fidelite_nationale as fn

pytestmark = pytest.mark.unit

SCRIPT = Path(fn.__file__)


# ------------------------------------------------------------ drift checks --


def test_copied_constants_match_the_application():
    from app.etl.base import DEPARTEMENTS
    from app.etl_arcep import config as arcep
    from app.etl_ban.config import BAN_BASE_URL
    from app.etl_cadastre.config import CADASTRE_BASE_URL
    from app.etl_dvf import config as dvf
    from app.etl_rnb_liens.config import RNB_EXPORT_URL_TEMPLATE
    from scripts.import_geo_boundaries import SECTIONS_URL

    assert list(fn.DEPARTEMENTS_102) == list(DEPARTEMENTS)
    assert fn.ARCEP_BASE_IMB_URL == arcep.BASE_IMB_URL
    assert fn.ARCEP_ELIGIBILITE_URL == arcep.ELIGIBILITE_URL
    assert fn.CADASTRE_BASE_URL == CADASTRE_BASE_URL
    assert fn.SECTIONS_URL == SECTIONS_URL
    assert fn.RNB_EXPORT_URL_TEMPLATE == RNB_EXPORT_URL_TEMPLATE
    assert fn.BAN_BASE_URL == BAN_BASE_URL
    expected_dvf = fn.DVF_BASE_URL + "2024/departements/2A.csv.gz"
    assert dvf.get_csv_url(2024, "2A") == expected_dvf
    assert fn.dvf_years() == dvf.YEARS


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "{}",
        '[{"cle_interop_ban": "44109_0001_00001"}, {"cle_interop_ban": ""}]',
        '[{"cle_interop_ban": "a"}, {"cle_interop_ban": "a"}, "x", {"other": 1}]',
        '[{"cle_interop_ban": 12}, {"cle_interop_ban": "b"}]',
    ],
)
def test_rnb_key_parsing_is_the_pipeline_one(raw):
    from app.etl_rnb_liens.transform import parse_cles_ban

    assert fn.parse_cles_ban(raw) == parse_cles_ban(raw)


@pytest.mark.parametrize(
    "commune", ["44109", "2A004", "2B033", "97101", "97611", "", "7"]
)
def test_departement_rules_are_the_pipeline_ones(commune):
    from app.etl_dvf.transform import _derive_code_departement as dvf_rule

    assert fn.derive_departement_dvf(commune) == dvf_rule(pd.Series([commune]))[0]


def test_dpe_loaded_rule_excludes_tombstones_and_live_reads_sources():
    queries = " ".join(q for q, _ in fn.LOADED_SQL["dpe"][:2])
    assert queries.count("WHERE NOT is_tombstone") == 2
    source = SCRIPT.read_text(encoding="utf-8")
    assert "from app.etl_dpe.sources import DPE_SOURCES" in source
    assert "meg-83tjwtg8dyz4vv7h1dqe" not in source  # identifiers never copied


# ------------------------------------------------------------- no writing --


# The one write of the script: the ``floors`` mode generating
# app/scope/producer_counts.py on the workstation (plan 04.1-17), never run
# on the hypervisor. Named exactly, so that no other write can slip in.
_FLOORS_WRITE = "Path(args.out).write_text(module, encoding='utf-8', newline='\\n')"


def test_counting_code_opens_no_file_for_writing():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    forbidden_attrs = {
        "write_text",
        "write_bytes",
        "mkdir",
        "unlink",
        "rename",
        "replace",
        "rmdir",
        "touch",
        "remove",
        "makedirs",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name == "open":
                modes = [
                    arg.value
                    for arg in [*node.args[1:], *(k.value for k in node.keywords)]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                ]
                assert not any(set(m) & set("wax+") for m in modes), ast.unparse(node)
            if ast.unparse(node) == _FLOORS_WRITE:
                continue
            if isinstance(func, ast.Attribute) and func.attr in forbidden_attrs:
                # str.replace on text is fine; Path.replace/rename would move files.
                assert func.attr == "replace" and isinstance(
                    func.value, (ast.Constant, ast.Call, ast.Subscript, ast.Name)
                ), ast.unparse(node)
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = {alias.name for alias in node.names}
            module = getattr(node, "module", None) or ""
            assert not ({"shutil", "tempfile"} & (names | {module}))
    assert "mode=ro&immutable=1" in SCRIPT.read_text(encoding="utf-8")


# ------------------------------------------------------------------ diff --


def _payload(counts, **extra):
    return {"counts": counts, "primary_metric": "adresses", **extra}


def test_diff_equal_gap_and_one_sided_departements():
    producer = _payload({"44": {"adresses": 10}, "85": {"adresses": 7}})
    charge = _payload(
        {"44": {"adresses": 10}, "85": {"adresses": 5}, "13": {"adresses": 0}}
    )
    result = fn.diff(producer, charge)
    cells = result["departements"]
    assert cells["44"]["classe"] == "egal"
    assert cells["85"] == {
        "producteur": 7,
        "charge": 5,
        "ecart": -2,
        "classe": "ecart_a_consigner",
    }
    # A departement absent from the producer is neither equal nor zero.
    assert cells["13"]["classe"] == "absent_producteur"
    assert cells["13"]["producteur"] is None and cells["13"]["ecart"] is None


def test_diff_absent_base_and_panne_are_distinct_states():
    producer = _payload({"44": {"adresses": 3}}, pannes={"85": "HTTP 503"})
    charge = _payload({"85": {"adresses": 4}})
    cells = fn.diff(producer, charge, departements=["44", "85"])["departements"]
    assert cells["44"]["classe"] == "absent_base"
    assert cells["44"]["charge"] is None
    assert cells["85"]["classe"] == "panne_producteur"


def test_gap_is_explained_only_by_a_motive_naming_that_gap():
    producer = _payload({"44": {"adresses": 10}, "85": {"adresses": 10}})
    charge = _payload({"44": {"adresses": 8}, "85": {"adresses": 7}})
    explanations = [
        {"metric": "adresses", "departement": "44", "ecart": -2, "motif": "dup"},
        {"metric": "adresses", "departement": "85", "ecart": -2, "motif": "dup"},
    ]
    cells = fn.diff(producer, charge, explanations=explanations)["departements"]
    assert cells["44"]["classe"] == "ecart_explique"
    assert cells["44"]["motif"] == "dup"
    # Same motive, other gap: not explained.
    assert cells["85"]["classe"] == "ecart_a_consigner"
    with pytest.raises(SystemExit):
        fn.diff(
            producer,
            charge,
            explanations=[
                {"metric": "adresses", "departement": "44", "ecart": -2, "motif": " "}
            ],
        )


def test_diff_reports_live_count_without_judging_with_it():
    producer = _payload({"44": {"adresses": 10}})
    live = _payload({"44": {"adresses": 12}})
    charge = _payload({"44": {"adresses": 10}})
    cell = fn.diff(producer, charge, live=live)["departements"]["44"]
    assert cell["classe"] == "egal"
    assert cell["vivant"] == 12 and cell["ecart_vivant_moins_reference"] == 2


def test_diff_lists_one_sided_metrics_apart():
    producer = _payload({"44": {"adresses": 1, "base_imb:immeubles": 4}})
    charge = _payload({"44": {"adresses": 1, "diagnostics_dpe:tombstones": 2}})
    result = fn.diff(producer, charge)
    assert set(result["metriques"]) == {"adresses"}
    assert result["non_comparees"] == {
        "producteur_seulement": ["base_imb:immeubles"],
        "base_seulement": ["diagnostics_dpe:tombstones"],
    }


# ------------------------------------------------------------- counters --


def _gz_text(text: str) -> io.BytesIO:
    return io.BytesIO(gzip.compress(text.encode("utf-8")))


class NonSeekable(io.RawIOBase):
    """An HTTP-like body: read only, no seek, short reads."""

    def __init__(self, data: bytes, chunk: int = 7) -> None:
        self._data, self._chunk = data, chunk

    def readable(self):
        return True

    def readinto(self, buffer):
        size = min(len(buffer), self._chunk, len(self._data))
        buffer[:size] = self._data[:size]
        self._data = self._data[size:]
        return size


def test_values_agg_counts_and_rows_without_departement():
    payload = {
        "total": 20,
        "total_other": 0,
        "aggs": [{"value": "44", "total": 12}, {"value": "2A", "total": 5}],
    }
    assert fn.values_agg_counts(payload) == ({"44": 12, "2A": 5}, 3)
    with pytest.raises(fn.ProducerFailureError):
        fn.values_agg_counts({**payload, "total_other": 1})


def test_arcep_inner_join_filters_and_files_under_the_file_departement():
    base = (
        "imb_id;imb_x;addr_code_insee;addr_nom_voie\n"
        "I1;0;44109;Rue\n"
        "I2;0;2A004;Rue\n"  # addressed elsewhere: departement of the file
        "I3;0;;Rue\n"  # missing INSEE: departement of the file
        "NA;0;44109;Rue\n"  # pandas NA key: never joined
        "I4;0;97101;Rue\n"
    )
    actuel = (
        "imb_id;code_operateur;code_techno;classe_debit_descendant\n"
        "I1;OPA;FO;THD\n"
        "I1;OPB;FO;THD\n"
        "I1;OPC;;THD\n"  # empty technologie: dropped by swap()
        "I2;OPA;CU;HD\n"
        "I3;OPA;FO;THD\n"
        "NA;OPA;FO;THD\n"
        "I9;OPA;FO;THD\n"  # not in base_imb: inner join drops it
        "I4;OPA;FO;THD\n"
    )
    index = fn.arcep_base_index(fn.text(fn.decompressed(_gz_text(base))), "44")
    assert len(index) == 4
    joined = fn.arcep_join_counts(fn.text(fn.decompressed(_gz_text(actuel))), index)
    assert joined == {
        "44": {"eligibilite_fixe": 5, "eligibilite_fixe:id_imb_distincts": 4},
    }


def test_arcep_duplicate_base_key_multiplies_like_pandas_merge():
    base = "imb_id;addr_code_insee\nI1;44109\nI1;85001\n"
    actuel = "imb_id;code_techno\nI1;FO\n"
    index = fn.arcep_base_index(io.StringIO(base), "44")
    joined = fn.arcep_join_counts(io.StringIO(actuel), index)
    assert {k: v["eligibilite_fixe"] for k, v in joined.items()} == {"44": 2}


PARCELS = (
    '{"type":"FeatureCollection","features":[\n'
    '{"type":"Feature","id":"44002000AA0102","geometry":{"type":"Polygon",'
    '"coordinates":[[[-1.4,47.0],[-1.5,47.1],[-1.6,47.0],[-1.4,47.0]]]},'
    '"properties":{"id":"44002000AA0102","commune":"44002","prefixe":"000"}},\n'
    '{"type":"Feature","id":"2A004000AB0001","geometry":{"type":"MultiPolygon",'
    '"coordinates":[[[[8.7,41.9],[8.8,41.9],[8.8,42.0],[8.7,41.9]]],'
    "[[[8.1,41.1],[8.2,41.1],[8.1,41.2],[8.1,41.1]],"
    "[[8.11,41.11],[8.12,41.11],[8.11,41.12],[8.11,41.11]]]]},"
    '"properties":{"commune":"2A004"}},\n'
    '{"type":"Feature","id":"97101000AB0001","geometry":null,'
    '"properties":{"commune":"97101"}}\n'
    "]}\n"
)


def test_geojson_parcels_features_positions_and_departement():
    counts, unattributed = fn.geojson_parcel_counts(fn.decompressed(_gz_text(PARCELS)))
    assert unattributed == 0
    assert counts == {
        "44": {"parcelles": 1, "parcelles:sommets": 4},
        "2A": {"parcelles": 1, "parcelles:sommets": 12},
        "971": {"parcelles": 1, "parcelles:sommets": 0},
    }


def test_geojson_sections_distinct_property_ids():
    sections = (
        '{"type":"FeatureCollection","features":[\n'
        '{"type":"Feature","id":"a","geometry":null,"properties":{"id":"S1"}},\n'
        '{"type":"Feature","id":"b","geometry":null,"properties":{"id":"S1"}},\n'
        '{"type":"Feature","id":"c","geometry":null,"properties":'
        '{"commune":"44109","code":"AB"}}]}\n'  # the producer's last line
    )
    assert fn.geojson_section_ids(io.BytesIO(sections.encode())) == (3, 2)


def _rnb_zip(rows, members=1) -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=";")
    writer.writerow(["rnb_id", "point", "shape", "status", "addresses", "plots"])
    writer.writerows(rows)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(members):
            archive.writestr(f"RNB_44_{index}.csv", stream.getvalue())
    return buffer.getvalue()


RNB_ROWS = [
    ["B1", "", "x" * 200_000, "constructed", '[{"cle_interop_ban":"K1"}]', "[]"],
    [
        "B2",
        "",
        "",
        "constructed",
        '[{"cle_interop_ban":"K1"},{"cle_interop_ban":"K2"},{"cle_interop_ban":"K2"}]',
        "[]",
    ],
    ["B3", "", "", "constructed", "[]", "[]"],  # no key: dropped
    ["", "", "", "constructed", '[{"cle_interop_ban":"K3"}]', "[]"],
    ["B1", "", "", "constructed", '[{"cle_interop_ban":"K1"}]', "[]"],  # duplicate
]


def test_rnb_stream_reader_pairs_and_statistics():
    member = fn.ZipFirstMember(NonSeekable(_rnb_zip(RNB_ROWS)))
    pairs, stats = fn.rnb_pairs(fn.text(io.BufferedReader(member)))
    assert pairs == {("B1", "K1"), ("B2", "K1"), ("B2", "K2")}
    assert stats == {"batiments": 4, "batiments_sans_cle": 1, "sans_rnb_id": 1}


def test_rnb_zip_with_two_members_is_refused():
    member = fn.ZipFirstMember(NonSeekable(_rnb_zip(RNB_ROWS[:1], members=2), 4096))
    with pytest.raises(ValueError, match="more than one member"):
        io.BufferedReader(member).read()


def test_ban_rows_need_lon_and_lat():
    data = (
        "id;lon;lat;nom_voie\n"
        "a;-1.5;47.2;Rue\n"
        "b;;47.2;Rue\n"
        "c;-1.5;NA;Rue\n"
        "d;2.3;48.8;Rue\n"
    )
    assert fn.ban_rows(io.StringIO(data)) == {"adresses": 2, "lignes_source": 4}


def test_dvf_rows_grain_and_parcel_pairs_over_years():
    header = "id_mutation,code_commune,id_parcelle,longitude,latitude\n"
    year1 = header + (
        "M1,44109,P1,-1.5,47.2\n"
        "M1,44109,P1,-1.5,47.2\n"
        "M1,44109,P2,-1.5,47.2\n"
        "M2,44109,,-1.5,47.2\n"  # no parcel: not a pair
        "M3,44109,P3,,47.2\n"  # no coordinates: loaded all the same (04.4 D-01)
        "M4,2A004,P4,8.7,41.9\n"
    )
    year2 = header + "M5,44109,P5,-1.5,47.2\n"
    state = fn.DvfDepartement()
    state.add(io.StringIO(year1))
    state.add(io.StringIO(year2))
    assert state.counts() == {
        "44": {"mutations": 6, "mutations_grain": 4, "mutation_parcelles": 4},
        "2A": {"mutations": 1, "mutations_grain": 1, "mutation_parcelles": 1},
    }
    assert state.source_rows == 7


def _archive_with(tmp_path, files):
    """A one-run archive holding *files* ({name: local path}) as objects."""
    entries = []
    for name, source in files.items():
        raw = source.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        target = tmp_path / "archive" / "batiments" / "objects" / sha[:2] / sha
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        entries.append({"name": name, "sha256": sha})
    runs = tmp_path / "archive" / "batiments" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "1-x.json").write_text(json.dumps({"files": entries}), encoding="utf-8")
    return fn.ArchiveRun(tmp_path / "archive", "batiments", "1-x")


def test_archive_batiments_names_a_departement_without_gpkg(tmp_path):
    gpkg = tmp_path / "BDT_3-5_GPKG_LAMB93_D044-ED2025-12-15.gpkg"
    with sqlite3.connect(gpkg) as conn:
        conn.execute("CREATE TABLE batiment (cleabs TEXT)")
        conn.executemany("INSERT INTO batiment VALUES (?)", [("a",), ("b",)])
    producer_7z = tmp_path / "bdtopo_971.7z"
    producer_7z.write_bytes(b"not read by the script")
    run = fn.Counting("batiments", ["44", "971"])
    archive = _archive_with(tmp_path, {gpkg.name: gpkg, "bdtopo_971.7z": producer_7z})
    fn.archive_batiments(run, archive)
    assert run.counts == {"44": {"batiments": 2}}
    # The producer .7z is not opened here: a named failure, never a zero.
    assert set(run.pannes) == {"971"}


def test_gpkg_feature_count_read_only(tmp_path):
    path = tmp_path / "D044.gpkg"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE batiment (cleabs TEXT)")
        conn.executemany("INSERT INTO batiment VALUES (?)", [("a",), ("b",)])
    assert fn.gpkg_feature_count(path) == 2
    assert fn.departement_from_code3("044") == "44"
    assert fn.departement_from_code3("02A") == "2A"
    assert fn.departement_from_code3("971") == "971"


# ---------------------------------------------------------- loaded / cli --


class FakeCursor:
    def __init__(self, answers):
        self.answers = answers
        self.queries = []

    def execute(self, query):
        self.queries.append(query)

    def fetchall(self):
        return self.answers[len(self.queries) - 1]


def test_collect_loaded_names_metrics_per_departement():
    cursor = FakeCursor(
        [
            [("44", "diagnostics_dpe:existant", 3), ("44", "diagnostics_dpe:neuf", 1)],
            [("44", 4), ("85", 2)],
            [("44", 1)],
        ]
    )
    assert fn.collect_loaded(cursor, "dpe") == {
        "44": {
            "diagnostics_dpe:existant": 3,
            "diagnostics_dpe:neuf": 1,
            "diagnostics_dpe": 4,
            "diagnostics_dpe:tombstones": 1,
        },
        "85": {"diagnostics_dpe": 2},
    }


def _archive(root: Path, dataset: str, run_id: str, files: dict[str, bytes]):
    entries = []
    for relpath, data in files.items():
        sha = hashlib.sha256(data).hexdigest()
        obj = root / dataset / "objects" / sha[:2] / sha
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(data)
        entries.append(
            {
                "relpath": relpath,
                "name": Path(relpath).name,
                "bytes": len(data),
                "sha256": sha,
                "object": f"objects/{sha[:2]}/{sha}",
            }
        )
    runs = root / dataset / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    record = {"dataset": dataset, "run_id": run_id, "origin": "telechargement"}
    (runs / f"{run_id}.json").write_text(json.dumps({**record, "files": entries}))


def _run_cli(capsys, argv):
    assert fn.main(argv) == 0
    return json.loads(capsys.readouterr().out)


def test_count_archive_ban_then_diff_end_to_end(tmp_path, capsys):
    ban44 = "id;lon;lat\na;1;2\nb;;2\n"
    _archive(
        tmp_path,
        "ban",
        "32-20260925T211831Z",
        {"raw/adresses-44.csv": ban44.encode(), "raw/adresses-85.csv": b"id;lon;lat\n"},
    )
    producer = _run_cli(
        capsys,
        [
            "--script-sha256",
            "ab" * 32,
            "count",
            "--source",
            "ban",
            "--archive-run",
            "ban:32-20260925T211831Z",
            "--archive-root",
            str(tmp_path),
            "--departements",
            "44,85",
        ],
    )
    assert producer["mode"] == "archive"
    assert producer["counts"]["44"]["adresses"] == 1
    # Zero rows in 85 is a zero, not an absence.
    assert producer["counts"]["85"]["adresses"] == 0
    assert producer["generation"]["run_id"] == "32-20260925T211831Z"
    assert producer["script_sha256_origin"] == "file"
    p_path = tmp_path / "p.json"
    l_path = tmp_path / "l.json"
    p_path.write_text(json.dumps(producer), encoding="utf-8")
    l_path.write_text(
        json.dumps({"counts": {"44": {"adresses": 1}}, "dataset": "ban"}),
        encoding="utf-8",
    )
    result = _run_cli(
        capsys, ["diff", "--producer", str(p_path), "--loaded", str(l_path)]
    )
    assert result["departements"]["44"]["classe"] == "egal"
    assert result["departements"]["85"]["classe"] == "absent_base"
    assert result["inputs"]["producer"]["sha256"]


def test_count_archive_dpe_reads_manifest_and_csv(tmp_path, capsys):
    def csv_bytes(rows):
        return ("numero_dpe\n" + "".join(f'"{r}"\n' for r in rows)).encode()

    files = {
        "g-dpe03existant-44.csv": csv_bytes(["a", "b"]),
        "g-dpe02neuf-44.csv": csv_bytes(["c"]),
    }
    manifest = {
        "import_id": "g",
        "files": [
            {
                "source_slug": slug,
                "department": "44",
                "path": f"/data/{name}",
                "rows": rows,
                "initial_total": rows,
                "final_total": rows,
            }
            for slug, name, rows in (
                ("dpe03existant", "g-dpe03existant-44.csv", 2),
                ("dpe02neuf", "g-dpe02neuf-44.csv", 1),
            )
        ],
    }
    files["manifest.json"] = json.dumps(manifest).encode()
    _archive(tmp_path, "dpe", "41-20261012T215324Z", files)
    payload = _run_cli(
        capsys,
        [
            "count",
            "--source",
            "dpe",
            "--archive-run",
            "dpe:41-20261012T215324Z",
            "--archive-root",
            str(tmp_path),
            "--departements",
            "44",
        ],
    )
    assert payload["counts"]["44"] == {
        "diagnostics_dpe": 3,
        "diagnostics_dpe:existant": 2,
        "diagnostics_dpe:neuf": 1,
    }
    assert payload["pannes"] == {}


def test_live_count_separates_404_from_failure(monkeypatch, capsys):
    def fake_open(self, url):
        self._record(url, 200, {}, url)
        if "adresses-44" in url:
            return _gz_text("id;lon;lat\na;1;2\n")
        if "adresses-85" in url:
            return None  # 404
        raise fn.ProducerFailureError("HTTP 503")

    monkeypatch.setattr(fn.Http, "open", fake_open)
    monkeypatch.setattr(fn.time, "sleep", lambda _: None)
    payload = _run_cli(
        capsys, ["count", "--source", "ban", "--departements", "44,85,13"]
    )
    assert payload["counts"] == {"44": {"adresses": 1, "lignes_source": 1}}
    assert payload["absent"]["85"]["etat"] == "absent_producteur"
    assert payload["pannes"]["13"].startswith("ProducerFailureError")
    assert payload["captured_at"] and payload["script_sha256"]


# ------------------------------------------------- coverage and receipts --


def test_declared_out_of_coverage_is_its_own_state():
    producer = _payload(
        {"44": {"mutations": 3}, "975": {"mutations": 2}}, primary_metric="mutations"
    )
    charge = _payload({"44": {"mutations": 3}}, primary_metric="mutations")
    declared = [
        {"metric": "*", "departement": "67", "etat": "hors_couverture", "motif": "AM"},
        {
            "metric": "mutations",
            "departement": "975",
            "etat": "hors_couverture",
            "motif": "COM",
        },
    ]
    cells = fn.diff(
        producer, charge, explanations=declared, departements=["44", "67", "975"]
    )["departements"]
    assert cells["67"]["classe"] == "hors_couverture"
    assert cells["67"]["motif"] == "AM"
    # The producer publishes it, the base does not load it: declared, kept apart.
    assert cells["975"] == {
        "producteur": 2,
        "charge": None,
        "ecart": None,
        "classe": "hors_couverture",
        "motif": "COM",
    }
    # Undeclared, the same absence stays an absence.
    undeclared = fn.diff(producer, charge, departements=["67"])["departements"]
    assert undeclared["67"]["classe"] == "absent_des_deux"


def test_loaded_rows_under_a_coverage_declaration_raise_an_alert():
    producer = _payload({"67": {"mutations": 2}}, primary_metric="mutations")
    charge = _payload({"67": {"mutations": 2}}, primary_metric="mutations")
    declared = [
        {"metric": "*", "departement": "67", "etat": "hors_couverture", "motif": "AM"}
    ]
    cell = fn.diff(producer, charge, explanations=declared)["departements"]["67"]
    assert cell["classe"] == "egal"
    assert "hors couverture" in cell["alerte"]


def _receipt(stage, removed, published):
    return {
        "run_id": "14-20260924T231954Z",
        "counting": {
            "source_rows": stage,
            "source_rows_unit": "ligne physique CSV",
            "scope_filter": {"_flat": {"removed": removed, "without_departement": 0}},
            "stages_rows_affected": {"stage:a.csv": stage, "scope_filter": removed},
        },
        "tables": {"t": {"total_rows": published, "published_rows_by_departement": {}}},
    }


def test_receipts_compare_published_to_kept_records(tmp_path, capsys):
    for dataset, published in (("antennes", 5), ("equipements", 4)):
        folder = tmp_path / dataset
        folder.mkdir()
        (folder / "r.json").write_text(json.dumps(_receipt(10, 5, published)))
        (folder / "latest.json").write_text(json.dumps({"manifest": "r.json"}))
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"antennes": {"t": 5}, "equipements": {"t": 3}}))
    payload = _run_cli(
        capsys,
        [
            "receipts",
            "--imports-dir",
            str(tmp_path),
            "--datasets",
            "antennes,equipements",
            "--base-counts",
            str(base),
        ],
    )
    rows = payload["datasets"]
    assert rows["antennes"]["classe"] == "egal"
    assert rows["antennes"]["retenus_apres_perimetre"] == 5
    assert rows["equipements"]["classe"] == "ecart_a_consigner"
    assert rows["equipements"]["ecart"] == -1
    assert rows["equipements"]["base_actuelle"]["classe"] == "ecart_a_consigner"
    assert rows["antennes"]["receipt"]["sha256"]
    assert payload["inputs"]["base_counts"]["sha256"]


def test_unmeasured_scope_detail_is_never_read_as_zero():
    # Copropriete run 130: the per-table detail is "non mesuré", the
    # scope_filter stage counter measured 577 removed rows.
    receipt = _receipt(656565, 577, 647751)
    receipt["counting"]["scope_filter"] = "non mesuré"
    summary = fn.receipt_summary(receipt)
    assert summary["retires_par_perimetre"] == 577
    assert summary["dont_sans_departement"] is None
    assert summary["retenus_apres_perimetre"] == 655988
    assert summary["ecart_publies_moins_retenus"] == -8237
    # Neither measured: no removal, no kept count, no gap, never a zero.
    receipt["counting"]["stages_rows_affected"]["scope_filter"] = "non mesuré"
    summary = fn.receipt_summary(receipt)
    assert summary["retires_par_perimetre"] is None
    assert summary["retenus_apres_perimetre"] is None
    assert summary["ecart_publies_moins_retenus"] is None
    assert (
        fn.classify(summary["retenus_apres_perimetre"], 647751, None)["ecart"] is None
    )


def test_wfs_total_is_kept_apart_from_territories(monkeypatch, capsys):
    def fake_open(self, url):
        self._record(url, 200, {}, url)
        hits = 10 if "BBOX" not in url else 1
        return io.BytesIO(f'<wfs:FeatureCollection numberMatched="{hits}"'.encode())

    monkeypatch.setattr(fn.Http, "open", fake_open)
    payload = _run_cli(capsys, ["count", "--source", "bdtopo_wfs"])
    assert payload["totals"] == {"batiments_wfs": len(fn.WFS_TERRITORIES)}
    assert payload["details"]["total_wfs"] == 10
    assert payload["details"]["territoires_moins_total"] == len(fn.WFS_TERRITORIES) - 10


# ------------------------------------------------------------------- bdnb --

BDNB_RUN = "7-20261102T120000Z"
BDNB_DATASET = "bdnb_batiment_parcelle"
LAST_MODIFIED = "Mon, 05 Oct 2026 10:47:40 GMT"


def test_bdnb_copies_match_the_application():
    from app.etl_bdnb import config, transform
    from app.etl_bdnb import pipeline as bdnb_pipeline

    assert fn.BDNB_EXPORT_URL_TEMPLATE == config.EXPORT_URL_TEMPLATE
    assert fn.BDNB_MEMBER_RELATIONS == config.MEMBER_RELATIONS
    assert fn.BDNB_MEMBER_FFO == config.MEMBER_FFO
    assert fn.BDNB_MEMBER_VERSION == config.MEMBER_VERSION
    assert fn.BDNB_CSV_SEPARATOR == config.CSV_SEPARATOR
    assert fn.BDNB_RELATION_COLUMNS == config.RELATION_COLUMNS
    assert fn.BDNB_FFO_COLUMNS == config.FFO_COLUMNS
    assert fn.BDNB_VERSION_COLUMNS == config.VERSION_COLUMNS
    assert fn.BDNB_MAX_MEMBER_BYTES == config.MAX_MEMBER_BYTES
    assert fn.BDNB_MAX_COMPRESSION_RATIO == config.MAX_COMPRESSION_RATIO
    assert fn.BDNB_DEPT_RE.pattern == transform._DEPT_RE.pattern
    assert fn.BDNB_LABEL_RE.pattern == transform.LABEL_RE.pattern
    assert fn.BDNB_MILLESIME_RE.pattern == transform.MILLESIME_RE.pattern
    assert fn.bdnb_label("2026-02-b") == transform.label_de_millesime("2026-02-b")
    # The count reproduces the SQL of the swap: an edit of it is reviewed here.
    sql = bdnb_pipeline._TRANSFER_SQL + "\n" + bdnb_pipeline._DUPLICATES_SQL
    digest = hashlib.sha256(" ".join(sql.split()).encode()).hexdigest()
    assert digest == fn.BDNB_SWAP_SQL_SHA256
    assert "LEFT JOIN" in fn.FILTERS["bdnb"]


def test_bdnb_loaded_query_returns_exactly_the_closed_list():
    (query, metric), *others = fn.LOADED_SQL[BDNB_DATASET]
    assert not others and metric == ""
    names = re.findall(r"\('(bdnb[^']*)',", query)
    assert tuple(names) == fn.METRIQUES_COMPAREES["bdnb"]
    assert fn.LOADED_PRIMARY[BDNB_DATASET] == fn.PRIMARY_METRIC["bdnb"]


def _bdnb_zips(directory: Path, exports) -> dict[str, Path]:
    from tests.etl.bdnb_fixture import build_export

    return {dept: build_export(directory, dept, e) for dept, e in exports.items()}


def _bdnb_archive(root: Path, exports, label: str = "2026-02.b") -> None:
    """A bdnb archive run: the producer zips under their names, and a record
    carrying *label* (what the runner records)."""
    built = _bdnb_zips(root / "built", exports)
    _archive(
        root,
        BDNB_DATASET,
        BDNB_RUN,
        {f"raw/{path.name}": path.read_bytes() for path in built.values()},
    )
    record_path = root / BDNB_DATASET / "runs" / f"{BDNB_RUN}.json"
    record = json.loads(record_path.read_text())
    record["source_period_label"] = label
    record_path.write_text(json.dumps(record))


def _bdnb_count(capsys, root: Path, *extra: str) -> dict:
    return _run_cli(
        capsys,
        [
            "count",
            "--source",
            "bdnb",
            "--archive-run",
            f"{BDNB_DATASET}:{BDNB_RUN}",
            "--archive-root",
            str(root),
            *extra,
        ],
    )


def _bdnb_metrics_of(rows) -> dict[str, dict[str, int]]:
    """Compared metrics of published rows (code, parcelle, groupe, principale,
    annee, mur, toit), as ``loaded`` counts them in the base."""
    counts: dict[str, dict[str, int]] = {}
    parcelles: dict[str, set[str]] = {}
    for code, parcelle, _groupe, principale, annee, mur, toit in rows:
        slot = counts.setdefault(code, dict.fromkeys(fn.METRIQUES_COMPAREES["bdnb"], 0))
        slot["bdnb_batiment_parcelle"] += 1
        slot["bdnb:principales"] += principale
        slot["bdnb:annee_renseignee"] += annee is not None
        slot["bdnb:mur_renseigne"] += mur is not None
        slot["bdnb:toit_renseigne"] += toit is not None
        parcelles.setdefault(code, set()).add(parcelle)
    for code, seen in parcelles.items():
        counts[code]["bdnb:parcelles_distinctes"] = len(seen)
    return counts


def _compared(payload) -> dict[str, dict[str, int]]:
    keep = fn.METRIQUES_COMPAREES["bdnb"]
    return {
        dept: {m: v for m, v in metrics.items() if m in keep}
        for dept, metrics in payload["counts"].items()
    }


# The receipt of the mini-export run, as written by hand in
# tests/integration/test_bdnb_pipeline.py (test_the_receipt_counts_...).
MINI_RECEIPT = {
    "producteur_relations_2A": 1,
    "producteur_ffo_2A": 1,
    "relations_ecartees_2A": 0,
    "producteur_relations_44": 2,
    "producteur_ffo_44": 2,
    "relations_ecartees_44": 0,
    "producteur_relations_85": 19,
    "producteur_ffo_85": 17,
    "relations_ecartees_85": 1,
    "publiees_2A": 1,
    "publiees_44": 2,
    "publiees_85": 17,
    "doublons_inter_fichiers": 1,
    "ffo_divergents": 0,
}


def test_bdnb_archive_count_is_the_written_rows_and_the_receipt(tmp_path, capsys):
    from tests.etl.bdnb_fixture import LABEL, attendu, mini_exports

    _bdnb_archive(tmp_path, mini_exports())
    payload = _bdnb_count(capsys, tmp_path, "--departements", "2A,44,85")

    assert payload["mode"] == "archive"
    assert payload["primary_metric"] == "bdnb_batiment_parcelle"
    # Compared metrics = the published rows written by hand (the integration
    # test locks those rows against the real swap).
    assert _compared(payload) == _bdnb_metrics_of(attendu())
    # Raw metrics = the counters of the receipt.
    assert fn.bdnb_receipt_counters(payload) == MINI_RECEIPT
    assert payload["totals"]["bdnb:brut:parcelle_id_longueur_max"] == 14
    assert payload["details"]["etiquettes"] == {"2A": LABEL, "44": LABEL, "85": LABEL}
    assert payload["millesime"] == LABEL
    assert payload["pannes"] == {} and payload["absent"] == {}


def test_bdnb_raw_metrics_are_the_pipeline_stage_counters(
    tmp_path, capsys, monkeypatch
):
    from app.config import settings
    from app.etl_bdnb.pipeline import BdnbBatimentParcellePipeline
    from tests.etl.bdnb_fixture import mini_exports

    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    exports = mini_exports()
    _bdnb_archive(tmp_path, exports)
    payload = _bdnb_count(capsys, tmp_path)
    pipeline = BdnbBatimentParcellePipeline()
    for path in _bdnb_zips(tmp_path / "etl-in", exports).values():
        pipeline.transform(path)
    receipt = fn.bdnb_receipt_counters(payload)
    stage = pipeline.stage_counters()
    assert len(stage) == 9
    assert {name: receipt[name] for name in stage} == stage


def _export(relations, ffo, **kwargs):
    from tests.etl.bdnb_fixture import Export

    return Export(relations=tuple(relations), ffo=tuple(ffo), **kwargs)


def replace_raw(export, name, data):
    from dataclasses import replace

    return replace(export, raw={**export.raw, name: data})


def test_bdnb_five_files_and_a_doubled_pair_give_four_rows(tmp_path, capsys):
    pair = ("bdnb-bg-0001-0000-0001", "01001000AA0001")
    exports = {
        f"0{i}": _export(
            [(f"bdnb-bg-000{i}-0000-0001", f"0{i}001000AA0001", "1")],
            [(f"bdnb-bg-000{i}-0000-0001", "1950", "PIERRE", "TUILES")],
        )
        for i in range(1, 5)
    }
    # The fifth file repeats the pair of 01, not as its principal parcel.
    exports["05"] = _export([(*pair, "0")], [(pair[0], "1950", "PIERRE", "TUILES")])
    _bdnb_archive(tmp_path, exports)

    payload = _bdnb_count(capsys, tmp_path)

    # Kept once, in 01 (smallest code), principal by bool_or: 4, never 5.
    assert payload["totals"]["bdnb_batiment_parcelle"] == 4
    assert payload["counts"]["05"]["bdnb_batiment_parcelle"] == 0
    assert payload["counts"]["01"]["bdnb:principales"] == 1
    assert payload["totals"]["bdnb:brut:doublons_inter_fichiers"] == 1
    assert payload["totals"]["bdnb:brut:relations_fichier"] == 5
    assert fn.bdnb_receipt_counters(payload)["doublons_inter_fichiers"] == 1


def test_bdnb_divergent_ffo_rows_are_counted_across_and_within_files(tmp_path, capsys):
    exports = {
        "44": _export(
            [("G1", "44000000AA0001", "1"), ("G2", "44000000AA0002", "1")],
            # G2: two rows of one file disagree on the year.
            [("G1", "1930", "PIERRE", ""), ("G2", "", "", ""), ("G2", "1990", "", "")],
        ),
        "85": _export(
            [("G1", "85000000AA0001", "1")],
            # G1 again, other wall: divergent between files.
            [("G1", "1930", "BOIS", "")],
        ),
    }
    _bdnb_archive(tmp_path, exports)

    payload = _bdnb_count(capsys, tmp_path)

    assert payload["totals"]["bdnb:brut:ffo_divergents"] == 2
    assert payload["counts"]["44"]["bdnb:brut:ffo_divergents"] == 2
    # G2 keeps the row of 1990 (annee ascending, NULLS LAST), as DISTINCT ON.
    assert payload["counts"]["44"]["bdnb:annee_renseignee"] == 2


def _bdnb_random_exports():
    import random

    from tests.etl.bdnb_fixture import mini_exports

    rng = random.Random(20261102)
    groupes = [f"bdnb-bg-R{i:03d}-0000-0001" for i in range(150)]
    parcelles = [f"0{i % 9 + 1}000000RR{i:04d}" for i in range(200)]
    exports = mini_exports()
    for dept in ("01", "2B", "13"):
        relations = [
            (rng.choice(groupes), rng.choice(parcelles), rng.choice("10"))
            for _ in range(300)
        ]
        ffo = [
            (g, rng.choice(["", "1900", "1975", "2001"]), rng.choice(["", "BOIS"]), "")
            for g in rng.sample(groupes, 90)
        ]
        exports[dept] = _export(relations, ffo)
    return exports


def test_bdnb_one_pass_or_several_give_the_same_count(tmp_path, capsys):
    _bdnb_archive(tmp_path, _bdnb_random_exports())

    outputs = []
    for passes in ("1", "3", "7"):
        payload = _bdnb_count(capsys, tmp_path, "--passes", passes)
        assert payload["details"]["passes"] == int(passes)
        outputs.append(
            {k: payload[k] for k in ("counts", "totals", "absent", "pannes")}
        )
    assert outputs[0] == outputs[1] == outputs[2]
    assert outputs[0]["totals"]["bdnb:brut:doublons_inter_fichiers"] > 0
    assert outputs[0]["totals"]["bdnb:brut:ffo_divergents"] > 0
    # K derived from --memoire-max when not given.
    payload = _bdnb_count(capsys, tmp_path, "--memoire-max", "4000")
    assert payload["details"]["passes"] > 1
    assert {k: payload[k] for k in ("counts", "totals")} == {
        k: outputs[0][k] for k in ("counts", "totals")
    }


@pytest.mark.parametrize(
    ("relations", "ffo", "motif"),
    [
        ([("G1", "85000000AA0001", "2")], [], "parcelle_principale"),
        ([("G1", "85000000AA0001", "1")], [("G1", "19x4", "", "")], "année"),
        ([("G1", "85000000AA0001", "1")], [("G1", "40000", "", "")], "smallint"),
    ],
)
def test_bdnb_a_file_the_etl_refuses_is_a_named_failure(
    tmp_path, capsys, monkeypatch, relations, ffo, motif
):
    from tests.etl.bdnb_fixture import mini_exports

    monkeypatch.setattr(fn.time, "sleep", lambda _: None)
    exports = {"44": mini_exports()["44"], "85": _export(relations, ffo)}
    _bdnb_archive(tmp_path, exports)

    payload = _bdnb_count(capsys, tmp_path)

    assert set(payload["pannes"]) == {"85"} and motif in payload["pannes"]["85"]
    assert set(payload["counts"]) == {"44"}


def test_bdnb_reading_rules_are_the_transform_ones(tmp_path, capsys):
    from app.etl_bdnb.transform import transform_export
    from tests.etl.bdnb_fixture import FFO, RELATIONS, export_name

    relations = (
        "﻿batiment_groupe_id;parcelle_id;code_departement_insee;"
        "parcelle_principale\n"
        ' G1 ; 85000000AA0001 ;"85";1\n'
        "\n"  # blank line: skipped, never a record
        ';85000000AA0002;"85";1\n'
        'G2;;"85";0\n'
    )
    ffo = (
        "batiment_groupe_id;code_departement_insee;nb_niveau;annee_construction;"
        "usage_niveau_1_txt;mat_mur_txt;mat_toit_txt;nb_log\n"
        'G1;"85";1; 1984 ;x; BETON ;;1\n'
        ';"85";1;1990;x;;;1\n'
    )
    ok = _export([], [], raw={RELATIONS: relations.encode(), FFO: ffo.encode()})
    _bdnb_archive(tmp_path, {"85": ok})
    payload = _bdnb_count(capsys, tmp_path)
    zip_path = tmp_path / "built" / export_name("85")
    comptes = transform_export(zip_path, tmp_path / "out.csv", "85")
    raw = payload["counts"]["85"]
    assert raw["bdnb:brut:relations_fichier"] == comptes.relations_lues == 3
    assert raw["bdnb:brut:relations_ecartees"] == comptes.relations_ecartees == 2
    assert raw["bdnb:brut:ffo_fichier"] == comptes.ffo_lues == 2
    assert raw["bdnb_batiment_parcelle"] == 1
    assert raw["bdnb:annee_renseignee"] == raw["bdnb:mur_renseigne"] == 1
    assert raw["bdnb:toit_renseigne"] == 0

    # A short row has no principal flag: refused by both.
    short = relations + "G3;85000000AA0003\n"
    refused = _export([], [], raw={RELATIONS: short.encode(), FFO: ffo.encode()})
    path = _bdnb_zips(tmp_path / "refused", {"85": refused})["85"]
    with pytest.raises(ValueError, match="parcelle_principale"):
        transform_export(path, tmp_path / "out2.csv", "85")
    with pytest.raises(fn._BdnbDeptError, match="parcelle_principale"):
        fn.bdnb_passes({"85": fn.BdnbExport(lambda: path.open("rb"))}, 1)


# ------------------------------------------------------------ bdnb: diff --


def _bdnb_payloads(tmp_path, capsys, label="2026-02.b"):
    from tests.etl.bdnb_fixture import attendu, mini_exports

    root = tmp_path / f"archive-{label}"
    _bdnb_archive(root, mini_exports(), label=label)
    producer = _bdnb_count(capsys, root)
    charge = {
        "dataset": BDNB_DATASET,
        "counts": _bdnb_metrics_of(attendu()),
        "primary_metric": "bdnb_batiment_parcelle",
        "source_period_label": "2026-02.b",
        "effective_departments": ["2A", "44", "85"],
    }
    return producer, charge


def _diff_cli(capsys, tmp_path, producer, charge) -> tuple[int, dict]:
    p_path, l_path = tmp_path / "p.json", tmp_path / "l.json"
    p_path.write_text(json.dumps(producer), encoding="utf-8")
    l_path.write_text(json.dumps(charge), encoding="utf-8")
    code = fn.main(["diff", "--producer", str(p_path), "--loaded", str(l_path)])
    return code, json.loads(capsys.readouterr().out)


def test_bdnb_diff_equal_on_every_compared_metric(tmp_path, capsys):
    producer, charge = _bdnb_payloads(tmp_path, capsys)
    code, result = _diff_cli(capsys, tmp_path, producer, charge)
    assert code == 0 and result["erreurs"] == []
    assert set(result["metriques"]) == set(fn.METRIQUES_COMPAREES["bdnb"])
    assert result["classes"] == {"egal": 6 * 3}
    assert result["millesime"]["classe"] == "egal"
    # Raw metrics are judged against the receipt, never against the base.
    seulement = result["non_comparees"]["producteur_seulement"]
    assert "bdnb:brut:relations_fichier" in seulement


@pytest.mark.parametrize("side", ["producteur", "base"])
def test_bdnb_diff_fails_when_a_compared_metric_is_missing(tmp_path, capsys, side):
    producer, charge = _bdnb_payloads(tmp_path, capsys)
    payload = producer if side == "producteur" else charge
    for metrics in payload["counts"].values():
        del metrics["bdnb:mur_renseigne"]
    code, result = _diff_cli(capsys, tmp_path, producer, charge)
    assert code == 1
    assert any(
        e.startswith(f"{side}: metrique comparee absente bdnb:mur_renseigne")
        for e in result["erreurs"]
    )
    # Never compared on the intersection, which would hide it.
    assert "bdnb:mur_renseigne" in result["metriques"]


def test_bdnb_diff_fails_on_two_millesime_labels(tmp_path, capsys):
    # The archive record names another millesime than its zips and the base.
    producer, charge = _bdnb_payloads(tmp_path, capsys, label="2026-02.a")
    code, result = _diff_cli(capsys, tmp_path, producer, charge)
    assert code == 1 and result["millesime"]["classe"] == "ecart"
    assert result["millesime"]["enregistrement"] == "2026-02.a"
    # Every compared metric is still equal: the millesime alone fails.
    assert result["classes"] == {"egal": 6 * 3}

    producer, charge = _bdnb_payloads(tmp_path, capsys)
    producer["details"]["etiquettes"]["44"] = "2025-07.b"
    assert _diff_cli(capsys, tmp_path, producer, charge)[0] == 1

    producer, charge = _bdnb_payloads(tmp_path, capsys)
    charge["source_period_label"] = None
    assert _diff_cli(capsys, tmp_path, producer, charge)[0] == 1


def _only_44(tmp_path, capsys, *count_args):
    """Archive and base holding 44 alone (Codex review 1, finding 2)."""
    from tests.etl.bdnb_fixture import attendu, mini_exports

    _bdnb_archive(tmp_path / "archive", {"44": mini_exports()["44"]})
    producer = _bdnb_count(capsys, tmp_path / "archive", *count_args)
    rows = [r for r in attendu() if r[0] == "44"]
    charge = {
        "dataset": BDNB_DATASET,
        "counts": _bdnb_metrics_of(rows),
        "primary_metric": "bdnb_batiment_parcelle",
        "source_period_label": "2026-02.b",
    }
    return producer, charge


def test_bdnb_diff_fails_when_an_expected_departement_is_missing(tmp_path, capsys):
    # 44 and 85 asked to count; neither the archive nor the base has 85.
    producer, charge = _only_44(tmp_path, capsys, "--departements", "44,85")
    charge["effective_departments"] = ["44"]
    code, result = _diff_cli(capsys, tmp_path, producer, charge)
    assert code == 1
    assert result["metriques"]["bdnb_batiment_parcelle"]["85"]["classe"] == (
        "absent_des_deux"
    )
    for side in ("producteur", "base"):
        assert any(
            e.startswith(f"{side}: metrique") and "'85'" in e for e in result["erreurs"]
        )

    # The generation's perimeter alone (base) names 85 too.
    producer, charge = _only_44(tmp_path, capsys)
    charge["effective_departments"] = ["44", "85"]
    assert _diff_cli(capsys, tmp_path, producer, charge)[0] == 1
    # And diff --departements.
    charge["effective_departments"] = ["44"]
    p_path, l_path = tmp_path / "p.json", tmp_path / "l.json"
    p_path.write_text(json.dumps(producer), encoding="utf-8")
    l_path.write_text(json.dumps(charge), encoding="utf-8")
    argv = ["diff", "--producer", str(p_path), "--loaded", str(l_path)]
    assert fn.main([*argv, "--departements", "44,85"]) == 1
    capsys.readouterr()
    # Without any perimeter, the diff cannot say what it checked.
    del charge["effective_departments"]
    assert _diff_cli(capsys, tmp_path, producer, charge)[0] == 1


def test_bdnb_declared_out_of_coverage_departement_is_not_expected(tmp_path, capsys):
    producer, charge = _only_44(tmp_path, capsys, "--departements", "44,85")
    charge["effective_departments"] = ["44"]
    declared = [
        {"metric": "*", "departement": "85", "etat": "hors_couverture", "motif": "x"}
    ]
    result = fn.diff(producer, charge, explanations=declared)
    assert result["erreurs"] == []
    assert result["metriques"]["bdnb_batiment_parcelle"]["85"]["classe"] == (
        "hors_couverture"
    )
    assert result["metriques"]["bdnb_batiment_parcelle"]["44"]["classe"] == "egal"


def test_bdnb_collect_loaded_and_metadata():
    class Cursor(FakeCursor):
        def fetchone(self):
            return ("2026-02.b", ["85", "44"])

    rows = [("85", "bdnb_batiment_parcelle", 17), ("85", "bdnb:principales", 16)]
    cursor = Cursor([rows])
    assert fn.collect_loaded(cursor, BDNB_DATASET) == {
        "85": {"bdnb_batiment_parcelle": 17, "bdnb:principales": 16}
    }
    assert fn.collect_metadata(cursor, BDNB_DATASET) == {
        "source_period_label": "2026-02.b",
        "effective_departments": ["44", "85"],
    }
    assert "dataset_metadata" in cursor.queries[-1]


# ---------------------------------------------------- bdnb: live ranges --


class _Response:
    def __init__(self, url, status, headers, body):
        self.url, self.status, self.headers, self.body = url, status, headers, body

    def read(self):
        return self.body

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def _serve_ranges(monkeypatch, objects, statuses=None, ignore_range=()):
    """urlopen over in-memory objects: byte ranges answered 206, an error
    status per URL, or the whole object (200) for the URLs of *ignore_range*."""
    asked: list[tuple[str, str]] = []
    etags = dict.fromkeys(objects, '"etag-1"')

    def urlopen(request, timeout):
        url, spec = request.full_url, request.get_header("Range")
        asked.append((url, spec))
        status = (statuses or {}).get(url)
        if status is not None:
            raise fn.urllib.error.HTTPError(url, status, "x", {}, None)
        data = objects[url]
        headers = {"ETag": etags[url], "Last-Modified": LAST_MODIFIED}
        if url in ignore_range:
            return _Response(url, 200, headers, data)
        first, last = spec.removeprefix("bytes=").split("-")
        if first == "":
            start, end = max(0, len(data) - int(last)), len(data) - 1
        else:
            start, end = int(first), min(int(last), len(data) - 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
        return _Response(url, 206, headers, data[start : end + 1])

    monkeypatch.setattr(fn.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(fn.time, "sleep", lambda _: None)
    return asked, etags


def _url(dept: str) -> str:
    return fn.BDNB_EXPORT_URL_TEMPLATE.format(millesime="2026-02-b", dept=dept)


def _bdnb_live_objects(tmp_path, exports) -> dict[str, bytes]:
    zips = _bdnb_zips(tmp_path / "s3", exports)
    return {_url(dept.lower()): path.read_bytes() for dept, path in zips.items()}


def _live(capsys, departements, *extra):
    return _run_cli(
        capsys,
        [
            "count",
            "--source",
            "bdnb",
            "--millesime",
            "2026-02-b",
            "--departements",
            departements,
            *extra,
        ],
    )


def test_bdnb_live_ranges_count_like_the_archive(tmp_path, capsys, monkeypatch):
    from tests.etl.bdnb_fixture import GROUPE, LABEL, mini_exports

    exports = mini_exports()
    # A large incompressible member that the count must never download.
    exports["85"] = replace_raw(exports["85"], GROUPE, os.urandom(400_000))
    objects = _bdnb_live_objects(tmp_path, exports)
    _serve_ranges(monkeypatch, objects, statuses={_url("971"): 403, _url("13"): 500})
    live = _live(capsys, "2A,44,85,971,13")
    _bdnb_archive(tmp_path / "archive", exports)
    archived = _bdnb_count(capsys, tmp_path / "archive")

    assert live["mode"] == "producteur"
    assert live["counts"] == archived["counts"]
    assert live["millesime"] == LABEL
    # 403 on this bucket = the object does not exist; any other error = panne.
    assert live["absent"]["971"] == {
        "etat": "absent_producteur",
        "status": 403,
        "url": _url("971"),
    }
    assert set(live["pannes"]) == {"13"} and "HTTP 500" in live["pannes"]["13"]
    zip85 = live["details"]["zips"]["85"]
    assert zip85["content_length"] == len(objects[_url("85")])
    assert zip85["etag"] == '"etag-1"' and zip85["last_modified"] == LAST_MODIFIED
    assert zip85["etiquette"] == LABEL
    assert zip85["octets_lus"] < zip85["content_length"] / 2
    assert live["details"]["content_length_total"] == sum(
        len(objects[_url(d)]) for d in ("2a", "44", "85")
    )


def test_bdnb_live_whole_object_answer_or_changed_etag_is_a_failure(
    tmp_path, capsys, monkeypatch
):
    from tests.etl.bdnb_fixture import GROUPE, mini_exports

    exports = mini_exports()
    exports["85"] = replace_raw(exports["85"], GROUPE, os.urandom(400_000))
    objects = _bdnb_live_objects(tmp_path, exports)
    _asked, etags = _serve_ranges(monkeypatch, objects, ignore_range=(_url("44"),))
    real_range = fn.Http.range
    versions = iter(range(100))

    def changing(self, url, spec):
        answer = real_range(self, url, spec)
        if url == _url("85"):
            # Replaced between any two ranges, retries included.
            etags[url] = f'"etag-{next(versions)}"'
        return answer

    monkeypatch.setattr(fn.Http, "range", changing)
    payload = _live(capsys, "44,85")
    assert "without a byte range" in payload["pannes"]["44"]
    assert "object changed" in payload["pannes"]["85"]
    assert payload["counts"] == {}


def test_bdnb_live_cut_body_is_retried_then_a_named_failure(
    tmp_path, capsys, monkeypatch
):
    import http.client

    from tests.etl.bdnb_fixture import mini_exports

    objects = _bdnb_live_objects(tmp_path, mini_exports())
    _serve_ranges(monkeypatch, objects)
    served = fn.urllib.request.urlopen
    cuts = {"44": 1, "85": 99}

    def cutting(request, timeout):
        response = served(request, timeout)
        dept = next(d for d in cuts if f"_dep{d}_" in request.full_url)
        if cuts[dept] > 0:
            cuts[dept] -= 1

            def read():
                raise http.client.IncompleteRead(b"", 10)

            response.read = read
        return response

    monkeypatch.setattr(fn.urllib.request, "urlopen", cutting)
    payload = _live(capsys, "44,85")
    # One cut on 44: the per-file count restarts and succeeds.
    assert set(payload["counts"]) == {"44"}
    # Cut every time on 85: a named failure, never a crash nor a zero.
    assert "IncompleteRead" in payload["pannes"]["85"]


def test_bdnb_live_label_other_than_the_target_is_a_failure(
    tmp_path, capsys, monkeypatch
):
    from tests.etl.bdnb_fixture import mini_exports, with_label

    exports = mini_exports()
    exports["44"] = with_label(exports["44"], "2026-02.a")
    _serve_ranges(monkeypatch, _bdnb_live_objects(tmp_path, exports))
    payload = _live(capsys, "44,85")
    assert payload["pannes"] == {"44": "BDNB: millésime 2026-02.a, attendu 2026-02.b"}
    assert set(payload["counts"]) == {"85"}


def test_bdnb_live_needs_a_well_formed_millesime():
    with pytest.raises(SystemExit, match="AAAA-MM-x"):
        fn.main(["count", "--source", "bdnb", "--departements", "85"])


def test_bdnb_floors_use_relations_read_and_publishable_rows(tmp_path):
    producteurs = tmp_path / "producteurs"
    producteurs.mkdir()
    fixtures = SCRIPT.parents[1] / "tests" / "fixtures" / "producteurs"
    for path in fixtures.glob("*.json"):
        if not path.name.startswith("bdnb-"):
            (producteurs / path.name).write_bytes(path.read_bytes())
    payload = {
        "captured_at": "2026-11-02T00:00:00Z",
        "counts": {
            "85": {"bdnb:brut:relations_fichier": 10, "bdnb_batiment_parcelle": 9}
        },
    }
    (producteurs / "bdnb-20261102.json").write_text(json.dumps(payload))
    namespace: dict = {}
    exec(fn.floors_module(producteurs, "ab" * 32), namespace)
    assert namespace["PRODUCER_COUNTS"]["bdnb_batiment_parcelle"] == {
        "lignes_importees": {"85": 10},
        "lignes_stockees": {"85": 9},
    }


def test_bdnb_spaced_column_names_are_counted_like_the_etl_reads_them(tmp_path, capsys):
    """Review Opus 4: the ETL strips the column names for its check and for
    the keys of its rows; the count reads them the same way."""
    from app.etl_bdnb.transform import transform_export
    from tests.etl.bdnb_fixture import export_name, mini_exports, with_spaced_headers

    export = mini_exports()["85"]
    _bdnb_archive(tmp_path / "propre", {"85": export})
    _bdnb_archive(tmp_path / "espaces", {"85": with_spaced_headers("85", export)})
    propre = _bdnb_count(capsys, tmp_path / "propre")
    espaces = _bdnb_count(capsys, tmp_path / "espaces")

    assert espaces["pannes"] == {}
    assert espaces["counts"] == propre["counts"]
    zip_path = tmp_path / "espaces" / "built" / export_name("85")
    comptes = transform_export(zip_path, None, "85")
    raw = espaces["counts"]["85"]
    assert raw["bdnb:brut:relations_fichier"] == comptes.relations_lues
    assert raw["bdnb:brut:ffo_fichier"] == comptes.ffo_lues
