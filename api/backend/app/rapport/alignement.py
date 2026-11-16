"""Fields of the UI-SPEC § 7 the front computed until 04.7-17, now served.

Each field is read from the theme it describes, never from a second read,
with the rules of the front (``adresse-rapport.ts``: ``accrocheDe``,
``alerteDe``, ``statutDe``, ``tonAffiche``, ``phraseRattachement``), so that
the front reads them instead of computing them. Nothing is invented: a
served theme without figure has no headline.

``maillons`` holds one link (the attachment itself) until a theme declares
its chain: the confidence stays the weakest link (UI-SPEC § 7, gap written
in 04.7-17-PLAN).
"""

import re
from collections.abc import Sequence

from app.rapport.libelles import (
    LIBELLE_STATUT,
    LIBELLES_MOTIFS_COURTS,
    NBSP,
    SINGULIERS,
    libelle_code,
    libelle_court_motif,
    nombre_fr,
    valeur_chiffre,
)
from app.rapport.schemas import (
    ETATS_SANS_REPONSE,
    ETATS_SERVIS,
    Maillon,
    Rattachement,
    Theme,
)

ACCROCHE_MAX = 40
# A zero whose headline « Aucun » would say more than its source: outside
# any noise exposure plan is not « no aircraft noise ».
ACCROCHES_ZERO = {"peb": "Hors plan"}
MOT_CONFIANCE = {"sure": "sûr", "probable": "probable", "incertaine": "incertain"}
# Scales whose value describes an entity found from the address: its link
# must be established before a favourable reading.
ECHELLES_RATTACHEES = frozenset({"batiment", "parcelle"})
# Whose link a bare alert speaks of in the head, beside the parcel's sentence.
SUJETS_ALERTE = {
    "dpe": " des DPE à l\u2019adresse",
    "batiment": " du bâtiment à l\u2019adresse",
}
_CLASSE = re.compile(r"[A-G]")
_PARCELLE = re.compile(r"[0-9AB]{5}[0-9]{3}([0-9A-Z]{2})([0-9]{4})")


def _lien_etabli(theme: Theme) -> bool:
    if theme.rattachement is not None:
        return theme.rattachement.confiance == "sure"
    return theme.granularite.niveau not in ECHELLES_RATTACHEES


def ton_affiche(theme: Theme) -> str:
    """The served tone, except that a favourable reading needs an
    established link and no partial outage; a vigilance is kept whatever the
    link (the verdict may be about that very uncertainty)."""
    if theme.etat not in ETATS_SERVIS:
        return theme.ton
    if theme.ton == "rassurant" and (
        theme.pannes_partielles or not _lien_etabli(theme)
    ):
        return "neutre"
    return theme.ton


def statut_texte(theme: Theme) -> str | None:
    """The word of the status badge; None for a served neutral theme."""
    if theme.etat == "manque":
        if theme.motif:
            return libelle_court_motif(theme.motif)
        return LIBELLE_STATUT["manque"]
    if theme.etat in ("panne", "hors_couverture", "non_concerne", "en_cours"):
        return LIBELLE_STATUT[theme.etat]
    ton = ton_affiche(theme)
    if ton in ("vigilance", "rassurant"):
        return LIBELLE_STATUT[ton]
    return LIBELLE_STATUT["zero"] if theme.etat == "zero" else None


def _minuscule(texte: str) -> str:
    if re.match(r"[A-Z0-9]{2}", texte):
        return texte
    return texte[:1].lower() + texte[1:]


def accroche(theme: Theme) -> str | None:
    """Headline of a row: the first figure written for a reader (« 11 DPE
    déclarés », « Classe F »), the word of a state without answer, « Aucun »
    for a zero without figure; None beyond 40 characters, never cut."""
    if theme.etat in ETATS_SANS_REPONSE or theme.etat == "non_concerne":
        texte = statut_texte(theme)
    elif not theme.chiffres:
        texte = ACCROCHES_ZERO.get(theme.id, "Aucun") if theme.etat == "zero" else None
    else:
        c = theme.chiffres[0]
        if c.unite == "count" and isinstance(c.valeur, int | float):
            mot = SINGULIERS.get(c.libelle, c.libelle) if c.valeur < 2 else c.libelle
            texte = f"{nombre_fr(c.valeur, 0)} {_minuscule(mot)}"
        elif c.unite == "classe_dpe":
            mot = "Classe" if _CLASSE.fullmatch(str(c.valeur)) else "Classes"
            texte = f"{mot} {c.valeur}"
        else:
            texte = valeur_chiffre(c)
    return texte if texte is not None and len(texte) <= ACCROCHE_MAX else None


