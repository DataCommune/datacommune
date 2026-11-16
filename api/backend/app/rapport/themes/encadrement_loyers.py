"""« Encadrement des loyers » : whether a prefectoral order caps the rents.

Granularity: commune; an arrondissement is read on its commune. Read live
from the list of the communes under rent control (loi ELAN, art. 140),
gathered by Naistor from the decrees and the prefectoral orders, each row
with its source. The order of the day is the one whose dates hold the date
of the report: an order past its end is said ended, never extended; an
order to come is said to come, the order before it being unknown (the list
keeps the latest one only), never said absent. An order without end date
is said so, and is no longer read as in force after the end of the
experiment of article 140. When the decree covers part of the commune
only, the address is not placed: the verdict says so. The reference rents
of the address stay out of coverage (not imported).

Tone rule: neutre (a rule of the market, no valence). A commune absent from
the list does not concern the address, said as an absence from the list
with its date; an unreadable list is an outage.
"""

from datetime import date

from app.rapport.partage import (
    EncadrementLu,
    PeriodeEncadrement,
    encadrement_au_point,
)
from app.rapport.schemas import VERDICT_MAX, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    date_longue,
    granularite,
    sans_reponse,
    sources_de,
    theme,
)

LOYERS_REFERENCE = {"etat": "hors_couverture", "motif": "loyers_reference_non_importes"}
HORS_LISTE = "La commune n'est pas dans la liste des communes à loyers encadrés."
# End of the experiment of article 140 of the loi ELAN (eight years, loi 3DS
# of 21 February 2022), as the prefectoral orders of 2026 state it (Paris,
# IDF-2026-06-12-00003): an order without end date is not read beyond it.
FIN_EXPERIMENTATION = date(2026, 11, 24)


def _statut(periode: PeriodeEncadrement, jour: date) -> str | None:
    """« en_vigueur », « a_venir » or « echu »; None without start date."""
    if periode.du is None:
        return None
    if periode.du > jour:
        return "a_venir"
    fin = periode.au or FIN_EXPERIMENTATION
    return "en_vigueur" if jour <= fin else "echu"


def _retenue(periodes: tuple[PeriodeEncadrement, ...], jour: date):
    """The order in force on *jour*, else the latest one by start."""
    en_cours = [p for p in periodes if _statut(p, jour) == "en_vigueur"]
    return max(en_cours or periodes, key=lambda p: p.du or date.min)


def _verdict(periode: PeriodeEncadrement, statut: str) -> str:
    """The first wording within the length of a verdict."""
    partie = periode.zone == "partie"
    ou = " dans une partie de la commune" if partie else ""
    reserve = " : l'adresse n'est pas située par rapport au périmètre" if partie else ""
    if statut == "en_vigueur" and periode.au is not None:
        candidats = [
            f"Loyers encadrés{ou} jusqu'au {date_longue(periode.au)}{reserve}.",
            f"Loyers encadrés{ou} jusqu'au {date_longue(periode.au)}.",
        ]
    elif statut == "en_vigueur":
        depuis = date_longue(periode.du or FIN_EXPERIMENTATION)
        candidats = [
            f"Loyers encadrés{ou} depuis le {depuis}, sans date de fin publiée"
            f"{reserve}.",
            f"Loyers encadrés{ou} depuis le {depuis}, sans date de fin publiée.",
        ]
    elif statut == "a_venir":
        debut = date_longue(periode.du or FIN_EXPERIMENTATION)
        candidats = [
            f"Arrêté d'encadrement des loyers{ou} en vigueur à partir du {debut} "
            "(l'arrêté précédent n'est pas dans la liste).",
            f"Arrêté d'encadrement des loyers{ou} à partir du {debut} ; arrêté "
            "précédent non listé.",
        ]
    else:
        fin = f" le {date_longue(periode.au)}" if periode.au else ""
        candidats = [
            f"Arrêté d'encadrement des loyers échu{fin} ; aucun arrêté plus récent "
            "n'est connu."
        ]
    return next(c for c in candidats if len(c) <= VERDICT_MAX)


def _details(lu: EncadrementLu, periode: PeriodeEncadrement, statut) -> dict:
    return {
        "commune": lu.commune,
        "statut": statut,
        "perimetre": periode.zone,
        "territoire": periode.territoire,
        "decret": periode.decret,
        "arrete": {
            "numero": periode.arrete,
            "date": periode.arrete_date.isoformat() if periode.arrete_date else None,
            "source": periode.source,
            "url": periode.source_url,
        },
        "en_vigueur_du": periode.du.isoformat() if periode.du else None,
        "en_vigueur_au": periode.au.isoformat() if periode.au else None,
        "arrete_precedent": "inconnu" if statut == "a_venir" else None,
        "liste_verifiee_le": (
            periode.verifie_le.isoformat() if periode.verifie_le else None
        ),
        "loyers_reference": dict(LOYERS_REFERENCE),
    }


async def lire(contexte: ContexteRapport) -> Theme:
    hebergement = contexte.hosting.hostings["encadrement_communes"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources_de(THEME)
        )
    lu = contexte.amonts.get(THEME.id)
    if lu is None:
        return sans_reponse(
            THEME, "manque", "commune_indeterminee", sources=sources_de(THEME)
        )
    if not isinstance(lu, EncadrementLu):
        return sans_reponse(
            THEME, "panne", "lecture_indisponible", sources=sources_de(THEME)
        )
    liste = lu.liste_verifiee_le.isoformat() if lu.liste_verifiee_le else None
    sources = sources_de(THEME, {"encadrement_communes": liste})
    if not lu.periodes:
        return theme(
            THEME,
            etat="non_concerne",
            ton="neutre",
            verdict=HORS_LISTE,
            details={"commune": lu.commune, "liste_verifiee_le": liste},
            sources=sources,
        )
    jour = contexte.as_of
    periode = _retenue(lu.periodes, jour)
    statut = _statut(periode, jour)
    details = _details(lu, periode, statut)
    if statut is None:
        return sans_reponse(
            THEME, "manque", "dates_non_publiees", details=details, sources=sources
        )
    if statut == "echu" and periode.au is None:
        return sans_reponse(
            THEME, "manque", "fin_non_publiee", details=details, sources=sources
        )
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=_verdict(periode, statut),
        details=details,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="encadrement_loyers",
    section="marche",
    ordre=55,
    titre="Encadrement des loyers",
    granularite=granularite("commune"),
    sources=("encadrement_communes",),
    lire=lire,
    amont=encadrement_au_point,
)
