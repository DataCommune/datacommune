"""« Radon » : radon potential class of the commune (Géorisques, IRSN).

Granularity: commune (read by INSEE code). Classes as published: 1 low,
2 low with factors that may favour the transfer into buildings,
3 significant.

Tone rule: vigilance for class 3, neutre for class 2, rassurant for class 1;
any other label is neutre.
"""

from app.rapport.partage import risque_communal, risques_commune_amont
from app.rapport.schemas import Theme, Ton
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    sources_de,
    theme,
)

LIBELLES = {
    "1": "faible",
    "2": "faible, avec des facteurs pouvant favoriser son entrée dans les bâtiments",
    "3": "significatif",
}


_TONS: dict[str, Ton] = {"3": "vigilance", "2": "neutre", "1": "rassurant"}


def ton_radon(classe: str) -> Ton:
    return _TONS.get(classe, "neutre")


async def lire(contexte: ContexteRapport) -> Theme:
    valeur = risque_communal(contexte, THEME, "radon")
    if isinstance(valeur, Theme):
        return valeur
    classe = str(valeur.valeur).strip()
    libelle = LIBELLES.get(classe)
    verdict = (
        f"Potentiel radon de la commune : catégorie {classe} ({libelle})."
        if libelle
        else f"Potentiel radon de la commune : {classe}."
    )
    return theme(
        THEME,
        etat="ok",
        ton=ton_radon(classe),
        verdict=verdict,
        details={"classe": classe},
        sources=sources_de(THEME, {"georisques": valeur.periode}),
    )


THEME = DefinitionTheme(
    id="radon",
    section="risques",
    ordre=30,
    titre="Radon",
    granularite=granularite("commune"),
    sources=("georisques",),
    lire=lire,
    amont=risques_commune_amont,
)
