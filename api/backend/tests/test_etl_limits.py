"""Security limits for ETL downloads and extractions (lot L4).

Covers P8-01/B-02/LC-01/A-01 (size caps at download and decompression,
per-member and cumulative, anti-zip-bomb ratio) and A-02 (no DSN
credentials in ogr2ogr argv).
"""

import gzip
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.etl.base import split_dsn_credentials
from app.etl.download import (
    SizeLimitError,
    assert_zip_extraction_safe,
    decompress_gz_bounded,
    extract_zip_member_bounded,
    streaming_download,
)

pytestmark = pytest.mark.unit

SECRET_DSN = "postgresql://datacommune:hunter2@localhost:5432/datacommune"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _streaming_client(chunks: list[bytes], content_length: int | None = None):
    """Build a fake httpx.AsyncClient whose stream() yields *chunks*."""
    response = MagicMock()
    response.status_code = 200
    response.headers = (
        {"content-length": str(content_length)} if content_length is not None else {}
    )

    async def _aiter(_size):
        for chunk in chunks:
            yield chunk

    response.aiter_bytes = _aiter

    @asynccontextmanager
    async def _stream(_method, _url, follow_redirects=True):
        yield response

    client = MagicMock()
    client.stream = _stream
    return client


def _make_zip(tmp_path: Path, entries: dict[str, bytes], name: str = "src.zip"):
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for member, payload in entries.items():
            zf.writestr(member, payload)
    return zip_path


# ---------------------------------------------------------------------------
# Download cap (P8-01 / B-02)
# ---------------------------------------------------------------------------


async def test_download_over_cap_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etl_max_download_bytes", 1024)
    out = tmp_path / "big.bin"
    client = _streaming_client([b"x" * 800, b"x" * 800], content_length=1600)

    with pytest.raises(SizeLimitError):
        await streaming_download(client, "http://example/big.bin", out)

    assert not out.exists()


async def test_download_under_cap_ok(tmp_path):
    out = tmp_path / "ok.bin"
    client = _streaming_client([b"x" * 100], content_length=100)

    assert await streaming_download(client, "http://example/ok.bin", out) is True
    assert out.read_bytes() == b"x" * 100


# ---------------------------------------------------------------------------
# Gzip decompression cap + ratio (P8-01)
# ---------------------------------------------------------------------------


def test_gz_decompression_over_cap_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etl_max_decompressed_bytes", 1024)
    gz_path = tmp_path / "data.gz"
    gz_path.write_bytes(gzip.compress(b"y" * 4096))
    out = tmp_path / "data"

    with pytest.raises(SizeLimitError):
        decompress_gz_bounded(gz_path, out)

    assert not out.exists()


def test_gz_ratio_over_cap_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etl_max_compression_ratio", 2)
    gz_path = tmp_path / "data.gz"
    gz_path.write_bytes(gzip.compress(b"y" * 4096))
    out = tmp_path / "data"

    with pytest.raises(SizeLimitError):
        decompress_gz_bounded(gz_path, out)


def test_gz_legit_decompression_ok(tmp_path):
    payload = b"legit data\n" * 10
    gz_path = tmp_path / "data.gz"
    gz_path.write_bytes(gzip.compress(payload))
    out = tmp_path / "data"

    decompress_gz_bounded(gz_path, out)

    assert out.read_bytes() == payload


# ---------------------------------------------------------------------------
# ZIP extraction limits: per-member, cumulative, ratio (B-02 / LC-01)
# ---------------------------------------------------------------------------


def test_zip_member_over_cap_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etl_max_decompressed_bytes", 1024)
    zip_path = _make_zip(tmp_path, {"big.csv": b"z" * 4096})

    with zipfile.ZipFile(zip_path) as zf, pytest.raises(SizeLimitError):
        assert_zip_extraction_safe(zip_path, zf)


def test_zip_cumulative_over_cap_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etl_max_decompressed_bytes", 1024)
    zip_path = _make_zip(tmp_path, {"a.csv": b"a" * 800, "b.csv": b"b" * 800})

    with zipfile.ZipFile(zip_path) as zf, pytest.raises(SizeLimitError):
        assert_zip_extraction_safe(zip_path, zf)


def test_zip_ratio_over_cap_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etl_max_compression_ratio", 2)
    zip_path = _make_zip(tmp_path, {"bomb.csv": b"z" * 4096})

    with zipfile.ZipFile(zip_path) as zf, pytest.raises(SizeLimitError):
        assert_zip_extraction_safe(zip_path, zf)


def test_zip_legit_passes(tmp_path):
    zip_path = _make_zip(tmp_path, {"a.csv": "col1,col2\n1,2\n" * 50})

    with zipfile.ZipFile(zip_path) as zf:
        assert_zip_extraction_safe(zip_path, zf)


def test_extract_member_bounded_writes_file(tmp_path):
    zip_path = _make_zip(tmp_path, {"a.csv": "col1,col2\n1,2\n" * 50})
    target = tmp_path / "out" / "a.csv"

    with zipfile.ZipFile(zip_path) as zf:
        extract_zip_member_bounded(zf, zf.infolist()[0], target)

    assert target.read_text().startswith("col1,col2")


def test_extract_member_bounded_over_cap_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etl_max_decompressed_bytes", 1024)
    zip_path = _make_zip(tmp_path, {"big.csv": b"z" * 4096})
    target = tmp_path / "out" / "big.csv"

    with zipfile.ZipFile(zip_path) as zf, pytest.raises(SizeLimitError):
        extract_zip_member_bounded(zf, zf.infolist()[0], target)

    assert not target.exists()


# ---------------------------------------------------------------------------
# A-02: no DSN credentials in ogr2ogr argv
# ---------------------------------------------------------------------------


def test_split_dsn_credentials_moves_password_to_env():
    safe_dsn, env = split_dsn_credentials(SECRET_DSN)

    assert "hunter2" not in safe_dsn
    assert env.get("PGPASSWORD") == "hunter2"


def test_split_dsn_without_password_unchanged():
    dsn = "postgresql://localhost:5432/db"

    safe_dsn, env = split_dsn_credentials(dsn)

    assert safe_dsn == dsn
    assert env == {}


def test_bdtopo_ogr_cmd_has_no_dsn_secret():
    from app.etl_bdtopo.pipeline import BdTopoPipeline

    cmd, env = BdTopoPipeline()._build_ogr_cmd(
        "C:/tmp/x.gpkg", "SELECT cleabs FROM batiment", SECRET_DSN, "EPSG:2154"
    )

    assert all("hunter2" not in part for part in cmd)
    assert env.get("PGPASSWORD") == "hunter2"


def test_cadastre_ogr_cmd_has_no_dsn_secret():
    from app.etl_cadastre.pipeline import CadastrePipeline

    cmd, env = CadastrePipeline()._build_ogr_cmd(
        "/vsigzip/C:/tmp/x.json.gz", SECRET_DSN
    )

    assert all("hunter2" not in part for part in cmd)
    assert env.get("PGPASSWORD") == "hunter2"
