"""Refuse, in a tree about to be published, the real data the test fixtures were captured from.

    python scripts/check-real-data.py <list.json> <tree>

The fixtures of the address pages were captured from real addresses, then anonymised (05-07,
lot 3). The list of the real values is private (it would publish them): the private repository
keeps it out of the public tree and passes it here. It holds
  - "chaines": strings refused anywhere, case-insensitive (address keys, labels, parcel and
    document identifiers, coordinates);
  - "ventes": sales {date, prix} refused when the date (ISO, dd/mm/yyyy, or month and year as the
    site renders it) and the price (raw, or grouped by thousands as the site renders it) appear
    within FENETRE characters of each other (one sale object, one table row, one rendered card).
Files under .git and files that are not UTF-8 text are skipped.

Output: one `REAL_DATA_FOUND <path> <chaine|vente>#<index>` line per finding (the value itself
is never printed), then `REAL_DATA_CHECK files=<n> chaines=<k> ventes=<m> found=<f>`.
Exit 0 clean, 1 findings, 2 unusable input (missing or empty list: `REAL_DATA_BLIND`, a check
that compares against nothing must not pass).
"""

import datetime as dt
import json
import re
import sys
from pathlib import Path

SEPARATEURS = "[ \u00a0\u202f]"
FENETRE = 500
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre"]


def formes_prix(prix: int) -> re.Pattern:
    groupes = f"{prix:,}".split(",")
    variantes = [str(prix)] + ([SEPARATEURS.join(groupes)] if len(groupes) > 1 else [])
    return re.compile(r"(?<![\d.,])(?:" + "|".join(variantes) + r")(?![\d])")


def formes_date(iso: str) -> list[str]:
    d = dt.date.fromisoformat(iso)
    return [iso, d.strftime("%d/%m/%Y"), f"{MOIS[d.month - 1]} {d.year}"]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    liste, arbre = Path(argv[1]), Path(argv[2])
    if not arbre.is_dir():
        print(f"REAL_DATA_BLIND: tree not found: {arbre}", file=sys.stderr)
        return 2
    try:
        donnees = json.loads(liste.read_text(encoding="utf-8"))
        chaines = [c.lower() for c in donnees.get("chaines", [])]
        ventes = [(formes_date(v["date"]), formes_prix(int(v["prix"]))) for v in donnees.get("ventes", [])]
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"REAL_DATA_BLIND: unreadable list {liste}: {type(e).__name__}", file=sys.stderr)
        return 2
    if not chaines or not ventes:
        print("REAL_DATA_BLIND: the list has no string or no sale; refusing a check against nothing",
              file=sys.stderr)
        return 2

    lus = trouves = 0
    for f in sorted(arbre.rglob("*")):
        rel = f.relative_to(arbre)
        if ".git" in rel.parts or not f.is_file():
            continue
        try:
            texte = f.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        if "\x00" in texte:
            continue
        lus += 1
        bas = texte.lower()
        for i, c in enumerate(chaines):
            if c in bas:
                print(f"REAL_DATA_FOUND {rel.as_posix()} chaine#{i}")
                trouves += 1
        for i, (dates, prix) in enumerate(ventes):
            if any(any(d in bas[max(0, m.start() - FENETRE):m.end() + FENETRE] for d in dates)
                   for m in prix.finditer(texte)):
                print(f"REAL_DATA_FOUND {rel.as_posix()} vente#{i}")
                trouves += 1
    print(f"REAL_DATA_CHECK files={lus} chaines={len(chaines)} ventes={len(ventes)} found={trouves}")
    return 1 if trouves else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
