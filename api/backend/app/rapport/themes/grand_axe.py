"""« Grands axes routiers » : the nearest major road within 500 m (BD TOPO).

Granularity: radius of 500 m around the BAN point. Major roads are the
sections of importance 1 or 2 of the BD TOPO road hierarchy, in service,
neither in a tunnel nor fictive, read live from the WFS of the Géoplateforme.
The distance is measured to the axis of the road; the regulation counts the
width of a noise sector from the edge of the carriageway, a few metres
nearer.

Tone rule, from the widths of the sectors affected by noise (arrêté du
30 mai 1996, art. 4: 300 m for category 1, 100 m for category 3):
vigilance when a road of importance 1 is within 300 m or a road of
importance 2 within 100 m; neutre when the nearest major road is farther,
within 500 m; rassurant when none is within 500 m on a complete read. These
are thresholds of prudence, never the classification of the road, which only
a prefectoral order sets.
"""

from app.rapport.partage import CoucheWfs, TronconsLus, troncons_au_point
from app.rapport.schemas import VERDICT_MAX, Chiffre, Theme, Ton
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

RAYON_M = 500
SEUILS_M = {"1": 300, "2": 100}
DETAILS_MAX = 5
COUCHE = CoucheWfs(
    nom="BDTOPO_V3:troncon_de_route",
    filtre="importance IN ('1','2') AND etat_de_l_objet='En service'",
    proprietes=(
        "nature",
        "importance",
        "cpx_numero",
        "cpx_classement_administratif",
        "cpx_toponyme_route_nommee",
        "nom_collaboratif_gauche",
        "position_par_rapport_au_sol",
        "fictif",
    ),
    rayon_m=RAYON_M,
)
_TYPES = {
    "Autoroute": "autoroute",
    "Nationale": "route nationale",
    "Départementale": "route départementale",
}


def _type(proprietes: dict) -> str:
    classement = str(proprietes.get("cpx_classement_administratif") or "")
    type_ = _TYPES.get(classement.split("/")[0].strip())
    if type_:
        return type_
    if proprietes.get("nature") == "Type autoroutier":
        return "voie de type autoroutier"
    return "grand axe"


def _nom(proprietes: dict) -> str | None:
    for cle in ("cpx_numero", "cpx_toponyme_route_nommee", "nom_collaboratif_gauche"):
        valeur = proprietes.get(cle)
        if isinstance(valeur, str) and valeur.strip():
            return valeur.strip()[:40]
    return None


def axes(lu: TronconsLus) -> list[dict]:
    """One line per road and importance, at its nearest section: a section of
    importance 1 keeps its own line behind a nearer one of the same road."""
    vus: dict[tuple, dict] = {}
    for troncon in lu.troncons:
        p = troncon.proprietes
        cle = (_nom(p) or troncon.distance_m, _type(p), str(p.get("importance")))
        if cle not in vus:
            vus[cle] = {
                "nom": _nom(p),
                "type": _type(p),
                "importance": str(p.get("importance")),
                "distance_m": troncon.distance_m,
            }
    return list(vus.values())


def proche(axe: dict) -> bool:
    return axe["distance_m"] <= SEUILS_M.get(axe["importance"], 0)


def libelle(axe: dict) -> str:
    if axe["nom"]:
        return f"{axe['nom']} ({axe['type']}) à {nombre(axe['distance_m'])} m"
    return f"{axe['type'].capitalize()} à {nombre(axe['distance_m'])} m"


def verdict_axes(liste: list[dict], complet: bool) -> tuple[str, Ton]:
    """(verdict, tone) of the major roads read, nearest first."""
    plus_proche = liste[0]
    declencheur = next((a for a in liste if proche(a)), None)
    ton: Ton = "neutre" if declencheur is None else "vigilance"
    # A partial read may miss a nearer section: never « the nearest ».
    plus = "le plus proche" if complet else "observé"
    if declencheur is None:
        phrases = [f"Grand axe routier {plus} : {libelle(plus_proche)}"]
    else:
        phrases = [f"Grand axe routier proche : {libelle(declencheur)}"]
        if declencheur is not plus_proche:
            autre = "le plus proche" if complet else "autre observé"
            phrases.insert(0, f"{phrases[0]} ; {autre} : {libelle(plus_proche)}")
    suffixe = "" if complet else " (lecture incomplète)"
    candidats = [f"{p}{suffixe}." for p in phrases] + [f"{p}." for p in phrases]
    return next((v for v in candidats if len(v) <= VERDICT_MAX), candidats[-1]), ton


async def lire(contexte: ContexteRapport) -> Theme:
    sources = sources_de(THEME, {"axes_transport": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["axes_transport"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources
        )
    if contexte.point is None:
        return sans_reponse(THEME, "manque", "position_indeterminee", sources=sources)
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, TronconsLus):
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    liste = axes(lu)
    details = {"axes": liste[:DETAILS_MAX], "liste_incomplete": not lu.complet}
    if not liste and not lu.complet:
        return sans_reponse(
            THEME, "manque", "lecture_incomplete", details=details, sources=sources
        )
    if not liste:
        return theme(
            THEME,
            etat="zero",
            ton="rassurant",
            verdict="Aucun grand axe routier à moins de 500 m.",
            details=details,
            sources=sources,
        )
    verdict, ton = verdict_axes(liste, lu.complet)
    return theme(
        THEME,
        etat="ok",
        ton=ton,
        verdict=verdict,
        chiffres=[
            Chiffre(
                id="distance",
                libelle="Grand axe routier le plus proche"
                if lu.complet
                else "Grand axe routier observé",
                valeur=liste[0]["distance_m"],
                unite="metre",
                millesime=contexte.as_of.isoformat(),
            )
        ],
        details=details,
        sources=sources,
    )


async def amont(contexte: ContexteAmont) -> object:
    return await troncons_au_point(contexte, COUCHE)


THEME = DefinitionTheme(
    id="grand_axe",
    section="vivre",
    ordre=50,
    titre="Grands axes routiers",
    granularite=granularite("rayon", RAYON_M),
    sources=("axes_transport",),
    lire=lire,
    amont=amont,
)
