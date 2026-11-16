#!/usr/bin/env python3
"""Former commune names of the INSEE COG, for the city detection of the geocoder.

Writes ``app/ban/anciennes_communes.csv``: one row per former commune name and
current commune it now belongs to (« Ancenis » → 44003 Ancenis-Saint-Géréon,
merged in 2019). Read by ``app.ban.queries`` when no current commune ends the
query (quick 261102-xaj).

Input: three files of the Code officiel géographique, millésime 2026, from
https://www.insee.fr/fr/statistiques/fichier/8740222/ :
``v_commune_2026.csv`` (communes on 2026-01-01), ``v_commune_depuis_1943.csv``
(every name and code since 1943, with its end date) and
``v_mvt_commune_2026.csv`` (the events: mergers, name changes, splits).

Kept: every name a commune bore until an end date since 2015-01-01, the
wave of the communes nouvelles (merger, name change, association), unless a
current commune bears it, punctuation aside — the exact detection answers
those. A code still a commune today keeps its own code (« Ancenis » → 44003);
a code that disappeared follows the events to the communes of today. A name
that reaches no current commune is reported and left out.

Why 2015: measured on the street names of 44 and 85 (quick 261102-xaj,
``plans/01-fausses-detections.txt``), every name since 1943 detects a commune
at the end of 0.47 % of them, mostly older names now borne by hamlets and
streets (« allée abbé pierre » → Pierre); since 2015, 0.19 %, keeping the
names still in use (Ancenis, Saint-Géréon, Varades, Chéméré).

Usage (from ``api/backend/``, module form so ``app`` resolves):
    uv run python -m scripts.build_anciennes_communes <directory of the 3 files>
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from app.ban.queries import ANCIENNES_COMMUNES_PATH, normalize_address_text

_COMMUNES = "v_commune_2026.csv"
_HISTORIQUE = "v_commune_depuis_1943.csv"
_EVENEMENTS = "v_mvt_commune_2026.csv"
DEPUIS = "2015-01-01"


def _lire(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class _Evenements:
    """The communes of today that a code became, through the COG events."""

    def __init__(self, evenements: list[dict[str, str]], actuelles: set[str]):
        self._actuelles = actuelles
        # (code, date) -> codes of the communes that code became on that date.
        self._suites: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in evenements:
            if row["TYPECOM_AV"] == "COM" and row["TYPECOM_AP"] == "COM":
                self._suites[(row["COM_AV"], row["DATE_EFF"])].add(row["COM_AP"])
        self._dates: dict[str, list[str]] = defaultdict(list)
        for code, date in sorted(self._suites):
            self._dates[code].append(date)

    def aujourdhui(self, code: str, apres: str, profondeur: int = 0) -> set[str]:
        """Current codes of *code* as it was on *apres*; empty if none."""
        if code in self._actuelles:
            return {code}
        if profondeur > 50:
            return set()
        for date in (d for d in self._dates[code] if d >= apres):
            cibles = self._suites[(code, date)] - {code}
            if cibles:
                return set().union(
                    *(self.aujourdhui(cible, date, profondeur + 1) for cible in cibles)
                )
        return set()


def anciennes_communes(
    dossier: Path, depuis: str = DEPUIS
) -> tuple[list[tuple[str, str]], list[str]]:
    """(former name, current code) pairs of the names ended since *depuis*,
    sorted; and the names left out."""
    actuelles = {
        row["COM"]: row["LIBELLE"]
        for row in _lire(dossier / _COMMUNES)
        if row["TYPECOM"] == "COM"
    }
    noms_actuels = {normalize_address_text(nom) for nom in actuelles.values()}
    evenements = _Evenements(_lire(dossier / _EVENEMENTS), set(actuelles))

    paires: set[tuple[str, str]] = set()
    ecartes: list[str] = []
    for row in _lire(dossier / _HISTORIQUE):
        if row["TYPECOM"] != "COM" or row["DATE_FIN"] < depuis:
            continue
        nom = row["LIBELLE"]
        if normalize_address_text(nom) in noms_actuels:
            continue
        codes = evenements.aujourdhui(row["COM"], row["DATE_FIN"])
        if not codes:
            ecartes.append(f"{nom} ({row['COM']}, {row['DATE_FIN']})")
        paires.update((nom, code) for code in codes)
    return sorted(paires), sorted(ecartes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dossier", type=Path, help="directory of the 3 COG files")
    args = parser.parse_args()
    paires, ecartes = anciennes_communes(args.dossier)
    with ANCIENNES_COMMUNES_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(("nom", "code_commune"))
        writer.writerows(paires)
    print(f"{len(paires)} rows -> {ANCIENNES_COMMUNES_PATH}")
    for nom in ecartes:
        print(f"left out, no current commune: {nom}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
