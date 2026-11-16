"""Shared download helpers for ETL pipelines.

Provides streaming downloads (no RAM buffering), retry with tenacity,
rate limiting via asyncio.Semaphore, disk space checks, and post-download
integrity verification (Content-Length + gzip CRC).
"""

import asyncio
import gzip
import logging
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

logger = logging.getLogger(__name__)


# -- Constants ----------------------------------------------------------------

_CHUNK_SIZE = 65_536  # 64 KB chunks (shared by downloads and integrity checks)

# -- Size limits (P8-01 / B-02 / LC-01) ----------------------------------------
#
# The ETL disk volume also carries the PostgreSQL data directory, so an
# abnormal upstream file must never be able to fill it. These are safety
# nets against compromised/degraded sources, sized well above the largest
# legitimate historical datasets.

MAX_DOWNLOAD_BYTES = 10 * 1024**3  # 10 GB per downloaded file
MAX_DECOMPRESSED_BYTES = 20 * 1024**3  # 20 GB per gzip decompression
MAX_DECOMPRESSION_RATIO = 100  # decompressed/compressed ceiling (gzip bomb guard)

DEFAULT_MAX_ZIP_MEMBER_BYTES = 2 * 1024**3  # 2 GB per ZIP member (declared+actual)
DEFAULT_MAX_ZIP_TOTAL_BYTES = 20 * 1024**3  # cumulative declared uncompressed size
DEFAULT_MAX_ZIP_RATIO = 100  # declared uncompressed / archive size ceiling


class SizeLimitError(Exception):
    """Download/extraction exceeded a size cap -- file rejected, disk protected."""


# -- Integrity verification ---------------------------------------------------

# Threshold below which we do a full CRC check (read entire file through gzip).
# Above this, magic-bytes check is sufficient — CRC is validated implicitly
# when the file is decompressed later.
_GZIP_FULL_CHECK_THRESHOLD = 50 * 1024 * 1024  # 50 MB

_GZIP_MAGIC = b"\x1f\x8b"


class IntegrityError(Exception):
    """Download integrity check failed -- file is corrupt or truncated."""


def validate_gzip_integrity(path: Path) -> None:
    """Validate a gzip file's integrity.

    For files < 50 MB: checks magic bytes AND reads through the entire
    gzip stream to verify CRC.  For larger files: checks magic bytes
    only (the full CRC is validated implicitly during decompression).

    Raises IntegrityError on corrupt or truncated files.
    """
    # Magic bytes check (all sizes)
    try:
        with open(path, "rb") as f:
            magic = f.read(2)
    except OSError as exc:
        raise IntegrityError(f"Cannot read gzip file: {path.name}: {exc}") from exc

    if magic != _GZIP_MAGIC:
        raise IntegrityError(f"Not a valid gzip file: {path.name}")

    # Full CRC check for small files (fast enough to be worthwhile)
    if path.stat().st_size < _GZIP_FULL_CHECK_THRESHOLD:
        try:
            with gzip.open(path, "rb") as gz:
                while gz.read(_CHUNK_SIZE):
                    pass
        except EOFError as exc:
            raise IntegrityError(f"Truncated gzip file: {path.name}") from exc
        except (gzip.BadGzipFile, OSError) as exc:
            raise IntegrityError(f"Corrupt gzip file: {path.name}") from exc


# Per-loop semaphore: each event loop gets its own Semaphore instance
# to avoid cross-loop issues when scheduler and API use different loops.
_semaphores: dict[int, asyncio.Semaphore] = {}


def _get_semaphore() -> asyncio.Semaphore:
    """Get or create a download concurrency semaphore for the current loop."""
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    if loop_id not in _semaphores:
        _semaphores[loop_id] = asyncio.Semaphore(settings.etl_download_concurrency)
    return _semaphores[loop_id]


def check_disk_space(path: str | Path, min_gb: int | None = None) -> None:
    """Raise RuntimeError if free disk space at *path* is below threshold."""
    min_gb = settings.etl_min_disk_gb if min_gb is None else min_gb
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(target)
    free_gb = usage.free / (1024**3)
    if free_gb < min_gb:
        msg = (
            f"Insufficient disk space at {target}: "
            f"{free_gb:.1f} GB free, need {min_gb} GB"
        )
        raise RuntimeError(msg)
    logger.info("Disk space check OK: %.1f GB free at %s", free_gb, target)


def get_download_dir(dataset_name: str) -> Path:
    """Return the temp download directory for a dataset, creating it."""
    download_dir = Path(settings.etl_temp_dir) / f"datacommune_{dataset_name}"
    download_dir.mkdir(parents=True, exist_ok=True)
    return download_dir


