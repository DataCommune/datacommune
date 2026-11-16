"""« Qualité de l'air » : the ATMO index forecast for the day.

Granularity: the commune (the arrondissement in Paris, Lyon and Marseille
when the AASQA serves it), else the intercommunality (EPCI) the AASQA
publishes the index for; never the address. The verdict names the zone
read. The index is a forecast of one day, never an exposure: the annual
means by station (Geod'Air) are out of coverage until they are imported
(04.7-07b), and the detail says so.

Tone rule: neutre, whatever the index. Index 0 (« absent ») or no row of
the day for the commune nor its EPCI is a gap; index 7 (« événement ») is
said as such, without a figure.
"""

from app.common.geo import normalize_code_insee
from app.rapport.partage import AirLu, IndiceAtmo, air_au_point
from app.rapport.schemas import VERDICT_MAX, Chiffre, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    date_longue,
    granularite,
    granularite_arrondissement,
    sans_reponse,
    sources_de,
    theme,
)

# Wording of the 2021 index (notice Atmo France), by code.
LIBELLES = {
    1: "bon",
    2: "moyen",
    3: "dégradé",
    4: "mauvais",
    5: "très mauvais",
    6: "extrêmement mauvais",
}
EVENEMENT = 7
MOYENNES_ANNUELLES = {
    "etat": "hors_couverture",
    "motif": "moyennes_geodair_non_importees",
}
# Gaps of the day: the index may be published, or computed, later in the day.
MANQUES_DU_JOUR = frozenset(
    {"aucun_indice_du_jour", "indices_contradictoires", "indice_absent"}
)
VERDICTS_MANQUE = {
    "aucun_indice_du_jour": (
        "Aucun indice ATMO publié aujourd'hui pour la commune ni son intercommunalité."
    ),
    "indices_contradictoires": (
        "Indices ATMO contradictoires publiés aujourd'hui : aucun n'est retenu."
    ),
    "indice_absent": "Indice ATMO non calculé aujourd'hui pour cette zone.",
    "commune_indeterminee": (
        "La commune de l'adresse n'est pas déterminée : aucun indice ATMO lu."
    ),
}


def _zone(indice: IndiceAtmo) -> str:
    if indice.zone_type == "epci":
        avec_nom = f"l'intercommunalité ({indice.zone_nom})"
        return avec_nom if indice.zone_nom else "l'intercommunalité"
    if normalize_code_insee(indice.zone_code) != indice.zone_code:
        return "l'arrondissement"
    return "la commune"


def _details(indice: IndiceAtmo) -> dict:
    return {
        "indice": {
            "code": indice.code,
            "libelle": LIBELLES.get(indice.code),
            "date": indice.date,
            "sous_indices": indice.sous_indices,
        },
        "zone": {
            "type": indice.zone_type,
            "code": indice.zone_code,
            "nom": indice.zone_nom,
        },
        "aasqa": indice.aasqa,
        "moyennes_annuelles": MOYENNES_ANNUELLES,
    }


async def lire(contexte: ContexteRapport) -> Theme:
    hebergement = contexte.hosting.hostings["qualite_air"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources_de(THEME)
        )
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, AirLu):
        return sans_reponse(
            THEME, "panne", "amont_indisponible", sources=sources_de(THEME)
        )
    indice = lu.indice
    if indice is None:
        motif = lu.motif or "aucun_indice_du_jour"
        return sans_reponse(
            THEME,
            "manque",
            motif,
            verdict=VERDICTS_MANQUE[motif],
            details={"moyennes_annuelles": MOYENNES_ANNUELLES},
            sources=sources_de(THEME),
        )
    sources = sources_de(THEME, {"qualite_air": indice.date})
    details = _details(indice)
    niveau = granularite("epci" if indice.zone_type == "epci" else "commune")
    if (
        indice.zone_type != "epci"
        and normalize_code_insee(indice.zone_code) != indice.zone_code
    ):
        # The AASQA serves the arrondissement itself (04.7-09).
        niveau = granularite_arrondissement(indice.zone_code)
    if indice.code == 0:
        return sans_reponse(
            THEME,
            "manque",
            "indice_absent",
            verdict=VERDICTS_MANQUE["indice_absent"],
            details=details,
            sources=sources,
            granularite=niveau,
        )
    jour = date_longue(indice.date)
    zone = _zone(indice)
    if indice.code == EVENEMENT:
        debut = f"Air du {jour} : épisode exceptionnel signalé (indice « événement »)"
        chiffres = []
    else:
        debut = (
            f"Qualité de l'air prévue le {jour} : {LIBELLES[indice.code]} "
            f"({indice.code} sur 6)"
        )
        chiffres = [
            Chiffre(
                id="indice_atmo",
                libelle="Indice ATMO du jour",
                valeur=indice.code,
                unite="indice_sur_6",
                millesime=indice.date,
            )
        ]
    verdict = f"{debut}, pour {zone}."
    if len(verdict) > VERDICT_MAX:
        verdict = f"{debut}, pour l'intercommunalité."
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=verdict,
        chiffres=chiffres,
        details=details,
        granularite=niveau,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="qualite_air",
    section="vivre",
    ordre=64,
    titre="Qualité de l'air",
    granularite=granularite("commune"),
    sources=("qualite_air",),
    lire=lire,
    amont=air_au_point,
)
