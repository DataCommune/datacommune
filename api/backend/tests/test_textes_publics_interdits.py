"""Forbidden DPE wordings never reach a text served by the phase 4 routes (04-08).

The public pages render what the API serves: a sentence written in one of these
modules reaches the address page, the document page, the export or a comparator
as is. This module reads every string literal of those modules (``ast``,
docstrings excluded: they are never served) and refuses:

* the forbidden wordings of the DPE display policy (04-UI-SPEC « Politique
  d'affichage DPE »: a DPE is a document declared at an address, never the DPE
  of a dwelling or of a property);
* the forbidden conclusions of 04-DPE-EVALUATION.md, list confirmed by Ianis on
  2026-10-06 (C11 of 04-DECISIONS-COLONNES-DPE.md): dwelling identity proven,
  reliability or precision of a dwelling link, a coverage or concordance rate
  presented as a precision, an automatic choice of a document for a dwelling,
  accuracy drawn from the import fidelity, generalisation beyond 44/85.

The state-dependent prohibition of that list (« aucun DPE » for a partial, failed
or out of coverage inventory) is not a literal: it is guarded on the rendered
pages by ``frontend/tests/e2e/honnetete-publique.spec.ts``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_APP = Path(__file__).resolve().parents[1] / "app"

# Modules whose literals are served to the public pages of phase 4 (address,
# document, export, comparators).
MODULES_SERVIS: tuple[Path, ...] = (
    *sorted((_APP / "adresse").glob("*.py")),
    _APP / "dpe" / "document_public.py",
    _APP / "dpe" / "comparaison_public.py",
    _APP / "dpe" / "comparison_projection.py",
)

_I = re.IGNORECASE

# Typographic apostrophe and no-break spaces, read as their plain forms.
_APOSTROPHE = chr(0x2019)
_ESPACES_INSECABLES = (chr(0x00A0), chr(0x202F))

# 04-UI-SPEC « Politique d'affichage DPE », « Formulations interdites ».
FORMULATIONS_POLITIQUE_DPE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("le DPE de l'adresse", re.compile(r"\ble DPE de l'adresse\b", _I)),
    ("le DPE du logement", re.compile(r"\bDPE du logement\b", _I)),
    ("DPE du bien", re.compile(r"\bDPE du bien\b", _I)),
    ("votre DPE", re.compile(r"\bvotre DPE\b", _I)),
    (
        "le logement sujet d'une valeur DPE",
        re.compile(r"\ble logement (consomme|émet|est classé|a une étiquette)\b", _I),
    ),
    ("attribué à ce logement", re.compile(r"\battribué à ce logement\b", _I)),
    ("Logement recherché", re.compile(r"\bLogement recherché\b", _I)),
    ("Document 01 (rang pris pour un identifiant)", re.compile(r"\bDocument 01\b", _I)),
)

# 04-DPE-EVALUATION.md « Conclusions permises et interdites », « Interdit » (C11).
CONCLUSIONS_INTERDITES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "identité du logement prouvée, vérifiée ou retrouvée",
        re.compile(
            # A negation states the limit: « pas un logement identifié » is allowed.
            r"(?<!pas un )(?<!aucun )\blogement identifié\b|\bidentité vérifiée\b"
            r"|\bDPE vérifié pour ce logement\b|\bcorrespond à votre logement\b"
            r"|\ble bon DPE\b|\bDPE de ce logement\b",
            _I,
        ),
    ),
    (
        "fiabilité, précision ou confiance du rattachement logement",
        re.compile(
            r"\bfiabilité du rattachement\b|\bprécision du rattachement\b"
            r"|\btaux de fiabilité\b|\bindice de confiance\b",
            _I,
        ),
    ),
    (
        "taux de couverture ou de concordance présenté comme une précision",
        re.compile(
            r"\d+(?:[.,]\d+)?\s?%[^.\n]{0,60}"
            r"\b(précision|exactitude|fiabilité|fiable|certitude)\b"
            r"|\b(précision|exactitude|fiabilité|certitude)\b[^.\n]{0,60}"
            r"\d+(?:[.,]\d+)?\s?%"
            r"|\b176\s?/\s?200\b",
            _I,
        ),
    ),
    (
        "choix automatique d'un document pour un logement",
        re.compile(
            r"\bDPE attribué\b|\bDPE retenu pour ce logement\b"
            r"|\bDPE le plus probable\b",
            _I,
        ),
    ),
    (
        "exactitude générale tirée de la fidélité de l'import",
        re.compile(r"\bdonnées vérifiées\b|\bDPE exact\b|\bsurface exacte\b", _I),
    ),
    (
        "généralisation hors 44/85 ou à partir de petits corpus",
        re.compile(r"\bfiable en France\b|\b0 erreur constatée\b", _I),
    ),
)

INTERDITS = FORMULATIONS_POLITIQUE_DPE + CONCLUSIONS_INTERDITES


def _normaliser(texte: str) -> str:
    """Typographic apostrophes and no-break spaces read as their plain forms."""
    texte = texte.replace(_APOSTROPHE, "'")
    for espace in _ESPACES_INSECABLES:
        texte = texte.replace(espace, " ")
    return texte


_PORTEURS_DE_DOCSTRING = (
    ast.Module,
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
)


def _docstrings(arbre: ast.AST) -> set[int]:
    """Ids of the docstring nodes of *arbre*: documentation, never served."""
    ids: set[int] = set()
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, _PORTEURS_DE_DOCSTRING) or not noeud.body:
            continue
        premier = noeud.body[0]
        if (
            isinstance(premier, ast.Expr)
            and isinstance(premier.value, ast.Constant)
            and isinstance(premier.value.value, str)
        ):
            ids.add(id(premier.value))
    return ids


def litteraux_servis(source: str) -> list[tuple[int, str]]:
    """``(line, text)`` of every string literal of *source* that is not a docstring.

    An f-string is read once, through its literal parts: a forbidden wording
    written around a placeholder is still written.
    """
    arbre = ast.parse(source)
    exclus = _docstrings(arbre)
    exclus |= {
        id(partie)
        for noeud in ast.walk(arbre)
        if isinstance(noeud, ast.JoinedStr)
        for partie in noeud.values
    }
    textes: list[tuple[int, str]] = []
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.JoinedStr):
            parties = [
                p.value if isinstance(p, ast.Constant) else "{}" for p in noeud.values
            ]
            textes.append((noeud.lineno, "".join(str(p) for p in parties)))
        elif (
            isinstance(noeud, ast.Constant)
            and isinstance(noeud.value, str)
            and id(noeud) not in exclus
        ):
            textes.append((noeud.lineno, noeud.value))
    return textes


def violations(source: str) -> list[str]:
    """Each forbidden wording found in a served literal of *source*, with its line."""
    trouvees: list[str] = []
    for ligne, texte in litteraux_servis(source):
        lu = _normaliser(texte)
        for nom, motif in INTERDITS:
            if motif.search(lu):
                trouvees.append(f"l.{ligne}: {nom} ({texte!r})")
    return trouvees


def test_les_modules_nommes_existent():
    """A renamed module would turn the guard into a silent pass."""
    manquants = [str(p) for p in MODULES_SERVIS if not p.is_file()]
    assert manquants == []
    assert len(MODULES_SERVIS) >= 5


@pytest.mark.parametrize(
    "module", MODULES_SERVIS, ids=lambda p: p.relative_to(_APP).as_posix()
)
def test_aucune_formulation_interdite_dans_un_texte_servi(module: Path):
    assert violations(module.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "texte",
    [
        "Voici le DPE du logement.",
        f"Le DPE de l{_APOSTROPHE}adresse est classé C.",
        "le logement consomme 120 kWh",
        "Logement recherché",
        "Document 01",
        "Identité vérifiée par DataCommune",
        "Le logement identifié est au 2e étage.",
        "Indice de confiance : élevé",
        "17,1 % de précision",
        "Fiabilité : 88 %",
        "176/200 documents retrouvés",
        "DPE le plus probable",
        "Données vérifiées",
        "Fiable en France",
    ],
)
def test_la_garde_refuse_chaque_formulation_injectee(texte: str):
    """Counter-proof: each forbidden wording written in a served literal is refused."""
    assert violations(f"LIBELLE = {texte!r}\n") != []


def test_la_garde_lit_les_f_strings_et_ignore_les_docstrings():
    source = '''
"""Module docstring: never served, may quote « le DPE du logement » to forbid it."""

def libelle(n):
    """Docstring: « DPE attribué » is forbidden."""
    return f"{n} documents · DPE du logement"
'''
    trouvees = violations(source)
    assert len(trouvees) == 1
    assert "DPE du logement" in trouvees[0]


def test_les_formulations_permises_passent():
    """The permitted conclusions of 04-DPE-EVALUATION.md are not refused."""
    permises = [
        "3 DPE déclarés à cette adresse",
        "Documents DPE déclarés à cette adresse",
        "Une adresse, un bâtiment, un DPE : les éléments concordent",
        "Ce constat ne prouve pas l'identité d'un logement.",
        "Ce document n'est rattaché à aucun logement précis.",
        "Logement décrit par le document",
        "Ce que le diagnostiqueur a déclaré, pas un logement identifié.",
        "Taux de vacance 9,4 %",
    ]
    for texte in permises:
        assert violations(f"LIBELLE = {texte!r}\n") == [], texte
