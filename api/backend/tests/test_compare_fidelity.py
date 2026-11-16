"""Replayable fidelity comparator (plan 02-12, D-25 to D-28).

Every case builds its own reference, candidate, receipts and run-ids in a
temporary directory: the frozen references of ``tests/fixtures/fidelity_ct275``
are copied, never modified. The comparator runs in-process through ``main``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import compare_fidelity as cf
from scripts.extract_fidelity_reference import write_manifest

BACKEND = Path(__file__).parents[1]
FIXTURES = BACKEND / "tests" / "fixtures" / "fidelity_ct275"
HEAD_SHA = subprocess.run(
    ["git", "-C", str(BACKEND), "rev-parse", "HEAD"],
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()
V1_KEYS = sorted(cf.PERIMETRE)


# ------------------------------------------------------------------ helpers --


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _rehash(directory: Path) -> None:
    """Rewrite the manifest of a candidate directory after an edit."""
    manifest = _read(directory / "manifest.json")
    write_manifest(
        directory,
        source_label=manifest["source"]["label"],
        alembic_version=manifest["source"]["alembic_version"],
        methods=manifest["methods"],
        extracted_at=manifest["extracted_at"],
        script_sha256=manifest["script_sha256"],
    )


def _receipt(pipeline: str, run_id: str, sha: str = HEAD_SHA) -> dict[str, Any]:
    return {
        "dataset": pipeline,
        "run_id": run_id,
        "repo_sha": sha,
        "etl_run": {"status": "success", "error_message": None},
        "counting": {"source_rows_unit": "ligne physique CSV"},
        "tables": {},
    }


class Env:
    """A self-contained comparison: reference, candidate, receipts, inputs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.ref = root / "reference"
        self.cand = root / "candidate"
        self.imports = root / "imports"
        shutil.copytree(FIXTURES, self.ref)
        shutil.copytree(FIXTURES, self.cand)
        # The frozen manifest lists DVF history files kept outside the
        # repository; the candidate manifest is rewritten without them.
        _rehash(self.cand)
        self.run_ids: dict[str, Any] = {"pipelines": {}, "alias": dict(cf.ALIASES)}
        for index, pipeline in enumerate(
            sorted(cf.LOCAL_PIPELINES | set(cf.REFERENTIAL_RECEIPTS.values()))
        ):
            run_id = f"{index + 1}-20260925T000000Z"
            self.add_receipt(pipeline, run_id)
            if pipeline in cf.LOCAL_PIPELINES:
                self.run_ids["pipelines"][pipeline] = run_id
        self.run_ids_path = root / "run-ids.json"
        self.classifications: dict[str, Any] = {}
        self.classifications_path = root / "classifications.json"
        self.datasets_md = root / "02-DATASETS.md"
        self.datasets_md.write_text(
            "<!-- v1-keys:start -->\n"
            + ", ".join(V1_KEYS)
            + "\n<!-- v1-keys:end -->\n",
            encoding="utf-8",
        )
        self.out = root / "report.json"

    def add_receipt(
        self,
        pipeline: str,
        run_id: str,
        *,
        manifest: str | None = None,
        sha: str = HEAD_SHA,
        write_receipt: bool = True,
    ) -> None:
        folder = self.imports / pipeline
        folder.mkdir(parents=True, exist_ok=True)
        _write(
            folder / "latest.json",
            {
                "dataset": pipeline,
                "run_id": run_id,
                "manifest": manifest or f"{run_id}.json",
                "repo_sha": sha,
            },
        )
        if write_receipt:
            _write(folder / f"{run_id}.json", _receipt(pipeline, run_id, sha))

    def edit_candidate(self, name: str, mutate) -> None:
        payload = _read(self.cand / name)
        mutate(payload)
        _write(self.cand / name, payload)
        _rehash(self.cand)

    def run(self, *extra: str, run_ids: bool = True, **kw: Any) -> int:
        _write(self.run_ids_path, self.run_ids)
        _write(self.classifications_path, self.classifications)
        argv = [
            "--reference-dir",
            str(self.ref),
            "--candidate-dir",
            str(self.cand),
            "--classifications",
            str(self.classifications_path),
            "--out",
            str(self.out),
            "--imports-dir",
            str(self.imports),
            "--datasets-md",
            str(self.datasets_md),
        ]
        if run_ids:
            argv += ["--run-ids", str(self.run_ids_path)]
        return cf.main(argv + list(extra))

    def report(self) -> dict[str, Any]:
        return _read(self.out)

    def ecart(self, key: str) -> dict[str, Any]:
        matches = [e for e in self.report()["ecarts"] if e["cle"] == key]
        assert len(matches) == 1, (key, [e["cle"] for e in self.report()["ecarts"]])
        return matches[0]

    def classify(self, key: str, categorie: str, preuve: str = "KI-D1", **extra: Any):
        observed = self.ecart(key)
        self.classifications[key] = {
            "categorie": categorie,
            "preuve": preuve,
            "reference_sha256": observed["reference_sha256"],
            "candidate_sha256": observed["candidate_sha256"],
            **extra,
        }


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _set_count(table: str, dept: str, value: int):
    def mutate(payload: dict[str, Any]) -> None:
        payload["tables"][table]["counts"][dept] = value

    return mutate


