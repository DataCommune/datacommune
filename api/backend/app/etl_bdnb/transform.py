"""Pure reading/reshaping of a BDNB departemental export into COPY rows.

Kept free of DB and network so the whole reading contract is unit-testable.
The zip is opened with ``zipfile`` and only the three members named in
``config`` are read, as streams: ``batiment_groupe.csv`` (190 MB with its WKT
for 85) and the 120 other members are never opened, nothing is ever
extracted to disk.

Output grain: ONE ROW PER SOURCE RECORD, tagged by its nature -- a
``relation`` row per (building group, parcel) relation, an ``ffo`` row per
group of ``batiment_groupe_ffo_bat``. The join and the deduplication belong to
``BdnbBatimentParcellePipeline.swap()``, in SQL, across every file of the run.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.etl_bdnb.config import (
    CSV_SEPARATOR,
    FFO_COLUMNS,
    MAX_COMPRESSION_RATIO,
    MAX_MEMBER_BYTES,
    MEMBER_FFO,
    MEMBER_RELATIONS,
    MEMBER_VERSION,
    NATURE_FFO,
    NATURE_RELATION,
    OUTPUT_COLUMNS,
    RELATION_COLUMNS,
    VERSION_COLUMNS,
)

logger = logging.getLogger(__name__)

# ``open_data_millesime_2026-02-b_dep85_csv.zip`` (producer name) or
# ``dep85.zip``: two characters, Corsica lower case.
_DEPT_RE = re.compile(r"(?:^|_)dep([0-9]{2}|2[ab])(?:_csv)?\.zip$")
# Millesime as the export labels it in its version member: ``2026-02.b``.
LABEL_RE = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])\.[a-z]")
# The same millesime as the URL writes it: ``2026-02-b``.
MILLESIME_RE = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])-[a-z]")

_BOOLEENS = {"1": "t", "0": "f"}


@dataclass(frozen=True)
class VersionExport:
    """The most recent line of the export's version history."""

    label: str
    date_publication: date


@dataclass(frozen=True)
class ComptesExport:
    """Rows of one export, per nature: read, and set aside without an id."""

    relations_lues: int
    relations_ecartees: int
    ffo_lues: int
    ffo_ecartees: int


def label_de_millesime(millesime: str) -> str:
    """``2026-02-b`` (URL) -> ``2026-02.b`` (label of the version member)."""
    if not MILLESIME_RE.fullmatch(millesime):
        raise ValueError(f"BDNB: millésime mal formé {millesime!r}")
    return f"{millesime[:7]}.{millesime[8:]}"


def departement_du_fichier(path: Path) -> str:
    """Departement of an export file: ``dep85`` -> ``85``, ``dep2a`` -> ``2A``."""
    match = _DEPT_RE.search(path.name)
    if match is None:
        raise ValueError(f"BDNB: département illisible dans {path.name}")
    return match.group(1).upper()


@contextmanager
def _ouvrir(archive: zipfile.ZipFile, name: str) -> Iterator[io.TextIOWrapper]:
    """Yield member *name* as a strict UTF-8 text stream.

    Refused before it is opened: an absent member, a member above
    ``MAX_MEMBER_BYTES`` uncompressed or above ``MAX_COMPRESSION_RATIO``
    (T-19-04). ``zipfile`` reads no more than the declared size and checks the
    CRC at the end. ``utf-8-sig`` strips a leading BOM and is plain strict
    UTF-8 otherwise; no other encoding is ever tried.
    """
    try:
        info = archive.getinfo(name)
    except KeyError:
        raise ValueError(f"BDNB: membre absent {name}") from None
    if info.file_size > MAX_MEMBER_BYTES:
        raise ValueError(
            f"BDNB: {name} fait {info.file_size} octets, "
            f"au-delà du plafond {MAX_MEMBER_BYTES}"
        )
    if info.file_size > MAX_COMPRESSION_RATIO * max(info.compress_size, 1):
        raise ValueError(
            f"BDNB: {name} compressé {info.compress_size} -> {info.file_size} "
            f"octets, rapport au-delà de {MAX_COMPRESSION_RATIO}"
        )
    with archive.open(info) as member:
        try:
            yield io.TextIOWrapper(
                member, encoding="utf-8-sig", errors="strict", newline=""
            )
        except UnicodeDecodeError as exc:
            raise ValueError(f"BDNB: octets non UTF-8 dans {name}") from exc


