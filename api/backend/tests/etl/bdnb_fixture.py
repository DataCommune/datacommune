"""Synthetic BDNB departemental exports, built in code (plan 04.7-19).

No binary is committed: :func:`build_export` writes a zip shaped like the
producer's (``csv/`` members, headers of the 2026-02.b dictionary, quoted
departement code, multiline version history, an unread ``batiment_groupe.csv``
and documentation members) from plain tuples.

:func:`mini_exports` is the mini-export of the plan: a principal and a
non-principal relation, a group without ffo row and an ffo row without year,
a parcel of twelve groups, a relation duplicated between ``dep44`` and
``dep85``, a ``dep2a`` file, accented values and a member preceded by a BOM.
Its published rows are written by hand in :func:`attendu`, never derived by a
copy of the rule.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

LABEL = "2026-02.b"
MILLESIME_URL = "2026-02-b"
BOM = "﻿"

# Parcels of the addresses A and B of tests/integration/test_rapport_contract.py.
PARCELLE_A = "85191000RA0001"
PARCELLE_B = "85191000RB0001"

RELATIONS = "csv/rel_batiment_groupe_parcelle.csv"
FFO = "csv/batiment_groupe_ffo_bat.csv"
VERSION = "csv/metadonnees_bdnb_version.csv"
GROUPE = "csv/batiment_groupe.csv"

_RELATIONS_HEADER = (
    "batiment_groupe_id;parcelle_id;code_departement_insee;parcelle_principale"
)
_FFO_HEADER = (
    "batiment_groupe_id;code_departement_insee;nb_niveau;annee_construction;"
    "usage_niveau_1_txt;mat_mur_txt;mat_toit_txt;nb_log"
)


@dataclass(frozen=True)
class Export:
    """One departemental export: relations (group, parcel, ``1``/``0``) and
    ffo rows (group, year, wall, roof), as the producer writes them."""

    relations: tuple[tuple[str, str, str], ...]
    ffo: tuple[tuple[str, str, str, str], ...]
    label: str = LABEL
    # Members written with a leading BOM.
    bom: tuple[str, ...] = ()
    # Members left out of the zip.
    omit: tuple[str, ...] = ()
    # Members replaced by these exact bytes.
    raw: dict[str, bytes] = field(default_factory=dict)


def _version_history(label: str) -> str:
    """The history of the version member: newest first, multiline fields."""
    return (
        "millesime;version;date_publication;changelog_rapide;changelog_detaille\n"
        f'{label};v0.7.12;2026/10/07;Audits énergétiques;"\n# CHANGELOG {label}\n\n'
        '| Fournisseur | Jeu |\n|:--|:--|\n"\n'
        '2026-02.a;v0.7.11;2026/05/15;Refonte;"\n# CHANGELOG 2026-02.a\n"\n'
        '2025-07.b;v0.7.10;2025/12/09;Correctifs;"ligne unique"\n'
    )


def _members(dept: str, export: Export) -> dict[str, str]:
    code = f'"{dept}"'
    relations = [_RELATIONS_HEADER] + [
        f"{groupe};{parcelle};{code};{principale}"
        for groupe, parcelle, principale in export.relations
    ]
    ffo = [_FFO_HEADER] + [
        f"{groupe};{code};2;{annee};Résidentiel individuel;{mur};{toit};1"
        for groupe, annee, mur, toit in export.ffo
    ]
    groupes = sorted({r[0] for r in export.relations if r[0]})
    batiment_groupe = ["batiment_groupe_id;code_departement_insee;geom_groupe"] + [
        f"{groupe};{code};MULTIPOLYGON(((0 0,1 0,1 1,0 0)))" for groupe in groupes
    ]
    return {
        VERSION: _version_history(export.label),
        RELATIONS: "\n".join(relations) + "\n",
        FFO: "\n".join(ffo) + "\n",
        GROUPE: "\n".join(batiment_groupe) + "\n",
        "csv/rel_batiment_groupe_parcelle.csvt": '"String(22)","String(14)"\n',
        "doc/README.md": "# BDNB\nLicence Ouverte v2.0 d'Etalab.\n",
    }


def export_name(dept: str, millesime: str = MILLESIME_URL) -> str:
    """The producer's file name of the export of *dept*."""
    return f"open_data_millesime_{millesime}_dep{dept.lower()}_csv.zip"


