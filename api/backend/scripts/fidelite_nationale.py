#!/usr/bin/env python3
"""National fidelity counts: producer, archived generation, loaded base (04.1-03).

There is no reference base outside 44/85: fidelity is judged against the count
of the sources of the generation that was loaded, with the ETL's own filters
reproduced here and cited in every output (``filter``). Live producer counts
size the national import (plan 22); they never judge an import of another date.

Four modes, JSON on stdout only for the first three (they open no file for
writing, so they can run as root on the hypervisor through
``ssh <hypervisor> 'python3 - ...' < this file``):

    count  --source S [--departements L] [--archive-run DATASET:RUN_ID]
           producer files read as streams (stdlib only), or the objects of an
           archived run (SOURCE_ARCHIVE_DIR, read-only mount of the tool
           container). Sources: arcep cadastre cadastre_sections rnb ban dvf
           dpe bdtopo_wfs, and batiments (archive only: GPKG of the edition).
    loaded --dataset D
           rows of the served tables per departement (psycopg, DATABASE_URL,
           read-only transaction; run by ``wb-tool.sh --read-only``).
    diff   --producer P.json --loaded L.json [--live V.json] [--explain E.json]
           per departement and metric: producer, loaded, gap and class
           (egal, ecart_explique, ecart_a_consigner, absent_producteur,
           absent_base, panne_producteur). A gap is ``ecart_explique`` only
           when an explanation names that exact metric, departement and gap.
    floors --producteurs DIR --out FILE
           generates app/scope/producer_counts.py from the producer counts
           (plan 04.1-17); workstation only, the one mode that writes.

Sent on stdin, the script cannot hash itself: the caller passes the SHA-256 of
the file it sends (``--script-sha256``), recorded with its origin.

Copies of the ETL constants (departements, URL templates, parsing rules) are
deliberate: the host has no ``app`` package. tests/test_fidelite_nationale.py
fails when a copy drifts from the application.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import gzip
import hashlib
import http.client
import io
import json
import os
import re
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, BinaryIO

# Copy of app.etl.base.DEPARTEMENTS (102 codes), checked by the test.
DEPARTEMENTS_102: tuple[str, ...] = (
    *[f"{i:02d}" for i in range(1, 20)],
    "2A",
    "2B",
    *[str(i) for i in range(21, 96)],
    *[str(i) for i in range(971, 977)],
)

# URL templates copied from the pipelines (checked by the test).
ARCEP_BASE = "https://data.arcep.fr/fixe/maconnexioninternet"
ARCEP_BASE_IMB_URL = f"{ARCEP_BASE}/base_imb/last/departement/base_imb_{{dept}}.csv.gz"
ARCEP_ELIGIBILITE_URL = (
    f"{ARCEP_BASE}/eligibilite/last/departement/actuel_{{dept}}.csv.gz"
)
CADASTRE_BASE_URL = (
    "https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/geojson/departements"
)
SECTIONS_URL = (
    "https://cadastre.data.gouv.fr/data/etalab-cadastre/latest/geojson/"
    "departements/{dept}/cadastre-{dept}-sections.json.gz"
)
RNB_EXPORT_URL_TEMPLATE = (
    "https://rnb-opendata.s3.fr-par.scw.cloud/files/RNB_{dept}.csv.zip"
)

# BDNB departemental export (app/etl_bdnb/config.py and transform.py, checked
# by the test). Millesime as in the URL (``2026-02-b``), departement on two
# characters, Corsica lower case; an absent object answers 403 (listing refused).
BDNB_EXPORT_URL_TEMPLATE = (
    "https://open-data.s3.fr-par.scw.cloud/bdnb_millesime_{millesime}/"
    "millesime_{millesime}_dep{dept}/"
    "open_data_millesime_{millesime}_dep{dept}_csv.zip"
)
BDNB_MEMBER_RELATIONS = "csv/rel_batiment_groupe_parcelle.csv"
BDNB_MEMBER_FFO = "csv/batiment_groupe_ffo_bat.csv"
BDNB_MEMBER_VERSION = "csv/metadonnees_bdnb_version.csv"
BDNB_CSV_SEPARATOR = ";"
BDNB_RELATION_COLUMNS: tuple[str, ...] = (
    "batiment_groupe_id",
    "parcelle_id",
    "parcelle_principale",
)
BDNB_FFO_COLUMNS: tuple[str, ...] = (
    "batiment_groupe_id",
    "annee_construction",
    "mat_mur_txt",
    "mat_toit_txt",
)
BDNB_VERSION_COLUMNS: tuple[str, ...] = ("millesime", "version", "date_publication")
BDNB_MAX_MEMBER_BYTES = 2 * 1024**3
BDNB_MAX_COMPRESSION_RATIO = 50
BDNB_DEPT_RE = re.compile(r"(?:^|_)dep([0-9]{2}|2[ab])(?:_csv)?\.zip$")
BDNB_LABEL_RE = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])\.[a-z]")
BDNB_MILLESIME_RE = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])-[a-z]")
BDNB_YEAR_RE = re.compile(r"-?[0-9]+")
# SHA-256 of _TRANSFER_SQL + "\n" + _DUPLICATES_SQL of app/etl_bdnb/pipeline.py,
# whitespace collapsed: the bdnb count reproduces that SQL, so any edit of the
# swap turns the test red until this count is reviewed.
BDNB_SWAP_SQL_SHA256 = (
    "a1078095ea1ade62724d71c08ed640227d2f8112a0257e6a840070c179a023ba"
)
BAN_BASE_URL = "https://adresse.data.gouv.fr/data/ban/adresses/latest/csv"
DVF_BASE_URL = "https://files.data.gouv.fr/geo-dvf/latest/csv/"
WFS_HITS_URL = (
    "https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
    "&TYPENAMES=BDTOPO_V3:batiment&RESULTTYPE=hits"
)

# Territory envelopes for the WFS estimate (lat, lon order of EPSG:4326 in
# WFS 2.0). Each keeps its neighbours out (977/978 north of 971).
WFS_TERRITORIES: dict[str, tuple[float, float, float, float]] = {
    "metropole": (41.3, -5.3, 51.2, 9.6),
    "971": (15.8, -61.9, 16.6, -60.9),
    "972": (14.3, -61.3, 14.95, -60.75),
    "973": (2.0, -54.7, 5.9, -51.5),
    "974": (-21.45, 55.2, -20.85, 55.9),
    "976": (-13.1, 44.9, -12.6, 45.35),
    "975": (46.7, -56.5, 47.2, -56.1),
    "977": (17.85, -62.95, 17.98, -62.78),
    "978": (18.0, -63.17, 18.13, -62.96),
}

# pandas.read_csv default NA strings: the ETL reads with pandas, so a value in
# this set is missing there (dropna, NaN merge keys, NULLIF('') after export).
PANDAS_NA = frozenset(
    {
        "",
        "#N/A",
        "#N/A N/A",
        "#NA",
        "-1.#IND",
        "-1.#QNAN",
        "-NaN",
        "-nan",
        "1.#IND",
        "1.#QNAN",
        "<NA>",
        "N/A",
        "NA",
        "NULL",
        "NaN",
        "None",
        "n/a",
        "nan",
        "null",
    }
)

FIELD_SIZE_LIMIT = 16 * 1024 * 1024  # app/etl_rnb_liens/transform.py
USER_AGENT = "datacommune-fidelite-nationale/1"
HTTP_TIMEOUT = 300
RETRIES = 3

FILTERS: dict[str, str] = {
    "arcep": (
        "app/etl_arcep/pipeline.py transform(): inner join base_imb_XX x actuel_XX "
        "on imb_id (pandas merge, NA keys never kept); swap(): id_imb and "
        "technologie non empty; code_departement = _derive_code_departement("
        "addr_code_insee of base_imb), file departement when missing"
    ),
    "cadastre": (
        "app/etl_cadastre/pipeline.py: every feature of cadastre-XX-parcelles.json.gz "
        "(ogr2ogr); code_departement = _COMMUNE_DEPARTEMENT_SQL on property commune; "
        "swap() excludes geometries empty after ST_MakeValid (not reproducible "
        "without GEOS: counted in the base, never here); sommets = positions of "
        "the GeoJSON rings, closing point included (ST_NPoints)"
    ),
    "cadastre_sections": (
        "scripts/import_geo_boundaries.py insert_sections(): every feature of "
        "cadastre-XX-sections.json.gz, departement = file, ON CONFLICT (id): "
        "distinct properties.id per departement"
    ),
    "rnb": (
        "app/etl_rnb_liens/transform.py iter_link_rows(): rows with rnb_id, one pair "
        "per distinct non-empty addresses[].cle_interop_ban, buildings without key "
        "dropped; pipeline.py swap(): DISTINCT ON (id_rnb, cle_ban); departement = "
        "file. Cross-file duplicates measured only for 4 files or fewer"
    ),
    "ban": (
        "app/etl_ban/transform.py transform_ban_csv() and pipeline.py swap(): rows "
        "of adresses-XX.csv(.gz) with lon and lat non missing; departement = file"
    ),
    "dvf": (
        "app/etl_dvf/config.py YEARS, transform.py transform_dvf_csv(): every "
        "row, coordinates or not (phase 04.4 D-01), code_departement = _derive_code_"
        "departement(code_commune); reconstruction.py: mutations_grain = distinct "
        "(code_departement, id_mutation), mutation_parcelles = distinct "
        "(id_mutation, id_parcelle, code_departement) with id_parcelle non null"
    ),
    "dpe": (
        "app/etl_dpe/sources.py DpeSourceDescriptor.filter_for(): one query per "
        "(source, departement) on code_departement_ban; rows without departement "
        "never requested (counted apart: sans_departement); loaded = rows "
        "is_tombstone false (app/etl_dpe/pipeline.py)"
    ),
    "bdtopo_wfs": (
        "estimate only: WFS BDTOPO_V3:batiment RESULTTYPE=hits per territory "
        "envelope; the live WFS is not the edition; the count that is "
        "authoritative is the Feature Count of the edition GPKG at import "
        "(plan 05)"
    ),
    "batiments": (
        "app/etl_bdtopo/pipeline.py stage()/swap(): every row of layer batiment "
        "of the departement GPKG (code_departement = file departement, then "
        "WHERE code_departement IS NOT NULL)"
    ),
    "bdnb": (
        "app/etl_bdnb/transform.py transform_export(): members "
        "csv/rel_batiment_groupe_parcelle.csv and csv/batiment_groupe_ffo_bat.csv "
        "of each departemental zip, strict UTF-8 (a leading BOM stripped), values "
        "stripped, empty -> NULL; a relation without batiment_groupe_id or "
        "parcelle_id set aside (relations_ecartees), an ffo row without "
        "batiment_groupe_id dropped; parcelle_principale 1/0 and an integer year "
        "(smallint), else the file is refused; departement = file. pipeline.py "
        "swap() _TRANSFER_SQL: relations GROUP BY (parcelle_id, "
        "batiment_groupe_id) over EVERY file of the run, kept in the smallest "
        'code (COLLATE "C") with bool_or(parcelle_principale); ffo row of a '
        "group from its smallest code (then annee, mur, toit ascending, NULLS "
        "LAST); LEFT JOIN on the group. _DUPLICATES_SQL: doublons_inter_fichiers "
        "= pairs in more than one file, ffo_divergents = groups whose ffo rows "
        "carry more than one (annee, mur, toit), same file included; both "
        "attributed here to the departement of the kept row, their totals are "
        "the receipt counters. Exact for any number of files: K passes split by "
        "crc32 of batiment_groupe_id (pairs and join) and of parcelle_id "
        "(parcelles_distinctes)"
    ),
}

PRIMARY_METRIC = {
    "arcep": "eligibilite_fixe",
    "cadastre": "parcelles",
    "cadastre_sections": "geo_sections",
    "rnb": "rnb_liens",
    "ban": "adresses",
    "dvf": "mutations",
    "dpe": "diagnostics_dpe",
    "bdtopo_wfs": "batiments_wfs",
    "batiments": "batiments",
    "bdnb": "bdnb_batiment_parcelle",
}

# Closed lists: ``diff`` compares exactly these metrics for the source and
# fails when one is missing on a side, instead of comparing the intersection
# (a metric forgotten on one side would never be checked). Defined after the
# join and the deduplication of the swap, the only ones the base can restore.
METRIQUES_COMPAREES: dict[str, tuple[str, ...]] = {
    "bdnb": (
        "bdnb_batiment_parcelle",
        "bdnb:principales",
        "bdnb:annee_renseignee",
        "bdnb:mur_renseigne",
        "bdnb:toit_renseigne",
        "bdnb:parcelles_distinctes",
    ),
}
# Sources whose ``diff`` also checks one millesime: labels of the archived
# zips, label of the archive record, source_period_label of the base (D5).
CONTROLE_MILLESIME = frozenset({"bdnb"})

DEPARTEMENT_RE = re.compile(r"^(?:[0-9]{2}|2[AB]|97[0-9])$")


class ProducerFailureError(RuntimeError):
    """A producer failure that is not a 404 (never a zero, never a gap)."""


class HttpStatusError(ProducerFailureError):
    """An HTTP error status of a range read."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


