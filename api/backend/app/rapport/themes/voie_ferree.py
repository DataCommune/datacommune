"""« Voies ferrées » : the nearest main railway within 500 m (BD TOPO).

Granularity: radius of 500 m around the BAN point. Main railways are the
sections of nature « Voie ferrée principale » or « LGV » of the BD TOPO, in
service and not in a tunnel, read live from the WFS of the Géoplateforme;
tramway, metro and service tracks are left out. The distance is measured to
the axis of the track; the regulation counts from the nearest rail.

Tone rule, from the widths of the sectors affected by noise (arrêté du
30 mai 1996, art. 4: 300 m for category 1, 100 m for category 3):
vigilance when a high-speed line is within 300 m or another main line within
100 m; neutre when the nearest main railway is farther, within 500 m;
rassurant when none is within 500 m on a complete read. Thresholds of
prudence, never the classification of the line.
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
SEUILS_M = {"LGV": 300, "Voie ferrée principale": 100}
DETAILS_MAX = 5
COUCHE = CoucheWfs(
    nom="BDTOPO_V3:troncon_de_voie_ferree",
    filtre=(
        "nature IN ('Voie ferrée principale','LGV') AND etat_de_l_objet='En service'"
    ),
    proprietes=(
        "nature",
        "nombre_de_voies",
        "electrifie",
        "vitesse_maximale",
        "cpx_toponyme",
        "position_par_rapport_au_sol",
    ),
    rayon_m=RAYON_M,
)


def _toponyme(proprietes: dict) -> str | None:
    valeur = proprietes.get("cpx_toponyme")
    return valeur.strip()[:40] if isinstance(valeur, str) and valeur.strip() else None


def lignes(lu: TronconsLus) -> list[dict]:
    """One line per railway (nature and name), at its nearest section."""
    vues: dict[tuple, dict] = {}
    for troncon in lu.troncons:
        p = troncon.proprietes
        nature = str(p.get("nature"))
        cle = (nature, _toponyme(p) or troncon.distance_m)
        if cle not in vues:
            voies = p.get("nombre_de_voies")
            vues[cle] = {
                "nature": nature,
                "nom": _toponyme(p),
                "nombre_de_voies": voies if isinstance(voies, int) else None,
                "electrifiee": p.get("electrifie")
                if isinstance(p.get("electrifie"), bool)
                else None,
                "vitesse_maximale_kmh": p.get("vitesse_maximale")
                if isinstance(p.get("vitesse_maximale"), int)
                else None,
                "distance_m": troncon.distance_m,
            }
    return list(vues.values())


def proche(ligne: dict) -> bool:
    return ligne["distance_m"] <= SEUILS_M.get(ligne["nature"], 0)


def libelle(ligne: dict) -> str:
    nature = "LGV" if ligne["nature"] == "LGV" else "voie ferrée principale"
    nom = f" « {ligne['nom']} »" if ligne["nom"] else ""
    return f"{nature}{nom} à {nombre(ligne['distance_m'])} m"


def verdict_lignes(liste: list[dict], complet: bool) -> tuple[str, Ton]:
    """(verdict, tone) of the main railways read, nearest first."""
    plus_proche = liste[0]
    declencheur = next((ligne for ligne in liste if proche(ligne)), None)
    ton: Ton = "neutre" if declencheur is None else "vigilance"
    # A partial read may miss a nearer section: never « the nearest ».
    plus = "la plus proche" if complet else "observée"
    if declencheur is None:
        phrases = [f"Voie ferrée {plus} : {libelle(plus_proche)}"]
    else:
        phrases = [f"Voie ferrée proche : {libelle(declencheur)}"]
        if declencheur is not plus_proche:
            autre = "la plus proche" if complet else "autre observée"
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
    liste = lignes(lu)
    details = {"voies": liste[:DETAILS_MAX], "liste_incomplete": not lu.complet}
    if not liste and not lu.complet:
        return sans_reponse(
            THEME, "manque", "lecture_incomplete", details=details, sources=sources
        )
    if not liste:
        return theme(
            THEME,
            etat="zero",
            ton="rassurant",
            verdict="Aucune voie ferrée principale à moins de 500 m.",
            details=details,
            sources=sources,
        )
    verdict, ton = verdict_lignes(liste, lu.complet)
    return theme(
        THEME,
        etat="ok",
        ton=ton,
        verdict=verdict,
        chiffres=[
            Chiffre(
                id="distance",
                libelle="Voie ferrée principale la plus proche"
                if lu.complet
                else "Voie ferrée principale observée",
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
    id="voie_ferree",
    section="vivre",
    ordre=52,
    titre="Voies ferrées",
    granularite=granularite("rayon", RAYON_M),
    sources=("axes_transport",),
    lire=lire,
    amont=amont,
)
