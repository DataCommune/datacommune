"""« Nucléaire » : inside a particular intervention plan (PPI) of a nuclear site.

Granularity: address when the Géorisques risk report reads the BAN point,
else commune (common rule, :func:`app.rapport.partage.risque_du_rapport`).
The distance to the nearest site needs a list of sites with coordinates
under a licence read on the producer's page: not served here.

Tone rule: the common rule.
"""

from app.rapport.partage import (
    PhrasesRisque,
    amont_risque,
    amont_risque_lu,
    risque_du_rapport,
)
from app.rapport.schemas import Theme
from app.rapport.socle import ContexteRapport, DefinitionTheme, granularite

PHRASES = PhrasesRisque(
    adresse=(
        "L'adresse est dans le périmètre d'un plan particulier d'intervention "
        "nucléaire."
    ),
    hors_zone=(
        "Risque nucléaire recensé dans la commune ; l'adresse est hors du périmètre."
    ),
    commune=(
        "La commune est dans le périmètre d'un plan particulier d'intervention "
        "nucléaire."
    ),
    non_concerne="La commune n'est dans aucun périmètre d'intervention nucléaire.",
)


async def lire(contexte: ContexteRapport) -> Theme:
    amont = amont_risque_lu(contexte, THEME)
    return risque_du_rapport(
        contexte,
        THEME,
        amont.rapport,
        "nucleaire",
        PHRASES,
        gaspar=amont.gaspar,
        libelles_gaspar=("nucléaire",),
    )


THEME = DefinitionTheme(
    id="nucleaire",
    section="risques",
    ordre=64,
    titre="Nucléaire",
    granularite=granularite("adresse"),
    sources=("georisques",),
    lire=lire,
    amont=amont_risque,
)
