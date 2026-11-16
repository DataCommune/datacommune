"""« Débroussaillement » : legal clearing obligation (OLD) on the parcel (ERRIAL).

Granularity: parcel. ERRIAL says, for the parcel it finds at the BAN point,
whether it lies in a zone of legal clearing obligation. A conclusion is
served only when ERRIAL's parcel is the parcel of the address; the forest
fire status of the Géorisques risk report is in the details.

Tone rule: vigilance when the parcel carries the obligation; not concerned
(neutral) when it does not; no conclusion without the parcel of the address.
"""

import asyncio

from app.rapport.partage import (
    AmontDebroussaillement,
    RapportRisques,
    errial,
    errial_lu,
    rapport_risques,
)
from app.rapport.schemas import Rattachement, Theme
from app.rapport.socle import (
    ContexteAmont,
    ContexteRapport,
    DefinitionTheme,
    granularite,
    sans_reponse,
    source,
    theme,
)

ENTITE = "parcelle_errial"


async def lire(contexte: ContexteRapport) -> Theme:
    amont = contexte.amonts.get(THEME.id)
    if not isinstance(amont, AmontDebroussaillement):
        amont = AmontDebroussaillement(errial=amont, rapport=None)
    lu = errial_lu(contexte, THEME, amont.errial)
    if isinstance(lu, Theme):
        return lu
    sources = [source("errial", contexte.as_of.isoformat())]
    feu = (
        amont.rapport.statuts.get("feuForet")
        if isinstance(amont.rapport, RapportRisques)
        else None
    )
    details = {
        "feu_de_foret_commune": feu.commune if feu is not None else None,
        "feu_de_foret_present": feu.present if feu is not None else None,
    }
    if lu.concordance is not True:
        motif = (
            "parcelle_errial_differente"
            if lu.concordance is False
            else "parcelle_non_verifiee"
        )
        return sans_reponse(
            THEME,
            "manque",
            motif,
            rattachement=Rattachement(
                entite=ENTITE, cle=lu.parcelle, confiance="incertaine", motif=motif
            ),
            details=details,
            sources=sources,
        )
    rattachement = Rattachement(entite=ENTITE, cle=lu.parcelle, confiance="sure")
    obligation = lu.resume.get("obligation_debroussaillement")
    if obligation is None:
        # ERRIAL serves None for a value it could not read.
        return sans_reponse(
            THEME,
            "panne",
            "amont_indisponible",
            rattachement=rattachement,
            sources=sources,
        )
    if obligation:
        return theme(
            THEME,
            etat="ok",
            ton="vigilance",
            verdict=(
                "La parcelle est en zone d'obligation légale de débroussaillement."
            ),
            details=details,
            rattachement=rattachement,
            sources=sources,
        )
    return theme(
        THEME,
        etat="non_concerne",
        ton="neutre",
        verdict="La parcelle est hors zone d'obligation légale de débroussaillement.",
        details=details,
        rattachement=rattachement,
        sources=sources,
    )


async def amont(contexte: ContexteAmont) -> AmontDebroussaillement:
    avis, rapport = await asyncio.gather(errial(contexte), rapport_risques(contexte))
    return AmontDebroussaillement(errial=avis, rapport=rapport)


THEME = DefinitionTheme(
    id="debroussaillement",
    section="risques",
    ordre=54,
    titre="Débroussaillement",
    granularite=granularite("parcelle"),
    sources=("errial", "georisques"),
    lire=lire,
    amont=amont,
    rattachement_entite=ENTITE,
)
