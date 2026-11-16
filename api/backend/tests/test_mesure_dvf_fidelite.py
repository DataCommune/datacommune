"""scripts/mesure_dvf_fidelite.py on a synthetic archive (plan 04.4-03).

The archive is written under ``tmp_path`` in the layout of the source archive
(``runs/<run>.json`` and content-addressed objects). Each ``SYN-`` mutation
of the fixture hits one set of the 2026-10-19 measure; the expected counts are
derived by hand in the comments, never by calling the script's own helpers.
"""

import csv
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.etl_dvf.config import COLUMNS_TO_KEEP
from scripts import mesure_dvf_fidelite as m

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[1]
RUN = "48-20261019T171140Z"
NULL = "\\N"

_MAISON = {"code_type_local": "1", "type_local": "Maison"}
_APPART = {"code_type_local": "2", "type_local": "Appartement"}
_DEPENDANCE = {"code_type_local": "3", "type_local": "Dépendance"}


def _l(id_mutation, parcelle, geo=True, **champs):
    row = dict.fromkeys(COLUMNS_TO_KEEP, "")
    row.update(
        id_mutation=id_mutation,
        date_mutation="2021-06-15",
        numero_disposition="000001",
        nature_mutation="Vente",
        valeur_fonciere="200000",
        adresse_numero="1",
        adresse_nom_voie="SYN-VOIE",
        code_postal="44000",
        code_commune="44109",
        nom_commune="SYN-COMMUNE",
        id_parcelle=parcelle,
    )
    if geo:
        row.update(longitude="-1.55", latitude="47.21")
    row.update(champs)
    return row


def _bati(kind, surface, pieces):
    return {**kind, "surface_reelle_bati": surface, "nombre_pieces_principales": pieces}


ROWS_44 = [
    # A: the house has no coordinates, the land does: served as terrain_seul.
    _l("SYN-A", "44SYN000000001", geo=False, **_bati(_MAISON, "100", "4")),
    _l("SYN-A", "44SYN000000002", surface_terrain="500"),
    # B: two distinct houses, one lost: a price invented, local distinct lost.
    _l("SYN-B", "44SYN000000003", **_bati(_MAISON, "90", "4")),
    _l("SYN-B", "44SYN000000004", geo=False, **_bati(_MAISON, "60", "3")),
    # C: the same house on two parcels, one lost: local repeated by parcel.
    _l("SYN-C", "44SYN000000005", **_bati(_MAISON, "131", "5")),
    _l("SYN-C", "44SYN000000006", geo=False, **_bati(_MAISON, "131", "5")),
    # D: multi-commune, the first line is in the commune of higher code.
    _l(
        "SYN-D",
        "44SYN000000007",
        code_commune="44190",
        nom_commune="SYN-AUTRE",
        adresse_nom_voie="SYN-VOIE-9",
        code_postal="44700",
        **_bati(_APPART, "50", "2"),
    ),
    _l("SYN-D", "44SYN000000008", **_DEPENDANCE),
    # E: no geocoded line, no parcel, the latest date of the file.
    _l("SYN-E", "", geo=False, date_mutation="2021-12-28", **_bati(_APPART, "40", "2")),
    # F: two identical houses on two parcels, all geocoded: D-02 merges them.
    _l("SYN-F", "44SYN000000010", **_bati(_MAISON, "80", "4")),
    _l("SYN-F", "44SYN000000011", **_bati(_MAISON, "80", "4")),
    # K: a geocoded flat sold on 2020-12-25: inside the served window (from
    # 2020-12-20, H being the latest served mutation), outside the window after
    # the reimport (from 2020-12-28, E being the latest of all).
    _l(
        "SYN-K",
        "44SYN000000012",
        date_mutation="2020-12-25",
        **_bati(_APPART, "50", "2"),
    ),
]
ROWS_85 = [
    _l(
        "SYN-G",
        "85SYN000000001",
        code_commune="85191",
        code_postal="85000",
        **_bati(_APPART, "45", "2"),
    ),
    # H: two natures; max() of the base and first-filled of compte_lignes differ.
    _l(
        "SYN-H",
        "85SYN000000002",
        code_commune="85191",
        date_mutation="2021-12-20",
        **_bati(_APPART, "45", "2"),
    ),
    _l(
        "SYN-H",
        "85SYN000000003",
        code_commune="85191",
        date_mutation="2021-12-20",
        nature_mutation="Vente en l'état futur d'achèvement",
        **_DEPENDANCE,
    ),
    # I: a land line without commune code (departement 00 in the ETL).
    _l("SYN-I", "85SYN000000004", code_commune="", surface_terrain="300"),
    # J: one house on one parcel, repeated on its culture lines S and J, all
    # geocoded: two locals for the naive count and for D-02 (same parcel),
    # one under variant V (max over the pairs parcel, culture).
    _l(
        "SYN-J",
        "85SYN000000005",
        code_commune="85191",
        code_nature_culture="S",
        surface_terrain="400",
        **_bati(_MAISON, "96", "4"),
    ),
    _l(
        "SYN-J",
        "85SYN000000005",
        code_commune="85191",
        code_nature_culture="J",
        surface_terrain="150",
        **_bati(_MAISON, "96", "4"),
    ),
]


