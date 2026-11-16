"""« Secteur affecté par le bruit » : the noise sector of land transport, as
the published PLU reports it (API Carto GPU, information of CNIG type 14).

Granularity: address (the information area is read at the BAN point). The
prefect classifies the noisy roads and railways and sets, on each side, a
sector where new buildings need reinforced acoustic insulation; no national
source serves these orders, and the PLU reports the sector only when its
author digitised it, without its category or width.

Tone rule: vigilance when the PLU places the address in such a sector;
otherwise a gap (manque), never reassuring: a PLU may not report the sector,
and the prefectoral order prevails.
"""

from app.rapport.partage import InformationsPlu, informations_plu_au_point
from app.rapport.schemas import Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    sans_reponse,
    sources_de,
    theme,
)


async def lire(contexte: ContexteRapport) -> Theme:
    sources = sources_de(THEME, {"urbanisme": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["urbanisme"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources
        )
    if contexte.point is None:
        return sans_reponse(THEME, "manque", "position_indeterminee", sources=sources)
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, InformationsPlu):
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    details = {"secteurs": lu.secteurs_bruit, "informations_plu": lu.informations}
    if lu.secteurs_bruit:
        return theme(
            THEME,
            etat="ok",
            ton="vigilance",
            verdict=(
                "Le PLU place l'adresse dans un secteur affecté par le bruit des "
                "transports : isolation acoustique renforcée pour le neuf."
            ),
            details=details,
            sources=sources,
        )
    return sans_reponse(
        THEME,
        "manque",
        "non_reporte_au_plu" if lu.informations else "sans_information_plu",
        verdict=(
            "Aucun secteur affecté par le bruit reporté au PLU ici ; "
            "l'arrêté préfectoral de classement sonore fait foi."
        ),
        details=details,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="secteur_bruit",
    section="vivre",
    ordre=56,
    titre="Secteur affecté par le bruit",
    granularite=granularite("adresse"),
    sources=("urbanisme",),
    lire=lire,
    amont=informations_plu_au_point,
)
