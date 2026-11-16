"""« Revenus » : median disposable income of the inhabitants of the commune.

Granularity: commune (Filosofi, INSEE). ``app.revenus.queries.revenus_par_codes``:
the latest year. The verdict, the figures and the state are those of the
commune only.

The detail adds, each with its own millésime and state, the 200 m cell of
Filosofi holding the BAN point and the IRIS holding it (04.7-07):

- the cell is the one the Géoplateforme serves, **Filosofi 2015** (proved
  equal to the Insee file of 2015): a mean living standard per person, never
  set beside the commune's median as a comparison; an imputed cell
  (``i_car_est``) is not served. The cells of 2021 come with an import
  (04.7-07b);
- the IRIS is named, with the make-up of its population of 15 or more by
  socio-professional category (census, Melodi).

An outage of the cell or of the IRIS never changes the state of the theme:
it is named in ``pannes_partielles``.

Tone rule: neutre (a description of the commune, no valence).
"""

from typing import Any

from app.rapport.libelles import arrondi
from app.rapport.partage import (
    MILLESIME_CARREAUX,
    CarreauLu,
    IrisLu,
    PanneAmont,
    RevenusAmont,
    revenus_au_point,
)
from app.rapport.schemas import Chiffre, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    LignesCommunales,
    granularite,
    lignes_communales,
    nombre,
    sans_reponse,
    sources_de,
    theme,
)
from app.revenus.queries import revenus_par_codes

CHAMPS_COMMUNE = (
    "nb_menages_fiscaux",
    "revenu_q1",
    "revenu_q3",
    "revenu_d1",
    "revenu_d9",
    "indice_gini",
    "part_imposee",
)


def _panne() -> dict:
    return {"etat": "panne", "motif": "amont_indisponible"}


def _non_lu(contexte: ContexteRapport, cle: str) -> dict:
    hebergement = contexte.hosting.hostings[cle]
    if hebergement.etat != "heberge":
        return {"etat": "hors_couverture", "motif": hebergement.motif}
    return {"etat": "manque", "motif": "position_indeterminee"}


def _carreau(contexte: ContexteRapport, lu: object) -> dict:
    if lu is None:
        return _non_lu(contexte, "filosofi_carreaux")
    if not isinstance(lu, CarreauLu):
        return _panne()
    carreau = lu.carreau
    if carreau is None:
        return {"etat": "manque", "motif": lu.motif}
    if carreau.impute:
        return {"etat": "manque", "motif": "carreau_impute"}
    return {
        "etat": "ok",
        "id": carreau.id,
        "millesime": MILLESIME_CARREAUX,
        "individus": carreau.individus,
        "menages": carreau.menages,
        "niveau_vie_moyen": carreau.niveau_vie_moyen,
        "part_menages_pauvres": carreau.part_menages_pauvres,
    }


def _composition(lu: IrisLu) -> dict:
    if lu.panne_composition:
        return _panne()
    if lu.composition is None:
        return {
            "etat": "manque",
            "motif": lu.motif_composition or "composition_non_publiee",
        }
    return {
        "etat": "ok",
        "annee": lu.composition.annee,
        "population_15_ans_plus": lu.composition.population_15_ans_plus,
        "parts": lu.composition.parts,
    }


def _iris(contexte: ContexteRapport, lu: object) -> dict:
    if lu is None:
        return _non_lu(contexte, "iris")
    if not isinstance(lu, IrisLu):
        return _panne()
    if lu.iris is None:
        return {"etat": "manque", "motif": lu.motif}
    return {
        "etat": "ok",
        "code": lu.iris.code,
        "nom": lu.iris.nom,
        "type": lu.iris.type,
        "composition": _composition(lu),
    }


async def lire(contexte: ContexteRapport) -> Theme:
    amont = contexte.amonts.get(THEME.id)
    if not isinstance(amont, RevenusAmont):
        amont = RevenusAmont(carreau=PanneAmont(), iris=PanneAmont())
    carreau, iris = _carreau(contexte, amont.carreau), _iris(contexte, amont.iris)
    composition = iris.get("composition", {})
    pannes = [
        cle
        for cle, en_panne in (
            ("filosofi_carreaux", carreau["etat"] == "panne"),
            ("iris", iris["etat"] == "panne" or composition.get("etat") == "panne"),
        )
        if en_panne
    ]
    details: dict[str, Any] = {"filosofi_carreau": carreau, "iris": iris}
    millesimes = {
        "filosofi_carreaux": carreau.get("millesime"),
        "iris": composition.get("annee"),
    }
    lu = await lignes_communales(contexte, THEME, "revenus", revenus_par_codes)
    if isinstance(lu, Theme):
        return sans_reponse(
            THEME,
            lu.etat,
            lu.motif,
            details=details,
            sources=sources_de(THEME, millesimes),
            pannes_partielles=pannes,
        )
    details["territoire"] = lu.territoire
    return lu.situer(_mediane(lu, details, millesimes, pannes))


def _mediane(
    lu: LignesCommunales, details: dict[str, Any], millesimes: dict, pannes: list
) -> Theme:
    ligne = lu.lignes[0] if lu.lignes else None
    if ligne is None or ligne.get("revenu_median") is None:
        # Filosofi masks the small communes (statistical secrecy): a gap.
        return sans_reponse(
            THEME,
            "manque",
            "revenu_non_publie",
            verdict=f"Le revenu médian n'est pas publié pour {lu.lieu}.",
            details=details,
            sources=sources_de(THEME, {**millesimes, "revenus": lu.periode}),
            pannes_partielles=pannes,
        )
    annee = str(ligne["annee"])
    mediane = int(arrondi(ligne["revenu_median"], 0))
    chiffres = [
        Chiffre(
            id="revenu_median",
            libelle="Revenu médian par unité de consommation",
            valeur=mediane,
            unite="euro_par_an",
            millesime=annee,
        )
    ]
    if ligne.get("taux_pauvrete") is not None:
        chiffres.append(
            Chiffre(
                id="taux_pauvrete",
                libelle="Taux de pauvreté",
                valeur=float(arrondi(ligne["taux_pauvrete"], 2)),
                unite="pourcent",
                millesime=annee,
            )
        )
    details.update({champ: ligne.get(champ) for champ in CHAMPS_COMMUNE})
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=(
            f"Revenu médian des habitants de {lu.lieu} : {nombre(mediane)} € "
            f"par an et par unité de consommation ({annee})."
        ),
        chiffres=chiffres,
        details=details,
        sources=sources_de(THEME, {**millesimes, "revenus": annee}),
        pannes_partielles=pannes,
    )


THEME = DefinitionTheme(
    id="revenus",
    section="vivre",
    ordre=90,
    titre="Revenus",
    granularite=granularite("commune"),
    sources=("revenus", "filosofi_carreaux", "iris"),
    lire=lire,
    amont=revenus_au_point,
)
