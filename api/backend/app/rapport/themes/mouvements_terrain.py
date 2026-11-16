"""« Cavités et mouvements de terrain » : BRGM inventories within 500 m.

Granularity: radius of 500 m around the BAN point. Underground cavities
(BDCavité) and ground movements (BDMvt) as Géorisques serves them by radius,
each with its geodesic distance when the source gives a position. An
inventory is not an exhaustive survey: none recorded is a measured zero of
the inventory, never a geotechnical guarantee.

Tone rule: vigilance when at least one cavity or movement is recorded within
500 m; rassurant when none is and both inventories were read entirely. With
one inventory unread and nothing in the other, the theme is an outage that
says which one: no global conclusion.
"""

import asyncio

from app.rapport.partage import (
    AmontTerrain,
    LignesLues,
    distance_au_point,
    lignes_au_point,
)
from app.rapport.schemas import Chiffre, Theme, Ton
from app.rapport.socle import (
    ContexteAmont,
    ContexteRapport,
    DefinitionTheme,
    granularite,
    pluriel,
    sans_reponse,
    sources_de,
    theme,
)

RAYON_M = 500
DETAILS_MAX = 5


def ton_terrain(total: int, complet: bool) -> Ton:
    if total > 0:
        return "vigilance"
    return "rassurant" if complet else "neutre"


def _elements(contexte: ContexteRapport, lu: LignesLues, *cles: str) -> list[dict]:
    point = contexte.point
    elements = [
        {
            "type": next((ligne.get(c) for c in cles if ligne.get(c)), None),
            "distance_m": distance_au_point(point, ligne) if point else None,
        }
        for ligne in lu.lignes
    ]
    elements.sort(key=lambda e: (e["distance_m"] is None, e["distance_m"] or 0))
    return elements[:DETAILS_MAX]


async def lire(contexte: ContexteRapport) -> Theme:
    sources = sources_de(THEME, {"georisques": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["georisques"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources
        )
    if contexte.point is None:
        return sans_reponse(THEME, "manque", "position_indeterminee", sources=sources)
    amont = contexte.amonts.get(THEME.id)
    if not isinstance(amont, AmontTerrain):
        amont = AmontTerrain(cavites=amont, mouvements=amont)
    lus = {
        cle: valeur
        for cle, valeur in (
            ("cavites", amont.cavites),
            ("mouvements", amont.mouvements),
        )
        if isinstance(valeur, LignesLues)
    }
    pannes = [cle for cle in ("cavites", "mouvements") if cle not in lus]
    if not lus:
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    cavites = len(lus["cavites"].lignes) if "cavites" in lus else 0
    mouvements = len(lus["mouvements"].lignes) if "mouvements" in lus else 0
    total = cavites + mouvements
    complet = not pannes and all(lu.complet for lu in lus.values())
    details = {
        "cavites": _elements(contexte, lus["cavites"], "type", "type_cavite")
        if "cavites" in lus
        else None,
        "mouvements": _elements(contexte, lus["mouvements"], "type")
        if "mouvements" in lus
        else None,
        "liste_incomplete": not complet,
    }
    chiffres = [
        Chiffre(
            id=cle,
            libelle=libelle,
            valeur=nombre_,
            unite="count",
            millesime=contexte.as_of.isoformat(),
        )
        for cle, libelle, nombre_ in (
            ("cavites", "Cavités à 500 m", cavites),
            ("mouvements", "Mouvements de terrain à 500 m", mouvements),
        )
        if cle in lus and nombre_
    ]
    if total == 0 and pannes:
        # One inventory unread: no global conclusion, the other is only said.
        verdict = (
            "Inventaire des cavités indisponible ; aucun mouvement de terrain "
            "recensé à moins de 500 m."
            if "cavites" in pannes
            else "Inventaire des mouvements de terrain indisponible ; aucune "
            "cavité recensée à moins de 500 m."
        )
        return sans_reponse(
            THEME,
            "panne",
            "inventaire_indisponible",
            verdict=verdict,
            details=details,
            sources=sources,
            pannes_partielles=pannes,
        )
    if total == 0 and not complet:
        return sans_reponse(
            THEME, "manque", "lecture_incomplete", details=details, sources=sources
        )
    if total == 0:
        return theme(
            THEME,
            etat="zero",
            ton=ton_terrain(0, complet),
            verdict=(
                "Aucune cavité ni aucun mouvement de terrain recensé à moins de 500 m."
            ),
            details=details,
            sources=sources,
            pannes_partielles=pannes,
        )
    parties = [
        pluriel(cavites, "cavité") if cavites else None,
        pluriel(mouvements, "mouvement de terrain", "mouvements de terrain")
        if mouvements
        else None,
    ]
    prefixe = "Au moins " if not complet else ""
    return theme(
        THEME,
        etat="ok",
        ton=ton_terrain(total, complet),
        verdict=f"{prefixe}{' et '.join(p for p in parties if p)} à moins de 500 m.",
        chiffres=chiffres,
        details=details,
        sources=sources,
        pannes_partielles=pannes,
    )


async def amont(contexte: ContexteAmont) -> AmontTerrain:
    cavites, mouvements = await asyncio.gather(
        lignes_au_point(contexte, "/cavites", RAYON_M, pages_max=3),
        lignes_au_point(contexte, "/mvt", RAYON_M, pages_max=3),
    )
    return AmontTerrain(cavites=cavites, mouvements=mouvements)


THEME = DefinitionTheme(
    id="mouvements_terrain",
    section="risques",
    ordre=18,
    titre="Cavités et mouvements de terrain",
    granularite=granularite("rayon", RAYON_M),
    sources=("georisques",),
    lire=lire,
    amont=amont,
)
