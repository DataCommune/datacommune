"""Old-vintage targeting of a pipeline (plan 02-11, D-27, PROV-03).

``run_etl.py <dataset> --millesime <label>`` asks the producer for the vintage
the old base read, when the producer still serves it. Proven here without
network nor database, by URL construction:

- a pipeline that cannot target a vintage is refused (exit 2,
  ``millésime non ciblable: <dataset>``) before any scope read, runner or
  download;
- a malformed label is refused before any download;
- a pinned pipeline downloads the dated URL of its declared mechanism and
  ``source_period_label`` names that label;
- without ``--millesime`` the URL is unchanged and ``source_period_label``
  stays None, which the provenance assembly renders ``inconnue``: an import
  date is never a vintage.
"""

from __future__ import annotations

import contextlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import run_etl
from app.datasets.provenance import ProvenanceFacts, assemble_provenance
from app.etl.errors import MalformedMillesimeError, MillesimeNotTargetableError
from app.etl_ban import pipeline as ban_module
from app.etl_ban.pipeline import BanPipeline
from app.scope.model import InstanceScope

MECHANISMS = {"segment_date", "url_archive", "parametre_version"}


def _scope(*keys: str) -> InstanceScope:
    return InstanceScope(datasets=frozenset(keys), departements=("44", "85"))


def _main_exit(monkeypatch, capsys, argv: list[str]) -> tuple[int, str]:
    """Run run_etl.main(); fail if it reads the scope or builds a runner."""
    monkeypatch.setattr(sys, "argv", ["run_etl", *argv])

    def no_scope(_dsn):
        pytest.fail("the instance scope was read before the refusal")

    def no_runner(*_a, **_k):
        pytest.fail("a runner was built before the refusal")

    monkeypatch.setattr(run_etl.etl_runner, "default_scope_loader", no_scope)
    monkeypatch.setattr(run_etl, "PipelineRunner", no_runner)
    with pytest.raises(SystemExit) as exc:
        run_etl.main()
    return exc.value.code, capsys.readouterr().err


# --------------------------------------------------------------- refusals --


@pytest.mark.parametrize(
    "name",
    sorted(
        name
        for name, cls in run_etl.PIPELINE_MAP.items()
        if not cls().supports_millesime_pin
    ),
)
def test_pipeline_without_mechanism_is_refused_before_anything(
    monkeypatch, capsys, name
):
    code, err = _main_exit(monkeypatch, capsys, [name, "--millesime", "2026-01-01"])
    assert code == 2
    assert f"millésime non ciblable: {name}" in err


def test_default_pipeline_declares_no_mechanism():
    from app.etl_vacance.pipeline import VacancePipeline

    pipeline = VacancePipeline()
    assert pipeline.supports_millesime_pin is False
    assert pipeline.millesime_mechanism is None
    with pytest.raises(MillesimeNotTargetableError, match="vacance"):
        pipeline.pin_millesime("2026-01-01")
    assert pipeline.source_period_label is None


def test_every_declared_mechanism_is_in_the_closed_list():
    declared = {
        cls().millesime_mechanism
        for cls in run_etl.PIPELINE_MAP.values()
        if cls().supports_millesime_pin
    }
    assert declared, "no pipeline can target a vintage"
    assert declared <= MECHANISMS


@pytest.mark.parametrize(
    "label",
    [
        "latest",
        "2026-06",
        "2026-13-01",
        "2026-02-30",
        "2026-06-17/../latest",
        "../2026-06-17",
        "2026-06-17 ",
        "",
    ],
)
def test_malformed_label_is_refused_before_any_download(monkeypatch, capsys, label):
    code, err = _main_exit(monkeypatch, capsys, ["ban", "--millesime", label])
    assert code == 2
    assert "millésime mal formé: ban" in err
    with pytest.raises(MalformedMillesimeError):
        BanPipeline().pin_millesime(label)


@pytest.mark.parametrize(
    "argv",
    [
        ["--millesime", "2026-06-17"],
        ["ban", "cadastre", "--millesime", "2026-06-17"],
        ["ban", "--millesime", "2026-06-17", "--from-archive", "1-20260101T000000Z"],
    ],
)
def test_millesime_requires_exactly_one_dataset_and_no_replay(
    monkeypatch, capsys, argv
):
    code, err = _main_exit(monkeypatch, capsys, argv)
    assert code == 2
    assert "--millesime" in err