# ------------------------------------------------------------------ helpers --


def now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def script_identity(given: str | None) -> dict[str, Any]:
    path = Path(__file__) if "__file__" in globals() else None
    if path is not None and path.is_file():
        return {"script_sha256": file_sha256(path), "script_sha256_origin": "file"}
    return {
        "script_sha256": given,
        "script_sha256_origin": "caller, SHA-256 of the file sent on stdin"
        if given
        else "unknown (stdin, no --script-sha256)",
    }


def is_missing(value: str | None) -> bool:
    return value is None or value in PANDAS_NA


def derive_departement_dvf(code_commune: str) -> str:
    """app/etl_dvf/transform.py _derive_code_departement._extract."""
    if not isinstance(code_commune, str) or len(code_commune) < 2:
        return "00"
    if code_commune.startswith("97") and len(code_commune) >= 3:
        return code_commune[:3]
    if code_commune.startswith(("2A", "2B")):
        return code_commune[:2]
    return code_commune[:2]


def departement_from_code3(code: str) -> str:
    """BD TOPO ``D044``/``D02A``/``D971`` file code to the base's code."""
    return code[1:] if code.startswith("0") else code


def parse_departements(value: str | None) -> list[str]:
    if not value:
        return list(DEPARTEMENTS_102)
    codes = [code.strip() for code in value.split(",") if code.strip()]
    for code in codes:
        if not DEPARTEMENT_RE.fullmatch(code):
            raise SystemExit(f"invalid departement code: {code!r}")
    return codes


# ------------------------------------------------------------------ streams --


def _peekable(stream: BinaryIO) -> BinaryIO:
    return stream if hasattr(stream, "peek") else io.BufferedReader(stream)  # type: ignore[arg-type]


def decompressed(stream: BinaryIO) -> BinaryIO:
    """The bytes of *stream*, gunzipped when it starts with the gzip magic."""
    stream = _peekable(stream)
    if stream.peek(2)[:2] == b"\x1f\x8b":  # type: ignore[attr-defined]
        return gzip.GzipFile(fileobj=stream)  # type: ignore[return-value]
    return stream


def text(stream: BinaryIO) -> io.TextIOWrapper:
    return io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="")


