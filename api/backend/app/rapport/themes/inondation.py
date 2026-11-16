"""« Inondation » : flood exposure at the address, with the commune's plans.

Granularity: address when the Géorisques risk report reads the BAN point,
else commune (common rule, :func:`app.rapport.partage.risque_du_rapport`).
The details carry the commune's flood atlases (AZI) and territories at
important risk (TRI) from GASPAR, and the TRI flood extents ERRIAL finds on
the parcel of the address (only when ERRIAL's parcel is the address's).

Tone rule: the common rule; a parcel inside a TRI flood extent is also a
point to check at the address, which prevails over the commune's list when
the risk report is down (ERRIAL is then named among the sources).
"""

import asyncio

from app.rapport.partage import (
    AmontInondation,
    ErrialLu,
    LignesLues,
    PhrasesRisque,
    errial,
    lignes_commune,
    rapport_risques,
    risque_du_rapport,
    risques_commune_amont,
)
from app.rapport.schemas import Theme
from app.rapport.socle import (
    ContexteAmont,
    ContexteRapport,
    DefinitionTheme,
    granularite,
    source,
)

PHRASES = PhrasesRisque(
    adresse="Risque d'inondation recensé à l'adresse.",
    hors_zone=(
        "Risque d'inondation recensé dans la commune ; l'adresse est hors des "
        "zones cartographiées."
    ),
    commune=(
        "Risque d'inondation recensé dans la commune ; non déterminé à l'adresse."
    ),
    non_concerne="Aucun risque d'inondation recensé dans la commune.",
)


def _libelles(lu: object, cle: str) -> list[str] | None:
    if not isinstance(lu, LignesLues):
        return None
    return [str(ligne[cle]).strip() for ligne in lu.lignes if ligne.get(cle)]


async def lire(contexte: ContexteRapport) -> Theme:
    amont = contexte.amonts.get(THEME.id)
    if not isinstance(amont, AmontInondation):
        amont = AmontInondation(rapport=amont, azi=None, tri=None, errial=None)
    atlas = _libelles(amont.azi, "libelle_azi")
    territoires = _libelles(amont.tri, "libelle_tri")
    tri_parcelle = (
        amont.errial.tris
        if isinstance(amont.errial, ErrialLu) and amont.errial.concordance is True
        else None
    )
    pannes = [
        cle
        for cle, valeur in (("azi", atlas), ("tri", territoires))
        if valeur is None and contexte.hosting.hostings["georisques"].etat == "heberge"
    ]
    return risque_du_rapport(
        contexte,
        THEME,
        amont.rapport,
        "inondation",
        PHRASES,
        details={
            "atlas_zones_inondables": atlas,
            "territoires_risque_important": territoires,
            "tri_parcelle": tri_parcelle,
        },
        vigilance_a_l_adresse=bool(tri_parcelle),
        pannes_partielles=pannes,
        gaspar=amont.gaspar,
        libelles_gaspar=("inondation",),
        sources_preuve=[source("errial", contexte.as_of.isoformat())],
    )


async def amont(contexte: ContexteAmont) -> AmontInondation:
    rapport, azi, tri, avis, gaspar = await asyncio.gather(
        rapport_risques(contexte),
        lignes_commune(contexte, "/gaspar/azi"),
        lignes_commune(contexte, "/gaspar/tri"),
        errial(contexte),
        risques_commune_amont(contexte),
    )
    return AmontInondation(
        rapport=rapport, azi=azi, tri=tri, errial=avis, gaspar=gaspar
    )


THEME = DefinitionTheme(
    id="inondation",
    section="risques",
    ordre=12,
    titre="Inondation",
    granularite=granularite("adresse"),
    sources=("georisques", "errial"),
    lire=lire,
    amont=amont,
)