def cleanup_download_dir(dataset_name: str) -> None:
    """Remove the temp download directory for a dataset after success."""
    download_dir = Path(settings.etl_temp_dir) / f"datacommune_{dataset_name}"
    if download_dir.exists():
        shutil.rmtree(download_dir, ignore_errors=True)
        logger.info("Cleaned up download dir: %s", download_dir)


# -- Retry decorator factory --------------------------------------------------


def _make_retry_decorator(max_retries: int | None = None):  # type: ignore[return]
    """Build a tenacity retry decorator for HTTP operations."""
    attempts = settings.etl_max_retries if max_retries is None else max_retries
    return retry(
        retry=retry_if_exception_type(
            (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                IntegrityError,
            )
        ),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        before_sleep=lambda state: logger.warning(
            "Retry %d/%d after error: %s",
            state.attempt_number,
            attempts,
            state.outcome.exception() if state.outcome else "unknown",
        ),
        reraise=True,
    )


# -- Bounded decompression (P8-01) ---------------------------------------------


def decompress_gz_bounded(
    gz_path: Path,
    output_path: Path,
    *,
    max_decompressed_bytes: int | None = None,
    max_ratio: int | None = None,
) -> None:
    """Decompress *gz_path* to *output_path* with bomb guards.

    Enforces an absolute cap on bytes written AND a maximum
    decompressed/compressed ratio, whichever is tighter. Defaults come
    from settings (etl_max_decompressed_bytes / etl_max_compression_ratio)
    so deployments can tune them without code changes. The partial output
    file is removed on any failure.
    """
    if max_decompressed_bytes is None:
        max_decompressed_bytes = settings.etl_max_decompressed_bytes
    if max_ratio is None:
        max_ratio = settings.etl_max_compression_ratio
    compressed_size = gz_path.stat().st_size
    limit = min(max_decompressed_bytes, compressed_size * max_ratio)
    written = 0
    try:
        with gzip.open(gz_path, "rb") as gz_in, open(output_path, "wb") as f:
            while chunk := gz_in.read(_CHUNK_SIZE):
                written += len(chunk)
                if written > limit:
                    raise SizeLimitError(
                        f"Decompression aborted for {output_path.name}: "
                        f"exceeded limit of {limit} bytes "
                        f"(cap={max_decompressed_bytes}, "
                        f"ratio={max_ratio}x of {compressed_size} compressed)"
                    )
                f.write(chunk)
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise


# -- ZIP extraction guards (B-02 / LC-01) ---------------------------------------


def _sanitize_zip_member_name(name: str) -> Path:
    """Reduce a ZIP member path to safe relative components.

    Mirrors stdlib ZipFile.extract() sanitization: absolute components,
    drive letters and '..' traversal are stripped.
    """
    parts = [
        part
        for part in name.replace("\\", "/").split("/")
        if part not in ("", ".", "..") and ":" not in part
    ]
    return Path(*parts)


