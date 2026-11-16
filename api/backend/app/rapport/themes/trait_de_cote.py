"""« Trait de côte » : coastline retreat (Géorisques risk report).

Granularity: address when the Géorisques risk report reads the BAN point,
else commune (common rule, :func:`app.rapport.partage.risque_du_rapport`).
Géorisques lists the communes of the « trait de côte » decree; the coastal
flooding status (``risqueCotier``) is in the details.

Tone rule: the common rule.
"""

from app.rapport.partage import (
    PhrasesRisque,
    RapportRisques,
    amont_risque,
    amont_risque_lu,
    risque_du_rapport,
)
from app.rapport.schemas import Theme
from app.rapport.socle import ContexteRapport, DefinitionTheme, granularite

PHRASES = PhrasesRisque(
    adresse="Recul du trait de côte recensé à l'adresse.",
    hors_zone=(
        "Recul du trait de côte recensé dans la commune ; l'adresse est hors "
        "des zones exposées."
    ),
    commune=(
        "Recul du trait de côte recensé dans la commune ; non déterminé à l'adresse."
    ),
    non_concerne="La commune n'est pas concernée par le recul du trait de côte.",
)


async def lire(contexte: ContexteRapport) -> Theme:
    amont = amont_risque_lu(contexte, THEME)
    lu = amont.rapport
    cotier = lu.statuts.get("risqueCotier") if isinstance(lu, RapportRisques) else None
    return risque_du_rapport(
        contexte,
        THEME,
        lu,
        "reculTraitCote",
        PHRASES,
        details={
            "risque_cotier": {
                "present": cotier.present,
                "statut_commune": cotier.commune,
                "statut_adresse": cotier.adresse,
            }
            if cotier is not None
            else None
        },
        gaspar=amont.gaspar,
        libelles_gaspar=("trait de côte",),
    )


THEME = DefinitionTheme(
    id="trait_de_cote",
    section="risques",
    ordre=52,
    titre="Trait de côte",
    granularite=granularite("adresse"),
    sources=("georisques",),
    lire=lire,
    amont=amont_risque,
)
