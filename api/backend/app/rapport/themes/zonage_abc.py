"""« Zonage ABC » : housing-market tension zone of the commune.

Granularity: commune. ``app.zonage_abc.queries.zonage_par_codes``: the zone
(A bis, A, B1, B2, C) of the order in force, with its date.

Tone rule: neutre (a classification, no valence).
"""

from app.rapport.schemas import Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    LignesCommunales,
    granularite,
    lignes_communales,
    sans_reponse,
    sources_de,
    theme,
)
from app.zonage_abc.queries import zonage_par_codes


async def lire(contexte: ContexteRapport) -> Theme:
    lu = await lignes_communales(contexte, THEME, "zonage_abc", zonage_par_codes)
    if isinstance(lu, Theme):
        return lu
    return lu.situer(await _servir(contexte, lu))


async def _servir(contexte: ContexteRapport, lu: LignesCommunales) -> Theme:
    zones = sorted(
        {str(ligne["zone"]).strip() for ligne in lu.lignes if ligne.get("zone")}
    )
    if len(zones) != 1:
        return sans_reponse(
            THEME,
            "manque",
            "zone_non_publiee" if not zones else "zones_multiples",
            details={"zones": zones},
            sources=sources_de(THEME, {"zonage_abc": lu.periode}),
        )
    ligne = lu.lignes[0]
    millesime = ligne.get("date_arrete") or lu.periode
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=(f"Zone {zones[0]} pour {lu.lieu} (tension du marché du logement)."),
        details={
            "territoire": lu.territoire,
            "zone": zones[0],
            "date_arrete": ligne.get("date_arrete"),
            "reclassement": ligne.get("reclassement"),
        },
        sources=sources_de(THEME, {"zonage_abc": millesime}),
    )


THEME = DefinitionTheme(
    id="zonage_abc",
    section="marche",
    ordre=60,
    titre="Zonage ABC",
    granularite=granularite("commune"),
    sources=("zonage_abc",),
    lire=lire,
)
