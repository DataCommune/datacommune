"""« Urbanisme » : zone of the planning document and prescriptions at the point.

Granularity: address (read at the BAN point, API Carto GPU, live). The
« Urbanisme » theme of the address page (:func:`app.adresse.lieu.theme_urbanisme`)
on the shared upstream read. No document published at the point is a gap,
never « no zone ».

Tone rule: vigilance when at least one prescription applies at the point
(protected element, reserved site, wooded area...), else neutre.
"""

from app.adresse.lieu import (
    FIELD_PRESCRIPTIONS,
    FIELD_ZONE_PLU,
    AmontLieu,
    theme_urbanisme,
)
from app.rapport.partage import amont_lieu, champ
from app.rapport.schemas import Chiffre, Theme, Ton
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    en_entier,
    etat_de_valeur,
    granularite,
    pluriel,
    sans_reponse,
    sources_de,
    theme,
)


def ton_urbanisme(prescriptions: int) -> Ton:
    return "vigilance" if prescriptions > 0 else "neutre"


async def lire(contexte: ContexteRapport) -> Theme:
    amont_ = AmontLieu(urbanisme=contexte.amonts.get("urbanisme"))
    lu = theme_urbanisme(contexte.lieu, amont_, contexte.as_of)
    sources = sources_de(THEME, {"urbanisme": contexte.as_of.isoformat()})
    if lu.etat != "value":
        return sans_reponse(THEME, etat_de_valeur(lu.etat), lu.motif, sources=sources)
    zones = champ(lu, FIELD_ZONE_PLU)
    prescriptions = champ(lu, FIELD_PRESCRIPTIONS)
    pannes = (
        ["prescriptions"]
        if prescriptions is not None and prescriptions.etat == "temporary_error"
        else []
    )
    if zones is None or zones.etat != "value":
        etat = etat_de_valeur(zones.etat) if zones is not None else "manque"
        motif = zones.motif if zones is not None else None
        verdict = (
            "Aucun document d'urbanisme publié au point de l'adresse."
            if motif == "aucun_document_publie"
            else None
        )
        return sans_reponse(
            THEME,
            etat,
            motif,
            verdict=verdict,
            sources=sources,
            pannes_partielles=pannes,
        )
    libelles = [element.libelle for element in zones.elements]
    n_prescriptions = (
        en_entier(prescriptions.valeur)
        if prescriptions is not None and prescriptions.etat == "value"
        else None
    )
    details = {
        "zones": libelles,
        "prescriptions": [e.libelle for e in prescriptions.elements]
        if prescriptions is not None
        else [],
        "prescriptions_etat": prescriptions.etat if prescriptions is not None else None,
        "liste_incomplete": lu.liste_incomplete,
    }
    zone = libelles[0] if len(libelles) == 1 else None
    debut = (
        f"Zone {zone[:60]} au point de l'adresse"
        if zone
        else f"{pluriel(len(libelles), 'zone')} au point de l'adresse"
    )
    if n_prescriptions is None:
        verdict = f"{debut} ; prescriptions non lues."
    elif n_prescriptions == 0:
        verdict = f"{debut}, sans prescription."
    else:
        verdict = f"{debut} ; {pluriel(n_prescriptions, 'prescription')} à vérifier."
    chiffres = []
    if n_prescriptions is not None:
        chiffres.append(
            Chiffre(
                id="prescriptions",
                libelle="Prescriptions au point",
                valeur=n_prescriptions,
                unite="count",
                millesime=contexte.as_of.isoformat(),
            )
        )
    return theme(
        THEME,
        etat="ok",
        ton=ton_urbanisme(n_prescriptions or 0),
        verdict=verdict,
        chiffres=chiffres,
        details=details,
        sources=sources,
        pannes_partielles=pannes,
    )


async def amont(contexte) -> object:
    return (await amont_lieu(contexte)).urbanisme


THEME = DefinitionTheme(
    id="urbanisme",
    section="marche",
    ordre=40,
    titre="Urbanisme",
    granularite=granularite("adresse"),
    sources=("urbanisme",),
    lire=lire,
    amont=amont,
)
