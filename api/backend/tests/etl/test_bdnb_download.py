"""Download, millesime and replay of the BDNB pipeline, without database.

The data.gouv.fr dataset is the one read on 2026-11-02
(``tests/fixtures/bdnb/datagouv-dataset.json``, useful fields kept); the S3
bucket is replaced by ``respx`` serving the synthetic exports of
``tests/etl/bdnb_fixture``. What is checked: the current millesime is
discovered (D6), the URLs of the departements (two characters, Corsica lower
case), the label of every export against the target, the provenance served
(``2026-02.b``, never the URL form ``2026-02-b``), the bounded concurrency, the
replay label check and the DROM out of coverage (D9).
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.common.source_catalog import DATASET_CATALOG, source_covers
from app.config import settings
from app.etl import base
from app.etl.errors import MalformedMillesimeError
from app.etl_bdnb import pipeline as module
from app.etl_bdnb.config import DATAGOUV_DATASET_URL, EXPORT_URL_TEMPLATE
from app.etl_bdnb.pipeline import (
    BdnbBatimentParcellePipeline,
    BdnbExportError,
    millesime_courant,
)
from app.scope import InstanceScope
from tests.etl.bdnb_fixture import build_export, mini_exports, with_label

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "bdnb"
DATAGOUV = json.loads((FIXTURE / "datagouv-dataset.json").read_text("utf-8"))


@pytest.fixture(autouse=True)
def _temp(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    monkeypatch.setattr(settings, "etl_max_retries", 1)


def _pipeline(*departements: str) -> BdnbBatimentParcellePipeline:
    pipeline = BdnbBatimentParcellePipeline()
    pipeline.bind_scope(InstanceScope(frozenset({"bdnb"}), departements))
    return pipeline


def _url(dept: str, millesime: str = "2026-02-b") -> str:
    return EXPORT_URL_TEMPLATE.format(millesime=millesime, dept=dept.lower())


def _serve(router, tmp_path, exports, millesime="2026-02-b"):
    for dept, export in exports.items():
        content = build_export(tmp_path / "s3", dept, export).read_bytes()
        router.get(_url(dept, millesime)).respond(200, content=content)


def test_the_current_millesime_is_discovered_on_datagouv():
    with respx.mock() as router:
        router.get(DATAGOUV_DATASET_URL).respond(200, json=DATAGOUV)

        async def lire():
            async with httpx.AsyncClient() as client:
                return await millesime_courant(client)

        assert asyncio.run(lire()) == "2026-02-b"


def test_no_millesime_in_the_resources_is_refused():
    sans = {**DATAGOUV, "resources": [{"url": "https://api-portail.bdnb.io/x"}]}
    with respx.mock() as router:
        router.get(DATAGOUV_DATASET_URL).respond(200, json=sans)

        async def lire():
            async with httpx.AsyncClient() as client:
                return await millesime_courant(client)

        with pytest.raises(RuntimeError, match="aucun millésime"):
            asyncio.run(lire())


def test_only_the_url_form_of_the_millesime_can_be_targeted():
    pipeline = BdnbBatimentParcellePipeline()
    pipeline.pin_millesime("2026-02-b")
    assert pipeline.pinned_millesime == "2026-02-b"
    for malforme in ("2026-02.b", "2026-02", "latest", "../2026-02-b"):
        with pytest.raises(MalformedMillesimeError):
            BdnbBatimentParcellePipeline().pin_millesime(malforme)


def test_a_discovered_download_serves_the_label_of_the_export(tmp_path):
    pipeline = _pipeline("44", "85", "2A")
    with respx.mock() as router:
        router.get(DATAGOUV_DATASET_URL).respond(200, json=DATAGOUV)
        _serve(router, tmp_path, mini_exports())
        paths = pipeline.download()

    assert sorted(p.name for p in paths) == [
        "open_data_millesime_2026-02-b_dep2a_csv.zip",
        "open_data_millesime_2026-02-b_dep44_csv.zip",
        "open_data_millesime_2026-02-b_dep85_csv.zip",
    ]
    assert pipeline.source_period_label == "2026-02.b"
    assert pipeline.source_updated_at.isoformat() == "2026-10-07T00:00:00+00:00"


def test_a_pinned_download_serves_the_label_never_the_url_form(tmp_path):
    """Without the source_period_label override, the base class would serve
    the pinned URL form ``2026-02-b`` (base.py, ``_pinned_millesime``)."""
    pipeline = _pipeline("85")
    pipeline.pin_millesime("2026-02-b")
    with respx.mock() as router:
        _serve(router, tmp_path, {"85": mini_exports()["85"]})
        pipeline.download()

    assert pipeline.source_period_label == "2026-02.b"
    assert base.BasePipeline.source_period_label.fget(pipeline) == "2026-02-b"


def test_an_export_of_another_millesime_is_refused(tmp_path):
    pipeline = _pipeline("44", "85")
    pipeline.pin_millesime("2026-02-b")
    exports = mini_exports()
    exports["44"] = with_label(exports["44"], "2026-02.a")
    with respx.mock() as router:
        _serve(router, tmp_path, {d: exports[d] for d in ("44", "85")})
        with pytest.raises(BdnbExportError, match=r"millésime 2026-02\.a, attendu"):
            pipeline.download()


@pytest.mark.parametrize("status", [403, 404])
def test_a_departement_the_catalogue_publishes_must_be_delivered(tmp_path, status):
    pipeline = _pipeline("44", "85")
    pipeline.pin_millesime("2026-02-b")
    with respx.mock() as router:
        _serve(router, tmp_path, {"85": mini_exports()["85"]})
        router.get(_url("44")).respond(status)
        with pytest.raises(BdnbExportError, match=f"44 refusé \\(HTTP {status}\\)"):
            pipeline.download()


def test_at_most_four_departements_download_at_once(tmp_path, monkeypatch):
    departements = ("01", "02", "03", "04", "05", "06", "07")
    pipeline = _pipeline(*departements)
    pipeline.pin_millesime("2026-02-b")
    zips = {
        d: build_export(tmp_path / "s3", d, mini_exports()["85"]) for d in departements
    }
    en_cours = {"n": 0, "max": 0}

    async def download(_client, url, output_path, **_kwargs):
        en_cours["n"] += 1
        en_cours["max"] = max(en_cours["max"], en_cours["n"])
        await asyncio.sleep(0.01)
        dept = url.rsplit("_dep", 1)[1][:2]
        output_path.write_bytes(zips[dept].read_bytes())
        en_cours["n"] -= 1
        return True

    monkeypatch.setattr(module, "verified_download", download)
    assert len(pipeline.download()) == 7
    assert en_cours["max"] == 4


def test_the_overseas_departements_are_out_of_coverage():
    entry = DATASET_CATALOG["bdnb"]
    assert DATAGOUV["spatial"]["zones"] == ["country-subset:fr:metro"]
    for code in ("971", "972", "973", "974", "975", "976"):
        assert not source_covers(entry, code), code
    for code in ("2A", "2B", "44", "85"):
        assert source_covers(entry, code), code
    pipeline = _pipeline("85", "971", "2A")
    assert pipeline.expected_department_coverage == ["85", "2A"]
    assert pipeline.expected_file_count == 2


def _replay(paths, label):
    return SimpleNamespace(
        record={"source_period_label": label}, link=lambda _directory: paths
    )


def test_a_resumed_run_journals_the_counts_of_the_files_it_skips(tmp_path):
    """A resume skips the files its checkpoint loaded (runner): the counts of
    44, never transformed by this instance, are read again from its zip."""
    exports = mini_exports()
    complet = _pipeline("44", "85")
    complet.pin_millesime("2026-02-b")
    reprise = _pipeline("44", "85")
    reprise.pin_millesime("2026-02-b")
    with respx.mock() as router:
        _serve(router, tmp_path, {d: exports[d] for d in ("44", "85")})
        for pipeline in (complet, reprise):
            paths = {p.name: p for p in pipeline.download()}
            dep85 = paths["open_data_millesime_2026-02-b_dep85_csv.zip"]
            if pipeline is complet:
                pipeline.transform(paths["open_data_millesime_2026-02-b_dep44_csv.zip"])
            pipeline.transform(dep85)
            dep85.unlink()  # stage() drops a loaded input from the cache

    attendus = complet.stage_counters()
    assert set(attendus) == {
        f"{nom}_{dept}"
        for nom in ("producteur_relations", "producteur_ffo", "relations_ecartees")
        for dept in ("44", "85")
    }
    assert reprise.stage_counters() == attendus


def test_a_resume_whose_skipped_zip_is_gone_refuses_to_journal(tmp_path):
    pipeline = _pipeline("44")
    pipeline._sources = {"44": tmp_path / "absent.zip"}
    with pytest.raises(RuntimeError, match="44 absent"):
        pipeline.stage_counters()


def test_a_replay_reads_the_label_of_each_restored_zip(tmp_path):
    exports = mini_exports()
    paths = [build_export(tmp_path, d, exports[d]) for d in ("44", "85")]
    pipeline = _pipeline("44", "85")

    assert pipeline.restore_source_inputs(_replay(paths, "2026-02.b"), tmp_path) == (
        paths
    )
    assert pipeline.source_period_label == "2026-02.b"


@pytest.mark.parametrize("label", ["2026-02.a", None, "2026-02-b"])
def test_a_replay_whose_zips_do_not_carry_the_record_label_is_refused(tmp_path, label):
    exports = mini_exports()
    paths = [build_export(tmp_path, d, exports[d]) for d in ("44", "85")]
    with pytest.raises(BdnbExportError, match="archive"):
        _pipeline("44", "85").restore_source_inputs(_replay(paths, label), tmp_path)


def test_a_replay_mixing_two_millesimes_is_refused(tmp_path):
    exports = mini_exports()
    paths = [
        build_export(tmp_path, "44", with_label(exports["44"], "2026-02.a")),
        build_export(tmp_path, "85", exports["85"]),
    ]
    with pytest.raises(BdnbExportError, match="étiquettes"):
        _pipeline("44", "85").restore_source_inputs(
            _replay(paths, "2026-02.b"), tmp_path
        )


def test_the_producer_counts_of_each_file_are_journaled(tmp_path):
    pipeline = _pipeline("44", "85")
    exports = mini_exports()
    for dept in ("44", "85"):
        pipeline.transform(build_export(tmp_path, dept, exports[dept]))

    assert pipeline.stage_counters() == {
        "producteur_relations_44": 2,
        "producteur_ffo_44": 2,
        "relations_ecartees_44": 0,
        "producteur_relations_85": 19,
        "producteur_ffo_85": 17,
        "relations_ecartees_85": 1,
    }


def test_a_refused_export_ends_the_import_instead_of_being_skipped(tmp_path):
    exports = mini_exports()
    path = build_export(tmp_path, "85", with_label(exports["85"], "2026-02.a"))
    pipeline = _pipeline("85")
    pipeline.pin_millesime("2026-02-b")
    pipeline._label_cible = "2026-02.b"
    with pytest.raises(BdnbExportError) as refus:
        pipeline.validate_schema(path)
    from app.etl.errors import FatalInputError

    assert isinstance(refus.value, FatalInputError)