def _lecteur(stream: io.TextIOWrapper, name: str, required: tuple[str, ...]):
    """A DictReader over *stream*, refused when a required column is missing.

    The names are stripped once, for the check AND the keys of the rows: a
    header `` parcelle_id`` passes the check, so it must also be read.
    """
    reader = csv.DictReader(stream, delimiter=CSV_SEPARATOR)
    reader.fieldnames = [col.strip() for col in (reader.fieldnames or [])]
    missing = sorted(set(required) - set(reader.fieldnames))
    if missing:
        raise ValueError(f"BDNB: colonnes absentes de {name}: {missing}")
    return reader


def _version(archive: zipfile.ZipFile) -> VersionExport:
    """The newest line of the version history (multiline quoted fields)."""
    with _ouvrir(archive, MEMBER_VERSION) as stream:
        lignes = [
            (row["date_publication"].strip(), row["millesime"].strip())
            for row in _lecteur(stream, MEMBER_VERSION, VERSION_COLUMNS)
        ]
    if not lignes:
        raise ValueError(f"BDNB: historique de version vide ({MEMBER_VERSION})")
    publiee, label = max(lignes)
    if not LABEL_RE.fullmatch(label):
        raise ValueError(f"BDNB: millésime illisible {label!r}")
    try:
        jour = datetime.strptime(publiee, "%Y/%m/%d").date()
    except ValueError:
        raise ValueError(f"BDNB: date de publication illisible {publiee!r}") from None
    return VersionExport(label=label, date_publication=jour)


def read_version(zip_path: Path) -> VersionExport:
    """Millesime label and publication date of the export *zip_path*."""
    with zipfile.ZipFile(zip_path) as archive:
        return _version(archive)


def validate_export(zip_path: Path) -> VersionExport:
    """Check the three members and their headers; return the version read."""
    with zipfile.ZipFile(zip_path) as archive:
        version = _version(archive)
        for name, required in (
            (MEMBER_RELATIONS, RELATION_COLUMNS),
            (MEMBER_FFO, FFO_COLUMNS),
        ):
            with _ouvrir(archive, name) as stream:
                _lecteur(stream, name, required)
    return version


def _annee(valeur: str, name: str) -> str:
    """A year as written for COPY; empty stays empty (NULL), non-integer refused."""
    texte = valeur.strip()
    if texte and not re.fullmatch(r"-?[0-9]+", texte):
        raise ValueError(f"BDNB: année non entière {texte!r} dans {name}")
    return texte


def transform_export(
    zip_path: Path, output_path: Path | None, dept: str
) -> ComptesExport:
    """Stream the two data members of *zip_path* into a COPY-ready CSV.

    Every row carries the departement of the FILE (*dept*). An empty string
    becomes NULL at COPY; ``1``/``0`` become booleans. A row without its ids is
    set aside and counted, never loaded. *output_path* None counts the export
    by the same reading, writing nothing (a resume, for a file already loaded).
    """
    if output_path is None:
        output_path = Path(os.devnull)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    relations_lues = relations_ecartees = ffo_lues = ffo_ecartees = 0
    with (
        zipfile.ZipFile(zip_path) as archive,
        open(output_path, "w", encoding="utf-8", newline="") as out,
    ):
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(OUTPUT_COLUMNS)
        with _ouvrir(archive, MEMBER_RELATIONS) as stream:
            for row in _lecteur(stream, MEMBER_RELATIONS, RELATION_COLUMNS):
                relations_lues += 1
                groupe = (row["batiment_groupe_id"] or "").strip()
                parcelle = (row["parcelle_id"] or "").strip()
                if not groupe or not parcelle:
                    relations_ecartees += 1
                    continue
                principale = (row["parcelle_principale"] or "").strip()
                if principale not in _BOOLEENS:
                    raise ValueError(
                        f"BDNB: parcelle_principale {principale!r} "
                        f"dans {MEMBER_RELATIONS}"
                    )
                writer.writerow(
                    [NATURE_RELATION, groupe, parcelle, dept, _BOOLEENS[principale]]
                    + [""] * 3
                )
        with _ouvrir(archive, MEMBER_FFO) as stream:
            for row in _lecteur(stream, MEMBER_FFO, FFO_COLUMNS):
                ffo_lues += 1
                groupe = (row["batiment_groupe_id"] or "").strip()
                if not groupe:
                    ffo_ecartees += 1
                    continue
                writer.writerow(
                    [
                        NATURE_FFO,
                        groupe,
                        "",
                        dept,
                        "",
                        _annee(row["annee_construction"] or "", MEMBER_FFO),
                        (row["mat_mur_txt"] or "").strip(),
                        (row["mat_toit_txt"] or "").strip(),
                    ]
                )
    comptes = ComptesExport(relations_lues, relations_ecartees, ffo_lues, ffo_ecartees)
    logger.info("Transformed BDNB export %s: %s", zip_path.name, comptes)
    return comptes
