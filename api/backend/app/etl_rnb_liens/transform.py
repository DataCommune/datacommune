"""Pure parsing/reshaping of an RNB departmental export into link rows.

Kept free of DB and network so the whole reshaping contract is unit-testable:
the RNB export embeds JSON inside CSV fields, and every attachment decision of
Lot C rests on reading that JSON exactly like the live RNB call does.

Grain produced here: ONE ROW PER (building, BAN key) PAIR. A building with no
BAN key produces nothing -- a keyless building could never be a carrier nor
widen anything, so ingesting it would only bloat the table the join scans.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from app.etl_rnb_liens.config import CSV_SEPARATOR, EXPECTED_COLUMNS, OUTPUT_COLUMNS

logger = logging.getLogger(__name__)

# The ``shape`` column holds MULTIPOLYGON WKT that routinely exceeds the csv
# module's 128 KB default field limit. Raised to a bounded 16 MB (not
# sys.maxsize, which overflows the C long on Windows).
_FIELD_SIZE_LIMIT = 16 * 1024 * 1024


def pg_text_array(values: Iterable[str]) -> str:
    """Render *values* as a PostgreSQL ``text[]`` literal for COPY.

    Empty input yields ``{}`` (never an empty string, which does not cast to
    ``text[]``). Quotes and backslashes are escaped even though RNB parcel ids
    are alphanumeric -- the escaping is what makes the literal safe, not the
    current shape of the data.
    """
    parts = []
    for value in values:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f'"{escaped}"')
    return "{" + ",".join(parts) + "}"


def parse_cles_ban(raw: str) -> list[str]:
    """Extract the building's BAN interoperability keys, order-preserving.

    ``addresses`` is a JSON array embedded in the CSV field, each item carrying
    ``cle_interop_ban`` (plus street/city labels we do not ingest -- BAN is
    already local). Malformed JSON yields an empty list rather than raising: a
    single bad row must not abort a 1M-row export.
    """
    keys: list[str] = []
    for item in _json_items(raw, "addresses"):
        cle = item.get("cle_interop_ban")
        if isinstance(cle, str) and cle:
            keys.append(cle)
    return list(dict.fromkeys(keys))


def parse_parcelles(raw: str) -> list[str]:
    """Extract parcel ids, ordered by DESCENDING footprint cover ratio.

    ``plots`` is a JSON array of ``{"id": ..., "bdg_cover_ratio": ...}``. The
    ratio orders the list -- ``parcelles[0]`` is the principal parcel -- but is
    not itself stored (see migration 079). A missing/unparseable ratio sorts
    last rather than dropping the parcel: the parcel link is what the lookup
    needs, the ordering is a convenience.
    """
    scored: list[tuple[float, str]] = []
    for item in _json_items(raw, "plots"):
        parcelle = item.get("id")
        if not isinstance(parcelle, str) or not parcelle:
            continue
        try:
            ratio = float(item.get("bdg_cover_ratio"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            ratio = -1.0
        scored.append((ratio, parcelle))
    scored.sort(key=lambda pair: -pair[0])
    return list(dict.fromkeys(parcelle for _, parcelle in scored))


def _json_items(raw: str, field: str) -> list[dict]:
    """Parse a CSV-embedded JSON array, returning only its dict items."""
    text = (raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except ValueError:
        logger.warning("RNB export: unparseable %s field, row skipped", field)
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def iter_link_rows(
    rows: Iterable[dict[str, str]],
    dept_code: str,
    millesime: str,
) -> Iterator[list[str]]:
    """Explode RNB building rows into (building, BAN key) output rows.

    *dept_code* is stamped from the source FILE (one export per department,
    same convention as the BAN pipeline) and *millesime* is the export's
    upstream publication date (empty string when unknown -- never invented).
    """
    for row in rows:
        id_rnb = (row.get("rnb_id") or "").strip()
        if not id_rnb:
            continue
        cles = parse_cles_ban(row.get("addresses", ""))
        if not cles:
            # No BAN key: nothing to link. Same skip as the live call.
            continue
        parcelles = pg_text_array(parse_parcelles(row.get("plots", "")))
        statut = (row.get("status") or "").strip()
        for cle in cles:
            yield [id_rnb, cle, dept_code, statut, parcelles, millesime]


def validate_export_header(zip_path: Path) -> None:
    """Raise ValueError if the zipped CSV lacks a required column."""
    with _open_export(zip_path) as stream:
        header = next(csv.reader(stream, delimiter=CSV_SEPARATOR))

    actual = {col.strip() for col in header}
    missing = set(EXPECTED_COLUMNS) - actual
    extra = actual - set(EXPECTED_COLUMNS)
    if extra:
        logger.info(
            "RNB export %s carries %d unused columns: %s",
            zip_path.name,
            len(extra),
            sorted(extra),
        )
    if missing:
        msg = (
            f"RNB export schema mismatch in {zip_path.name}: "
            f"missing required columns {sorted(missing)}"
        )
        raise ValueError(msg)


def transform_rnb_export(
    zip_path: Path,
    output_path: Path,
    dept_code: str,
    millesime: str,
) -> int:
    """Stream *zip_path* into a COPY-ready CSV at *output_path*. Return rows.

    Reads the zipped CSV member as a stream: the dept 44 export is ~1 GB
    uncompressed, and nothing here needs it on disk or in RAM.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with (
        _open_export(zip_path) as stream,
        open(output_path, "w", encoding="utf-8", newline="") as out,
    ):
        reader = csv.DictReader(stream, delimiter=CSV_SEPARATOR)
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(OUTPUT_COLUMNS)
        for values in iter_link_rows(reader, dept_code, millesime):
            writer.writerow(values)
            written += 1
    logger.info(
        "Transformed RNB export %s: %d (batiment, cle BAN) rows",
        zip_path.name,
        written,
    )
    return written


@contextmanager
def _open_export(zip_path: Path) -> Iterator[io.TextIOWrapper]:
    """Yield the single CSV member of *zip_path* as a UTF-8 text stream.

    Closes both the member stream and the archive handle on exit -- the export
    is opened twice per run (header validation, then transform) and a leaked
    handle on a 250 MB archive is not free.
    """
    csv.field_size_limit(_FIELD_SIZE_LIMIT)
    with zipfile.ZipFile(zip_path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            msg = f"Expected exactly one CSV in {zip_path.name}, found {members}"
            raise ValueError(msg)
        with archive.open(members[0]) as member:
            yield io.TextIOWrapper(member, encoding="utf-8", newline="")