# --------------------------------------------------------------- baseline --


def test_reference_equal_to_itself_has_no_gap(env: Env) -> None:
    assert env.run() == 0
    report = env.report()
    assert report["ecarts"] == []
    assert report["a_arbitrer"] == []
    assert set(report["datasets"]) == set(cf.DATASET_TABLES)
    assert report["datasets"]["vacance"]["verdict"] == "conforme"
    assert (
        report["datasets"]["gares"]["run_id"]
        == (report["datasets"]["transports"]["run_id"])
    )
    assert report["perimetre"]["rnb"] == "proxy:rnb_liens"
    assert len(report["perimetre"]) == 27
    # Nothing of batiments is in v_commune_synthese: level 2 compared nothing.
    assert report["datasets"]["batiments"]["niveaux"]["2"] == "sans_objet"
    assert report["datasets"]["batiments"]["verdict"] == "conforme"
    # The three tables with a known-false attribution never read as conforme.
    assert report["datasets"]["scolaire"]["niveaux"]["1"] == "comparaison_impossible"
    assert report["verdict_niveaux_1_2"] == "conforme_avec_comparaisons_impossibles"


def test_lovac_false_zeros_are_a_compared_gap(env: Env) -> None:
    env.edit_candidate(
        "lovac_false_zeros.json",
        lambda p: p.update(counts={"total": 0, "44": 0, "85": 0}, codes_44_85=[]),
    )
    # The view masks a zero rate without denominator (taux_vacance null on
    # both sides) and level 1 counts rows: only the KI-D1 recount sees it.
    assert env.run("--dataset", "vacance") == 1
    fz = env.report()["datasets"]["vacance"]["faux_zeros"]
    assert fz["reference"] == {"44": 15, "85": 20}
    assert fz["candidate"] == {"44": 0, "85": 0}
    assert env.ecart("1:lovac_faux_zeros:44")["delta"] == -15
    assert env.ecart("1:lovac_faux_zeros:85")["categorie"] == "inexpliquee"
    env.classify("1:lovac_faux_zeros:44", "correction_voulue", preuve="KI-D1")
    env.classify("1:lovac_faux_zeros:85", "correction_voulue", preuve="KI-D1")
    assert env.run("--dataset", "vacance") == 0
    assert env.report()["datasets"]["vacance"]["verdict"] == "ecarts_expliques"


# ------------------------------------------------------------------ run-ids --


def test_missing_run_ids_exits_2(env: Env) -> None:
    assert env.run("--dataset", "vacance", run_ids=False) == 2


def test_run_ids_without_the_compared_dataset_exits_2(env: Env) -> None:
    del env.run_ids["pipelines"]["vacance"]
    assert env.run("--dataset", "vacance") == 2


def test_run_id_without_resolvable_receipt_exits_2(env: Env) -> None:
    env.run_ids["pipelines"]["vacance"] = "123"
    assert env.run("--dataset", "vacance") == 2


def test_pointer_with_latest_manifest_exits_2(env: Env) -> None:
    run_id = env.run_ids["pipelines"]["vacance"]
    env.add_receipt("vacance", run_id, manifest="latest.json")
    assert env.run("--dataset", "vacance") == 2


def test_pointer_with_divergent_manifest_exits_2(env: Env) -> None:
    env.add_receipt("vacance", "123", manifest="wrong-name.json", write_receipt=False)
    env.run_ids["pipelines"]["vacance"] = "123"
    assert env.run("--dataset", "vacance") == 2