def etat_libelle(theme: Theme) -> str | None:
    """The reason of a gap or of an out of coverage, worded."""
    if theme.etat in ("manque", "hors_couverture") and theme.motif:
        return LIBELLES_MOTIFS_COURTS.get(theme.motif) or libelle_code(theme.motif)
    return None


def alerte(rattachement: Rattachement | None, sujet: str = "") -> str | None:
    """Alert line of an attachment that is not sure; None when sure. *sujet*
    names what a bare alert speaks of, where its row does not say it."""
    if rattachement is None or rattachement.confiance == "sure":
        return None
    if rattachement.motif:
        return f"{libelle_code(rattachement.motif)}."
    mot = MOT_CONFIANCE[rattachement.confiance]
    if rattachement.candidats and rattachement.candidats > 1:
        return f"{rattachement.candidats} rattachements possibles{NBSP}: lien {mot}."
    return f"Rattachement {mot}{sujet}."


def alerte_du_theme(theme: Theme, sujet: str = "") -> str | None:
    """The alert of a theme: a zero without reason attached nothing, so it
    has no link to doubt (« Aucun DPE déclaré », not « Rattachement
    probable »); its served confidence stays in the detail."""
    lien = theme.rattachement
    if theme.etat == "zero" and lien is not None and not lien.motif:
        return None
    return alerte(lien, sujet)


def aligner(theme: Theme) -> Theme:
    """*theme* with the fields of the UI-SPEC § 7 it implies, and the tone
    the page shows: never favourable on a link that is not established
    (UI-SPEC § 5, rule 4 as amended in 04.7-17)."""
    rattachement = theme.rattachement
    if rattachement is not None:
        rattachement = rattachement.model_copy(
            update={
                "alerte": alerte_du_theme(theme),
                "maillons": [
                    Maillon(lien=rattachement.entite, confiance=rattachement.confiance)
                ],
            }
        )
    return theme.model_copy(
        update={
            "ton": ton_affiche(theme),
            "accroche": accroche(theme),
            "etat_libelle": etat_libelle(theme),
            "rattachement": rattachement,
        }
    )


def libelle_parcelle(identifiant: str) -> str:
    """« ZZ 177 » for ``44109000ZZ0177``; the raw id in another form."""
    m = _PARCELLE.fullmatch(identifiant)
    if m is None:
        return identifiant
    return f"{m.group(1).removeprefix('0')} {int(m.group(2))}"


def phrase_rattachement(themes: Sequence[Theme]) -> str | None:
    """Attachment sentence of the head: the parcel of the point, or why
    there is none, then the alert of the DPE or of the building when their
    link is not sure, named because it is not the link of the parcel. None
    when none of them says anything."""
    par_id = {t.id: t for t in themes}
    phrases: list[str] = []
    parcelle = par_id.get("parcelle")
    lien = parcelle.rattachement if parcelle is not None else None
    if (
        parcelle is not None
        and parcelle.etat == "ok"
        and lien
        and lien.confiance == "sure"
        and lien.cle
    ):
        nom = libelle_parcelle(lien.cle)
        phrases.append(f"Le point de l\u2019adresse tombe dans la parcelle {nom}.")
    elif parcelle is not None and (texte := alerte(lien)):
        phrases.append(texte)
    for id_, sujet in SUJETS_ALERTE.items():
        theme = par_id.get(id_)
        if theme is not None and (texte := alerte_du_theme(theme, sujet)):
            phrases.append(texte)
            break
    return " ".join(dict.fromkeys(phrases)) or None
