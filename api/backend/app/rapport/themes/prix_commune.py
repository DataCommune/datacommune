"""« Prix au m² » : median price per m² of the commune, houses and flats (DVF).

Granularity: commune. Landmark ④ of the address page
(:func:`app.adresse.service.repere_reference_commune`): the same medians,
window and absence states as the commune page. Never a price of the address.

Tone rule: neutre (a reference price, no valence).
"""

from app.adresse.service import repere_reference_commune
from app.rapport.libelles import arrondi
from app.rapport.schemas import Chiffre, EtatTheme, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    etat_de_valeur,
    granularite,
    nombre,
    sans_reponse,
    source,
    theme,
)

_PRIORITE: tuple[EtatTheme, ...] = ("panne", "hors_couverture")
_TYPES = (
    ("maison", "maisons", "une maison"),
    ("appartement", "appartements", "un appartement"),
)


async def lire(contexte: ContexteRapport) -> Theme:
    repere = await repere_reference_commune(
        contexte.session, contexte.identite.commune, contexte.scope, contexte.hosting
    )
    valeurs = {cle: getattr(repere, attribut) for cle, attribut, _ in _TYPES}
    millesime = next((v.periode for v in valeurs.values() if v.periode), None)
    sources = [source("dvf", millesime)]
    absences = {
        cle: v.motif or v.etat for cle, v in valeurs.items() if v.etat != "value"
    }
    servies = {cle: v for cle, v in valeurs.items() if v.etat == "value"}
    if not servies:
        etats = {etat_de_valeur(v.etat) for v in valeurs.values()}
        etat: EtatTheme = next((e for e in _PRIORITE if e in etats), "manque")
        motif = next(
            (v.motif for v in valeurs.values() if etat_de_valeur(v.etat) == etat), None
        )
        return sans_reponse(
            THEME, etat, motif, details={"absences": absences}, sources=sources
        )
    chiffres = [
        Chiffre(
            id=cle,
            libelle=f"Prix médian {libelle}",
            valeur=int(arrondi(servies[cle].valeur, 0)),
            unite="euro_per_m2",
            millesime=servies[cle].periode,
        )
        for cle, libelle, _ in _TYPES
        if cle in servies
    ]
    parties = [
        f"{nombre(int(arrondi(servies[cle].valeur, 0)))} €/m² pour {article}"
        for cle, _, article in _TYPES
        if cle in servies
    ]
    commune = contexte.identite.commune
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=f"Prix médian dans la commune : {', '.join(parties)}.",
        chiffres=chiffres,
        details={
            "commune": {"code_insee": commune.code_insee, "nom": commune.nom},
            "fenetre": millesime,
            "absences": absences,
        },
        sources=sources,
        pannes_partielles=[
            cle for cle, v in valeurs.items() if v.etat == "temporary_error"
        ],
    )


THEME = DefinitionTheme(
    id="prix_commune",
    section="marche",
    ordre=10,
    titre="Prix au m²",
    granularite=granularite("commune"),
    sources=("commune", "dvf"),
    lire=lire,
)
