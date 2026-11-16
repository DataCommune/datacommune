"""« Délinquance » : crimes and offences recorded in the commune, against
its department, the same year (SSMSI).

Granularity: commune (the arrondissement in Paris, Lyon and Marseille, as
the SSMSI publishes them, and the verdict names the zone read), never the
address. Rates are per 1 000 dwellings for burglaries and per 1 000
inhabitants otherwise. Under its diffusion threshold the SSMSI publishes no
value: a gap, never a zero, and its estimate (``complement_info``) is never
read.

Tone rule, on burglaries of dwellings: vigilance when the commune's rate
exceeds 1.5 times its department's; rassurant when it is under half of it;
neutre at the bounds, in between, or without a departmental rate.
"""

from app.rapport.libelles import arrondi
from app.rapport.partage import (
    DelinquanceLue,
    IndicateurDelinquance,
    delinquance_au_point,
)
from app.rapport.schemas import Chiffre, Theme, Ton
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    nombre,
    sans_reponse,
    sources_de,
    theme,
)

REFERENCE = "Cambriolages de logement"
# Indicator -> base of its rate.
INDICATEURS = {
    REFERENCE: "pour 1 000 logements",
    "Vols sans violence contre des personnes": "pour 1 000 habitants",
    "Violences physiques hors cadre familial": "pour 1 000 habitants",
    "Destructions et dégradations volontaires": "pour 1 000 habitants",
}
FORT, FAIBLE = 1.5, 0.5


def ton_delinquance(commune: float, departement: float | None) -> Ton:
    if departement is None:
        return "neutre"
    if commune > FORT * departement:
        return "vigilance"
    if commune < FAIBLE * departement:
        return "rassurant"
    return "neutre"


def _par_nom(liste: list[IndicateurDelinquance]) -> dict[str, IndicateurDelinquance]:
    return {i.indicateur: i for i in liste}


def _taux(valeur: float) -> str:
    return f"{nombre(valeur)} ‰"


async def lire(contexte: ContexteRapport) -> Theme:
    hebergement = contexte.hosting.hostings["delinquance"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources_de(THEME)
        )
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, DelinquanceLue):
        return sans_reponse(
            THEME, "panne", "amont_indisponible", sources=sources_de(THEME)
        )
    if lu.annee is None:
        return sans_reponse(
            THEME, "manque", "commune_absente", sources=sources_de(THEME)
        )
    sources = sources_de(THEME, {"delinquance": str(lu.annee)})
    commune, departement = _par_nom(lu.commune), _par_nom(lu.departement)
    niveau = "arrondissement" if lu.arrondissement else "commune"
    zone = "l'arrondissement" if lu.arrondissement else "la commune"
    details = {
        "annee": lu.annee,
        "zone": {"code": lu.zone, "niveau": niveau},
        "indicateurs": [
            {
                "indicateur": nom,
                "base": base,
                "taux_commune": commune[nom].taux_pour_mille
                if nom in commune
                else None,
                "nombre_commune": commune[nom].nombre if nom in commune else None,
                "diffuse": commune[nom].diffuse if nom in commune else False,
                "taux_departement": departement[nom].taux_pour_mille
                if nom in departement
                else None,
            }
            for nom, base in INDICATEURS.items()
        ],
    }
    reference = commune.get(REFERENCE)
    if reference is None or reference.taux_pour_mille is None:
        return sans_reponse(
            THEME,
            "manque",
            "non_diffuse",
            verdict=(
                f"Cambriolages de {lu.annee} non publiés pour {zone} : effectif "
                "sous le seuil de diffusion du SSMSI."
            ),
            details=details,
            sources=sources,
        )
    taux_dep = departement.get(REFERENCE)
    dep = taux_dep.taux_pour_mille if taux_dep else None
    verdict = (
        f"Cambriolages de logement en {lu.annee} : {_taux(reference.taux_pour_mille)}"
        f" dans {zone}"
    )
    verdict += f", {_taux(dep)} dans le département." if dep is not None else "."
    chiffres = [
        Chiffre(
            id="cambriolages_commune",
            libelle=f"Cambriolages pour 1 000 logements, {niveau}",
            valeur=float(arrondi(reference.taux_pour_mille, 1)),
            unite="pour_mille",
            millesime=str(lu.annee),
        )
    ]
    if dep is not None:
        chiffres.append(
            Chiffre(
                id="cambriolages_departement",
                libelle="Cambriolages pour 1 000 logements, département",
                valeur=float(arrondi(dep, 1)),
                unite="pour_mille",
                millesime=str(lu.annee),
            )
        )
    return theme(
        THEME,
        etat="ok" if reference.taux_pour_mille else "zero",
        ton=ton_delinquance(reference.taux_pour_mille, dep),
        verdict=verdict,
        chiffres=chiffres,
        details=details,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="delinquance",
    section="vivre",
    ordre=60,
    titre="Délinquance enregistrée",
    granularite=granularite("commune"),
    sources=("delinquance",),
    lire=lire,
    amont=delinquance_au_point,
)
