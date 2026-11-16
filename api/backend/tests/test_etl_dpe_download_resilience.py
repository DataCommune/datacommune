"""DPE download resilience: visible retries, retry budget, pair reuse (04.2-03).

Run 61 (2026-10-29, national) failed on one ``httpx.RemoteProtocolError``
("incomplete chunked read") after 2697 s of download. The error is raised
while httpx reads the body inside ``client.get``, hence inside the tenacity
decorator: the retry played, silently, and its whole budget was 30 s of
backoff (2+4+8+16) for 5 attempts. These tests pin the visible retry, the
larger budget and the reuse, by the next run, of the complete pairs of an
unpublished import. The transport stays wholly local.
"""

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.etl_dpe import config
from app.etl_dpe.pipeline import DpePipeline
from app.etl_dpe.sources import DPE_SOURCES
from tests.test_etl_dpe_sources import install_source_api

TRUNCATED = "peer closed connection without sending complete message body"


@pytest.fixture(autouse=True)
def _dpe_scope(bind_departements):
    bind_departements(("44", "85"), DpePipeline)


class _TruncatedBody(httpx.AsyncByteStream):
    """A chunked body whose peer closes before the end, like run 61."""

    async def __aiter__(self):
        yield b'{"results": ['
        raise httpx.RemoteProtocolError(f"{TRUNCATED} (incomplete chunked read)")


def _page_request(request) -> bool:
    return (
        request.url.path.endswith("/lines")
        and request.url.params.get("select") != "numero_dpe"
    )


def _truncate_pages(*, slug, department, times):
    """Truncate the body of the first *times* page reads of one pair."""
    state = {"left": times}

    def on_response(request, response):
        if (
            _page_request(request)
            and f"/{slug}/" in request.url.path
            and f'"{department}"' in request.url.params["qs"]
            and state["left"] > 0
        ):
            state["left"] -= 1
            return httpx.Response(200, stream=_TruncatedBody())
        return response

    return on_response