def copy_zip_member(
    zf: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    target: Path,
    *,
    max_member_bytes: int | None = None,
) -> Path:
    """Copy one ZIP member to an exact target path with runtime byte cap.

    The cap counts ACTUAL bytes read from the stream, so it holds even when
    the archive header lies about member.file_size.
    """
    limit = (
        DEFAULT_MAX_ZIP_MEMBER_BYTES if max_member_bytes is None else max_member_bytes
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with zf.open(member) as src, open(target, "wb") as dst:
            while chunk := src.read(_CHUNK_SIZE):
                written += len(chunk)
                if written > limit:
                    raise SizeLimitError(
                        f"ZIP member '{member.filename}' exceeds cap of "
                        f"{limit} bytes -- extraction aborted"
                    )
                dst.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def safe_extract_member(
    zf: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    dest_dir: str | Path,
    *,
    max_member_bytes: int | None = None,
) -> Path:
    """Extract one ZIP member under *dest_dir* with path sanitization + cap."""
    rel = _sanitize_zip_member_name(member.filename)
    if not rel.parts:
        raise ValueError(f"Unsafe or empty ZIP member name: {member.filename!r}")
    return copy_zip_member(
        zf, member, Path(dest_dir) / rel, max_member_bytes=max_member_bytes
    )


def check_zip_budget(
    zf: zipfile.ZipFile,
    *,
    max_member_bytes: int | None = None,
    max_total_bytes: int | None = None,
    max_ratio: int | None = None,
) -> None:
    """Pre-flight declared-size budget check against zip bombs (B-02/LC-01).

    Validates every member's declared uncompressed size against a per-member
    cap, the cumulative size against a total cap, and the cumulative/archive
    ratio against a ceiling. Runtime caps are still enforced during extraction
    (copy_zip_member) in case headers lie.
    """
    max_member = (
        DEFAULT_MAX_ZIP_MEMBER_BYTES if max_member_bytes is None else max_member_bytes
    )
    max_total = (
        DEFAULT_MAX_ZIP_TOTAL_BYTES if max_total_bytes is None else max_total_bytes
    )
    ratio_limit = DEFAULT_MAX_ZIP_RATIO if max_ratio is None else max_ratio

    declared_sizes = [info.file_size for info in zf.infolist() if not info.is_dir()]
    total_declared = sum(declared_sizes)

    if declared_sizes and max(declared_sizes) > max_member:
        offender = max(zf.infolist(), key=lambda i: i.file_size)
        raise SizeLimitError(
            f"ZIP member '{offender.filename}' declares {offender.file_size} "
            f"bytes, above per-member cap of {max_member}"
        )
    if total_declared > max_total:
        raise SizeLimitError(
            f"ZIP archive declares {total_declared} uncompressed bytes in "
            f"total, above cumulative cap of {max_total}"
        )
    archive_size = Path(zf.filename).stat().st_size if zf.filename else 0
    if archive_size > 0 and total_declared > archive_size * ratio_limit:
        raise SizeLimitError(
            f"ZIP archive ratio {total_declared}/{archive_size} exceeds "
            f"{ratio_limit}x -- suspected zip bomb"
        )


def assert_zip_extraction_safe(zip_path: str | Path, zf: zipfile.ZipFile) -> None:
    """Pre-flight guard for pipeline ZIP extractions (B-02 / LC-01).

    Settings-driven variant of check_zip_budget(): per-member and cumulative
    declared sizes are capped by etl_max_decompressed_bytes, and the
    declared/compressed ratio by etl_max_compression_ratio. Call BEFORE any
    extraction so a zip bomb is rejected without writing a byte to disk.
    """
    cap = settings.etl_max_decompressed_bytes
    ratio_limit = settings.etl_max_compression_ratio

    declared_sizes = [info.file_size for info in zf.infolist() if not info.is_dir()]
    total_declared = sum(declared_sizes)

    if declared_sizes and max(declared_sizes) > cap:
        offender = max(zf.infolist(), key=lambda i: i.file_size)
        raise SizeLimitError(
            f"ZIP member '{offender.filename}' declares {offender.file_size} "
            f"bytes, above per-member cap of {cap}"
        )
    if total_declared > cap:
        raise SizeLimitError(
            f"ZIP archive declares {total_declared} uncompressed bytes in "
            f"total, above cumulative cap of {cap}"
        )
    archive_size = Path(zip_path).stat().st_size
    if archive_size > 0 and total_declared > archive_size * ratio_limit:
        raise SizeLimitError(
            f"ZIP archive ratio {total_declared}/{archive_size} exceeds "
            f"{ratio_limit}x -- suspected zip bomb"
        )


def extract_zip_member_bounded(
    zf: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    target: Path,
) -> Path:
    """Extract one ZIP member to *target* with the settings-driven byte cap.

    The cap counts ACTUAL bytes read from the stream, so it holds even when
    the archive header lies about member.file_size (LC-01).
    """
    return copy_zip_member(
        zf, member, target, max_member_bytes=settings.etl_max_decompressed_bytes
    )


# -- Streaming download functions ---------------------------------------------


def _ignore_url(_url: str) -> None:
    """Default of ``on_final_url``: the final URL is not wanted."""


async def streaming_download(
    client: httpx.AsyncClient,
    url: str,
    output_path: Path,
    *,
    max_retries: int | None = None,
    max_bytes: int | None = None,
    on_final_url: Callable[[str], None] | None = None,
) -> bool:
    """Download a URL to disk via streaming (no full-response RAM buffer).

    Aborts and unlinks the partial file if the transfer exceeds *max_bytes*
    (P8-01). Defaults to settings.etl_max_download_bytes, resolved at call
    time so deployments can tune the cap. Returns True on success, False on
    404 (graceful skip). Raises SizeLimitError on cap overrun, other HTTP
    errors after retries. *on_final_url* receives the URL the body was read
    from, after redirects (a producer's ``last`` folder names its vintage
    there, C24).
    """
    if max_bytes is None:
        max_bytes = settings.etl_max_download_bytes
    report_final_url = on_final_url or _ignore_url
    sem = _get_semaphore()

    @_make_retry_decorator(max_retries)
    async def _do_download() -> bool:
        async with sem, client.stream("GET", url, follow_redirects=True) as response:
            if response.status_code == 404:
                return False

            # Raise for 429/5xx but let tenacity handle retry
            if response.status_code == 429:
                raise httpx.TimeoutException(f"Rate limited (429) for {url}")
            response.raise_for_status()

            # Capture expected size for post-download verification
            expected_size_raw = response.headers.get("content-length")

            # Fail fast when the source declares an oversized body
            if expected_size_raw is not None and int(expected_size_raw) > max_bytes:
                raise SizeLimitError(
                    f"Download aborted for {output_path.name}: declared size "
                    f"{int(expected_size_raw)} bytes exceeds cap {max_bytes}"
                )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            total_bytes = 0
            with open(output_path, "wb") as f:
                async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                    f.write(chunk)
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        f.close()
                        output_path.unlink(missing_ok=True)
                        raise SizeLimitError(
                            f"Download aborted for {output_path.name}: "
                            f"exceeded cap of {max_bytes} bytes"
                        )

            # Content-Length verification (best-effort: skip if header absent)
            if expected_size_raw is not None:
                expected_size = int(expected_size_raw)
                if total_bytes != expected_size:
                    output_path.unlink(missing_ok=True)
                    raise IntegrityError(
                        f"Size mismatch for {output_path.name}: "
                        f"expected {expected_size} bytes, got {total_bytes}"
                    )

            logger.debug(
                "Downloaded %s: %.1f MB",
                output_path.name,
                total_bytes / (1024 * 1024),
            )
            report_final_url(str(response.url))
            return True

    return await _do_download()


async def streaming_download_gz(
    client: httpx.AsyncClient,
    url: str,
    output_path: Path,
    *,
    max_retries: int | None = None,
) -> bool:
    """Download a .gz URL and decompress to disk via streaming.

    Downloads to a temp .gz file, then decompresses chunk-by-chunk
    to output_path. Never holds the full file in RAM.

    Returns True on success, False on 404.
    """
    gz_path = output_path.with_suffix(output_path.suffix + ".gz")

    ok = await streaming_download(client, url, gz_path, max_retries=max_retries)
    if not ok:
        return False

    # Decompress chunk-by-chunk (streaming, not gzip.decompress()),
    # bounded by size/ratio caps (P8-01).
    try:
        decompress_gz_bounded(gz_path, output_path)
    finally:
        # Always clean up the .gz temp file
        gz_path.unlink(missing_ok=True)

    return True


# -- Verified download wrappers -----------------------------------------------


async def verified_download(
    client: httpx.AsyncClient,
    url: str,
    output_path: Path,
    *,
    max_retries: int | None = None,
    on_final_url: Callable[[str], None] | None = None,
) -> bool:
    """Download with post-download integrity verification.

    Wraps streaming_download() (which already does Content-Length check
    and retries on IntegrityError).  For .gz files, additionally runs
    validate_gzip_integrity() after download.

    Returns True on success, False on 404. *on_final_url*: see
    :func:`streaming_download`.
    """
    ok = await streaming_download(
        client, url, output_path, max_retries=max_retries, on_final_url=on_final_url
    )
    if not ok:
        return False

    # Extra gzip integrity check for .gz files
    if output_path.suffix == ".gz":
        try:
            validate_gzip_integrity(output_path)
        except IntegrityError:
            output_path.unlink(missing_ok=True)
            raise

    return True


async def verified_download_gz(
    client: httpx.AsyncClient,
    url: str,
    output_path: Path,
    *,
    max_retries: int | None = None,
) -> bool:
    """Download a .gz URL, verify integrity, then decompress to disk.

    Like streaming_download_gz but with gzip integrity verification
    BEFORE decompression (fail-fast on corrupt archives).

    The download+verify cycle is wrapped in its own retry loop so that
    a corrupt .gz triggers a full re-download, not just a re-verify.

    Returns True on success, False on 404.
    """
    gz_path = output_path.with_suffix(output_path.suffix + ".gz")
    attempts = settings.etl_file_max_retries if max_retries is None else max_retries

    @retry(
        retry=retry_if_exception_type(IntegrityError),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        before_sleep=lambda state: logger.warning(
            "Integrity retry %d/%d for %s: %s",
            state.attempt_number,
            attempts,
            gz_path.name,
            state.outcome.exception() if state.outcome else "unknown",
        ),
        reraise=True,
    )
    async def _download_and_verify() -> bool:
        ok = await streaming_download(client, url, gz_path, max_retries=max_retries)
        if not ok:
            return False

        # Validate gzip before decompression
        try:
            validate_gzip_integrity(gz_path)
        except IntegrityError:
            gz_path.unlink(missing_ok=True)
            raise

        return True

    ok = await _download_and_verify()
    if not ok:
        return False

    # Decompress chunk-by-chunk (streaming, not gzip.decompress()),
    # bounded by size/ratio caps (P8-01).
    try:
        decompress_gz_bounded(gz_path, output_path)
    finally:
        # Always clean up the .gz temp file
        gz_path.unlink(missing_ok=True)

    return True
