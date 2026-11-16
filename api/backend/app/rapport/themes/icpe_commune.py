"""« Installations classées » : classified installations of the commune (ICPE).

Granularity: commune (read by INSEE code, counted on the total announced by
Géorisques). The distance and the Seveso threshold come with 04.7-04.

Tone rule: neutre (a count in the commune says nothing of the address).
"""

from app.rapport.partage import risque_communal, risques_commune_amont
from app.rapport.schemas import Chiffre, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    en_entier,
    granularite,
    pluriel,
    sources_de,
    theme,
)


async def lire(contexte: ContexteRapport) -> Theme:
    valeur = risque_communal(contexte, THEME, "icpe")
    if isinstance(valeur, Theme):
        return valeur
    nombre_ = en_entier(valeur.valeur)
    sources = sources_de(THEME, {"georisques": valeur.periode})
    details = {"nombre": nombre_, "liste_incomplete": valeur.liste_incomplete}
    if nombre_ == 0:
        return theme(
            THEME,
            etat="zero",
            ton="neutre",
            verdict="Aucune installation classée recensée dans la commune.",
            details=details,
            sources=sources,
        )
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=(
            f"{pluriel(nombre_, 'installation classée', 'installations classées')} "
            f"{'recensée' if nombre_ == 1 else 'recensées'}"
            " dans la commune."
        ),
        chiffres=[
            Chiffre(
                id="nombre",
                libelle="Installations classées",
                valeur=nombre_,
                unite="count",
                millesime=valeur.periode,
            )
        ],
        details=details,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="icpe_commune",
    section="risques",
    ordre=40,
    titre="Installations classées",
    granularite=granularite("commune"),
    sources=("georisques",),
    lire=lire,
    amont=risques_commune_amont,
)
