"""« Altitude » : elevation of the ground at the BAN point (IGN RGE ALTI).

Granularity: address (the elevation is computed at the point, from the
1 m to 5 m RGE ALTI grid, by the Géoplateforme altimetry service). Where the
RGE ALTI has no data the service writes -99999: a gap, never an altitude.
Until the scope hosts ``altimetrie`` the theme is out of coverage, without
any call.

Tone rule: neutre (an elevation alone says nothing of a risk).
"""

from app.rapport.libelles import arrondi
from app.rapport.partage import AltitudeLue, altitude_au_point
from app.rapport.schemas import Chiffre, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    nombre,
    sans_reponse,
    sources_de,
    theme,
)


async def lire(contexte: ContexteRapport) -> Theme:
    sources = sources_de(THEME, {"altimetrie": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["altimetrie"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources
        )
    if contexte.point is None:
        return sans_reponse(THEME, "manque", "position_indeterminee", sources=sources)
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, AltitudeLue):
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    if lu.metres is None:
        return sans_reponse(
            THEME,
            "manque",
            "hors_rge_alti",
            verdict="Le RGE ALTI ne donne pas d'altitude à ce point.",
            sources=sources,
        )
    metres = int(arrondi(lu.metres, 0))
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=f"Altitude du sol au point de l'adresse : {nombre(metres)} m.",
        chiffres=[
            Chiffre(
                id="altitude",
                libelle="Altitude",
                valeur=metres,
                unite="metre",
                millesime=contexte.as_of.isoformat(),
            )
        ],
        details={"altitude_m": lu.metres},
        sources=sources,
    )


THEME = DefinitionTheme(
    id="altitude",
    section="risques",
    ordre=17,
    titre="Altitude",
    granularite=granularite("adresse"),
    sources=("altimetrie",),
    lire=lire,
    amont=altitude_au_point,
)