def build_export(directory: Path, dept: str, export: Export) -> Path:
    """Write the zip of *export* for *dept* under its producer name."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / export_name(dept)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in _members(dept, export).items():
            if name in export.omit:
                continue
            if name in export.raw:
                archive.writestr(name, export.raw[name])
                continue
            prefix = BOM if name in export.bom else ""
            archive.writestr(name, (prefix + content).encode("utf-8"))
    return path


def _douze() -> list[str]:
    return [f"bdnb-bg-85D0-0000-00{i:02d}" for i in range(1, 13)]


def mini_exports(
    parcelle_a: str = PARCELLE_A, parcelle_b: str = PARCELLE_B
) -> dict[str, Export]:
    """The mini-export of the plan, per departement (``85``, ``44``, ``2A``)."""
    dep85 = Export(
        relations=(
            ("bdnb-bg-85A0-0000-0001", parcelle_a, "1"),
            ("bdnb-bg-85B0-0000-0001", parcelle_b, "1"),
            # Not the principal parcel of its group: still a building of B.
            ("bdnb-bg-85B0-0000-0002", parcelle_b, "0"),
            ("bdnb-bg-85B0-0000-0002", "85191000RB0002", "1"),
            ("bdnb-bg-85C0-0000-0001", "85191000RC0003", "1"),
            *((groupe, "85191000RD0004", "1") for groupe in _douze()),
            # Also in dep44, there as the principal parcel.
            ("bdnb-bg-DUP0-0000-0001", "85000000ZZ0001", "0"),
            # No group id: set aside, never loaded.
            ("", "85191000RE0005", "1"),
        ),
        ffo=(
            ("bdnb-bg-85A0-0000-0001", "1984", "AGGLOMERE", "TUILES"),
            ("bdnb-bg-85B0-0000-0001", "1975", "BRIQUES", "ARDOISES"),
            # bdnb-bg-85B0-0000-0002 has no ffo row.
            ("bdnb-bg-85C0-0000-0001", "", "PIERRE", ""),
            *((groupe, "1990", "BETON", "TERRASSE") for groupe in _douze()),
            ("bdnb-bg-DUP0-0000-0001", "1930", "MEULIÈRE", "TUILES"),
            # No relation: never published.
            ("bdnb-bg-85Z0-0000-0001", "2010", "BOIS", "ZINC"),
        ),
    )
    dep44 = Export(
        relations=(
            ("bdnb-bg-44A0-0000-0001", "44109000LB0369", "1"),
            ("bdnb-bg-DUP0-0000-0001", "85000000ZZ0001", "1"),
        ),
        ffo=(
            ("bdnb-bg-44A0-0000-0001", "2001", "BETON", "TUILES"),
            ("bdnb-bg-DUP0-0000-0001", "1930", "MEULIÈRE", "TUILES"),
        ),
        bom=(RELATIONS,),
    )
    dep2a = Export(
        relations=(("bdnb-bg-2A00-0000-0001", "2A004000AB0001", "1"),),
        ffo=(("bdnb-bg-2A00-0000-0001", "1850", "PIERRE", "LAUZES"),),
    )
    return {"85": dep85, "44": dep44, "2A": dep2a}


def attendu(parcelle_a: str = PARCELLE_A, parcelle_b: str = PARCELLE_B) -> list[tuple]:
    """Published rows of :func:`mini_exports`, written by hand.

    ``(code_departement, parcelle_id, batiment_groupe_id, parcelle_principale,
    annee_construction, mat_mur_txt, mat_toit_txt)``, ordered by departement
    (COLLATE "C"), parcel, group. The duplicated relation is kept once, in
    ``44`` (smallest code), principal (OR of the two files).
    """
    rows = [
        ("2A", "2A004000AB0001", "bdnb-bg-2A00-0000-0001", True, 1850, "PIERRE",
         "LAUZES"),
        ("44", "44109000LB0369", "bdnb-bg-44A0-0000-0001", True, 2001, "BETON",
         "TUILES"),
        ("44", "85000000ZZ0001", "bdnb-bg-DUP0-0000-0001", True, 1930, "MEULIÈRE",
         "TUILES"),
        ("85", parcelle_a, "bdnb-bg-85A0-0000-0001", True, 1984, "AGGLOMERE",
         "TUILES"),
        ("85", parcelle_b, "bdnb-bg-85B0-0000-0001", True, 1975, "BRIQUES",
         "ARDOISES"),
        ("85", parcelle_b, "bdnb-bg-85B0-0000-0002", False, None, None, None),
        ("85", "85191000RB0002", "bdnb-bg-85B0-0000-0002", True, None, None, None),
        ("85", "85191000RC0003", "bdnb-bg-85C0-0000-0001", True, None, "PIERRE",
         None),
        *(
            ("85", "85191000RD0004", groupe, True, 1990, "BETON", "TERRASSE")
            for groupe in _douze()
        ),
    ]  # fmt: skip
    return sorted(rows, key=lambda r: (r[0], r[1], r[2]))


def with_label(export: Export, label: str) -> Export:
    """*export* whose version member names another millesime."""
    return replace(export, label=label)


def with_spaced_headers(dept: str, export: Export) -> Export:
    """*export* whose three members name their columns with spaces around
    (`` batiment_groupe_id ;…``), rows unchanged."""
    raw = {}
    for name in (RELATIONS, FFO, VERSION):
        header, _, rest = _members(dept, export)[name].partition("\n")
        spaced = ";".join(f" {column} " for column in header.split(";"))
        raw[name] = f"{spaced}\n{rest}".encode()
    return replace(export, raw=raw)
