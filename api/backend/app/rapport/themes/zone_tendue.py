"""« Zone tendue » : whether the commune is listed for the vacant housing tax.

Granularity: commune; an arrondissement of Paris, Lyon or Marseille is read
on its commune, and the detail says so. Read live from the simulator of the
tense zones (DILA): two disjoint lists, the urban areas of more than 50 000
inhabitants and the communes with a marked imbalance between supply and
demand. Only the tax the list is published for is named: other effects of
a tense zone hang on other lists. « Zone tendue » is said of the urban
areas only; a commune of the second list is said listed for the tax.

Tone rule: neutre (a classification, no valence). A commune absent from
both lists does not concern the address; an unreadable file is an outage.
"""

from app.common.geo import normalize_code_insee
from app.rapport.partage import ZoneTendueLue, zone_tendue_au_point
from app.rapport.schemas import Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    sans_reponse,
    sources_de,
    theme,
)

VERDICTS = {
    "agglomeration": (
        "Commune en zone tendue : agglomération de plus de 50 000 habitants "
        "(taxe sur les logements vacants)."
    ),
    "desequilibre": (
        "Commune listée pour la taxe sur les logements vacants : déséquilibre "
        "marqué entre l'offre et la demande de logements."
    ),
}
HORS_ZONE = "La commune n'est pas en zone tendue (taxe sur les logements vacants)."


async def lire(contexte: ContexteRapport) -> Theme:
    hebergement = contexte.hosting.hostings["zone_tendue"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources_de(THEME)
        )
    lu = contexte.amonts.get(THEME.id)
    if lu is None:
        return sans_reponse(
            THEME, "manque", "commune_indeterminee", sources=sources_de(THEME)
        )
    if not isinstance(lu, ZoneTendueLue):
        return sans_reponse(
            THEME, "panne", "lecture_indisponible", sources=sources_de(THEME)
        )
    code = (contexte.identite.commune.code_insee or "").strip()
    details = {
        "commune": lu.commune,
        "lue_sur_la_commune": normalize_code_insee(code) != code,
        "categorie": lu.categorie,
        "liste": "taxe_logements_vacants",
    }
    if lu.categorie is None:
        return theme(
            THEME,
            etat="non_concerne",
            ton="neutre",
            verdict=HORS_ZONE,
            details=details,
            sources=sources_de(THEME),
        )
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=VERDICTS[lu.categorie],
        details=details,
        sources=sources_de(THEME),
    )


THEME = DefinitionTheme(
    id="zone_tendue",
    section="marche",
    ordre=62,
    titre="Zone tendue",
    granularite=granularite("commune"),
    sources=("zone_tendue",),
    lire=lire,
    amont=zone_tendue_au_point,
)