# ------------------------------------------------------------------ BAN --


def _ban_urls(monkeypatch, tmp_path: Path, pipeline: BanPipeline) -> list[str]:
    """URLs the BAN download asks for, without network."""
    seen: list[str] = []

    async def fake_download(_client, url, output_path, **_kw):
        seen.append(url)
        return False  # 404: nothing written

    monkeypatch.setattr(ban_module, "verified_download_gz", fake_download)
    monkeypatch.setattr(ban_module, "get_download_dir", lambda _name: tmp_path)
    pipeline.bind_scope(_scope("ban"))
    pipeline.download()
    return sorted(seen)


def test_ban_pinned_downloads_the_dated_folder(monkeypatch, tmp_path):
    pipeline = BanPipeline()
    assert pipeline.millesime_mechanism == "segment_date"
    pipeline.pin_millesime("2026-06-17")
    assert _ban_urls(monkeypatch, tmp_path, pipeline) == [
        "https://adresse.data.gouv.fr/data/ban/adresses/2026-06-17/csv/adresses-44.csv.gz",
        "https://adresse.data.gouv.fr/data/ban/adresses/2026-06-17/csv/adresses-85.csv.gz",
    ]
    assert pipeline.source_period_label == "2026-06-17"


def test_ban_without_millesime_keeps_latest_and_an_unknown_period(
    monkeypatch, tmp_path
):
    pipeline = BanPipeline()
    assert _ban_urls(monkeypatch, tmp_path, pipeline) == [
        "https://adresse.data.gouv.fr/data/ban/adresses/latest/csv/adresses-44.csv.gz",
        "https://adresse.data.gouv.fr/data/ban/adresses/latest/csv/adresses-85.csv.gz",
    ]
    assert pipeline.source_period_label is None
    period = assemble_provenance(
        "ban",
        ProvenanceFacts(
            last_success_at=datetime(2026, 9, 24, tzinfo=UTC),
            source_period_label=pipeline.source_period_label,
        ),
        now=datetime(2026, 9, 24, tzinfo=UTC),
    ).periode
    # The import date is never promoted to a vintage.
    assert period.statut == "inconnue"
    assert period.label is None


def test_pinned_pipeline_replaces_the_stored_period(monkeypatch):
    """A later run without --millesime must not inherit the pinned label.

    COALESCE would keep "2026-06-17" over a 'latest' download: a false
    vintage (PROV-03). A pipeline that can target a vintage always writes its
    own label, None included.
    """
    from app.etl import runner as runner_module

    calls: list[dict] = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            calls.append({"sql": sql, "params": params})

        def commit(self):
            pass

    monkeypatch.setattr(runner_module.psycopg, "connect", lambda _dsn: _Conn())
    import logging

    runner_module.PipelineRunner._update_dataset_metadata(
        "postgresql://x",
        "ban",
        1,
        1.0,
        logging.getLogger("t"),
        source_period_label=None,
        replace_source_period_label=True,
    )
    assert calls and calls[0]["params"]["replace_period"] is True
    assert calls[0]["params"]["period"] is None


# ---------------------------------------------------- other producers of L --


def _captured_urls(monkeypatch, tmp_path, name: str, pipeline) -> list[str]:
    """URLs the download of pipeline *name* asks for, without network."""
    import importlib

    module = importlib.import_module(type(pipeline).__module__)
    seen: list[str] = []

    async def fake_download(_client, url, _output_path, **_kw):
        seen.append(url)
        return False

    async def fake_extract(_client, url, *_a, **_kw):
        seen.append(url)
        return None

    for helper in ("verified_download", "verified_download_gz"):
        if hasattr(module, helper):
            monkeypatch.setattr(module, helper, fake_download)
    # BD TOPO streams each .7z unextracted since plan 04.1-05.
    if hasattr(pipeline, "_download_archive"):
        monkeypatch.setattr(pipeline, "_download_archive", fake_extract)
    monkeypatch.setattr(module, "get_download_dir", lambda _name: tmp_path)
    pipeline.bind_scope(_scope(*pipeline.scope_dataset_keys))
    # Every file 404s: some pipelines raise on an empty download.
    with contextlib.suppress(Exception):
        pipeline.download()
    return sorted(set(seen))