def test_receipt_reduced_to_empty_object_exits_2(env: Env) -> None:
    run_id = env.run_ids["pipelines"]["vacance"]
    _write(env.imports / "vacance" / f"{run_id}.json", {})
    assert env.run("--dataset", "vacance") == 2


def test_repo_sha_outside_git_history_exits_2(env: Env) -> None:
    run_id = env.run_ids["pipelines"]["vacance"]
    env.add_receipt("vacance", run_id, sha="f" * 40)
    assert env.run("--dataset", "vacance") == 2


def test_alias_to_a_missing_pipeline_exits_2(env: Env) -> None:
    del env.run_ids["pipelines"]["transports"]
    assert env.run("--dataset", "vacance") == 2


def test_run_ids_indexed_by_catalog_key_exits_2(env: Env) -> None:
    pipelines = env.run_ids["pipelines"]
    pipelines["rnb"] = pipelines.pop("rnb_liens")
    env.run_ids["alias"] = {"gares": "transports"}
    assert env.run("--dataset", "vacance") == 2


# ------------------------------------------------------------ references --


def test_falsified_reference_manifest_exits_2(env: Env) -> None:
    payload = _read(env.ref / "level1_counts.json")
    payload["tables"]["vacance"]["counts"]["44"] = 1
    _write(env.ref / "level1_counts.json", payload)
    assert env.run("--dataset", "vacance") == 2


def test_incomplete_perimetre_exits_2(env: Env) -> None:
    env.datasets_md.write_text(
        "<!-- v1-keys:start -->\n"
        + ", ".join([*V1_KEYS, "nouvelle_cle"])
        + "\n<!-- v1-keys:end -->\n",
        encoding="utf-8",
    )
    assert env.run("--dataset", "vacance") == 2