@pytest.fixture
def sleeps(monkeypatch):
    """Record tenacity's backoff instead of sleeping."""
    recorded = []

    async def record(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(DpePipeline._request_json.retry, "sleep", record)
    return recorded


def test_truncated_body_is_retried_and_each_retry_is_logged(
    monkeypatch, tmp_path, caplog, sleeps
):
    install_source_api(
        monkeypatch,
        tmp_path,
        on_response=_truncate_pages(slug="dpe02neuf", department="85", times=2),
    )
    caplog.set_level(logging.WARNING, logger="app.etl_dpe.pipeline")
    pipeline = DpePipeline()
    try:
        pipeline.download()
    finally:
        pipeline.close()
    retries = [
        record for record in caplog.records if record.message.startswith("DPE request")
    ]
    assert [record.levelno for record in retries] == [logging.WARNING] * 2
    for attempt, record in enumerate(retries, start=1):
        assert f"endpoint={DPE_SOURCES['dpe02neuf'].endpoint}" in record.message
        assert "departement=85" in record.message
        assert f"attempt={attempt}/{config.MAX_RETRIES}" in record.message
        assert "RemoteProtocolError" in record.message
        assert TRUNCATED in record.message
    assert sleeps == [2, 4]


def test_retry_budget_is_ten_attempts_up_to_300_seconds(monkeypatch, tmp_path, sleeps):
    calls = install_source_api(
        monkeypatch,
        tmp_path,
        on_response=_truncate_pages(slug="dpe02neuf", department="85", times=10**6),
    )
    pipeline = DpePipeline()
    with pytest.raises(httpx.RemoteProtocolError, match=TRUNCATED):
        pipeline.download()
    failed = [
        request
        for request in calls
        if _page_request(request)
        and "/dpe02neuf/" in request.url.path
        and '"85"' in request.url.params["qs"]
    ]
    assert config.MAX_RETRIES == 10
    assert len(failed) == 10
    # Exponential backoff capped at 300 s: about 13.5 min before giving up,
    # instead of the 30 s that run 61 had.
    assert sleeps == [2, 4, 8, 16, 32, 64, 128, 256, 300]
    assert not (tmp_path / "datacommune_dpe" / "manifest.json").exists()


# -- Pair reuse between runs ---------------------------------------------------

ALL_PAIRS = {(slug, department) for slug in DPE_SOURCES for department in ("44", "85")}


def _download(expect_failure=None):
    """One download run; returns (import_id, manifest or None)."""
    pipeline = DpePipeline()
    try:
        if expect_failure is not None:
            with pytest.raises(expect_failure):
                pipeline.download()
            return pipeline._import_id, None
        pipeline.download()
        return pipeline._import_id, pipeline.import_manifest
    finally:
        pipeline.close()


def _raw(tmp_path, import_id) -> Path:
    return tmp_path / "datacommune_dpe" / "raw" / import_id


def _receipt_pairs(tmp_path, import_id) -> set[tuple[str, str]]:
    pairs = set()
    for path in _raw(tmp_path, import_id).glob("*.pair.json"):
        entry = json.loads(path.read_text(encoding="utf-8"))["entry"]
        pairs.add((entry["source_slug"], entry["department"]))
    return pairs


def _downloaded_pairs(calls) -> set[tuple[str, str]]:
    """Pairs whose pages were requested from the source."""
    return {
        (
            request.url.path.split("/")[5],
            "44" if '"44"' in request.url.params["qs"] else "85",
        )
        for request in calls
        if _page_request(request)
    }


def _reuse_logs(caplog) -> list[str]:
    return [
        record.message
        for record in caplog.records
        if record.message.startswith("DPE download reuse:")
    ]


def test_failed_download_reuses_complete_pairs_on_next_run(
    monkeypatch, tmp_path, caplog, sleeps
):
    caplog.set_level(logging.INFO, logger="app.etl_dpe.pipeline")
    install_source_api(
        monkeypatch,
        tmp_path,
        on_response=_truncate_pages(slug="dpe02neuf", department="85", times=10**6),
    )
    failed_id, _ = _download(expect_failure=httpx.RemoteProtocolError)
    complete = _receipt_pairs(tmp_path, failed_id)
    # A pair without its whole page chain leaves no receipt.
    assert complete and ("dpe02neuf", "85") not in complete

    calls = install_source_api(monkeypatch, tmp_path)
    import_id, manifest = _download()
    assert _downloaded_pairs(calls) == ALL_PAIRS - complete
    assert _reuse_logs(caplog) == [
        f"DPE download reuse: {len(complete)} pairs from import {failed_id}"
    ]
    manifest.validate()
    for entry in manifest.files:
        assert entry.import_id == import_id
        assert Path(entry.path).parent == _raw(tmp_path, import_id).resolve()
        if entry.pair in complete:
            assert entry.reused_from == failed_id
            origin = _raw(tmp_path, failed_id) / Path(entry.path).name.replace(
                import_id, failed_id
            )
            assert Path(entry.path).read_bytes() == origin.read_bytes()
        else:
            assert entry.reused_from is None
    saved = json.loads(
        (tmp_path / "datacommune_dpe" / "manifest.json").read_text(encoding="utf-8")
    )
    assert {
        (entry["source_slug"], entry["department"]): entry["reused_from"]
        for entry in saved["files"]
    } == {pair: failed_id if pair in complete else None for pair in ALL_PAIRS}


def test_reuse_keeps_the_downloading_import_across_runs(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="app.etl_dpe.pipeline")
    install_source_api(monkeypatch, tmp_path)
    first, _ = _download()
    _download()
    calls = install_source_api(monkeypatch, tmp_path)
    _, manifest = _download()
    assert _downloaded_pairs(calls) == set()
    assert {entry.reused_from for entry in manifest.files} == {first}
    assert _reuse_logs(caplog)[-1] == f"DPE download reuse: 4 pairs from import {first}"


def test_damaged_pair_is_downloaded_again_without_touching_its_origin(
    monkeypatch, tmp_path
):
    install_source_api(monkeypatch, tmp_path)
    _, manifest = _download()
    damaged = next(
        entry for entry in manifest.files if entry.pair == ("dpe03existant", "85")
    )
    page = Path(damaged.pages[0].path)
    page.write_bytes(page.read_bytes() + b" ")
    original_csv = Path(damaged.path).read_bytes()
    calls = install_source_api(monkeypatch, tmp_path)
    _, manifest = _download()
    assert _downloaded_pairs(calls) == {("dpe03existant", "85")}
    by_pair = {entry.pair: entry for entry in manifest.files}
    assert by_pair[("dpe03existant", "85")].reused_from is None
    # The new download wrote new files, never through a link to the origin.
    assert Path(damaged.path).read_bytes() == original_csv
    assert page.read_bytes().endswith(b" ")


def test_reuse_refused_when_the_scope_changes(
    monkeypatch, tmp_path, caplog, bind_departements
):
    caplog.set_level(logging.INFO, logger="app.etl_dpe.pipeline")
    install_source_api(monkeypatch, tmp_path)
    _download()
    bind_departements(("44",), DpePipeline)
    calls = install_source_api(monkeypatch, tmp_path)
    _, manifest = _download()
    assert _downloaded_pairs(calls) == {(slug, "44") for slug in DPE_SOURCES}
    assert {entry.reused_from for entry in manifest.files} == {None}
    assert _reuse_logs(caplog) == []


def test_reuse_refused_when_the_column_selection_changes(monkeypatch, tmp_path):
    install_source_api(monkeypatch, tmp_path)
    _download()
    for slug, source in list(DPE_SOURCES.items()):
        monkeypatch.setitem(
            DPE_SOURCES, slug, replace(source, columns=source.columns[:-1])
        )
    calls = install_source_api(monkeypatch, tmp_path)
    _, manifest = _download()
    assert _downloaded_pairs(calls) == ALL_PAIRS
    assert {entry.reused_from for entry in manifest.files} == {None}


def test_reuse_refused_when_the_source_version_changes(monkeypatch, tmp_path):
    install_source_api(monkeypatch, tmp_path)
    _download()

    def new_version(request, response):
        if request.url.path.endswith("/lines"):
            return response
        metadata = response.json()
        metadata["dataUpdatedAt"] = "2026-10-29T03:00:00.000Z"
        return httpx.Response(200, json=metadata)

    calls = install_source_api(monkeypatch, tmp_path, on_response=new_version)
    _, manifest = _download()
    assert _downloaded_pairs(calls) == ALL_PAIRS
    assert {entry.reused_from for entry in manifest.files} == {None}


@pytest.mark.parametrize("max_age_hours, age_hours", [(72, 73), (0, 0)])
def test_reuse_is_bounded_in_time(monkeypatch, tmp_path, max_age_hours, age_hours):
    install_source_api(monkeypatch, tmp_path)
    first, _ = _download()
    observed = (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat()
    for path in _raw(tmp_path, first).glob("*.pair.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["entry"]["observed_at"] = observed
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(settings, "dpe_download_reuse_max_age_hours", max_age_hours)
    calls = install_source_api(monkeypatch, tmp_path)
    _download()
    assert _downloaded_pairs(calls) == ALL_PAIRS


# -- Bounded concurrency between pairs -----------------------------------------


def test_download_concurrency_keeps_five_and_is_bounded(monkeypatch):
    # 5 is the throughput that ran in production before this setting (44/85).
    monkeypatch.delenv("DPE_DOWNLOAD_CONCURRENCY", raising=False)
    assert Settings(_env_file=None).dpe_download_concurrency == 5
    for accepted in (1, 8):
        monkeypatch.setenv("DPE_DOWNLOAD_CONCURRENCY", str(accepted))
        assert Settings(_env_file=None).dpe_download_concurrency == accepted
    for refused in ("0", "9"):
        monkeypatch.setenv("DPE_DOWNLOAD_CONCURRENCY", refused)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


def _pair_bytes(manifest):
    """Pair order of the manifest, with the bytes of its CSV and pages."""
    return [
        (
            entry.pair,
            Path(entry.path).read_bytes(),
            [Path(page.path).read_bytes() for page in entry.pages],
            [(page.after, page.next_after, page.rows) for page in entry.pages],
        )
        for entry in manifest.files
    ]


def test_concurrent_pairs_keep_manifest_order_and_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "dpe_download_reuse_max_age_hours", 0)
    collect = DpePipeline._collect_pages
    active, peaks = set(), []

    async def tracked(self, client, source, department, output):
        pair = (source.slug, department)
        active.add(pair)
        peaks.append(len(active))
        # Overlapping pairs; the first pair in manifest order finishes last.
        await asyncio.sleep(0.2 if pair == ("dpe03existant", "44") else 0.05)
        try:
            return await collect(self, client, source, department, output)
        finally:
            active.discard(pair)

    monkeypatch.setattr(DpePipeline, "_collect_pages", tracked)
    results = {}
    for concurrency in (1, 3):
        monkeypatch.setattr(settings, "dpe_download_concurrency", concurrency)
        peaks.clear()
        install_source_api(monkeypatch, tmp_path / str(concurrency))
        _, manifest = _download()
        assert max(peaks) == concurrency
        results[concurrency] = _pair_bytes(manifest)
    assert [pair for pair, *_ in results[3]] == [
        (slug, department) for slug in DPE_SOURCES for department in ("44", "85")
    ]
    assert results[3] == results[1]
