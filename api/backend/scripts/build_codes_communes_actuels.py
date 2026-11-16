#!/usr/bin/env python3
"""Former commune codes of the INSEE COG and the one commune of today each became.

Writes ``app/etl_dvf/codes_communes_actuels.csv``, read by the DVF import
(``app.etl_dvf.transform``) to recode the sales geo-dvf still files under a
former code (KI-D7: Orée d'Anjou 49069 -> 49126, Pierrefitte-sur-Seine 93059 ->
93066 Saint-Denis).

Input: the three files of the Code officiel géographique read by
``scripts.build_anciennes_communes`` (same events, same reader).

Kept: every code that was a commune at some date since 2015-01-01 and is no
longer one today, when the events lead it to exactly one commune of today in the
same departement. Left out and reported: a split (several communes of today), a
code that reaches none, a change of departement. Those keep their former code at
the import, as before.

Usage (from ``api/backend/``):
    uv run python -m scripts.build_codes_communes_actuels <directory of the 3 files>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

from app.etl_dvf.transform import CODES_COMMUNES_ACTUELS_PATH
from scripts.build_anciennes_communes import (
    _COMMUNES,
    _EVENEMENTS,
    _HISTORIQUE,
    DEPUIS,
    _Evenements,
    _lire,
)

SOURCE = "https://www.insee.fr/fr/statistiques/fichier/8740222/"
MILLESIME = "2026"


def _departement(code: str) -> str:
    return code[:3] if code.startswith("97") else code[:2]


def codes_actuels(
    dossier: Path, depuis: str = DEPUIS
) -> tuple[list[tuple[str, str]], list[str]]:
    """(former code, code of today) pairs, sorted; and the codes left out."""
    actuelles = {
        row["COM"] for row in _lire(dossier / _COMMUNES) if row["TYPECOM"] == "COM"
    }
    evenements = _Evenements(_lire(dossier / _EVENEMENTS), actuelles)

    cibles: dict[str, set[str]] = {}
    for row in _lire(dossier / _HISTORIQUE):
        code = row["COM"]
        if row["TYPECOM"] != "COM" or not row["DATE_FIN"] or code in actuelles:
            continue
        if row["DATE_FIN"] < depuis:
            continue
        cibles.setdefault(code, set()).update(
            evenements.aujourdhui(code, row["DATE_FIN"])
        )

    paires: list[tuple[str, str]] = []
    ecartes: list[str] = []
    for code, codes in sorted(cibles.items()):
        if len(codes) != 1:
            ecartes.append(f"{code}: {len(codes)} communes of today {sorted(codes)}")
            continue
        (actuel,) = codes
        if _departement(actuel) != _departement(code):
            ecartes.append(f"{code}: departement changed to {actuel}")
            continue
        paires.append((code, actuel))
    return paires, ecartes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dossier", type=Path, help="directory of the 3 COG files")
    args = parser.parse_args()
    paires, ecartes = codes_actuels(args.dossier)
    empreintes = ", ".join(
        f"{nom} sha256 {hashlib.sha256((args.dossier / nom).read_bytes()).hexdigest()}"
        for nom in (_COMMUNES, _HISTORIQUE, _EVENEMENTS)
    )
    with CODES_COMMUNES_ACTUELS_PATH.open("w", encoding="utf-8", newline="") as f:
        f.write(
            f"# Source : INSEE, Code officiel géographique {MILLESIME} ({SOURCE}).\n"
            f"# Fichiers lus : {empreintes}.\n"
            "# Écrit par scripts/build_codes_communes_actuels.py : codes disparus "
            f"depuis le {DEPUIS}, une seule commune d'aujourd'hui, même département.\n"
        )
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(("ancien_code", "code_actuel"))
        writer.writerows(paires)
    print(f"{len(paires)} rows -> {CODES_COMMUNES_ACTUELS_PATH}")
    for ligne in ecartes:
        print(f"left out: {ligne}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
