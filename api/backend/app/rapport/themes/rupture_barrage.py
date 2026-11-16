"""« Rupture de barrage » : dam-break flooding (Géorisques risk report).

Granularity: address when the Géorisques risk report reads the BAN point,
else commune (common rule, :func:`app.rapport.partage.risque_du_rapport`).

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
    adresse="Risque de rupture de barrage recensé à l'adresse.",
    hors_zone=(
        "Rupture de barrage recensée dans la commune ; l'adresse n'est pas "
        "dans la zone concernée."
    ),
    commune=(
        "Rupture de barrage recensée dans la commune ; non déterminée à l'adresse."
    ),
    non_concerne="Aucun risque de rupture de barrage recensé dans la commune.",
)


async def lire(contexte: ContexteRapport) -> Theme:
    amont = amont_risque_lu(contexte, THEME)
    return risque_du_rapport(
        contexte,
        THEME,
        amont.rapport,
        "ruptureBarrage",
        PHRASES,
        gaspar=amont.gaspar,
        libelles_gaspar=("barrage",),
    )


THEME = DefinitionTheme(
    id="rupture_barrage",
    section="risques",
    ordre=16,
    titre="Rupture de barrage",
    granularite=granularite("adresse"),
    sources=("georisques",),
    lire=lire,
    amont=amont_risque,
)
