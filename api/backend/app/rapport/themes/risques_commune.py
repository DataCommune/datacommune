"""« Risques recensés » : risks listed for the commune (Géorisques GASPAR).

Granularity: commune (read by INSEE code, never at the point). None listed
is a gap, never « 0 risque » (address page, 04-06).

Tone rule: vigilance when at least one risk is listed: a point at the scale
of the commune, counted apart from the points of the address.
"""

from app.rapport.partage import risque_communal, risques_commune_amont
from app.rapport.schemas import Chiffre, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    sources_de,
    theme,
)

LISTES_AU_VERDICT = 3


async def lire(contexte: ContexteRapport) -> Theme:
    valeur = risque_communal(contexte, THEME, "gaspar")
    if isinstance(valeur, Theme):
        return valeur
    libelles = [element.libelle for element in valeur.elements]
    premiers = ", ".join(libelle.lower() for libelle in libelles[:LISTES_AU_VERDICT])
    suite = "…" if len(libelles) > LISTES_AU_VERDICT else ""
    verdict = f"Risques recensés sur la commune : {premiers}{suite}."
    if len(verdict) > 140:
        verdict = f"{len(libelles)} risques recensés sur la commune."
    return theme(
        THEME,
        etat="ok",
        ton="vigilance",
        verdict=verdict,
        chiffres=[
            Chiffre(
                id="nombre",
                libelle="Risques recensés",
                valeur=len(libelles),
                unite="count",
                millesime=valeur.periode,
            )
        ],
        details={"risques": libelles, "liste_incomplete": valeur.liste_incomplete},
        sources=sources_de(THEME, {"georisques": valeur.periode}),
    )


THEME = DefinitionTheme(
    id="risques_commune",
    section="risques",
    ordre=20,
    titre="Risques recensés",
    granularite=granularite("commune"),
    sources=("georisques",),
    lire=lire,
    amont=risques_commune_amont,
)