# name -> (label, a pinned URL for 44, the current URL for 44, malformed labels)
PRODUCERS = {
    "cadastre": (
        "2026-03-01",
        "https://cadastre.data.gouv.fr/data/etalab-cadastre/2026-03-01/geojson/"
        "departements/44/cadastre-44-parcelles.json.gz",
        "https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/geojson/"
        "departements/44/cadastre-44-parcelles.json.gz",
        ["2026-03", "latest", "2026-02-30", "2026-03-01/../x"],
    ),
    "dvf": (
        "2025-12",
        "https://files.data.gouv.fr/geo-dvf/2025-12/csv/2025/departements/44.csv.gz",
        "https://files.data.gouv.fr/geo-dvf/latest/csv/2025/departements/44.csv.gz",
        ["2025-12-01", "2025-13", "latest", "2025_12"],
    ),
    "connectivite": (
        "2026_T1",
        "https://data.arcep.fr/fixe/maconnexioninternet/eligibilite/2026_T1/"
        "departement/actuel_44.csv.gz",
        "https://data.arcep.fr/fixe/maconnexioninternet/eligibilite/last/"
        "departement/actuel_44.csv.gz",
        ["2026_T5", "2026-T1", "last", "2026_T1/../x"],
    ),
    "batiments": (
        "2025-12-15",
        "https://data.geopf.fr/telechargement/download/BDTOPO/"
        "BDTOPO_3-5_TOUSTHEMES_GPKG_LAMB93_D044_2025-12-15/"
        "BDTOPO_3-5_TOUSTHEMES_GPKG_LAMB93_D044_2025-12-15.7z",
        "https://data.geopf.fr/telechargement/download/BDTOPO/"
        "BDTOPO_3-5_TOUSTHEMES_GPKG_LAMB93_D044_2025-12-15/"
        "BDTOPO_3-5_TOUSTHEMES_GPKG_LAMB93_D044_2025-12-15.7z",
        ["2025-12", "2025-12-32", "latest"],
    ),
}


def test_list_l_is_exactly_the_producers_that_still_serve_the_old_vintage():
    pinnable = {
        name
        for name, cls in run_etl.PIPELINE_MAP.items()
        if cls().supports_millesime_pin
    }
    # bdnb_batiment_parcelle reads its label in the export it downloads, and
    # discovers the current millesime on data.gouv.fr: proven by
    # tests/etl/test_bdnb_download.py, not by the URL capture below (04.7-19).
    assert pinnable == {"ban", "bdnb_batiment_parcelle", *PRODUCERS}


@pytest.mark.parametrize("name", sorted(PRODUCERS))
def test_pinned_producer_downloads_the_targeted_vintage(monkeypatch, tmp_path, name):
    label, pinned_url, _current, _bad = PRODUCERS[name]
    pipeline = run_etl.PIPELINE_MAP[name]()
    assert pipeline.millesime_mechanism == "segment_date"
    pipeline.pin_millesime(label)
    urls = _captured_urls(monkeypatch, tmp_path, name, pipeline)
    assert pinned_url in urls
    assert pipeline.source_period_label == label


@pytest.mark.parametrize("name", sorted(PRODUCERS))
def test_producer_without_millesime_keeps_its_current_url(monkeypatch, tmp_path, name):
    _label, _pinned, current_url, _bad = PRODUCERS[name]
    pipeline = run_etl.PIPELINE_MAP[name]()
    urls = _captured_urls(monkeypatch, tmp_path, name, pipeline)
    assert current_url in urls
    assert pipeline.source_period_label is None


@pytest.mark.parametrize(
    "name, label",
    [(name, bad) for name, spec in sorted(PRODUCERS.items()) for bad in spec[3]],
)
def test_producer_refuses_a_malformed_label(monkeypatch, capsys, name, label):
    code, err = _main_exit(monkeypatch, capsys, [name, "--millesime", label])
    assert code == 2
    assert f"millésime mal formé: {name}" in err