class ZipFirstMember(io.RawIOBase):
    """First member of a zip read from a non-seekable stream (funzip in stdlib).

    The ETL opens the archive with zipfile and refuses more than one CSV
    member; a second member found after the first raises here too.
    """

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        header = self._read_exact(30)
        signature, _, flags, method = struct.unpack("<IHHH", header[:10])
        if signature != 0x04034B50:
            raise ValueError("not a zip local file header")
        csize = struct.unpack("<I", header[18:22])[0]
        name_len, extra_len = struct.unpack("<HH", header[26:30])
        self.name = self._read_exact(name_len).decode("utf-8", "replace")
        self._read_exact(extra_len)
        if method not in (0, 8):
            raise ValueError(f"unsupported zip method {method}")
        if method == 0 and (flags & 0x08 or csize == 0xFFFFFFFF):
            raise ValueError("stored member without size")
        self._method = method
        self._remaining = csize if method == 0 else None
        self._inflate = zlib.decompressobj(-15) if method == 8 else None
        self._pending = b""
        self._done = False

    def _read_exact(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self._stream.read(size - len(data))
            if not chunk:
                raise ValueError("truncated zip stream")
            data += chunk
        return data

    def readable(self) -> bool:
        return True

    def _fill(self) -> None:
        while not self._pending and not self._done:
            if self._inflate is None:
                assert self._remaining is not None
                if self._remaining == 0:
                    self._finish(b"")
                    return
                chunk = self._stream.read(min(1 << 20, self._remaining))
                if not chunk:
                    raise ValueError("truncated zip member")
                self._remaining -= len(chunk)
                self._pending = chunk
                continue
            chunk = self._stream.read(1 << 20)
            if not chunk:
                raise ValueError("truncated zip member")
            self._pending = self._inflate.decompress(chunk)
            if self._inflate.eof:
                self._finish(self._inflate.unused_data)

    def _finish(self, rest: bytes) -> None:
        self._done = True
        # After the member: an optional data descriptor, then the next header.
        tail = rest + self._stream.read(64)
        if b"PK\x03\x04" in tail[:24]:
            raise ValueError("zip carries more than one member")

    def readinto(self, buffer: Any) -> int:
        self._fill()
        if not self._pending:
            return 0
        size = min(len(buffer), len(self._pending))
        buffer[:size] = self._pending[:size]
        self._pending = self._pending[size:]
        return size


class Http:
    """Producer reads: headers kept per URL, 404 = absent, other errors = panne."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.headers: dict[str, dict[str, Any]] = {}

    def open(self, url: str) -> BinaryIO | None:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            response = urllib.request.urlopen(request, timeout=HTTP_TIMEOUT)
        except urllib.error.HTTPError as exc:
            self._record(url, exc.code, exc.headers, None)
            if exc.code == 404:
                return None
            raise ProducerFailureError(f"HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProducerFailureError(type(exc).__name__) from None
        self._record(url, response.status, response.headers, response.geturl())
        return response  # type: ignore[return-value]

    def range(self, url: str, spec: str) -> tuple[int, int, bytes, dict[str, Any]]:
        """``(start, total, body, headers)`` of one byte range (``bytes=a-b``,
        ``bytes=-n``). Only a 206 is a range: a 200 would be the whole object.
        Any HTTP error raises ``HttpStatusError`` (the caller decides what a
        status means for its source)."""
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Range": spec}
        )
        try:
            response = urllib.request.urlopen(request, timeout=HTTP_TIMEOUT)
        except urllib.error.HTTPError as exc:
            self._record(url, exc.code, exc.headers, None)
            raise HttpStatusError(exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProducerFailureError(type(exc).__name__) from None
        with response:
            status = response.status
            content_range = response.headers.get("Content-Range") or ""
            try:
                body = response.read()
            except (http.client.HTTPException, OSError) as exc:
                # A cut body (IncompleteRead) is a failure, retried by the caller.
                raise ProducerFailureError(f"{type(exc).__name__} ({spec})") from None
            previous = self.headers.get(url, {})
            self._record(url, status, response.headers, response.geturl())
        match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", content_range)
        if status != 206 or match is None:
            raise ProducerFailureError(f"HTTP {status} without a byte range ({spec})")
        start, end, total = (int(group) for group in match.groups())
        if len(body) != end - start + 1:
            raise ProducerFailureError(f"short range body ({spec})")
        entry = self.headers[url]
        # The object size, not the size of this range.
        entry["content_length"] = str(total)
        entry["requetes"] = int(previous.get("requetes", 0)) + 1
        entry["octets_lus"] = int(previous.get("octets_lus", 0)) + len(body)
        return start, total, body, entry

    def _record(self, url: str, status: int, headers: Any, final: str | None) -> None:
        if url not in self.urls:
            self.urls.append(url)
        self.headers[url] = {
            "status": status,
            "final_url": final,
            "etag": headers.get("ETag") if headers else None,
            "last_modified": headers.get("Last-Modified") if headers else None,
            "content_length": headers.get("Content-Length") if headers else None,
        }


def with_retries(action: Callable[[], Any]) -> Any:
    """A per-file count restarts from zero; transient failures retried."""
    for attempt in range(1, RETRIES + 1):
        try:
            return action()
        except (ProducerFailureError, OSError, EOFError, zlib.error, ValueError) as exc:
            if attempt == RETRIES:
                raise ProducerFailureError(f"{type(exc).__name__}: {exc}") from None
            time.sleep(10 * attempt)
    return None  # pragma: no cover


# ----------------------------------------------------------------- counters --


def _lower_header(reader: Iterator[list[str]]) -> dict[str, int]:
    header = next(reader)
    return {name.strip().lower(): index for index, name in enumerate(header)}


def arcep_base_index(stream: io.TextIOBase, dept: str) -> dict[str, Any]:
    """imb_id -> departement (a list per occurrence) of base_imb_XX.

    The departement of the file, as app/etl_arcep/pipeline.py transform()
    files it since 04.1-21 (a building may be addressed elsewhere).
    """
    reader = csv.reader(stream, delimiter=";")
    columns = _lower_header(reader)
    i_id = columns["imb_id"]
    index: dict[str, Any] = {}
    for row in reader:
        key = row[i_id] if i_id < len(row) else ""
        if is_missing(key):
            continue
        code = dept
        previous = index.get(key)
        if previous is None:
            index[key] = code
        elif isinstance(previous, list):
            previous.append(code)
        else:
            index[key] = [previous, code]
    return index


def arcep_join_counts(stream: io.TextIOBase, index: dict[str, Any]) -> dict[str, Any]:
    """Rows of the inner join kept by swap(), per derived departement."""
    reader = csv.reader(stream, delimiter=";")
    columns = _lower_header(reader)
    i_id, i_techno = columns["imb_id"], columns["code_techno"]
    rows: Counter[str] = Counter()
    ids: dict[str, set[str]] = {}
    for row in reader:
        key = row[i_id] if i_id < len(row) else ""
        techno = row[i_techno] if i_techno < len(row) else ""
        if is_missing(key) or is_missing(techno):
            continue
        match = index.get(key)
        if match is None:
            continue
        for code in match if isinstance(match, list) else (match,):
            rows[code] += 1
            ids.setdefault(code, set()).add(key)
    return {
        code: {
            "eligibilite_fixe": rows[code],
            "eligibilite_fixe:id_imb_distincts": len(ids[code]),
        }
        for code in rows
    }


def _position_count(line: bytes) -> int:
    """Positions of a GeoJSON geometry line: ``[`` followed by a number."""
    return line.count(b"[-") + sum(line.count(b"[" + bytes([d])) for d in b"0123456789")


_COMMUNE_RE = re.compile(rb'"commune":"([^"]*)"')
_FEATURE = b'"type":"Feature"'


def geojson_parcel_counts(stream: BinaryIO) -> tuple[dict[str, dict[str, int]], int]:
    """Features and positions per departement of a cadastre parcel file.

    Returns the counts and the features that could not be attributed to a
    departement (no ``commune`` property, or positions of a line holding
    features of several departements): reported, never dropped silently.
    """
    counts: dict[str, dict[str, int]] = {}
    unattributed = 0
    for line in stream:
        features = line.count(_FEATURE)
        if not features:
            continue
        communes = _COMMUNE_RE.findall(line)
        if len(communes) != features:
            unattributed += features
            continue
        codes = []
        for commune in communes:
            raw = commune.decode("ascii", "replace")
            codes.append(raw[:3] if raw.startswith("97") else raw[:2])
        for code in codes:
            slot = counts.setdefault(code, {"parcelles": 0, "parcelles:sommets": 0})
            slot["parcelles"] += 1
        if len(set(codes)) == 1:
            counts[codes[0]]["parcelles:sommets"] += _position_count(line)
        else:
            unattributed += features
    return counts, unattributed


def geojson_section_ids(stream: BinaryIO) -> tuple[int, int]:
    """(features, distinct properties.id) of a cadastre sections file."""
    features = 0
    ids: set[str] = set()
    decoder = json.JSONDecoder()
    for line in stream:
        if _FEATURE not in line:
            continue
        # The last feature line also closes the collection ("}]}"): decode the
        # properties object alone, raw_decode ignores what follows it.
        start = line.find(b'"properties":')
        props = {}
        if start >= 0:
            tail = line[start + len(b'"properties":') :].decode("utf-8", "replace")
            props = decoder.raw_decode(tail.lstrip())[0] or {}
        features += 1
        ids.add(
            props.get(
                "id",
                f"{props.get('commune', '')}{props.get('prefixe', '000')}"
                f"{props.get('code', '')}",
            )
        )
    return features, len(ids)


def parse_cles_ban(raw: str) -> list[str]:
    """Copy of app/etl_rnb_liens/transform.py parse_cles_ban (checked by test)."""
    value = (raw or "").strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    keys = [
        item.get("cle_interop_ban")
        for item in parsed
        if isinstance(item, dict)
        and isinstance(item.get("cle_interop_ban"), str)
        and item.get("cle_interop_ban")
    ]
    return list(dict.fromkeys(keys))


def rnb_pairs(stream: io.TextIOBase) -> tuple[set[tuple[str, str]], dict[str, int]]:
    """Distinct (rnb_id, cle_ban) pairs of one export, and row statistics."""
    csv.field_size_limit(FIELD_SIZE_LIMIT)
    reader = csv.DictReader(stream, delimiter=";")
    pairs: set[tuple[str, str]] = set()
    stats = Counter({"batiments": 0, "batiments_sans_cle": 0, "sans_rnb_id": 0})
    for row in reader:
        id_rnb = (row.get("rnb_id") or "").strip()
        if not id_rnb:
            stats["sans_rnb_id"] += 1
            continue
        stats["batiments"] += 1
        keys = parse_cles_ban(row.get("addresses", ""))
        if not keys:
            stats["batiments_sans_cle"] += 1
        for key in keys:
            pairs.add((id_rnb, key))
    return pairs, dict(stats)


def ban_rows(stream: io.TextIOBase) -> dict[str, int]:
    reader = csv.reader(stream, delimiter=";")
    columns = _lower_header(reader)
    i_lon, i_lat = columns["lon"], columns["lat"]
    kept = total = 0
    for row in reader:
        total += 1
        lon = row[i_lon] if i_lon < len(row) else ""
        lat = row[i_lat] if i_lat < len(row) else ""
        if not is_missing(lon) and not is_missing(lat):
            kept += 1
    return {"adresses": kept, "lignes_source": total}


class DvfDepartement:
    """mutations, grain and parcel pairs of one departement over all years."""

    def __init__(self) -> None:
        self.mutations: Counter[str] = Counter()
        self.grain: dict[str, set[str]] = {}
        self.parcelles: dict[str, set[tuple[str, str]]] = {}
        self.source_rows = 0

    def add(self, stream: io.TextIOBase) -> None:
        reader = csv.reader(stream, delimiter=",")
        columns = _lower_header(reader)
        i_mut, i_com = columns["id_mutation"], columns["code_commune"]
        i_par = columns["id_parcelle"]
        # Phase 04.4 D-01: every line is loaded, coordinates or not.
        for row in reader:
            self.source_rows += 1
            commune = "" if is_missing(row[i_com]) else row[i_com]
            code = derive_departement_dvf(commune)
            mutation = row[i_mut]
            self.mutations[code] += 1
            self.grain.setdefault(code, set()).add(mutation)
            if not is_missing(row[i_par]):
                self.parcelles.setdefault(code, set()).add((mutation, row[i_par]))

    def counts(self) -> dict[str, dict[str, int]]:
        return {
            code: {
                "mutations": self.mutations[code],
                "mutations_grain": len(self.grain.get(code, ())),
                "mutation_parcelles": len(self.parcelles.get(code, ())),
            }
            for code in self.mutations
        }


def dvf_years(today: datetime | None = None) -> list[int]:
    """app/etl_dvf/config.py YEARS: 2020 to the current year included."""
    return list(range(2020, (today or datetime.now(UTC)).year + 1))


def values_agg_counts(payload: dict[str, Any]) -> tuple[dict[str, int], int]:
    """Departement totals of a data-fair values_agg, and rows without value."""
    if payload.get("total_other", 0):
        raise ProducerFailureError("values_agg truncated (total_other > 0)")
    counts = {str(item["value"]): int(item["total"]) for item in payload["aggs"]}
    return counts, int(payload["total"]) - sum(counts.values())


def gpkg_feature_count(path: Path) -> int:
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as conn:
        return int(conn.execute("SELECT count(*) FROM batiment").fetchone()[0])


# ------------------------------------------------------------- count: modes --


class Counting:
    """One count run: departements, counts, absences and failures."""

    def __init__(self, source: str, departements: list[str]) -> None:
        self.source = source
        self.departements = departements
        self.counts: dict[str, dict[str, int]] = {}
        self.absent: dict[str, dict[str, Any]] = {}
        self.pannes: dict[str, str] = {}
        self.details: dict[str, Any] = {}
        # Source options of the command line (bdnb: millesime, memory, passes).
        self.options: dict[str, Any] = {}

    def add(self, dept: str, metrics: dict[str, int]) -> None:
        slot = self.counts.setdefault(dept, {})
        for name, value in metrics.items():
            slot[name] = slot.get(name, 0) + value

    def guarded(self, dept: str, action: Callable[[], Any]) -> None:
        try:
            with_retries(action)
        except ProducerFailureError as exc:
            self.pannes[dept] = str(exc)
        except _AbsentError as exc:
            self.absent[dept] = exc.detail


class _AbsentError(Exception):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(detail)
        self.detail = detail


def _opened(http: Http, url: str) -> BinaryIO:
    stream = http.open(url)
    if stream is None:
        raise _AbsentError({"etat": "absent_producteur", "status": 404, "url": url})
    return stream


def live_arcep(run: Counting, http: Http) -> None:
    for dept in run.departements:

        def action(dept: str = dept) -> None:
            with _opened(http, ARCEP_BASE_IMB_URL.format(dept=dept)) as raw:
                index = arcep_base_index(text(decompressed(raw)), dept)
            with _opened(http, ARCEP_ELIGIBILITE_URL.format(dept=dept)) as raw:
                joined = arcep_join_counts(text(decompressed(raw)), index)
            run.add(dept, {"base_imb:immeubles": len(index)})
            for code, metrics in joined.items():
                run.add(code, metrics)

        run.guarded(dept, action)


def live_cadastre(run: Counting, http: Http) -> None:
    for dept in run.departements:
        url = f"{CADASTRE_BASE_URL}/{dept}/cadastre-{dept}-parcelles.json.gz"

        def action(dept: str = dept, url: str = url) -> None:
            with _opened(http, url) as raw:
                counts, unattributed = geojson_parcel_counts(decompressed(raw))
            for code, metrics in counts.items():
                run.add(code, metrics)
            if unattributed:
                run.details.setdefault("non_attribues", {})[dept] = unattributed

        run.guarded(dept, action)


def live_sections(run: Counting, http: Http) -> None:
    for dept in run.departements:

        def action(dept: str = dept) -> None:
            with _opened(http, SECTIONS_URL.format(dept=dept)) as raw:
                features, distinct = geojson_section_ids(decompressed(raw))
            run.add(dept, {"geo_sections": distinct, "sections:features": features})

        run.guarded(dept, action)


def rnb_counting(run: Counting, opener: Callable[[str], BinaryIO]) -> None:
    """One export per departement; cross-file DISTINCT ON kept when small."""
    global_pairs: set[tuple[str, str]] | None = (
        set() if len(run.departements) <= 4 else None
    )
    for dept in run.departements:

        def action(dept: str = dept) -> None:
            with opener(dept) as raw:
                member = ZipFirstMember(raw)
                pairs, stats = rnb_pairs(text(io.BufferedReader(member)))  # type: ignore[arg-type]
            kept = pairs
            if global_pairs is not None:
                kept = pairs - global_pairs
                global_pairs.update(pairs)
            run.add(dept, {"rnb_liens": len(kept), "rnb:paires_fichier": len(pairs)})
            run.add(dept, {f"rnb:{name}": value for name, value in stats.items()})

        run.guarded(dept, action)
    run.details["doublons_inter_fichiers"] = (
        "measured" if global_pairs is not None else "not measured (more than 4 files)"
    )


def live_ban(run: Counting, http: Http) -> None:
    for dept in run.departements:

        def action(dept: str = dept) -> None:
            with _opened(http, f"{BAN_BASE_URL}/adresses-{dept}.csv.gz") as raw:
                run.add(dept, ban_rows(text(decompressed(raw))))

        run.guarded(dept, action)


def live_dvf(run: Counting, http: Http) -> None:
    years = dvf_years()
    run.details["years"] = years
    for dept in run.departements:

        def action(dept: str = dept) -> None:
            state = DvfDepartement()
            found = []
            for year in years:
                url = f"{DVF_BASE_URL}{year}/departements/{dept}.csv.gz"
                stream = http.open(url)
                if stream is None:
                    continue
                with stream:
                    state.add(text(decompressed(stream)))
                found.append(year)
            if not found:
                raise _AbsentError({"etat": "absent_producteur", "status": 404})
            run.details.setdefault("annees_lues", {})[dept] = found
            for code, metrics in state.counts().items():
                run.add(code, metrics)
            run.add(dept, {"dvf:lignes_source": state.source_rows})

        run.guarded(dept, action)


def live_dpe(run: Counting, http: Http) -> None:
    from app.etl_dpe.sources import DPE_SOURCES  # identifiers read, not copied

    for slug, source in DPE_SOURCES.items():
        base = source.endpoint.removesuffix("/lines")
        meta_stream = _opened(http, base)
        with meta_stream:
            metadata = json.load(meta_stream)
        if metadata.get("id") != source.dataset_id or metadata.get("slug") != slug:
            raise SystemExit(f"DPE metadata of {slug} is not the configured dataset")
        url = f"{base}/values_agg?field=code_departement_ban&agg_size=1000&size=0"
        with _opened(http, url) as raw:
            counts, without = values_agg_counts(json.load(raw))
        run.details.setdefault("sources", {})[slug] = {
            "dataset_id": source.dataset_id,
            "regime": source.regime,
            "dataUpdatedAt": metadata.get("dataUpdatedAt"),
            "total": metadata.get("count"),
            "sans_departement": without,
        }
        for code, value in counts.items():
            run.add(
                code,
                {"diagnostics_dpe": value, f"diagnostics_dpe:{source.regime}": value},
            )
    wanted = set(run.departements)
    run.details["departements_hors_liste"] = {
        code: run.counts[code] for code in sorted(set(run.counts) - wanted)
    }
    run.counts = {code: run.counts[code] for code in run.counts if code in wanted}
    for dept in run.departements:
        if dept not in run.counts:
            run.absent[dept] = {"etat": "absent_producteur", "status": "no aggregate"}


def live_bdtopo_wfs(run: Counting, http: Http) -> None:
    pattern = re.compile(rb'numberMatched="([0-9]+)"')
    targets = {"total": None, **WFS_TERRITORIES}
    for name, box in targets.items():
        url = WFS_HITS_URL
        if box is not None:
            url += "&BBOX={},{},{},{},urn:ogc:def:crs:EPSG::4326".format(*box)

        def action(name: str = name, url: str = url) -> None:
            with _opened(http, url) as raw:
                match = pattern.search(raw.read(4096))
            if match is None:
                raise ProducerFailureError("numberMatched missing")
            if name == "total":
                # Unbounded count: kept apart, territories partition it.
                run.details["total_wfs"] = int(match.group(1))
            else:
                run.add(name, {"batiments_wfs": int(match.group(1))})

        run.guarded(name, action)
    run.details["territoires"] = {k: list(v) for k, v in WFS_TERRITORIES.items()}
    if "total_wfs" in run.details:
        run.details["territoires_moins_total"] = (
            sum(v["batiments_wfs"] for v in run.counts.values())
            - run.details["total_wfs"]
        )


# ------------------------------------------------------------------- bdnb --

# End of a zip: EOCD (22) + longest comment (65 535) + zip64 locator (20) and
# record (56). One suffix range reads it, central directory most often included.
_ZIP_TAIL = 22 + 65_535 + 20 + 56
_BDNB_MEMBERS = (BDNB_MEMBER_VERSION, BDNB_MEMBER_RELATIONS, BDNB_MEMBER_FFO)
# Value of a relation: code index * 4 + principal (2) + seen in another file
# (1), taken from this table so that millions of pairs share a few int objects.
_BDNB_REL_VALUES = tuple(range(4 * len(DEPARTEMENTS_102) + 4))
# Python bytes held per uncompressed byte of a member during one pass
# (relations: pair dict, parcel set and parcel dict; ffo: group dict), set by
# measure on 44+85 (plan 04.7-19, T4a: estimate 622 MB, peak 585 MB with K=1,
# 205 MB with K=3). They only size K, never a count.
BDNB_OCTETS_PAR_OCTET_RELATIONS = 8.0
BDNB_OCTETS_PAR_OCTET_FFO = 1.5
BDNB_MEMOIRE_MAX_DEFAUT = 4 * 1024**3
BDNB_COMPTEURS: tuple[str, ...] = (
    *METRIQUES_COMPAREES["bdnb"],
    "bdnb:brut:relations_fichier",
    "bdnb:brut:ffo_fichier",
    "bdnb:brut:relations_ecartees",
    "bdnb:brut:doublons_inter_fichiers",
    "bdnb:brut:ffo_divergents",
    "bdnb:brut:parcelle_id_longueur_max",
)


def peak_memory_bytes() -> int | None:
    """Peak resident memory of this process (stdlib: resource, or psapi)."""
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak) if sys.platform == "darwin" else int(peak) * 1024
    except ImportError:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        windll = getattr(ctypes, "windll")  # noqa: B009 - absent off Windows
        # A HANDLE is pointer-sized: without restype/argtypes ctypes would
        # pass the pseudo-handle -1 as a 32-bit int and the call would fail.
        windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        info = windll.psapi.GetProcessMemoryInfo
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if info(windll.kernel32.GetCurrentProcess(), counters, counters.cb):
            return int(counters.PeakWorkingSetSize)
    except (AttributeError, OSError):
        pass
    return None


def bdnb_label(millesime: str) -> str:
    """``2026-02-b`` (URL) -> ``2026-02.b`` (label of the version member)."""
    if not BDNB_MILLESIME_RE.fullmatch(millesime):
        raise SystemExit(f"bdnb: millesime {millesime!r} is not AAAA-MM-x (D6)")
    return f"{millesime[:7]}.{millesime[8:]}"


class _MissingRangeError(Exception):
    """A read outside the ranges held of a remote zip."""

    def __init__(self, start: int, size: int) -> None:
        super().__init__(f"bytes {start}+{size} not held")
        self.start, self.size = start, size


class RangeView:
    """Read-only, seekable view of the ranges of a remote zip held in memory,
    enough for ``zipfile``; a read outside them raises ``_MissingRangeError``."""

    def __init__(self, size: int, chunks: list[tuple[int, bytes]]) -> None:
        self.size, self._chunks, self._pos = size, chunks, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self._pos, 2: self.size}[whence]
        if base + offset < 0:
            raise ValueError("negative seek position")
        self._pos = base + offset
        return self._pos

    def read(self, size: int | None = -1) -> bytes:
        end = self.size if size is None or size < 0 else self._pos + size
        end = min(end, self.size)
        parts: list[bytes] = []
        pos = self._pos
        while pos < end:
            held = next(
                ((s, d) for s, d in self._chunks if s <= pos < s + len(d)), None
            )
            if held is None:
                raise _MissingRangeError(pos, end - pos)
            start, data = held
            parts.append(data[pos - start : end - start])
            pos += len(parts[-1])
        self._pos = pos
        return b"".join(parts)

    def close(self) -> None:
        return None

    def __enter__(self) -> RangeView:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class RemoteZip:
    """The ranges of one remote zip the count reads, fetched once and held.

    The tail (end of central directory), the directory, then the local header
    and the compressed bytes of each named member: about 2.4 % of a BDNB
    export. The first answer decides absence: 403 on this bucket. A changed
    ETag, Last-Modified or size between two ranges is a failure.
    """

    def __init__(self, http: Http, url: str, members: Iterable[str]) -> None:
        self.http, self.url = http, url
        self.chunks: list[tuple[int, bytes]] = []
        try:
            start, self.size, body, entry = http.range(url, f"bytes=-{_ZIP_TAIL}")
        except HttpStatusError as exc:
            if exc.status == 403:
                raise _AbsentError(
                    {"etat": "absent_producteur", "status": 403, "url": url}
                ) from None
            raise
        self.identity = (entry.get("etag"), entry.get("last_modified"), self.size)
        self.chunks.append((start, body))
        for info in self._directory(members):
            self._member(info)

    @property
    def held(self) -> int:
        return sum(len(data) for _, data in self.chunks)

    def open(self) -> RangeView:
        return RangeView(self.size, self.chunks)

    def _fetch(self, start: int, end: int) -> None:
        got, total, body, entry = self.http.range(self.url, f"bytes={start}-{end - 1}")
        identity = (entry.get("etag"), entry.get("last_modified"), total)
        if identity != self.identity or got != start:
            raise ProducerFailureError(f"object changed during the read: {self.url}")
        self.chunks.append((start, body))
        self.chunks.sort(key=lambda chunk: chunk[0])

    def _ensure(self, start: int, end: int) -> None:
        """Fetch what is not held yet of [start, end)."""
        end = min(end, self.size)
        pos = start
        while pos < end:
            held = next(
                (s + len(d) for s, d in self.chunks if s <= pos < s + len(d)), None
            )
            if held is not None:
                pos = held
                continue
            following = min((s for s, _ in self.chunks if s > pos), default=end)
            self._fetch(pos, min(end, following))
            pos = min(end, following)

    def _directory(self, members: Iterable[str]) -> list[zipfile.ZipInfo]:
        for _ in range(8):
            try:
                with zipfile.ZipFile(self.open()) as archive:  # type: ignore[arg-type]
                    names = set(archive.namelist())
                    return [archive.getinfo(n) for n in members if n in names]
            except _MissingRangeError as gap:
                self._ensure(gap.start, gap.start + max(gap.size, 1 << 20))
        raise ProducerFailureError(f"zip directory out of reach: {self.url}")

    def _member(self, info: zipfile.ZipInfo) -> None:
        start = info.header_offset
        # One range for the usual case (local extra as long as the central
        # one), then the exact end read in the local header.
        guess = len(info.filename.encode()) + len(info.extra) + 1024
        self._ensure(start, start + 30 + guess + info.compress_size)
        view = self.open()
        view.seek(start)
        name_len, extra_len = struct.unpack("<HH", view.read(30)[26:30])
        self._ensure(start, start + 30 + name_len + extra_len + info.compress_size)


@contextlib.contextmanager
def _bdnb_member(archive: zipfile.ZipFile, name: str) -> Iterator[io.TextIOWrapper]:
    """app/etl_bdnb/transform.py _ouvrir: caps, strict UTF-8, BOM stripped."""
    try:
        info = archive.getinfo(name)
    except KeyError:
        raise ValueError(f"BDNB: membre absent {name}") from None
    if info.file_size > BDNB_MAX_MEMBER_BYTES:
        raise ValueError(f"BDNB: {name} fait {info.file_size} octets")
    if info.file_size > BDNB_MAX_COMPRESSION_RATIO * max(info.compress_size, 1):
        raise ValueError(f"BDNB: {name} rapport de compression au-delà du plafond")
    with archive.open(info) as member:
        yield io.TextIOWrapper(
            member, encoding="utf-8-sig", errors="strict", newline=""
        )


def _bdnb_index(header: list[str], name: str, required: tuple[str, ...]) -> list[int]:
    """Positions of *required* in *header*, names stripped (the last one of a
    repeated name, as ``csv.DictReader``); refused when one is missing.

    Same reading as ``app/etl_bdnb/transform.py`` ``_lecteur``, which strips
    the names for the check and for the keys of the rows."""
    index = {column.strip(): position for position, column in enumerate(header)}
    missing = sorted(set(required) - set(index))
    if missing:
        raise ValueError(f"BDNB: colonnes absentes de {name}: {missing}")
    return [index[column] for column in required]


def _bdnb_version(archive: zipfile.ZipFile) -> str:
    """app/etl_bdnb/transform.py _version: label of the newest line."""
    with _bdnb_member(archive, BDNB_MEMBER_VERSION) as stream:
        reader = csv.DictReader(stream, delimiter=BDNB_CSV_SEPARATOR)
        reader.fieldnames = [column.strip() for column in reader.fieldnames or []]
        missing = sorted(set(BDNB_VERSION_COLUMNS) - set(reader.fieldnames))
        if missing:
            raise ValueError(f"BDNB: colonnes absentes de la version: {missing}")
        try:
            lignes = [
                (row["date_publication"].strip(), row["millesime"].strip())
                for row in reader
            ]
        except (AttributeError, KeyError) as exc:
            raise ValueError(f"BDNB: historique de version illisible: {exc}") from None
    if not lignes:
        raise ValueError("BDNB: historique de version vide")
    publiee, label = max(lignes)
    if not BDNB_LABEL_RE.fullmatch(label):
        raise ValueError(f"BDNB: millésime illisible {label!r}")
    datetime.strptime(publiee, "%Y/%m/%d")
    return label


class BdnbExport:
    """One export checked before the passes (label, headers of the two data
    members, their uncompressed sizes), and how to reopen it."""

    def __init__(self, opener: Callable[[], Any], held: int = 0) -> None:
        self.opener, self.held = opener, held
        self.sizes: dict[str, int] = {}
        try:
            with opener() as raw, zipfile.ZipFile(raw) as archive:
                self.label = _bdnb_version(archive)
                for name, required in (
                    (BDNB_MEMBER_RELATIONS, BDNB_RELATION_COLUMNS),
                    (BDNB_MEMBER_FFO, BDNB_FFO_COLUMNS),
                ):
                    with _bdnb_member(archive, name) as stream:
                        reader = csv.reader(stream, delimiter=BDNB_CSV_SEPARATOR)
                        _bdnb_index(next(reader, None) or [], name, required)
                    self.sizes[name] = archive.getinfo(name).file_size
        except (zipfile.BadZipFile, KeyError, struct.error) as exc:
            raise ValueError(f"BDNB: zip illisible: {type(exc).__name__}") from None


class _BdnbDeptError(Exception):
    def __init__(self, dept: str, reason: str) -> None:
        super().__init__(f"{dept}: {reason}")
        self.dept, self.reason = dept, reason


def _bdnb_ordre(row: tuple[Any, ...]) -> tuple[Any, ...]:
    """ORDER BY annee_construction, mat_mur_txt, mat_toit_txt (NULLS LAST)."""
    _code, annee, mur, toit = row
    return (annee is None, annee or 0, mur is None, mur or "", toit is None, toit or "")


def _bdnb_pass(  # noqa: C901 - one read of every file, kept in one loop for speed
    exports: dict[str, BdnbExport],
    codes: list[str],
    passes: int,
    k: int,
    tally: dict[str, dict[str, int]],
) -> None:
    """Pass *k*: pairs and join of the groups whose crc32 % passes == k, and
    distinct parcels of the parcels whose crc32 % passes == k.

    Files are read in ascending code (COLLATE "C" = code point order of these
    codes): the first file holding a pair or a group IS its smallest code.
    """
    rel: dict[str, int] = {}
    paires: set[str] = set()
    parcelle_code: dict[str, int] = {}
    parcelle_codes: dict[str, set[int]] = {}
    ffo: dict[str, tuple[Any, ...]] = {}
    ffo_lignes: dict[str, list[tuple[Any, ...]]] = {}
    attributs: dict[tuple[Any, ...], tuple[Any, ...]] = {}
    values, crc, tous = _BDNB_REL_VALUES, zlib.crc32, passes == 1
    for idx, code in enumerate(codes):
        try:
            with exports[code].opener() as raw, zipfile.ZipFile(raw) as archive:
                with _bdnb_member(archive, BDNB_MEMBER_RELATIONS) as stream:
                    reader = csv.reader(stream, delimiter=BDNB_CSV_SEPARATOR)
                    i_g, i_p, i_pp = _bdnb_index(
                        next(reader, None) or [],
                        BDNB_MEMBER_RELATIONS,
                        BDNB_RELATION_COLUMNS,
                    )
                    lues = ecartees = longueur = 0
                    for row in reader:
                        if not row:  # csv.DictReader skips blank lines
                            continue
                        lues += 1
                        n = len(row)
                        groupe = row[i_g].strip() if i_g < n else ""
                        parcelle = row[i_p].strip() if i_p < n else ""
                        if not groupe or not parcelle:
                            ecartees += 1
                            continue
                        principale = row[i_pp].strip() if i_pp < n else ""
                        if principale == "1":
                            flag = 2
                        elif principale == "0":
                            flag = 0
                        else:
                            raise ValueError(
                                f"BDNB: parcelle_principale {principale!r}"
                            )
                        if len(parcelle) > longueur:
                            longueur = len(parcelle)
                        if tous or crc(groupe.encode()) % passes == k:
                            key = groupe + "\x00" + parcelle
                            value = rel.get(key)
                            if value is None:
                                rel[key] = values[idx * 4 + flag]
                            else:
                                if value >> 2 != idx:
                                    value |= 1
                                rel[key] = values[value | flag]
                        if tous or crc(parcelle.encode()) % passes == k:
                            key = parcelle + "\x00" + groupe
                            if key not in paires:
                                paires.add(key)
                                first = parcelle_code.setdefault(parcelle, idx)
                                if first != idx:
                                    parcelle_codes.setdefault(parcelle, {first}).add(
                                        idx
                                    )
                with _bdnb_member(archive, BDNB_MEMBER_FFO) as stream:
                    reader = csv.reader(stream, delimiter=BDNB_CSV_SEPARATOR)
                    i_g, i_a, i_m, i_t = _bdnb_index(
                        next(reader, None) or [], BDNB_MEMBER_FFO, BDNB_FFO_COLUMNS
                    )
                    ffo_lues = 0
                    for row in reader:
                        if not row:
                            continue
                        ffo_lues += 1
                        n = len(row)
                        groupe = row[i_g].strip() if i_g < n else ""
                        if not groupe:
                            continue
                        texte = row[i_a].strip() if i_a < n else ""
                        if texte and not BDNB_YEAR_RE.fullmatch(texte):
                            raise ValueError(f"BDNB: année non entière {texte!r}")
                        annee = int(texte) if texte else None
                        if annee is not None and not -32768 <= annee <= 32767:
                            raise ValueError(f"BDNB: année hors smallint {annee}")
                        if not (tous or crc(groupe.encode()) % passes == k):
                            continue
                        mur = (row[i_m].strip() if i_m < n else "") or None
                        toit = (row[i_t].strip() if i_t < n else "") or None
                        ligne = (idx, annee, mur, toit)
                        ligne = attributs.setdefault(ligne, ligne)
                        premiere = ffo.get(groupe)
                        if premiere is None:
                            ffo[groupe] = ligne
                        elif premiere[1:] != ligne[1:]:
                            lignes = ffo_lignes.setdefault(groupe, [premiere])
                            if ligne not in lignes:
                                lignes.append(ligne)
        except (
            ValueError,
            KeyError,
            EOFError,
            OSError,
            zlib.error,
            zipfile.BadZipFile,
            _MissingRangeError,
        ) as exc:
            raise _BdnbDeptError(code, f"{type(exc).__name__}: {exc}") from None
        if k == 0:
            compte = tally[code]
            compte["bdnb:brut:relations_fichier"] = lues
            compte["bdnb:brut:relations_ecartees"] = ecartees
            compte["bdnb:brut:ffo_fichier"] = ffo_lues
            compte["bdnb:brut:parcelle_id_longueur_max"] = longueur
    for groupe, lignes in ffo_lignes.items():
        smallest = min(ligne[0] for ligne in lignes)
        ffo[groupe] = min((x for x in lignes if x[0] == smallest), key=_bdnb_ordre)
        tally[codes[smallest]]["bdnb:brut:ffo_divergents"] += 1
    # lignes, principales, doublons, annee, mur, toit per code index
    per_code = [[0] * 6 for _ in codes]
    for key, value in rel.items():
        compteur = per_code[value >> 2]
        compteur[0] += 1
        if value & 2:
            compteur[1] += 1
        if value & 1:
            compteur[2] += 1
        attrs = ffo.get(key[: key.index("\x00")])
        if attrs is not None:  # LEFT JOIN: a group without ffo keeps its row
            compteur[3] += attrs[1] is not None
            compteur[4] += attrs[2] is not None
            compteur[5] += attrs[3] is not None
    parcelles = Counter(parcelle_code.values())
    for parcelle, plusieurs in parcelle_codes.items():
        for idx in plusieurs - {parcelle_code[parcelle]}:
            parcelles[idx] += 1
    for idx, code in enumerate(codes):
        compte = tally[code]
        lignes_, principales, doublons, annee_, mur_, toit_ = per_code[idx]
        compte["bdnb_batiment_parcelle"] += lignes_
        compte["bdnb:principales"] += principales
        compte["bdnb:brut:doublons_inter_fichiers"] += doublons
        compte["bdnb:annee_renseignee"] += annee_
        compte["bdnb:mur_renseigne"] += mur_
        compte["bdnb:toit_renseigne"] += toit_
        compte["bdnb:parcelles_distinctes"] += parcelles[idx]


def bdnb_passes(
    exports: dict[str, BdnbExport], passes: int
) -> dict[str, dict[str, int]]:
    """Every metric per departement, in *passes* passes (same result for any)."""
    codes = sorted(exports)
    tally = {code: dict.fromkeys(BDNB_COMPTEURS, 0) for code in codes}
    for k in range(passes):
        _bdnb_pass(exports, codes, passes, k, tally)
    return tally


def bdnb_counting(
    run: Counting, exports: dict[str, BdnbExport], label: str | None
) -> None:
    """Count the checked exports; a file refused in a pass is a named failure
    and the count restarts without it (the ETL would refuse the whole run)."""
    run.details["etiquettes"] = {code: exports[code].label for code in sorted(exports)}
    if label is not None:
        for code in sorted(exports):
            if exports[code].label != label:
                run.pannes[code] = (
                    f"BDNB: millésime {exports[code].label}, attendu {label}"
                )
                del exports[code]
    relations = sum(e.sizes[BDNB_MEMBER_RELATIONS] for e in exports.values())
    ffo = sum(e.sizes[BDNB_MEMBER_FFO] for e in exports.values())
    held = sum(e.held for e in exports.values())
    memoire_max = int(run.options.get("memoire_max") or BDNB_MEMOIRE_MAX_DEFAUT)
    estimation = int(
        BDNB_OCTETS_PAR_OCTET_RELATIONS * relations + BDNB_OCTETS_PAR_OCTET_FFO * ffo
    )
    if memoire_max <= held:
        raise SystemExit(f"--memoire-max {memoire_max} under the {held} bytes held")
    passes = int(run.options.get("passes") or -(-estimation // (memoire_max - held)))
    passes = max(passes, 1)
    while True:
        try:
            tally = bdnb_passes(exports, passes)
            break
        except _BdnbDeptError as exc:
            run.pannes[exc.dept] = exc.reason
            del exports[exc.dept]
    for code, metrics in tally.items():
        run.add(code, metrics)
    run.details.update(
        passes=passes,
        memoire_max_octets=memoire_max,
        estimation_memoire_octets=estimation,
        octets_tenus=held,
        octets_decompresses={"relations": relations, "ffo": ffo},
        memoire_pointe_octets=peak_memory_bytes(),
    )


def bdnb_receipt_counters(payload: dict[str, Any]) -> dict[str, int]:
    """A bdnb count in the names of the import receipt (``count:*`` stages of
    ``stage_counters`` and ``swap_counters``): the raw metrics are judged
    against it, equality exact."""
    counters: dict[str, int] = {}
    for dept, metrics in payload["counts"].items():
        counters[f"producteur_relations_{dept}"] = metrics[
            "bdnb:brut:relations_fichier"
        ]
        counters[f"producteur_ffo_{dept}"] = metrics["bdnb:brut:ffo_fichier"]
        counters[f"relations_ecartees_{dept}"] = metrics["bdnb:brut:relations_ecartees"]
        # swap() counts published rows by GROUP BY: no row, no counter.
        if metrics["bdnb_batiment_parcelle"]:
            counters[f"publiees_{dept}"] = metrics["bdnb_batiment_parcelle"]
    totals = payload["totals"]
    counters["doublons_inter_fichiers"] = totals["bdnb:brut:doublons_inter_fichiers"]
    counters["ffo_divergents"] = totals["bdnb:brut:ffo_divergents"]
    return counters


def live_bdnb(run: Counting, http: Http) -> None:
    """Members read by HTTP ranges (D11): about 2 GB instead of 40."""
    millesime = str(run.options.get("millesime") or "")
    label = bdnb_label(millesime)
    exports: dict[str, BdnbExport] = {}
    urls: dict[str, str] = {}
    for dept in run.departements:
        url = BDNB_EXPORT_URL_TEMPLATE.format(millesime=millesime, dept=dept.lower())

        def action(dept: str = dept, url: str = url) -> None:
            remote = RemoteZip(http, url, _BDNB_MEMBERS)
            exports[dept] = BdnbExport(remote.open, held=remote.held)
            urls[dept] = url

        run.guarded(dept, action)
    zips = {}
    for dept, url in sorted(urls.items()):
        entry = http.headers.get(url, {})
        zips[dept] = {
            "url": url,
            "etag": entry.get("etag"),
            "last_modified": entry.get("last_modified"),
            "content_length": int(entry["content_length"]),
            "requetes": entry.get("requetes"),
            "octets_lus": entry.get("octets_lus"),
            "etiquette": exports[dept].label,
        }
    run.details["zips"] = zips
    run.details["content_length_total"] = sum(
        z["content_length"] for z in zips.values()
    )
    run.details["octets_lus_total"] = sum(z["octets_lus"] or 0 for z in zips.values())
    bdnb_counting(run, exports, label)


LIVE: dict[str, Callable[[Counting, Http], None]] = {
    "arcep": live_arcep,
    "cadastre": live_cadastre,
    "cadastre_sections": live_sections,
    "rnb": lambda run, http: rnb_counting(
        run, lambda dept: _opened(http, RNB_EXPORT_URL_TEMPLATE.format(dept=dept))
    ),
    "ban": live_ban,
    "dvf": live_dvf,
    "dpe": live_dpe,
    "bdtopo_wfs": live_bdtopo_wfs,
    "bdnb": live_bdnb,
}


def live_millesime(source: str, http: Http, run: Counting) -> Any:
    finals = [h.get("final_url") or "" for h in http.headers.values()]
    patterns = {
        "arcep": r"/([0-9]{4}_T[1-4])/departement/",
        "cadastre": r"/etalab-cadastre/([0-9]{4}-[0-9]{2}-[0-9]{2})/",
        "cadastre_sections": r"/etalab-cadastre/([0-9]{4}-[0-9]{2}-[0-9]{2})/",
        "ban": r"/adresses/([0-9]{4}-[0-9]{2}-[0-9]{2})/",
        "dvf": r"/geo-dvf/([^/]+)/csv/",
    }
    if source in patterns:
        found = sorted(
            {m.group(1) for url in finals if (m := re.search(patterns[source], url))}
        )
        if found:
            return found[0] if len(found) == 1 else found
        dates = sorted(
            parsedate_to_datetime(h["last_modified"]).date().isoformat()
            for h in http.headers.values()
            if h.get("last_modified")
        )
        return (
            {"last_modified_min": dates[0], "last_modified_max": dates[-1]}
            if dates
            else None
        )
    if source == "rnb":
        dates = sorted(
            {
                h["last_modified"]
                for h in http.headers.values()
                if h.get("last_modified")
            }
        )
        return {"last_modified": dates[-1] if dates else None, "distinct": len(dates)}
    if source == "bdnb":
        labels = sorted(set(run.details.get("etiquettes", {}).values()))
        return labels[0] if len(labels) == 1 else labels or None
    if source == "dpe":
        return {
            slug: info.get("dataUpdatedAt")
            for slug, info in run.details.get("sources", {}).items()
        }
    return None


# ------------------------------------------------------------ count: archive --


class ArchiveRun:
    """The objects of one archived run, opened read-only."""

    def __init__(self, root: Path, dataset: str, run_id: str) -> None:
        self.root, self.dataset, self.run_id = root, dataset, run_id
        record_path = root / dataset / "runs" / f"{run_id}.json"
        raw = record_path.read_bytes()
        self.record = json.loads(raw)
        self.record_sha256 = hashlib.sha256(raw).hexdigest()

    def path(self, entry: dict[str, Any]) -> Path:
        sha = entry["sha256"]
        return self.root / self.dataset / "objects" / sha[:2] / sha

    def entries(self, pattern: str) -> Iterator[tuple[re.Match[str], dict[str, Any]]]:
        regex = re.compile(pattern)
        for entry in self.record["files"]:
            match = regex.fullmatch(entry["name"])
            if match:
                yield match, entry

    def describe(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "run_id": self.run_id,
            "record": f"{self.dataset}/runs/{self.run_id}.json",
            "record_sha256": self.record_sha256,
            "origin": self.record.get("origin"),
            "replayed_from": self.record.get("replayed_from"),
            "started_at": self.record.get("started_at"),
            "source_period_label": self.record.get("source_period_label"),
        }


def archive_arcep(run: Counting, archive: ArchiveRun) -> None:
    files = {
        (m.group(1), m.group(2)): e
        for m, e in archive.entries(r"(actuel|base_imb)_([0-9AB]{2,3})\.csv(?:\.gz)?")
    }
    for dept in sorted({dept for _, dept in files}):

        def action(dept: str = dept) -> None:
            with archive.path(files[("base_imb", dept)]).open("rb") as raw:
                index = arcep_base_index(text(decompressed(raw)), dept)
            with archive.path(files[("actuel", dept)]).open("rb") as raw:
                joined = arcep_join_counts(text(decompressed(raw)), index)
            run.add(dept, {"base_imb:immeubles": len(index)})
            for code, metrics in joined.items():
                run.add(code, metrics)

        run.guarded(dept, action)


def archive_cadastre(run: Counting, archive: ArchiveRun) -> None:
    for match, entry in archive.entries(
        r"cadastre-([0-9AB]{2,3})-parcelles\.json(?:\.gz)?"
    ):

        def action(entry: dict[str, Any] = entry, dept: str = match.group(1)) -> None:
            with archive.path(entry).open("rb") as raw:
                counts, unattributed = geojson_parcel_counts(decompressed(raw))
            for code, metrics in counts.items():
                run.add(code, metrics)
            if unattributed:
                run.details.setdefault("non_attribues", {})[dept] = unattributed

        run.guarded(match.group(1), action)


def archive_sections(run: Counting, archive: ArchiveRun) -> None:
    pattern = r"(?:cadastre-|sections-)([0-9AB]{2,3})(?:-sections)?\.json(?:\.gz)?"
    for match, entry in archive.entries(pattern):

        def action(entry: dict[str, Any] = entry, dept: str = match.group(1)) -> None:
            with archive.path(entry).open("rb") as raw:
                features, distinct = geojson_section_ids(decompressed(raw))
            run.add(dept, {"geo_sections": distinct, "sections:features": features})

        run.guarded(match.group(1), action)


def archive_rnb(run: Counting, archive: ArchiveRun) -> None:
    files = {m.group(1): e for m, e in archive.entries(r"RNB_([0-9AB]{2,3})\.csv\.zip")}
    run.departements = sorted(files)
    rnb_counting(run, lambda dept: archive.path(files[dept]).open("rb"))


def archive_ban(run: Counting, archive: ArchiveRun) -> None:
    for match, entry in archive.entries(r"adresses-([0-9AB]{2,3})\.csv(?:\.gz)?"):

        def action(entry: dict[str, Any] = entry, dept: str = match.group(1)) -> None:
            with archive.path(entry).open("rb") as raw:
                run.add(dept, ban_rows(text(decompressed(raw))))

        run.guarded(match.group(1), action)


def archive_dvf(run: Counting, archive: ArchiveRun) -> None:
    by_dept: dict[str, list[dict[str, Any]]] = {}
    for entry in archive.record["files"]:
        match = re.fullmatch(
            r"(?:raw/)?([0-9]{4})/([0-9AB]{2,3})\.csv(?:\.gz)?", entry["relpath"]
        )
        if match:
            by_dept.setdefault(match.group(2), []).append(entry)
    for dept, entries in sorted(by_dept.items()):

        def action(dept: str = dept, entries: list[dict[str, Any]] = entries) -> None:
            state = DvfDepartement()
            for entry in entries:
                with archive.path(entry).open("rb") as raw:
                    state.add(text(decompressed(raw)))
            for code, metrics in state.counts().items():
                run.add(code, metrics)
            run.add(dept, {"dvf:lignes_source": state.source_rows})

        run.guarded(dept, action)


def archive_dpe(run: Counting, archive: ArchiveRun) -> None:
    by_name = {entry["name"]: entry for entry in archive.record["files"]}
    with archive.path(by_name["manifest.json"]).open("rb") as raw:
        manifest = json.load(raw)
    regimes = {"dpe03existant": "existant", "dpe02neuf": "neuf"}
    run.details["import_id"] = manifest.get("import_id")
    for item in manifest["files"]:
        dept, slug = item["department"], item["source_slug"]
        name = (
            Path(item["path"]).name
            if "\\" not in item["path"]
            else item["path"].rsplit("\\", 1)[-1]
        )
        with archive.path(by_name[name]).open("rb") as raw:
            records = sum(1 for _ in csv.reader(text(raw))) - 1
        run.details.setdefault("fichiers", []).append(
            {
                "source": slug,
                "departement": dept,
                "rows": item["rows"],
                "initial_total": item["initial_total"],
                "final_total": item["final_total"],
                "csv_records": records,
                "observed_at": item.get("observed_at"),
                "version_start": item.get("version_start"),
            }
        )
        if records != item["rows"]:
            run.pannes[dept] = f"{slug}: CSV records {records} != manifest rows"
        run.add(
            dept,
            {"diagnostics_dpe": records, f"diagnostics_dpe:{regimes[slug]}": records},
        )


def archive_batiments(run: Counting, archive: ArchiveRun) -> None:
    for match, entry in archive.entries(r".*_D([0-9AB]{3})-ED[0-9-]+\.gpkg"):
        dept = departement_from_code3(match.group(1))

        def action(entry: dict[str, Any] = entry, dept: str = dept) -> None:
            run.add(dept, {"batiments": gpkg_feature_count(archive.path(entry))})

        run.guarded(dept, action)
    # Since 04.1-05 the archive keeps the producer .7z, which this read-only
    # script cannot open: a requested departement without a GPKG is a named
    # failure, never a silent zero (04.1-21).
    for dept in run.departements:
        if dept not in run.counts and dept not in run.pannes:
            run.pannes[dept] = "batiments: no GPKG of this departement in the record"


def archive_bdnb(run: Counting, archive: ArchiveRun) -> None:
    """Every zip of the record: the swap deduplicated across all of them."""
    exports: dict[str, BdnbExport] = {}
    zips: dict[str, dict[str, Any]] = {}
    for entry in archive.record["files"]:
        match = BDNB_DEPT_RE.search(entry["name"])
        if match is None:
            continue
        dept = match.group(1).upper()
        if dept in zips:
            raise SystemExit(f"bdnb: two archived zips for departement {dept}")
        path = archive.path(entry)
        zips[dept] = {"name": entry["name"], "sha256": entry["sha256"]}

        def action(dept: str = dept, path: Path = path) -> None:
            exports[dept] = BdnbExport(lambda: path.open("rb"))

        run.guarded(dept, action)
    run.details["zips"] = dict(sorted(zips.items()))
    bdnb_counting(run, exports, None)


ARCHIVE: dict[str, Callable[[Counting, ArchiveRun], None]] = {
    "arcep": archive_arcep,
    "cadastre": archive_cadastre,
    "cadastre_sections": archive_sections,
    "rnb": archive_rnb,
    "ban": archive_ban,
    "dvf": archive_dvf,
    "dpe": archive_dpe,
    "batiments": archive_batiments,
    "bdnb": archive_bdnb,
}


def count(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source
    departements = parse_departements(args.departements)
    run = Counting(source, departements)
    run.options = {
        "millesime": getattr(args, "millesime", None),
        "memoire_max": getattr(args, "memoire_max", None),
        "passes": getattr(args, "passes", None),
    }
    payload: dict[str, Any] = {
        "source": source,
        "filter": FILTERS[source],
        "primary_metric": PRIMARY_METRIC[source],
        "started_at": now_utc(),
    }
    if args.archive_run:
        dataset, _, run_id = args.archive_run.partition(":")
        root = Path(args.archive_root or os.environ.get("SOURCE_ARCHIVE_DIR", ""))
        if not root.is_dir() or source not in ARCHIVE:
            raise SystemExit("archive root missing or source without archive mode")
        archive = ArchiveRun(root, dataset, run_id)
        ARCHIVE[source](run, archive)
        run.counts = {d: v for d, v in run.counts.items() if d in departements}
        payload.update(
            mode="archive",
            generation=archive.describe(),
            urls=[archive.describe()["record"]],
            headers={
                e["relpath"]: {"sha256": e["sha256"], "bytes": e["bytes"]}
                for e in archive.record["files"]
            },
            millesime=archive.record.get("source_period_label"),
        )
    else:
        if source not in LIVE:
            raise SystemExit(f"{source}: no live mode (archive only)")
        http = Http()
        LIVE[source](run, http)
        payload.update(
            mode="producteur",
            urls=http.urls,
            headers=http.headers,
            millesime=live_millesime(source, http, run),
        )
    totals: Counter[str] = Counter()
    for metrics in run.counts.values():
        for name, value in metrics.items():
            # A maximum (``*_max``) is the largest departement value, never a sum.
            totals[name] = (
                max(totals[name], value)
                if name.endswith("_max")
                else totals[name] + value
            )
    payload.update(
        captured_at=now_utc(),
        departements_demandes=departements,
        # Asked on the command line, hence expected by a closed-list ``diff``
        # (the default, every code, is not a perimeter).
        departements_explicites=bool(args.departements),
        counts=dict(sorted(run.counts.items())),
        totals=dict(sorted(totals.items())),
        absent=run.absent,
        pannes=run.pannes,
        details=run.details,
    )
    return payload


# -------------------------------------------------------------------- loaded --

LOADED_SQL: dict[str, list[tuple[str, str]]] = {
    "dpe": [
        (
            "SELECT code_departement, 'diagnostics_dpe:' || regime_dpe, count(*) "
            "FROM diagnostics_dpe WHERE NOT is_tombstone GROUP BY 1, 2",
            "",
        ),
        (
            "SELECT code_departement, count(*) FROM diagnostics_dpe "
            "WHERE NOT is_tombstone GROUP BY 1",
            "diagnostics_dpe",
        ),
        (
            "SELECT code_departement, count(*) FROM diagnostics_dpe "
            "WHERE is_tombstone GROUP BY 1",
            "diagnostics_dpe:tombstones",
        ),
    ],
    "connectivite": [
        (
            # One row per building since plan 04.1-16 (migration 106): the
            # source offer rows are the cardinality of ``offres``.
            "SELECT code_departement, sum(cardinality(offres)) "
            "FROM eligibilite_fixe GROUP BY 1",
            "eligibilite_fixe",
        ),
        (
            "SELECT code_departement, count(DISTINCT id_imb) "
            "FROM eligibilite_fixe GROUP BY 1",
            "eligibilite_fixe:id_imb_distincts",
        ),
    ],
    "cadastre": [
        ("SELECT code_departement, count(*) FROM parcelles GROUP BY 1", "parcelles"),
        (
            "SELECT code_departement, sum(ST_NPoints(geometrie)) "
            "FROM parcelles GROUP BY 1",
            "parcelles:sommets",
        ),
    ],
    "cadastre_sections": [
        (
            "SELECT departement_code, count(*) FROM geo_sections GROUP BY 1",
            "geo_sections",
        ),
    ],
    "rnb_liens": [
        ("SELECT code_departement, count(*) FROM rnb_liens GROUP BY 1", "rnb_liens"),
    ],
    "ban": [("SELECT code_departement, count(*) FROM adresses GROUP BY 1", "adresses")],
    "dvf": [
        ("SELECT code_departement, count(*) FROM mutations GROUP BY 1", "mutations"),
        (
            "SELECT code_departement, count(*) FROM mutations_grain GROUP BY 1",
            "mutations_grain",
        ),
        (
            "SELECT code_departement, count(*) FROM mutation_parcelles GROUP BY 1",
            "mutation_parcelles",
        ),
    ],
    "batiments": [
        ("SELECT code_departement, count(*) FROM batiments GROUP BY 1", "batiments"),
    ],
    # One scan for the closed list METRIQUES_COMPAREES["bdnb"].
    "bdnb_batiment_parcelle": [
        (
            "SELECT s.code_departement, m.metric, m.n FROM ("
            "SELECT code_departement, count(*) AS lignes, "
            "count(*) FILTER (WHERE parcelle_principale) AS principales, "
            "count(annee_construction) AS annee, count(mat_mur_txt) AS mur, "
            "count(mat_toit_txt) AS toit, count(DISTINCT parcelle_id) AS parcelles "
            "FROM bdnb_batiment_parcelle GROUP BY code_departement) s "
            "CROSS JOIN LATERAL (VALUES "
            "('bdnb_batiment_parcelle', s.lignes), "
            "('bdnb:principales', s.principales), "
            "('bdnb:annee_renseignee', s.annee), "
            "('bdnb:mur_renseigne', s.mur), "
            "('bdnb:toit_renseigne', s.toit), "
            "('bdnb:parcelles_distinctes', s.parcelles)) AS m(metric, n)",
            "",
        ),
    ],
}

# Millesime label served for the dataset (the third term of the bdnb
# ``millesime`` block of ``diff``) and perimeter of the generation the runner
# wrote (``effective_departments``): every departement of it is expected.
LOADED_METADATA_SQL: dict[str, str] = {
    "bdnb_batiment_parcelle": (
        "SELECT source_period_label, effective_departments FROM dataset_metadata "
        "WHERE dataset_name = 'bdnb_batiment_parcelle'"
    ),
}

LOADED_PRIMARY = {
    "dpe": "diagnostics_dpe",
    "connectivite": "eligibilite_fixe",
    "cadastre": "parcelles",
    "cadastre_sections": "geo_sections",
    "rnb_liens": "rnb_liens",
    "ban": "adresses",
    "dvf": "mutations",
    "batiments": "batiments",
    "bdnb_batiment_parcelle": "bdnb_batiment_parcelle",
}


def collect_metadata(cursor: Any, dataset: str) -> dict[str, Any]:
    """``source_period_label`` and ``effective_departments`` of the dataset;
    None for each without a metadata row."""
    cursor.execute(LOADED_METADATA_SQL[dataset])
    row = cursor.fetchone()
    label, departements = (None, None) if row is None else row
    return {
        "source_period_label": None if label is None else str(label),
        "effective_departments": None
        if departements is None
        else sorted(str(code) for code in departements),
    }


def collect_loaded(cursor: Any, dataset: str) -> dict[str, dict[str, int]]:
    """Run the dataset queries; a query returning (dept, n) names its metric,
    (dept, metric, n) carries it."""
    counts: dict[str, dict[str, int]] = {}
    for query, metric in LOADED_SQL[dataset]:
        cursor.execute(query)
        for row in cursor.fetchall():
            if metric:
                dept, name, value = row[0], metric, row[1]
            else:
                dept, name, value = row
            counts.setdefault(str(dept), {})[name] = int(value)
    return dict(sorted(counts.items()))


def loaded(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg

    dsn = os.environ.get(args.dsn_env, "")
    if not dsn:
        raise SystemExit(f"environment variable {args.dsn_env} is empty or unset")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.commit()
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), version(), "
                "(SELECT version_num FROM alembic_version LIMIT 1)"
            )
            database, version, revision = cursor.fetchone()
            counts = collect_loaded(cursor, args.dataset)
            extra = (
                collect_metadata(cursor, args.dataset)
                if args.dataset in LOADED_METADATA_SQL
                else {}
            )
    return {
        **extra,
        "dataset": args.dataset,
        "mode": "loaded",
        "primary_metric": LOADED_PRIMARY[args.dataset],
        "captured_at": now_utc(),
        "database": {
            "name": database,
            "server_version": version,
            "alembic_version": revision,
            "workbase_sha": os.environ.get("WORKBASE_SHA"),
        },
        "queries": [query for query, _ in LOADED_SQL[args.dataset]],
        "counts": counts,
    }


# ---------------------------------------------------------------------- diff --


def classify(
    producer: int | None,
    charge: int | None,
    explanation: str | None,
    panne: str | None = None,
    coverage: str | None = None,
) -> dict[str, Any]:
    """One cell: never a zero for an absence, never an explanation by threshold.

    *coverage* is a written out-of-coverage declaration (D-123, DVF 57/67/68
    and 976): with nothing loaded the cell is ``hors_couverture``, whatever the
    producer publishes; rows loaded despite it raise an ``alerte``.
    """
    cell: dict[str, Any] = {"producteur": producer, "charge": charge, "ecart": None}
    if panne is not None:
        cell.update(classe="panne_producteur", motif=panne)
    elif coverage is not None and charge is None:
        cell.update(classe="hors_couverture", motif=coverage)
    elif producer is None and charge is None:
        cell.update(classe="absent_des_deux")
    elif producer is None:
        cell.update(classe="absent_producteur")
    elif charge is None:
        cell.update(classe="absent_base")
    else:
        cell["ecart"] = charge - producer
        if cell["ecart"] == 0:
            cell["classe"] = "egal"
        elif explanation:
            cell.update(classe="ecart_explique", motif=explanation)
        else:
            cell["classe"] = "ecart_a_consigner"
        if coverage is not None:
            cell["alerte"] = f"charge alors que declare hors couverture: {coverage}"
    return cell


def explanations_index(
    items: Iterable[dict[str, Any]],
) -> tuple[dict[tuple[str, str, int], str], dict[tuple[str, str], str]]:
    """Gap explanations keyed by (metric, departement, exact gap), and
    out-of-coverage declarations keyed by (metric or ``*``, departement)."""
    gaps: dict[tuple[str, str, int], str] = {}
    coverage: dict[tuple[str, str], str] = {}
    for item in items:
        if not str(item.get("motif", "")).strip():
            raise SystemExit("an explanation without motif is refused")
        if item.get("etat") == "hors_couverture":
            coverage[(str(item["metric"]), str(item["departement"]))] = item["motif"]
            continue
        key = (str(item["metric"]), str(item["departement"]), int(item["ecart"]))
        gaps[key] = str(item["motif"])
    return gaps, coverage


def expected_perimeter(
    producer: dict[str, Any],
    charge: dict[str, Any],
    departements: list[str] | None,
) -> list[str] | None:
    """Departements a closed-list ``diff`` must find on both sides: those of
    ``diff --departements``, those asked explicitly to ``count``, and the
    perimeter of the generation (``effective_departments`` of the base).
    None when none of them is known."""
    sources = [departements, charge.get("effective_departments")]
    if producer.get("departements_explicites"):
        sources.append(producer.get("departements_demandes"))
    known = [codes for codes in sources if codes is not None]
    if not known:
        return None
    return sorted({str(code) for codes in known for code in codes})


def closed_list_errors(
    fermees: tuple[str, ...],
    p_counts: dict[str, dict[str, int]],
    l_counts: dict[str, dict[str, int]],
    expected: list[str] | None,
    hors_couverture: set[tuple[str, str]],
) -> list[str]:
    """Every metric of the closed list on both sides, for every departement a
    side reports AND every departement expected: a metric or a departement
    missing on one side is an error, never skipped. A departement declared out
    of coverage (``etat: hors_couverture``, metric or ``*``) is not expected."""
    erreurs = []
    if expected is None:
        erreurs.append(
            "perimetre attendu inconnu: ni diff --departements, ni count "
            "--departements, ni effective_departments dans la base"
        )
    for side, counts in (("producteur", p_counts), ("base", l_counts)):
        if not counts:
            erreurs.append(f"{side}: aucun departement")
        for metric in fermees:
            depts = set(counts) | {
                dept
                for dept in expected or ()
                if (metric, dept) not in hors_couverture
                and ("*", dept) not in hors_couverture
            }
            missing = sorted(d for d in depts if metric not in counts.get(d, {}))
            if missing or not any(metric in v for v in counts.values()):
                erreurs.append(f"{side}: metrique comparee absente {metric} {missing}")
    return erreurs


def millesime_block(producer: dict[str, Any], charge: dict[str, Any]) -> dict[str, Any]:
    """One millesime (D5): labels of the archived zips, label of the archive
    record, label served by the base; all present and equal, else ``ecart``."""
    zips = dict((producer.get("details") or {}).get("etiquettes") or {})
    record = (producer.get("generation") or {}).get("source_period_label")
    base = charge.get("source_period_label")
    values = [*zips.values(), record, base]
    equal = bool(zips) and None not in values and len(set(values)) == 1
    return {
        "etiquettes_zips": zips,
        "enregistrement": record,
        "base": base,
        "classe": "egal" if equal else "ecart",
    }


def diff(
    producer: dict[str, Any],
    charge: dict[str, Any],
    *,
    live: dict[str, Any] | None = None,
    explanations: Iterable[dict[str, Any]] = (),
    departements: list[str] | None = None,
) -> dict[str, Any]:
    index, coverage = explanations_index(explanations)
    p_counts, l_counts = producer.get("counts", {}), charge.get("counts", {})
    pannes = producer.get("pannes", {})
    depts = departements or sorted(set(p_counts) | set(l_counts) | set(pannes))
    p_metrics = {m for v in p_counts.values() for m in v}
    l_metrics = {m for v in l_counts.values() for m in v}
    fermees = METRIQUES_COMPAREES.get(producer.get("source"))
    erreurs: list[str] = []
    if fermees is not None:
        expected = expected_perimeter(producer, charge, departements)
        # An expected departement absent from both sides still gets its cells.
        depts = sorted(set(depts) | set(expected or ()))
        erreurs = closed_list_errors(
            fermees, p_counts, l_counts, expected, set(coverage)
        )
    compared = sorted(p_metrics & l_metrics) if fermees is None else list(fermees)
    primary = producer.get("primary_metric")
    if primary not in compared:
        primary = charge.get("primary_metric")
    per_metric: dict[str, dict[str, Any]] = {}
    for metric in compared:
        per_metric[metric] = {}
        for dept in depts:
            p_value = p_counts.get(dept, {}).get(metric)
            l_value = l_counts.get(dept, {}).get(metric)
            gap = None if p_value is None or l_value is None else l_value - p_value
            cell = classify(
                p_value,
                l_value,
                index.get((metric, dept, gap)) if gap is not None else None,
                pannes.get(dept),
                coverage.get((metric, dept)) or coverage.get(("*", dept)),
            )
            if live is not None:
                live_value = live.get("counts", {}).get(dept, {}).get(metric)
                cell["vivant"] = live_value
                cell["ecart_vivant_moins_reference"] = (
                    None
                    if live_value is None or p_value is None
                    else live_value - p_value
                )
            per_metric[metric][dept] = cell
    summary = Counter(
        cell["classe"] for cells in per_metric.values() for cell in cells.values()
    )
    extra: dict[str, Any] = {}
    if producer.get("source") in CONTROLE_MILLESIME:
        extra["millesime"] = millesime_block(producer, charge)
        if extra["millesime"]["classe"] != "egal":
            erreurs.append(f"millesime: {extra['millesime']}")
    return {
        **extra,
        "erreurs": erreurs,
        "compared_at": now_utc(),
        "reference": {
            "source": producer.get("source"),
            "mode": producer.get("mode"),
            "generation": producer.get("generation"),
            "captured_at": producer.get("captured_at"),
            "millesime": producer.get("millesime"),
            "filter": producer.get("filter"),
            "script_sha256": producer.get("script_sha256"),
        },
        "vivant": None
        if live is None
        else {
            "captured_at": live.get("captured_at"),
            "millesime": live.get("millesime"),
            "script_sha256": live.get("script_sha256"),
            "role": "information only: a live count never judges another generation",
        },
        "base": {
            "dataset": charge.get("dataset"),
            "captured_at": charge.get("captured_at"),
            "database": charge.get("database"),
            "queries": charge.get("queries"),
        },
        "primary_metric": primary,
        "departements": per_metric.get(primary, {}),
        "metriques": per_metric,
        "non_comparees": {
            "producteur_seulement": sorted(p_metrics - l_metrics),
            "base_seulement": sorted(l_metrics - p_metrics),
        },
        "classes": dict(sorted(summary.items())),
    }


# ------------------------------------------------------------------ receipts --

# Datasets read from a national file filtered to the scope (scope_filter): their
# fidelity is read in their import receipts, then checked against the base.
NATIONAL_FILTERED = (
    "foncier_pm",
    "equipements",
    "antennes",
    "copropriete",
    "transports",
    "scolaire",
    "monuments",
    "demographie",
    "fiscalite",
    "loyers",
    "revenus",
    "vacance",
    "zonage_abc",
)


def receipt_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    """Staged records, scope removals and published rows of one receipt.

    ``retenues`` = staged records (``stage:<file>`` counters, records, not
    physical lines) minus the rows the scope filter removed; a published count
    that differs is a gap of the pipeline's own filters, never subtracted away.
    A per-table detail "non mesuré" falls back to the ``scope_filter`` stage
    counter; with neither measured, nothing is kept nor compared (never 0).
    """
    counting = receipt.get("counting") or {}
    affected = counting.get("stages_rows_affected") or {}
    stages = {
        name: value
        for name, value in affected.items()
        if name.startswith("stage:") and isinstance(value, int)
    }
    scope = counting.get("scope_filter")
    removed: int | None = None
    without: int | None = None
    if isinstance(scope, dict):
        removed = sum(int(v.get("removed", 0)) for v in scope.values())
        without = sum(int(v.get("without_departement", 0)) for v in scope.values())
    elif isinstance(affected.get("scope_filter"), int):
        removed = affected["scope_filter"]
    published: dict[str, Any] = {}
    total = 0
    for table, info in (receipt.get("tables") or {}).items():
        published[table] = {
            "total_rows": info.get("total_rows"),
            "par_departement": info.get("published_rows_by_departement"),
        }
        total += int(info.get("total_rows") or 0)
    staged = sum(stages.values())
    kept = staged - removed if stages and removed is not None else None
    return {
        "run_id": receipt.get("run_id"),
        "scenario": receipt.get("scenario"),
        "source_rows": counting.get("source_rows"),
        "source_rows_unit": counting.get("source_rows_unit"),
        "enregistrements_charges_table_plate": staged if stages else None,
        "retires_par_perimetre": removed,
        "dont_sans_departement": without,
        "retenus_apres_perimetre": kept,
        "publies": published,
        "publies_total": total,
        "ecart_publies_moins_retenus": None if kept is None else total - kept,
    }


def receipts(args: argparse.Namespace) -> dict[str, Any]:
    imports = Path(args.imports_dir)
    base_totals = _read_json(args.base_counts) if args.base_counts else {}
    explanations, _ = explanations_index(
        _read_json(args.explain) if args.explain else ()
    )
    rows: dict[str, Any] = {}
    for dataset in args.datasets.split(",") if args.datasets else NATIONAL_FILTERED:
        latest = json.loads((imports / dataset / "latest.json").read_text("utf-8"))
        path = imports / dataset / latest["manifest"]
        raw = path.read_bytes()
        summary = receipt_summary(json.loads(raw))
        summary["receipt"] = {
            "path": path.as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        gap = summary["ecart_publies_moins_retenus"]
        summary.update(
            classify(
                summary["retenus_apres_perimetre"],
                summary["publies_total"],
                explanations.get((dataset, "44+85", gap)) if gap is not None else None,
            )
        )
        tables = base_totals.get(dataset)
        if tables is not None:
            in_base = sum(int(v) for v in tables.values())
            summary["base_actuelle"] = {
                "tables": tables,
                "total": in_base,
                "classe": "egal"
                if in_base == summary["publies_total"]
                else "ecart_a_consigner",
            }
        rows[dataset] = summary
    return {
        "mode": "receipts",
        "captured_at": now_utc(),
        "imports_dir": imports.as_posix(),
        "datasets": rows,
        "classes": dict(Counter(row["classe"] for row in rows.values())),
    }


# ----------------------------------------------------------------------- cli --


# --- floors: producer counts module of the import and volume floors (04.1-17) --

# Producer file source -> pipeline dataset, and the two units of its floors:
# ``lignes_importees`` what the import counts before publishing (flat rows, or
# the final candidate for DPE), ``lignes_stockees`` the rows of the published
# table the quality profile counts. Not used: cadastre_sections (a referential,
# no row floor) and bdtopo_wfs (metropole counted as one total).
FLOOR_SOURCES: dict[str, tuple[str, dict[str, str]]] = {
    "arcep": (
        "connectivite",
        {
            # Offer rows staged; one stored row per building (plan 04.1-16).
            "lignes_importees": "eligibilite_fixe",
            "lignes_stockees": "eligibilite_fixe:id_imb_distincts",
        },
    ),
    "ban": ("ban", {"lignes_importees": "adresses", "lignes_stockees": "adresses"}),
    "cadastre": (
        "cadastre",
        {"lignes_importees": "parcelles", "lignes_stockees": "parcelles"},
    ),
    "dpe": (
        "dpe",
        {"lignes_importees": "diagnostics_dpe", "lignes_stockees": "diagnostics_dpe"},
    ),
    "dvf": ("dvf", {"lignes_importees": "mutations", "lignes_stockees": "mutations"}),
    "rnb": (
        "rnb_liens",
        {"lignes_importees": "rnb:paires_fichier", "lignes_stockees": "rnb_liens"},
    ),
    "bdnb": (
        "bdnb_batiment_parcelle",
        {
            # Relation records of the producer files; publishable rows after
            # the swap (the floor of its _check_departement_floors).
            "lignes_importees": "bdnb:brut:relations_fichier",
            "lignes_stockees": "bdnb_batiment_parcelle",
        },
    ),
}
_FLOOR_FILE_RE = re.compile(r"^(?P<source>[a-z_]+)-(?P<date>\d{8})\.json$")


def floors_module(producteurs: Path, script_sha256: str | None) -> str:
    """The generated ``app/scope/producer_counts.py``: deterministic text."""
    found: dict[str, Path] = {}
    for path in sorted(producteurs.iterdir()):
        match = _FLOOR_FILE_RE.match(path.name)
        if match is None or match["source"] not in FLOOR_SOURCES:
            continue
        if match["source"] in found:
            raise ValueError(f"two producer files for {match['source']}")
        found[match["source"]] = path
    missing = sorted(set(FLOOR_SOURCES) - set(found))
    if missing:
        raise ValueError(f"producer files missing: {missing}")
    counts: dict[str, dict[str, dict[str, int]]] = {}
    provenance: dict[str, dict[str, str]] = {}
    for source, path in sorted(found.items()):
        payload = json.loads(path.read_text(encoding="utf-8"))
        dataset, units = FLOOR_SOURCES[source]
        counts[dataset] = {
            unit: {
                dept: int(per_dept[metric])
                for dept, per_dept in sorted(payload["counts"].items())
                if metric in per_dept
            }
            for unit, metric in sorted(units.items())
        }
        provenance[dataset] = {
            "fichier": path.name,
            "captured_at": str(payload["captured_at"]),
            "sha256": file_sha256(path),
            **{f"metrique_{unit}": metric for unit, metric in sorted(units.items())},
        }
    lines = [
        '"""Producer row counts per (dataset, unit, departement) (plan 04.1-17).',
        "",
        "GENERATED by ``scripts/fidelite_nationale.py floors``, never edited by",
        "hand: regenerate it from the producer counts of plan 04.1-03",
        "(``tests/fixtures/producteurs/*.json``). Sources:",
        "",
    ]
    for dataset, entry in sorted(provenance.items()):
        lines += [
            f"- {dataset}: {entry['fichier']}",
            f"  captured {entry['captured_at']}, sha256 {entry['sha256'][:16]}",
        ]
    lines += [
        "",
        f"Generator sha256: {script_sha256 or 'unknown'}.",
        '"""',
        "",
        "PRODUCER_COUNTS: dict[str, dict[str, dict[str, int]]] = {",
    ]
    for dataset, units in sorted(counts.items()):
        lines.append(f'    "{dataset}": {{')
        for unit, per_dept in sorted(units.items()):
            lines.append(f'        "{unit}": {{')
            lines += [f'            "{dept}": {n},' for dept, n in per_dept.items()]
            lines.append("        },")
        lines.append("    },")
    lines += ["}", "", "PRODUCER_COUNTS_PROVENANCE: dict[str, dict[str, str]] = {"]
    for dataset, entry in sorted(provenance.items()):
        lines.append(f'    "{dataset}": {{')
        lines += [
            f'        "{key}": "{value}",' for key, value in sorted(entry.items())
        ]
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _read_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--script-sha256", help="SHA-256 of the file sent on stdin")
    commands = parser.add_subparsers(dest="command", required=True)

    p_count = commands.add_parser("count")
    p_count.add_argument("--source", required=True, choices=sorted(FILTERS))
    p_count.add_argument("--departements")
    p_count.add_argument("--archive-run", help="DATASET:RUN_ID of the source archive")
    p_count.add_argument("--archive-root")
    p_count.add_argument("--millesime", help="bdnb: millesime of the URL, 2026-02-b")
    p_count.add_argument(
        "--memoire-max",
        type=int,
        help=f"bdnb: bytes for the passes (default {BDNB_MEMOIRE_MAX_DEFAUT})",
    )
    p_count.add_argument("--passes", type=int, help="bdnb: K, instead of --memoire-max")

    p_loaded = commands.add_parser("loaded")
    p_loaded.add_argument("--dataset", required=True, choices=sorted(LOADED_SQL))
    p_loaded.add_argument("--dsn-env", default="DATABASE_URL")

    p_diff = commands.add_parser("diff")
    p_diff.add_argument("--producer", required=True)
    p_diff.add_argument("--loaded", required=True)
    p_diff.add_argument("--live")
    p_diff.add_argument("--explain")
    p_diff.add_argument("--departements")

    p_receipts = commands.add_parser("receipts")
    p_receipts.add_argument("--imports-dir", required=True)
    p_receipts.add_argument("--datasets", help="default: NATIONAL_FILTERED")
    p_receipts.add_argument("--base-counts", help="JSON {dataset: {table: rows}}")
    p_receipts.add_argument("--explain")

    p_floors = commands.add_parser("floors")
    p_floors.add_argument("--producteurs", required=True)
    p_floors.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    if args.command == "floors":
        # The only mode that writes a file: run on the workstation, never on
        # the hypervisor (it reads versioned measurements, no producer, no base).
        # Hash of the LF text: the module is the same on a CRLF checkout.
        here = Path(__file__) if "__file__" in globals() else None
        generator = (
            hashlib.sha256(here.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            if here is not None and here.is_file()
            else args.script_sha256
        )
        module = floors_module(Path(args.producteurs), generator)
        Path(args.out).write_text(module, encoding="utf-8", newline="\n")
        return 0
    if args.command == "count":
        payload = count(args)
    elif args.command == "loaded":
        payload = loaded(args)
    elif args.command == "receipts":
        payload = receipts(args)
        payload["inputs"] = {
            name: {"path": value, "sha256": file_sha256(Path(value))}
            for name, value in (
                ("base_counts", args.base_counts),
                ("explain", args.explain),
            )
            if value
        }
    else:
        payload = diff(
            _read_json(args.producer),
            _read_json(args.loaded),
            live=_read_json(args.live) if args.live else None,
            explanations=_read_json(args.explain) if args.explain else (),
            departements=parse_departements(args.departements)
            if args.departements
            else None,
        )
        payload["inputs"] = {
            name: {"path": value, "sha256": file_sha256(Path(value))}
            for name, value in (
                ("producer", args.producer),
                ("loaded", args.loaded),
                ("live", args.live),
                ("explain", args.explain),
            )
            if value
        }
    payload.update(script_identity(args.script_sha256))
    # UTF-8 whatever the console code page (cp1252 on a Windows workstation).
    text_out = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
    sys.stdout.buffer.write(text_out.encode("utf-8") + b"\n")
    sys.stdout.flush()
    # diff: the output is written, then a closed-list or millesime error fails.
    return 1 if payload.get("erreurs") else 0


if __name__ == "__main__":
    sys.exit(main())