def _csv_bytes(rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS_TO_KEEP, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def write_archive(root, files, run=RUN):
    """Source-archive layout: ``runs/<run>.json`` and ``objects/<aa>/<sha>``."""
    entries = []
    for relpath, rows in files.items():
        raw = _csv_bytes(rows)
        sha = hashlib.sha256(raw).hexdigest()
        obj = root / "objects" / sha[:2] / sha
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(raw)
        entries.append(
            {
                "relpath": relpath,
                "name": relpath.rsplit("/", 1)[-1],
                "bytes": len(raw),
                "sha256": sha,
                "object": f"objects/{sha[:2]}/{sha}",
            }
        )
    (root / "runs").mkdir(parents=True, exist_ok=True)
    (root / "runs" / f"{run}.json").write_text(
        json.dumps({"run_id": run, "files": entries}), encoding="utf-8"
    )
    return root


@pytest.fixture
def archive(tmp_path):
    return write_archive(
        tmp_path / "dvf", {"raw/2021/44.csv": ROWS_44, "raw/2021/85.csv": ROWS_85}
    )


def _empreintes(archive):
    out = io.StringIO()
    m.mode_empreintes(str(archive), RUN, out)
    rows = list(csv.reader(io.StringIO(out.getvalue())))
    return {(r[0], r[1]): r for r in rows}


# -- archive -------------------------------------------------------------------


def test_control_block_reproduces_the_logic_of_the_2026_10_19_annex(archive):
    c = m.mode_archive(str(archive), RUN)["controle_2026_10_19"]
    assert c["rows_total"] == 18
    assert c["rows_geocoded"] == 14  # every line but A1, B2, C2, E
    assert c["mutations"] == 11
    assert c["sans_aucune_ligne_geocodee"] == 1  # E
    assert c["partiellement_geocodees"] == 3  # A, B, C
    assert c["A5b01_classe_changee"] == 3
    assert c["A5b01_vente_residentielle_devenue_non_residentielle"] == 1  # A
    assert c["A5b01_prix_m2_invente"] == 2  # B, C
    assert c["A5b01_prix_invente_dont_local_distinct_perdu"] == 1  # B
    assert c["A5b01_prix_invente_dont_local_repete_par_parcelle"] == 1  # C
    assert c["A5b02_multi_communes"] == 1  # D
    assert c["A5b02_adresse_et_commune_de_lignes_differentes"] == 1
    assert c["A5b02_dont_ventes_residentielles"] == 1
    assert c["A5b01_transitions"] == {
        "residentiel_maison -> terrain_seul": 1,
        "ensemble_residentiel -> residentiel_maison": 2,
    }


def test_rules_block_states_each_set_under_the_reference(archive):
    r = m.mode_archive(str(archive), RUN)["regles_04_4"]
    assert r["vente_residentielle_devenue_non_residentielle"] == {
        "total": 1,
        "encore_non_residentielles": 0,
    }
    assert r["prix_invente_local_distinct_perdu"] == {
        "total": 1,
        "prix_encore_calcule": 0,
    }
    assert r["local_repete_par_parcelle"] == {"total": 1, "mono_local_avec_prix": 1}
    assert r["adresse_commune_differente"] == {"total": 1, "encore_differentes": 0}
    assert r["sans_aucune_ligne_geocodee"] == {
        "total": 1,
        "presentes_sans_position": 1,
    }
    # The deduplication alone (variant V against one local per line), all
    # lines: C and F lose their second house (two parcels), J its second
    # culture line; B keeps two distinct houses.
    assert r["dedup_seul"] == {
        "compte": 3,
        "transitions": {"ensemble_residentiel -> residentiel_maison": 3},
        "fusions_maison_signature_complete": 3,
        "fusions_maison_dont_classe_changee": 3,
    }
    # Variant V against D-02 alone (culture ignored): only J, whose two lines
    # share one parcel, changes class (decision of Ianis, 2026-10-21).
    assert r["reclasse_par_v"] == {
        "compte": 1,
        "transitions": {"ensemble_residentiel -> residentiel_maison": 1},
    }
    assert r["classes"] == {
        "ensemble_residentiel": 1,
        "residentiel_appartement": 5,
        "residentiel_maison": 4,
        "terrain_seul": 1,
    }


def test_summary_carries_the_source_facts_and_no_identifier(archive):
    s = m.mode_archive(str(archive), RUN)
    assert s["run"] == RUN
    assert [f["relpath"] for f in s["fichiers"]] == [
        "raw/2021/44.csv",
        "raw/2021/85.csv",
    ]
    r = s["regles_04_4"]
    assert r["natures_divergentes"] == 1  # H
    assert r["lignes_sans_code_commune"] == 1  # I
    assert r["lignes_locaux_sans_parcelle"] == 1  # E
    assert r["date_max_toutes_lignes"] == "2021-12-28"  # E, no coordinates
    assert r["date_max_lignes_geocodees"] == "2021-12-20"  # H
    assert r["multi_communes_toutes_lignes"] == 1
    assert r["valeurs_ge_1e10"] == 0
    ids = "\n".join(["44|SYN-C"])
    assert r["ensembles_sha256"]["local_repete_par_parcelle"] == (
        hashlib.sha256(ids.encode("utf-8")).hexdigest()
    )
    text = json.dumps(s, ensure_ascii=False)
    assert "SYN-" not in text  # no mutation id, no address, no parcel


def test_a_tampered_object_is_refused(archive):
    record = json.loads((archive / "runs" / f"{RUN}.json").read_text("utf-8"))
    obj = archive / record["files"][0]["object"]
    obj.write_bytes(obj.read_bytes() + b"x")
    with pytest.raises(SystemExit, match="sha256"):
        m.mode_archive(str(archive), RUN)


# -- empreintes ----------------------------------------------------------------


def test_fingerprint_line_has_a_fixed_order_null_marker_and_hashed_address(archive):
    rows = _empreintes(archive)
    assert len(rows) == 11
    c = dict(
        zip([*m.EMPREINTE_COLONNES, "ensembles"], rows[("44", "SYN-C")], strict=True)
    )
    assert c["classe_mutation"] == "residentiel_maison"
    assert c["nb_locaux_maison"] == "1"
    assert c["nb_lignes"] == "2"
    assert c["surface_bati_totale"] == "131"
    assert c["valeur_fonciere"] == "200000.00"
    assert c["prix_m2"] == "1527"  # round(200000 / 131)
    assert c["mono_local"] == "true"
    assert c["position_connue"] == "true"
    assert (
        c["adresse_sha256"]
        == hashlib.sha256(b"SYN-COMMUNE|1|\\N|SYN-VOIE|44000").hexdigest()
    )
    assert c["ensembles"] == "local_repete_par_parcelle"
    e = dict(
        zip([*m.EMPREINTE_COLONNES, "ensembles"], rows[("44", "SYN-E")], strict=True)
    )
    assert e["position_connue"] == "false"
    assert e["surface_terrain_totale"] == NULL
    assert e["ensembles"] == "sans_aucune_ligne_geocodee"
    # D-04: the address of D is read in its retained commune (44109).
    d = dict(zip(m.EMPREINTE_COLONNES, rows[("44", "SYN-D")][:-1], strict=True))
    assert d["code_commune"] == "44109"
    assert (
        d["adresse_sha256"]
        == hashlib.sha256(b"SYN-COMMUNE|1|\\N|SYN-VOIE|44000").hexdigest()
    )
    assert rows[("44", "SYN-F")][-1] == NULL
    # The nature is the max() of the base aggregation (H).
    h = dict(zip(m.EMPREINTE_COLONNES, rows[("85", "SYN-H")][:-1], strict=True))
    assert h["nature_mutation"] == "Vente en l'état futur d'achèvement"
    assert ("00", "SYN-I") in rows
    # V: J is one house of 96 m2 and 4 rooms, its two land surfaces summed.
    j = dict(zip(m.EMPREINTE_COLONNES, rows[("85", "SYN-J")][:-1], strict=True))
    assert j["classe_mutation"] == "residentiel_maison"
    assert j["nb_locaux_maison"] == "1"
    assert j["surface_bati_totale"] == "96"
    assert j["nombre_pieces_totales"] == "4"
    assert j["surface_terrain_totale"] == "550"
    assert j["nb_parcelles"] == "1"
    assert j["prix_m2"] == "2083"  # round(200000 / 96)


def test_concatenated_on_stdin_with_the_standard_library_only(archive):
    """The host form: qualification.py + script piped to an isolated python."""
    source = (BACKEND / "app/etl_dvf/qualification.py").read_bytes() + (
        BACKEND / "scripts/mesure_dvf_fidelite.py"
    ).read_bytes()
    done = subprocess.run(
        [sys.executable, "-I", "-", "archive", "--racine", str(archive), "--run", RUN],
        input=source,
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert json.loads(done.stdout) == json.loads(
        json.dumps(m.mode_archive(str(archive), RUN))
    )


# -- sql-base ------------------------------------------------------------------


def test_fingerprint_sql_is_a_vetted_select(capsys):
    m.main(["sql-base"])
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("COPY (SELECT ")
    assert printed.endswith(") TO STDOUT WITH (FORMAT csv);")
    for word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"):
        assert word not in m.EMPREINTE_SQL.upper()


def test_fingerprint_price_follows_the_served_formula():
    from app.dvf.queries import _PRIX_M2_SQL

    assert " ".join(_PRIX_M2_SQL.split()) in " ".join(m.EMPREINTE_SQL.split())


# -- compare -------------------------------------------------------------------


def _write_base(tmp_path, rows, alter=None):
    path = tmp_path / "base.csv"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        for key, row in rows.items():
            row = row[:-1]  # the base has no set labels
            if alter and key in alter:
                row = alter[key](row)
            writer.writerow(row)
    return path


def _write_reference(tmp_path, archive):
    path = tmp_path / "reference.csv"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        m.mode_empreintes(str(archive), RUN, handle)
    summary = tmp_path / "resume.json"
    summary.write_text(json.dumps(m.mode_archive(str(archive), RUN)), "utf-8")
    return path, summary


def test_compare_identical_fingerprints_has_no_gap(tmp_path, archive):
    reference, summary = _write_reference(tmp_path, archive)
    base = _write_base(tmp_path, _empreintes(archive))
    report = m.mode_compare(str(base), str(reference), str(summary))
    assert set(report["ecarts"].values()) == {0}
    assert report["ensembles"]["local_repete_par_parcelle"] == {
        "total": 1,
        "mono_local_avec_prix": 1,
        "attendu": 1,
    }
    assert report["ensembles"]["sans_aucune_ligne_geocodee"] == {
        "total": 1,
        "presentes_sans_position": 1,
        "attendu": 1,
    }
    assert report["exemples"] == []


def test_compare_sees_an_altered_class_and_recounts_the_sets(tmp_path, archive):
    reference, summary = _write_reference(tmp_path, archive)
    i = m.EMPREINTE_COLONNES.index("classe_mutation")

    def to_ensemble(row):
        return [*row[:i], "ensemble_residentiel", *row[i + 1 :]]

    rows = _empreintes(archive)
    missing = rows.pop(("85", "SYN-G"))
    base = _write_base(tmp_path, rows, alter={("44", "SYN-C"): to_ensemble})
    report = m.mode_compare(str(base), str(reference), str(summary))
    assert report["ecarts"]["classe_mutation"] == 1
    assert report["ecarts"]["prix_m2"] == 0
    assert report["ecarts"]["mutations_manquantes"] == 1
    assert report["ecarts"]["mutations_en_trop"] == 0
    assert report["ensembles"]["local_repete_par_parcelle"]["mono_local_avec_prix"] == 0
    assert len(report["exemples"]) <= 20
    assert missing[1] not in json.dumps(report["ensembles"])


# -- communes (task 3) ---------------------------------------------------------
#
# v_commune_dvf keeps the mono-local residential sales (no VEFA), of one
# commune, with a positive surface and amount, dated from max(date_mutation)
# minus 12 months. Derived by hand on the fixture:
#
# served (geocoded lines, one local per line; window from 2020-12-20, H):
#   44109: B (maison 90, its second house lost), C (maison, one line kept),
#          K (appartement) -- A is terrain_seul, F an ensemble, E absent;
#   85191: G (appartement) -- H is a VEFA, J an ensemble.
# after (D-01 to D-04, V; window from 2020-12-28, E):
#   44109: A (its house back), C, F (V merges), E (no position, D-03);
#          B is an ensemble of two houses, K left the window;
#   85191: G, J (V merges its culture lines).
# The chain, one cause per step (entries / exits per commune):
#   reclassement_d01: 44109 +A -B -C (C counts two houses on all lines);
#   dedoublonnage_d02: 44109 +C +F, 85191 +J;
#   ajout_sans_position_d03: 44109 +E;
#   fenetre: 44109 -K.


def _sans_population(etat):
    return {k: v for k, v in etat.items() if k != "population_sha256"}


def test_archive_predicts_v_commune_dvf_per_commune_in_both_states(archive):
    s = m.mode_archive(str(archive), RUN)
    c = s["communes"]
    assert set(c) == {"44109", "85191"}
    assert _sans_population(c["44109"]["servi"]) == {
        "nb_mutations_12m": 3,
        "nb_mutations_12m_maison": 2,
        "nb_mutations_12m_appartement": 1,
    }
    assert _sans_population(c["44109"]["apres"]) == {
        "nb_mutations_12m": 4,
        "nb_mutations_12m_maison": 3,
        "nb_mutations_12m_appartement": 1,
    }
    population = "\n".join(
        sorted(
            [
                "44|SYN-A|residentiel_maison",
                "44|SYN-C|residentiel_maison",
                "44|SYN-E|residentiel_appartement",
                "44|SYN-F|residentiel_maison",
            ]
        )
    )
    assert c["44109"]["apres"]["population_sha256"] == (
        hashlib.sha256(population.encode("utf-8")).hexdigest()
    )
    assert c["44109"]["causes"] == {
        "reclassement_d01": {"entrees": 1, "sorties": 2},
        "dedoublonnage_d02": {"entrees": 2, "sorties": 0},
        "ajout_sans_position_d03": {"entrees": 1, "sorties": 0},
        "fenetre": {"entrees": 0, "sorties": 1},
    }
    assert c["85191"]["servi"]["nb_mutations_12m"] == 1
    assert c["85191"]["apres"]["nb_mutations_12m_maison"] == 1
    assert c["85191"]["causes"] == {"dedoublonnage_d02": {"entrees": 1, "sorties": 0}}
    assert s["fenetres_communes"] == {
        "servi": {"date_max": "2021-12-20", "debut": "2020-12-20"},
        "apres": {"date_max": "2021-12-28", "debut": "2020-12-28"},
    }
    assert "SYN-" not in json.dumps(s, ensure_ascii=False)


def test_commune_chain_adds_up_from_served_to_after(archive):
    for commune in m.mode_archive(str(archive), RUN)["communes"].values():
        net = sum(v["entrees"] - v["sorties"] for v in commune["causes"].values())
        assert (
            commune["servi"]["nb_mutations_12m"] + net
            == (commune["apres"]["nb_mutations_12m"])
        )


@pytest.mark.parametrize(
    ("date", "debut"),
    [
        ("2025-12-31", "2024-12-31"),
        ("2024-02-29", "2023-02-28"),  # the day bounded by the end of the month
        ("2021-12-28", "2020-12-28"),
    ],
)
def test_window_start_follows_postgresql_month_arithmetic(date, debut):
    assert m.debut_fenetre(date) == debut


def test_view_filters_are_the_served_ones():
    from app.commune.matview_ddl import INTERMEDIATE_MATVIEWS
    from app.dvf.couverture import CLASSES_STATISTIQUES

    assert m.CLASSES_STATISTIQUES == CLASSES_STATISTIQUES
    ddl = " ".join(dict(INTERMEDIATE_MATVIEWS)["v_commune_dvf"].split())
    for filtre in (
        "max(date_mutation) FROM mutations_grain ) - INTERVAL '12 months'",
        "AND est_vente = true AND hors_stats_vefa = false",
        "AND nb_communes = 1 AND surface_bati_totale > 0 AND valeur_fonciere > 0",
        "GROUP BY code_commune",
    ):
        assert filtre in ddl


def test_snapshot_sql_is_a_vetted_select(capsys):
    m.main(["sql-base", "--communes"])
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("COPY (SELECT code_insee, nb_mutations_12m")
    assert "FROM v_commune_dvf" in printed
    assert printed.endswith(") TO STDOUT WITH (FORMAT csv);")


# -- kpi -----------------------------------------------------------------------


def _snapshot(tmp_path, nom, communes):
    """CSV of SNAPSHOT_COMMUNES_SQL: COPY csv, no header, NULL empty."""
    path = tmp_path / f"{nom}.csv"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        for code, valeurs in communes.items():
            writer.writerow([code, *("" if v is None else v for v in valeurs)])
    return path


# (nb, maison, appartement, median, median maison, median appartement)
_AVANT = {"44109": (3, 2, 1, None, None, None), "85191": (1, 0, 1, None, None, None)}
_APRES = {"44109": (4, 3, 1, None, None, None), "85191": (2, 1, 1, None, None, None)}


def _reference_json(tmp_path, archive):
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(m.mode_archive(str(archive), RUN)), "utf-8")
    return path


def test_kpi_served_state_only_before_the_reimport(tmp_path, archive):
    reference = _reference_json(tmp_path, archive)
    avant = _snapshot(tmp_path, "avant", _AVANT)
    report = m.mode_kpi(str(avant), None, str(reference))
    assert report == {
        "communes_comparees": 2,
        "ecarts_comptes_servi": 0,
        "inexpliques": [],
    }


def test_kpi_before_and_after_names_every_cause(tmp_path, archive):
    reference = _reference_json(tmp_path, archive)
    report = m.mode_kpi(
        str(_snapshot(tmp_path, "avant", _AVANT)),
        str(_snapshot(tmp_path, "apres", _APRES)),
        str(reference),
    )
    assert report["communes_comparees"] == 2
    assert report["ecarts_comptes_servi"] == 0
    assert report["ecarts_comptes_apres"] == 0
    assert report["medianes_changees"] == 0
    assert report["causes"] == {
        "reclassement_d01": {"communes": 1, "entrees": 1, "sorties": 2},
        "dedoublonnage_d02": {"communes": 2, "entrees": 3, "sorties": 0},
        "ajout_sans_position_d03": {"communes": 1, "entrees": 1, "sorties": 0},
        "fenetre": {"communes": 1, "entrees": 0, "sorties": 1},
    }
    assert report["inexpliques"] == []


def test_kpi_flags_a_count_that_differs_from_the_prediction(tmp_path, archive):
    reference = _reference_json(tmp_path, archive)
    apres = {**_APRES, "44109": (5, 3, 2, None, None, None)}
    report = m.mode_kpi(
        str(_snapshot(tmp_path, "avant", _AVANT)),
        str(_snapshot(tmp_path, "apres", apres)),
        str(reference),
    )
    assert report["ecarts_comptes_apres"] == 1
    assert report["inexpliques"] == [
        {"code_insee": "44109", "raison": "compte_apres_different_de_la_prediction"}
    ]


def _etat(nb, maison, appart, population):
    return {
        "nb_mutations_12m": nb,
        "nb_mutations_12m_maison": maison,
        "nb_mutations_12m_appartement": appart,
        "population_sha256": population,
    }


def test_kpi_flags_a_median_moved_without_a_predicted_population_change(tmp_path):
    communes = {
        # same population in both states: a moved median is unexplained
        "44001": {
            "servi": _etat(6, 6, 0, "p1"),
            "apres": _etat(6, 6, 0, "p1"),
            "causes": {},
        },
        # the population changes by D-02: a moved median has a cause
        "44002": {
            "servi": _etat(6, 5, 1, "p2"),
            "apres": _etat(6, 6, 0, "p3"),
            "causes": {"dedoublonnage_d02": {"entrees": 1, "sorties": 1}},
        },
    }
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps({"run": RUN, "communes": communes}), "utf-8")
    avant = {"44001": (6, 6, 0, 3000, 3000, None), "44002": (6, 5, 1, 2500, 2400, None)}
    apres = {"44001": (6, 6, 0, 3100, 3100, None), "44002": (6, 6, 0, 2600, 2600, None)}
    report = m.mode_kpi(
        str(_snapshot(tmp_path, "avant", avant)),
        str(_snapshot(tmp_path, "apres", apres)),
        str(reference),
    )
    assert report["medianes_changees"] == 2
    assert report["inexpliques"] == [
        {
            "code_insee": "44001",
            "raison": "mediane_changee_sans_changement_de_population",
        }
    ]
    assert report["causes"] == {
        "dedoublonnage_d02": {"communes": 1, "entrees": 1, "sorties": 1}
    }


def test_kpi_names_the_edition_when_the_served_state_comes_from_another_run(
    tmp_path,
):
    def ecrit(nom, run, servi, apres):
        path = tmp_path / f"{nom}.json"
        commune = {"servi": servi, "apres": apres, "causes": {}}
        path.write_text(json.dumps({"run": run, "communes": {"44001": commune}}))
        return path

    servi_run = ecrit("servi", "34-x", _etat(5, 5, 0, "p1"), _etat(5, 5, 0, "p1"))
    reimport = ecrit("reimport", "49-y", _etat(6, 6, 0, "p2"), _etat(6, 6, 0, "p2"))
    report = m.mode_kpi(
        str(_snapshot(tmp_path, "avant", {"44001": (5, 5, 0, 3000, 3000, None)})),
        str(_snapshot(tmp_path, "apres", {"44001": (6, 6, 0, 3050, 3050, None)})),
        str(reimport),
        str(servi_run),
    )
    assert report["ecarts_comptes_servi"] == 0
    assert report["ecarts_comptes_apres"] == 0
    # Without identifiers in the summaries, the edition is a net count.
    assert report["causes"] == {"edition": {"communes": 1, "entrees": 1, "sorties": 0}}
    assert report["inexpliques"] == []
