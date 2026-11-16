"""The reasons worded by the API and by the front say the same thing (04.7-19).

The API serves the words (``app.rapport.libelles.MOTIFS``), but the front
still keeps its own table (``frontend/src/lib/data/adresse-rapport.ts``) and
falls back to the code made readable for an unknown one: a reason added on
one side only would read « Import absent » on the page. The front table is
read by a regular expression on its ``cle: 'texte'`` lines; a code present on
both sides must carry the same text, and ``import_absent`` must be on both.
Codes present on one side only are tolerated here (listed in 04.7-19-SUMMARY).
"""

import re
from pathlib import Path

import pytest

from app.rapport.libelles import MOTIFS

pytestmark = pytest.mark.unit

FRONT = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "lib"
    / "data"
    / "adresse-rapport.ts"
)
_TABLE = re.compile(
    r"^const MOTIFS: Record<string, string> = \{\n(.*?)^\};", re.M | re.S
)
_LIGNE = re.compile(r"^\t([a-z0-9_]+): '([^'\n]*)',?$")


def motifs_du_front() -> dict[str, str]:
    table = _TABLE.search(FRONT.read_text(encoding="utf-8"))
    assert table is not None, "table MOTIFS du front introuvable"
    lignes = [ligne for ligne in table.group(1).splitlines() if ligne.strip()]
    lues = {m.group(1): m.group(2) for m in map(_LIGNE.fullmatch, lignes) if m}
    # Every line of the table is a ``cle: 'texte'`` pair: nothing escapes the
    # reading (a line the expression cannot read would hide a code).
    illisibles = [ligne for ligne in lignes if not _LIGNE.fullmatch(ligne)]
    assert len(lues) == len(lignes), illisibles
    return lues


def test_a_reason_worded_on_both_sides_says_the_same_thing():
    front = motifs_du_front()
    communs = sorted(set(front) & set(MOTIFS))
    assert len(communs) >= 40
    assert {code: front[code] for code in communs} == {
        code: MOTIFS[code] for code in communs
    }


def test_import_absent_is_worded_on_both_sides():
    texte = "Données pas encore importées pour ce département"
    assert MOTIFS["import_absent"] == texte
    assert motifs_du_front().get("import_absent") == texte
