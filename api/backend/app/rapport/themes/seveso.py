"""« Sites Seveso » : Seveso classified installations within 5 km.

Granularity: radius of 5 km around the BAN point. The classified
installations Géorisques serves by radius (up to five pages of 100), kept
when their status is Seveso (upper or lower tier), each with its geodesic
distance. A read cut by the page bound is partial: « no Seveso site » is
said only on a complete read.

Tone rule: vigilance when a Seveso site is within 1 km, or an upper-tier
site within 5 km; neutral when only lower-tier sites lie between 1 and
5 km; rassurant when none is within 5 km on a complete read.
"""

from typing import Any

from app.rapport.partage import LignesLues, distance_au_point, lignes_au_point
from app.rapport.schemas import Chiffre, Theme, Ton
from app.rapport.socle import (
    ContexteAmont,
    ContexteRapport,
    DefinitionTheme,
    granularite,
    nombre,
    sans_reponse,
    sources_de,
    theme,
)

RAYON_M = 5000
PROCHE_M = 1000
NOM_MAX = 50


def niveau_seveso(statut: object) -> str | None:
    texte = str(statut or "").lower()
    if "seuil haut" in texte:
        return "haut"
    if "seuil bas" in texte:
        return "bas"
    return None


def ton_seveso(sites: list[dict], complet: bool) -> Ton:
    for site in sites:
        distance = site["distance_m"]
        if site["seuil"] == "haut" or (distance is not None and distance <= PROCHE_M):
            return "vigilance"
    if sites:
        return "neutre"
    return "rassurant" if complet else "neutre"


async def lire(contexte: ContexteRapport) -> Theme:
    sources = sources_de(THEME, {"georisques": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["georisques"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources
        )
    point = contexte.point
    if point is None:
        return sans_reponse(THEME, "manque", "position_indeterminee", sources=sources)
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, LignesLues):
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    sites: list[dict[str, Any]] = sorted(
        (
            {
                "nom": str(ligne.get("raisonSociale") or "").strip(),
                "seuil": niveau_seveso(ligne.get("statutSeveso")),
                "distance_m": distance_au_point(point, ligne),
                "activite": ligne.get("etatActivite"),
            }
            for ligne in lu.lignes
            if niveau_seveso(ligne.get("statutSeveso"))
        ),
        key=lambda s: (s["distance_m"] is None, s["distance_m"] or 0),
    )
    details = {"sites": sites, "liste_incomplete": not lu.complet}
    if not sites:
        if not lu.complet:
            return sans_reponse(
                THEME, "manque", "lecture_incomplete", details=details, sources=sources
            )
        return theme(
            THEME,
            etat="zero",
            ton="rassurant",
            verdict="Aucun site Seveso à moins de 5 km.",
            details=details,
            sources=sources,
        )
    proche = sites[0]
    nom = str(proche["nom"])[:NOM_MAX] or "un site"
    distance: int | None = proche["distance_m"]
    lieu_ = f", à {nombre(distance / 1000)} km" if distance is not None else ""
    verdict = f"Site Seveso seuil {proche['seuil']} le plus proche : {nom}{lieu_}."
    return theme(
        THEME,
        etat="ok",
        ton=ton_seveso(sites, lu.complet),
        verdict=verdict,
        chiffres=[
            Chiffre(
                id="sites",
                libelle="Sites Seveso à 5 km",
                valeur=len(sites),
                unite="count",
                millesime=contexte.as_of.isoformat(),
            ),
            *(
                [
                    Chiffre(
                        id="distance",
                        libelle="Site le plus proche",
                        valeur=distance,
                        unite="metre",
                        millesime=contexte.as_of.isoformat(),
                    )
                ]
                if distance is not None
                else []
            ),
        ],
        details=details,
        sources=sources,
    )


async def amont(contexte: ContexteAmont) -> object:
    return await lignes_au_point(
        contexte, "/installations_classees", RAYON_M, pages_max=5
    )


THEME = DefinitionTheme(
    id="seveso",
    section="risques",
    ordre=62,
    titre="Sites Seveso",
    granularite=granularite("rayon", RAYON_M),
    sources=("georisques",),
    lire=lire,
    amont=amont,
)
