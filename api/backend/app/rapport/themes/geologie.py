"""« Géologie » : dominant rock of the sector (BRGM lithology, 1:1 000 000).

Granularity: commune. The value is read at the BAN point, but on a map at
the millionth: a limit between two formations is known to a kilometre or
more, and the BRGM warns against presenting a layer at a finer scale than
its own. It is served as the rock of the sector, never as an observation at
the address. Until the scope hosts ``geologie`` the theme is out of
coverage, without any call.

Tone rule: neutre (a lithology alone says nothing of a risk).
"""

from app.rapport.partage import Lithologie, geologie_au_point
from app.rapport.schemas import VERDICT_MAX, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    sans_reponse,
    sources_de,
    theme,
)

PREFIXE = "Roche dominante du secteur (carte au millionième) : "


def _verdict(description: str, type_roche: str | None) -> str:
    """The rock as the map names it, its type when the sentence allows."""
    complet = f"{PREFIXE}{description.lower()}"
    if type_roche and len(complet) + len(type_roche) + 4 <= VERDICT_MAX:
        complet += f" ({type_roche.lower()})"
    if len(complet) + 1 > VERDICT_MAX:
        complet = complet[: VERDICT_MAX - 2].rstrip() + "…"
    return f"{complet}."


async def lire(contexte: ContexteRapport) -> Theme:
    sources = sources_de(THEME, {"geologie": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["geologie"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources
        )
    if contexte.point is None:
        return sans_reponse(THEME, "manque", "position_indeterminee", sources=sources)
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, list):
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    roches = list(
        dict.fromkeys(
            (r.description, r.type_roche) for r in lu if isinstance(r, Lithologie)
        )
    )
    if len(roches) != 1:
        return sans_reponse(
            THEME,
            "manque",
            "aucune_formation" if not roches else "formations_multiples",
            details={"formations": [d for d, _ in roches]},
            sources=sources,
        )
    description, type_roche = roches[0]
    verdict = _verdict(description, type_roche)
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=verdict,
        details={
            "formation": description,
            "type": type_roche,
            "echelle": "1/1 000 000",
        },
        sources=sources,
    )


THEME = DefinitionTheme(
    id="geologie",
    section="risques",
    ordre=19,
    titre="Géologie",
    granularite=granularite("commune"),
    sources=("geologie",),
    lire=lire,
    amont=geologie_au_point,
)