def test_perimetre_state_outside_the_closed_list_exits_2(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(cf.PERIMETRE, "permis", "absent")
    assert env.run("--dataset", "vacance") == 2


# ---------------------------------------------------------- classification --


def test_changed_count_without_classification_is_unexplained(env: Env) -> None:
    env.edit_candidate("level1_counts.json", _set_count("vacance", "44", 200))
    assert env.run("--dataset", "vacance") == 1
    gap = env.ecart("1:vacance:44")
    assert gap["categorie"] == "inexpliquee"
    assert gap["delta"] == -7
    assert env.report()["datasets"]["vacance"]["verdict"] == "inexpliquee"


def test_all_unexplained_with_empty_classifications_is_not_a_structure_error(
    env: Env,
) -> None:
    env.edit_candidate("level1_counts.json", _set_count("vacance", "44", 200))
    env.edit_candidate("level1_counts.json", _set_count("vacance", "total", 453))
    assert env.run("--dataset", "vacance") == 1
    report = env.report()
    assert {e["cle"] for e in report["ecarts"]} == {"1:vacance:44", "1:vacance:total"}
    assert {a["cle"] for a in report["a_arbitrer"]} == {
        "1:vacance:44",
        "1:vacance:total",
    }
    assert all(a["dataset"] == "vacance" and a["motif"] for a in report["a_arbitrer"])


def test_classified_gap_with_evidence_is_explained(env: Env) -> None:
    env.edit_candidate("level1_counts.json", _set_count("vacance", "total", 460))
    assert env.run("--dataset", "vacance") == 1
    env.classify("1:vacance:total", "correction_voulue", preuve="D-22")
    assert env.run("--dataset", "vacance") == 0
    assert env.ecart("1:vacance:total")["categorie"] == "correction_voulue"
    assert env.report()["datasets"]["vacance"]["verdict"] == "ecarts_expliques"
    assert env.report()["a_arbitrer"] == []


def test_classification_without_evidence_is_refused(env: Env) -> None:
    env.edit_candidate("level1_counts.json", _set_count("vacance", "total", 460))
    env.run("--dataset", "vacance")
    env.classify("1:vacance:total", "correction_voulue", preuve="")
    assert env.run("--dataset", "vacance") == 1
    gap = env.ecart("1:vacance:total")
    assert gap["categorie"] == "inexpliquee"
    assert gap["classement"].startswith("refuse")


def test_classified_value_that_changes_becomes_unexplained(env: Env) -> None:
    env.edit_candidate("level1_counts.json", _set_count("vacance", "total", 460))
    env.run("--dataset", "vacance")
    env.classify("1:vacance:total", "correction_voulue", preuve="D-22")
    assert env.run("--dataset", "vacance") == 0
    env.edit_candidate("level1_counts.json", _set_count("vacance", "total", 461))
    assert env.run("--dataset", "vacance") == 1
    gap = env.ecart("1:vacance:total")
    assert gap["categorie"] == "inexpliquee"
    assert gap["classement"] == "preuve_perimee"


def test_source_differente_without_resolvable_justification_is_refused(
    env: Env,
) -> None:
    env.edit_candidate("level1_counts.json", _set_count("vacance", "44", 206))
    env.run("--dataset", "vacance")
    env.classify(
        "1:vacance:44",
        "source_differente",
        preuve="2024 -> 2025",
        millesime_reference="2024",
        millesime_candidat="2025",
    )
    assert env.run("--dataset", "vacance") == 1
    assert env.ecart("1:vacance:44")["categorie"] == "inexpliquee"
    env.classifications["1:vacance:44"]["justificatif"] = {"chemin": "absent.zip"}
    assert env.run("--dataset", "vacance") == 1
    env.classifications["1:vacance:44"]["justificatif"] = {
        "chemin": "api/backend/tests/fixtures/fidelity_ct275/manifest.json"
    }
    assert env.run("--dataset", "vacance") == 0
    assert env.ecart("1:vacance:44")["categorie"] == "source_differente"


def test_source_differente_with_unknown_vintage_is_refused(env: Env) -> None:
    env.edit_candidate("level1_counts.json", _set_count("vacance", "44", 206))
    env.run("--dataset", "vacance")
    env.classify(
        "1:vacance:44",
        "source_differente",
        preuve="02-IMPORTS.md",
        millesime_reference="inconnu",
        millesime_candidat="2025",
        justificatif={"url": "https://example.invalid/a.zip", "sha256": "a" * 64},
    )
    assert env.run("--dataset", "vacance") == 1
    assert env.ecart("1:vacance:44")["categorie"] == "inexpliquee"


# --------------------------------------------------------- blocking gaps --


def test_missing_expected_column_is_a_regression(env: Env) -> None:
    def drop(payload: dict[str, Any]) -> None:
        payload["columns"].remove("taux_vacance")
        for row in payload["rows"].values():
            del row["taux_vacance"]

    env.edit_candidate("level2_panel.json", drop)
    assert env.run("--dataset", "vacance") == 1
    gap = env.ecart("2:44039:taux_vacance")
    assert gap["bloquant"] is True
    assert gap["categorie"] == "regression"
    assert env.report()["datasets"]["vacance"]["verdict"] == "regression"
    assert any(a["cle"] == "2:44039:taux_vacance" for a in env.report()["a_arbitrer"])


def test_missing_panel_commune_is_a_regression(env: Env) -> None:
    def drop(payload: dict[str, Any]) -> None:
        del payload["rows"]["85191"]
        payload["missing"] = ["85191"]

    env.edit_candidate("level2_panel.json", drop)
    assert env.run("--dataset", "vacance") == 1
    gap = env.ecart("2:85191:annee_vacance")
    assert gap["bloquant"] is True
    assert gap["categorie"] == "regression"
    assert env.report()["datasets"]["vacance"]["verdict"] == "regression"


def test_missing_reference_table_is_a_regression(env: Env) -> None:
    env.edit_candidate(
        "level1_counts.json",
        lambda p: p["tables"]["vacance"].update(status="absent", counts=None),
    )
    assert env.run("--dataset", "vacance") == 1
    assert env.ecart("1:vacance:44")["categorie"] == "regression"


def test_new_candidate_column_is_listed_without_blocking(env: Env) -> None:
    def add(payload: dict[str, Any]) -> None:
        payload["columns"].append("nouvelle")
        for row in payload["rows"].values():
            row["nouvelle"] = 1

    env.edit_candidate("level2_panel.json", add)
    assert env.run("--dataset", "vacance") == 0
    assert env.report()["niveau_2"]["colonnes_nouvelles"] == ["nouvelle"]


# ------------------------------------------- methode_de_reference_invalide --


def _recount(env: Env, *tables: str) -> None:
    _write(
        env.cand / "departemental_recount.json",
        {
            table: {
                "methode": "dc_departement_from_insee(code_insee)",
                "par_departement": {"44": 11934, "85": 6039},
            }
            for table in (tables or ("carte_scolaire",))
        },
    )


def test_invalid_reference_method_on_carte_scolaire_is_impossible(env: Env) -> None:
    env.edit_candidate(
        "level1_counts.json", _set_count("carte_scolaire", "total", 17979)
    )
    _recount(env)
    assert env.run("--dataset", "scolaire") == 1
    env.classify(
        "1:carte_scolaire:total",
        "methode_de_reference_invalide",
        preuve="02-05-SUMMARY m2",
    )
    assert env.run("--dataset", "scolaire") == 0
    report = env.report()
    assert report["datasets"]["scolaire"]["verdict"] == "comparaison_impossible"
    assert report["verdict_niveaux_1_2"] == "conforme_avec_comparaisons_impossibles"
    assert "carte_scolaire" in report["comparaisons_impossibles"]


def test_invalid_reference_method_without_recount_is_refused(env: Env) -> None:
    env.edit_candidate(
        "level1_counts.json", _set_count("carte_scolaire", "total", 17979)
    )
    env.run("--dataset", "scolaire")
    env.classify("1:carte_scolaire:total", "methode_de_reference_invalide", preuve="m2")
    assert env.run("--dataset", "scolaire") == 1
    assert env.ecart("1:carte_scolaire:total")["categorie"] == "inexpliquee"


def test_invalid_reference_method_on_another_table_is_refused(env: Env) -> None:
    env.edit_candidate("level1_counts.json", _set_count("antennes", "total", 23981))
    # A valid recount exists for antennes too: only the table check can refuse.
    _recount(env, "carte_scolaire", "antennes")
    env.run("--dataset", "antennes")
    env.classify("1:antennes:total", "methode_de_reference_invalide", preuve="m2")
    assert env.run("--dataset", "antennes") == 1
    gap = env.ecart("1:antennes:total")
    assert gap["categorie"] == "inexpliquee"
    assert gap["classement"] == (
        "refuse: methode_de_reference_invalide hors des trois tables"
    )


# --------------------------------------------------------------- markdown --


def test_markdown_block_is_rewritten_between_markers(env: Env) -> None:
    md = env.root / "02-FIDELITE.md"
    md.write_text(
        "# Titre\n\n"
        + cf.MD_START
        + "\nancien\n"
        + cf.MD_END
        + "\n\n## vacance\n\ntexte gardé\n",
        encoding="utf-8",
    )
    assert env.run("--dataset", "vacance", "--markdown", str(md)) == 0
    text = md.read_text(encoding="utf-8")
    assert "ancien" not in text
    assert "texte gardé" in text
    assert "| vacance |" in text


def test_markdown_without_markers_exits_2(env: Env) -> None:
    md = env.root / "02-FIDELITE.md"
    md.write_text("# Titre\n", encoding="utf-8")
    assert env.run("--dataset", "vacance", "--markdown", str(md)) == 2


def test_report_is_deterministic(env: Env) -> None:
    env.edit_candidate("level1_counts.json", _set_count("vacance", "44", 200))
    env.run()
    first = env.out.read_bytes()
    env.run()
    assert env.out.read_bytes() == first


# ------------------------------------------------------------------ golden --
# Opaque case ids and booleans only: no address, no private input.

GOLDEN_MAP = {
    "cases": {
        "golden-address-001": ["ban", "dpe", "dvf"],
        "golden-address-002": ["ban", "cadastre"],
    },
    "infrastructure_cases": ["runner", "manifest"],
}


def _golden(env: Env, cases: list[dict[str, Any]], golden_map=None) -> tuple[str, ...]:
    report = env.root / "golden-live-report.redacted.json"
    _write(
        report,
        {
            "ok": all(c.get("ok") for c in cases),
            "served_checkout_sha": HEAD_SHA,
            "cases": cases,
        },
    )
    gmap = env.root / "golden-map.json"
    _write(gmap, golden_map or GOLDEN_MAP)
    return ("--golden-report", str(report), "--golden-map", str(gmap))


def _case(case_id: str, ok: Any, *reasons: str) -> dict[str, Any]:
    return {"case_id": case_id, "type": "address", "ok": ok, "reasons": list(reasons)}


def consolidated_check(
    report: dict[str, Any],
    raw: bytes,
    manifest_ids: set[str],
    golden_map: dict[str, Any],
    blocages: list[dict[str, Any]] | None,
) -> str:
    """The CONSOLIDATED gate of plan 02-13 (task 1), as a function."""
    reel = {
        c.get("case_id"): c.get("ok")
        for c in (json.loads(raw.decode("utf-8")).get("cases") or [])
        if c.get("case_id") not in ("runner", "manifest")
    }
    golden = report.get("golden") or {}
    cases = golden.get("cases") or {}
    par = golden.get("par_dataset") or {}
    sha_ok = golden.get("sha256") == hashlib.sha256(raw).hexdigest()
    pseudo = sorted(set(cases) & {"runner", "manifest"})
    ens = sorted(manifest_ids ^ set(cases))
    nonbool = sorted(c for c, v in cases.items() if not isinstance(v.get("ok"), bool))
    menti = sorted(
        c
        for c, v in cases.items()
        if isinstance(v.get("ok"), bool) and c in reel and v["ok"] != reel[c]
    )
    ko = sorted(c for c, v in cases.items() if v.get("ok") is False)
    unattr = sorted(c for c in ko if not (cases[c].get("datasets") or []))
    nonclass = sorted(c for c in ko if not cases[c].get("classement"))
    g = report.get("verdict_global")
    gds = sorted({d for v in (golden_map.get("cases") or {}).values() for d in v})
    enatt = (
        {
            e.get("dataset")
            for e in blocages
            if isinstance(e, dict)
            and e.get("decision") == "en_attente"
            and str(e.get("motif") or "").strip()
        }
        if isinstance(blocages, list)
        else set()
    )
    declare = (
        bool(report.get("erreurs_infrastructure"))
        and bool(gds)
        and not (set(gds) - enatt)
        and g == "golden_bloque_infrastructure"
        and sha_ok
    )
    ok = (
        bool(cases)
        and bool(par)
        and sha_ok
        and not (pseudo or ens or nonbool or menti or unattr)
        and bool(g)
        and g != "golden_non_rejoue"
        and (not nonclass or g != "conforme")
    )
    if ok:
        return "CONSOLIDATED"
    return "CONSOLIDATED_BLOQUE_DECLARE" if declare else "CONSOLIDATED_BAD"


def test_without_golden_report_the_global_verdict_is_not_replayed(env: Env) -> None:
    assert env.run() == 0
    report = env.report()
    assert report["verdict_global"] == "golden_non_rejoue"
    assert report["golden"]["cases"] == {}
    assert report["golden"]["par_dataset"] == {}
    assert report["erreurs_infrastructure"] == []


def test_failed_golden_case_brings_its_datasets_to_regression(env: Env) -> None:
    args = _golden(
        env,
        [
            _case("golden-address-001", False, "dpe_attribution: unassigned"),
            _case("golden-address-002", True),
        ],
    )
    assert env.run(*args) == 1
    report = env.report()
    golden = report["golden"]
    # The shared example of plans 02-12 and 02-13, key for key.
    assert golden["cases"] == {
        "golden-address-001": {
            "ok": False,
            "datasets": ["ban", "dpe", "dvf"],
            "classement": None,
            "reasons": ["dpe_attribution: unassigned"],
        },
        "golden-address-002": {
            "ok": True,
            "datasets": ["ban", "cadastre"],
            "classement": None,
            "reasons": [],
        },
    }
    assert golden["par_dataset"]["dpe"] == {
        "cas": ["golden-address-001"],
        "echecs": ["golden-address-001"],
        "verdict": "regression",
    }
    assert golden["par_dataset"]["cadastre"] == {
        "cas": ["golden-address-002"],
        "echecs": [],
        "verdict": "conforme",
    }
    assert golden["served_checkout_sha"] == HEAD_SHA
    assert report["datasets"]["dpe"]["verdict"] == "regression"
    assert report["datasets"]["cadastre"]["verdict"] == "conforme"
    assert report["verdict_global"] == "regression"
    assert any(a["cle"] == "3:golden-address-001:dpe" for a in report["a_arbitrer"])


def test_unknown_case_id_is_a_coverage_error(env: Env) -> None:
    args = _golden(
        env,
        [
            _case("golden-address-001", True),
            _case("golden-address-002", True),
            _case("golden-address-099", True),
        ],
    )
    assert env.run(*args) == 1
    assert env.report()["erreurs_couverture_golden"] == [
        {"case_id": "golden-address-099", "motif": "inconnu_de_la_correspondance"}
    ]


def test_mapped_case_missing_from_the_report_is_a_coverage_error(env: Env) -> None:
    args = _golden(env, [_case("golden-address-001", True)])
    assert env.run(*args) == 1
    report = env.report()
    assert report["erreurs_couverture_golden"] == [
        {"case_id": "golden-address-002", "motif": "absent_du_rapport"}
    ]
    # m4: an incomplete coverage never reads conforme.
    assert report["verdict_global"] == "golden_couverture_incomplete"


def test_coverage_error_does_not_hide_a_regression(env: Env) -> None:
    env.edit_candidate("level1_counts.json", _set_count("vacance", "44", 200))
    args = _golden(env, [_case("golden-address-001", True)])
    assert env.run(*args) == 1
    assert env.report()["verdict_global"] == "inexpliquee"


def test_infrastructure_pseudo_cases_are_reported_apart(env: Env) -> None:
    args = _golden(env, [_case("runner", False, "live replay: BLOCKED")])
    assert env.run(*args) == 1
    report = env.report()
    assert report["erreurs_infrastructure"] == [
        {"case_id": "runner", "reasons": ["live replay: BLOCKED"]}
    ]
    assert "runner" not in report["golden"]["cases"]
    assert report["verdict_global"] == "golden_bloque_infrastructure"


def test_failed_case_with_valid_classification_allows_exit_0(env: Env) -> None:
    args = _golden(
        env,
        [
            _case("golden-address-001", False, "dpe_attribution: unassigned"),
            _case("golden-address-002", True),
        ],
    )
    assert env.run(*args) == 1
    for dataset in ("ban", "dpe", "dvf"):
        env.classify(
            f"3:golden-address-001:{dataset}", "correction_voulue", preuve="D-45"
        )
    assert env.run(*args) == 0
    report = env.report()
    assert report["golden"]["cases"]["golden-address-001"]["classement"] == {
        "ban": "correction_voulue",
        "dpe": "correction_voulue",
        "dvf": "correction_voulue",
    }
    assert report["verdict_global"] == "conforme_avec_comparaisons_impossibles"


def test_golden_ok_keeps_the_level_1_2_verdict(env: Env) -> None:
    env.edit_candidate(
        "level1_counts.json", _set_count("carte_scolaire", "total", 17979)
    )
    _recount(env)
    args = _golden(
        env, [_case("golden-address-001", True), _case("golden-address-002", True)]
    )
    env.run(*args)
    env.classify(
        "1:carte_scolaire:total", "methode_de_reference_invalide", preuve="02-05 m2"
    )
    assert env.run(*args) == 0
    assert env.report()["verdict_global"] == "conforme_avec_comparaisons_impossibles"


def test_golden_report_without_map_or_with_non_boolean_ok_exits_2(env: Env) -> None:
    args = _golden(env, [_case("golden-address-001", True)])
    assert env.run(*args[:2]) == 2
    bad = _golden(env, [_case("golden-address-001", "true")])
    assert env.run(*bad) == 2


def test_consolidation_passes_the_02_13_gate(env: Env) -> None:
    """Chain test: consolidate_golden, then the CONSOLIDATED gate of 02-13."""
    args = _golden(
        env,
        [
            _case("golden-address-001", False, "dpe_attribution: unassigned"),
            _case("golden-address-002", True),
        ],
    )
    env.run(*args)
    raw = Path(args[1]).read_bytes()
    ids = set(GOLDEN_MAP["cases"])
    assert consolidated_check(env.report(), raw, ids, GOLDEN_MAP, []) == "CONSOLIDATED"
    # Negative: a consolidation that lies about a case result is refused.
    lying = env.report()
    lying["golden"]["cases"]["golden-address-001"]["ok"] = True
    assert consolidated_check(lying, raw, ids, GOLDEN_MAP, []) == "CONSOLIDATED_BAD"
    # Negative: the consolidation of another report (fingerprint) is refused.
    assert (
        consolidated_check(env.report(), raw + b" ", ids, GOLDEN_MAP, [])
        == "CONSOLIDATED_BAD"
    )
    # A blocked replay passes only when every mapped dataset is declared.
    blocked = _golden(env, [_case("runner", False, "live replay: BLOCKED")])
    env.run(*blocked)
    raw = Path(blocked[1]).read_bytes()
    datasets = sorted({d for v in GOLDEN_MAP["cases"].values() for d in v})
    declared = [
        {"dataset": d, "motif": "runner", "decision": "en_attente"} for d in datasets
    ]
    assert (
        consolidated_check(env.report(), raw, ids, GOLDEN_MAP, declared)
        == "CONSOLIDATED_BLOQUE_DECLARE"
    )
    assert (
        consolidated_check(env.report(), raw, ids, GOLDEN_MAP, declared[:-1])
        == "CONSOLIDATED_BAD"
    )


def test_failed_case_on_a_dataset_not_compared_forbids_exit_0(env: Env) -> None:
    # georisques is an upstream proxy: no level 1-2 comparison can catch it.
    gmap = {
        "cases": {"golden-address-001": ["georisques"]},
        "infrastructure_cases": ["runner", "manifest"],
    }
    args = _golden(env, [_case("golden-address-001", False, "risques_state")], gmap)
    assert env.run(*args) == 1
    report = env.report()
    assert report["golden"]["par_dataset"]["georisques"]["verdict"] == "regression"
    assert report["verdict_global"] == "regression"


# ---------------------------------------------- D-15 review (02-12): receipts --

# Empty when HEAD has no parent: first commit of the public export, or a CI
# checkout of depth 2 on that commit. The test that needs it is then skipped.
PREVIOUS_SHA = subprocess.run(
    ["git", "-C", str(BACKEND), "rev-parse", "--verify", "--quiet", "HEAD~1"],
    capture_output=True,
    text=True,
    check=False,
).stdout.strip()


def _edit_receipt(env: Env, pipeline: str, **fields: Any) -> None:
    run_id = env.run_ids["pipelines"][pipeline]
    path = env.imports / pipeline / f"{run_id}.json"
    receipt = _read(path)
    receipt.update(fields)
    _write(path, receipt)


def test_receipt_of_another_dataset_exits_2(env: Env) -> None:
    _edit_receipt(env, "vacance", dataset="loyers")
    assert env.run("--dataset", "vacance") == 2


def test_receipt_repo_sha_different_from_the_pointer_exits_2(env: Env) -> None:
    if not PREVIOUS_SHA:
        pytest.skip("HEAD has no parent commit in this checkout")
    # Both commits are in the history: only the identity check can refuse.
    _edit_receipt(env, "vacance", repo_sha=PREVIOUS_SHA)
    assert env.run("--dataset", "vacance") == 2


def test_receipt_with_another_internal_run_id_exits_2(env: Env) -> None:
    _edit_receipt(env, "vacance", run_id="999-20260925T000000Z")
    assert env.run("--dataset", "vacance") == 2


# ---------------------------------------- D-15 review (02-12): classification --


def test_source_differente_with_url_but_no_64_hex_sha256_is_refused(env: Env) -> None:
    env.edit_candidate("level1_counts.json", _set_count("vacance", "44", 206))
    env.run("--dataset", "vacance")
    env.classify(
        "1:vacance:44",
        "source_differente",
        preuve="02-IMPORTS.md",
        millesime_reference="2024",
        millesime_candidat="2025",
        justificatif={"url": "https://example.invalid/a.zip", "sha256": "abc123"},
    )
    assert env.run("--dataset", "vacance") == 1
    gap = env.ecart("1:vacance:44")
    assert gap["categorie"] == "inexpliquee"
    assert gap["classement"] == "refuse: justificatif non resoluble"
    env.classifications["1:vacance:44"]["justificatif"]["sha256"] = "a" * 64
    assert env.run("--dataset", "vacance") == 0


def test_classification_with_another_reference_fingerprint_is_stale(env: Env) -> None:
    env.edit_candidate("level1_counts.json", _set_count("vacance", "total", 460))
    env.run("--dataset", "vacance")
    env.classify("1:vacance:total", "correction_voulue", preuve="D-22")
    env.classifications["1:vacance:total"]["reference_sha256"] = "0" * 64
    assert env.run("--dataset", "vacance") == 1
    gap = env.ecart("1:vacance:total")
    assert gap["categorie"] == "inexpliquee"
    assert gap["classement"] == "preuve_perimee"
