"""« Transports » : the nearest stop or station within a kilometre.

Granularity: radius of 1 km around the BAN point, at the geodesic distance
(never a travel time). « Réseaux et transports » of the address page, read
once: stops (GTFS) and SNCF stations. A radius that reaches a departement the
instance does not host is a partial read, never « no stop » (C16); so is a
category (stops or stations) the instance does not host: « no stop nor
station » is said only when both are hosted and read.

The detail names the nearest SNCF station within 20 km, whatever the radius
of the stops; a disc that reaches a departement the instance does not host
makes it « the nearest known ». The lines and their frequency at peak hour
need the GTFS timetables, not imported yet (04.7-07b): both are out of
coverage, never invented.

Tone rule: rassurant when a stop or station is at 500 m or less; neutre
beyond, or when the nearest station could not be read (a partial answer
never reassures); vigilance when none is within the radius (a measured
zero).
"""

from app.adresse.blocs import MOTIF_HORS_PERIMETRE
from app.adresse.lieu import FIELD_ARRETS, RAYON_ARRETS_M
from app.rapport.partage import (
    RAYON_GARE_M,
    GareProche,
    PanneAmont,
    champ,
    gare_proche,
    reseaux_transports,
)
from app.rapport.schemas import Chiffre, Theme, Ton
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    etat_de_valeur,
    granularite,
    nombre,
    sans_reponse,
    sources_de,
    theme,
)

PROCHE_M = 500
NOM_MAX = 60
HORAIRES = {"etat": "hors_couverture", "motif": "horaires_gtfs_non_importes"}


def ton_transports(distance_m: int) -> Ton:
    return "rassurant" if distance_m <= PROCHE_M else "neutre"


async def _gare(contexte: ContexteRapport) -> dict:
    lu = await gare_proche(contexte)
    if lu is None:
        hebergement = contexte.hosting.hostings["gares"]
        if hebergement.etat != "heberge":
            return {"etat": "hors_couverture", "motif": hebergement.motif}
        return {"etat": "manque", "motif": "position_indeterminee"}
    if isinstance(lu, PanneAmont) or not isinstance(lu, GareProche):
        return {"etat": "panne", "motif": "lecture_indisponible"}
    if lu.nom is None or lu.distance_m is None:
        if lu.complete:
            return {"etat": "zero", "rayon_m": RAYON_GARE_M}
        return {"etat": "hors_couverture", "motif": MOTIF_HORS_PERIMETRE}
    return {
        "etat": "ok",
        "nom": lu.nom,
        "distance_m": lu.distance_m,
        "plus_proche_connue": not lu.complete,
    }


async def lire(contexte: ContexteRapport) -> Theme:
    lu = await reseaux_transports(contexte)
    arrets = champ(lu, FIELD_ARRETS)
    # The station is read apart from the stops: their outage or their
    # coverage never hides it.
    gare = await _gare(contexte)
    pannes = ["gares"] if gare["etat"] == "panne" else []
    horaires = {
        "gare": gare,
        "lignes": dict(HORAIRES),
        "frequence_pointe": dict(HORAIRES),
    }
    if arrets is None:
        return sans_reponse(
            THEME, "panne", lu.motif, details=horaires, pannes_partielles=pannes
        )
    sources = sources_de(THEME, {"transports": arrets.periode})
    if arrets.etat != "value":
        return sans_reponse(
            THEME,
            etat_de_valeur(arrets.etat),
            arrets.motif,
            details=horaires,
            sources=sources,
            pannes_partielles=pannes,
        )
    elements = [
        {"nom": e.libelle, "distance_m": e.distance_m, "nature": e.nature}
        for e in arrets.elements
    ]
    mesures = [e for e in arrets.elements if e.distance_m is not None]
    non_couvertes = [
        cle
        for cle in ("transports", "gares")
        if contexte.hosting.hostings[cle].etat != "heberge"
    ]
    details = {
        "arrets": elements,
        "liste_incomplete": arrets.liste_incomplete or bool(non_couvertes),
        "motif": arrets.motif,
        "categories_non_couvertes": non_couvertes,
        **horaires,
    }
    if not mesures and non_couvertes:
        return sans_reponse(
            THEME,
            "hors_couverture",
            "categorie_non_couverte",
            details=details,
            sources=sources,
            pannes_partielles=pannes,
        )
    if not mesures:
        return theme(
            THEME,
            etat="zero",
            ton="vigilance",
            verdict="Aucun arrêt ni gare à moins d'1 km à vol d'oiseau.",
            details=details,
            sources=sources,
            pannes_partielles=pannes,
        )
    plus_proche = mesures[0]
    distance = plus_proche.distance_m or 0
    nom = plus_proche.libelle
    if len(nom) > NOM_MAX:
        nom = f"{nom[: NOM_MAX - 1]}…"
    nature = "Gare" if plus_proche.nature == "gare" else "Arrêt"
    return theme(
        THEME,
        etat="ok",
        # A station read down: the answer is partial and never reassures.
        ton="neutre" if pannes else ton_transports(distance),
        verdict=(
            f"{nature} le plus proche : {nom}, à {nombre(distance)} m à vol d'oiseau."
        ),
        chiffres=[
            Chiffre(
                id="distance",
                libelle="Arrêt le plus proche",
                valeur=distance,
                unite="metre",
                millesime=arrets.periode,
            )
        ],
        details=details,
        sources=sources,
        pannes_partielles=pannes,
    )


THEME = DefinitionTheme(
    id="transports",
    section="vivre",
    ordre=20,
    titre="Transports",
    granularite=granularite("rayon", RAYON_ARRETS_M),
    sources=("transports", "gares"),
    lire=lire,
)
