"""« Remontée de nappe » : groundwater flooding (Géorisques risk report).

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
    adresse="Risque de remontée de nappe recensé à l'adresse.",
    hors_zone=(
        "Remontée de nappe recensée dans la commune ; l'adresse est hors des "
        "zones cartographiées."
    ),
    commune=(
        "Remontée de nappe recensée dans la commune ; non déterminée à l'adresse."
    ),
    non_concerne="Aucun risque de remontée de nappe recensé dans la commune.",
)


async def lire(contexte: ContexteRapport) -> Theme:
    amont = amont_risque_lu(contexte, THEME)
    return risque_du_rapport(
        contexte,
        THEME,
        amont.rapport,
        "remonteeNappe",
        PHRASES,
        gaspar=amont.gaspar,
        libelles_gaspar=("nappe",),
    )


THEME = DefinitionTheme(
    id="remontee_nappe",
    section="risques",
    ordre=14,
    titre="Remontée de nappe",
    granularite=granularite("adresse"),
    sources=("georisques",),
    lire=lire,
    amont=amont_risque,
)
