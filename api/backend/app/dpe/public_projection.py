"""Public projection of the address declared in a DPE document (P-ADR-01).

Decision of Ianis (2026-10-02): the raw text typed by the diagnostician is
kept internally; the public field « adresse déclarée » is published only
when the text is established without complement, otherwise it is masked as
a whole with an explicit reason, the BAN address being shown apart. No
verified portion is ever published, the text is never corrected nor
replaced by the BAN label.

« Established without complement » (cautious: any doubt masks):

1. every stored complement column of the document is empty, and the full
   raw address carries none of the complement words of the measurement
   script (a word list may only add a refusal, never accept);
2. :func:`app.dpe.concordance.extraire_adresse_brute` decomposes the text
   entirely (number, closed-list suffix, street, optional postcode, optional
   commune name);
3. the decomposed street equals the BAN street of the address;
4. a commune name, when present, equals the BAN one (an unequal name stays
   in the street and fails 3);
5. a postcode, when present, equals the BAN postcode of the address (an
   unequal five-digit token may be an entry code: decision n° 25 of Ianis,
   2026-10-05, API WR-02).

The single projection of the page address, the document page, the
comparators, the CSV, the print and the server-rendered HTML (04-04, 04-06,
04-10, 04-11 consume it without copying it). ``adresse_complete_brut`` and
the complement columns are never published.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.adresse.schemas import AdresseDeclaree, MotifAdresseDeclaree

# Re-exported: the stored complement columns, read by the facts query too.
from app.dpe.concordance import COLONNES_COMPLEMENT as COLONNES_COMPLEMENT
from app.dpe.concordance import (
    extraire_adresse_brute,
    normaliser_libelle,
)

MOTIFS_ADRESSE_DECLAREE: tuple[MotifAdresseDeclaree, ...] = (
    "complement_present",
    "non_verifiable_sans_complement",
)

# Complement words of ``scripts/mesure_dpe_documents.COMPLEMENT_RE`` plus the
# civilities; searched in the normalised full raw address only to refuse.
_MOTS_COMPLEMENT = re.compile(
    r"(^| )(etage|etg|porte|bat|batiment|escalier|esc|appartement|appart|appt|apt"
    r"|chez|mme|mlle|monsieur|madame)( |$)"
)


@dataclass(frozen=True)
class AdresseBruteDocument:
    """Private address columns of one document; never serialized."""

    adresse_brut: str | None
    adresse_complete_brut: str | None = None
    complements: Mapping[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class IdentiteBan:
    """BAN street, commune and postcode of the address the document is declared at.

    ``nom_voie`` is None when the BAN rows of the key disagree: nothing can
    then be verified, the declared address is masked. ``code_postal`` is
    None when unknown or disagreeing: a typed postcode then masks the text.
    """

    nom_voie: str | None
    nom_commune: str | None = None
    code_postal: str | None = None


def _vide(texte: str | None) -> bool:
    return texte is None or not texte.strip()


def _hors_voie(texte: str | None, identite: IdentiteBan) -> str:
    """The normalised full raw address without the BAN street name.

    « 12 rue de la Porte » holds a complement word inside its street name:
    the word is searched outside the street only (API IN-03), any other
    occurrence still refuses.
    """
    complete = f" {normaliser_libelle(texte)} "
    voie = normaliser_libelle(identite.nom_voie)
    if voie:
        complete = complete.replace(f" {voie} ", " ")
    return complete.strip()


def _masquee(motif: MotifAdresseDeclaree) -> AdresseDeclaree:
    return AdresseDeclaree(etat="masquee", texte=None, motif=motif)


def projeter_adresse_declaree(
    document: AdresseBruteDocument, identite: IdentiteBan
) -> AdresseDeclaree:
    """Published as typed, masked with its reason, or absent (P-ADR-01)."""
    if _vide(document.adresse_brut):
        return AdresseDeclaree(etat="absente", texte=None, motif=None)
    if any(not _vide(valeur) for valeur in document.complements.values()):
        return _masquee("complement_present")
    if _MOTS_COMPLEMENT.search(_hors_voie(document.adresse_complete_brut, identite)):
        return _masquee("complement_present")
    decomposee = extraire_adresse_brute(
        document.adresse_brut, commune=identite.nom_commune
    )
    voie_ban = normaliser_libelle(identite.nom_voie)
    if decomposee is None or not voie_ban or decomposee.voie != voie_ban:
        return _masquee("non_verifiable_sans_complement")
    if decomposee.code_postal is not None and decomposee.code_postal != (
        (identite.code_postal or "").strip()
    ):
        return _masquee("non_verifiable_sans_complement")
    return AdresseDeclaree(etat="publiee", texte=document.adresse_brut, motif=None)
