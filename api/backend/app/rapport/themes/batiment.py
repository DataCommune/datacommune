"""« Bâtiment » : the building at the BAN point and the buildings of the parcel.

Granularity: building. « Bâtiment et parcelle » of the address page
(:func:`app.adresse.lieu.theme_batiment_parcelle`), read once: the count of
the buildings on the parcel that contains the point (BD TOPO, every building),
the height and floors of the building whose geometry covers the point, only
when exactly one does, and the RNB identifier the RNB links to the BAN key.
No dwelling and no lot is derived; without a single parcel nothing is
attributed.

Tone rule: neutre (a description of the building, no valence).
"""

from app.adresse.lieu import (
    FIELD_BATIMENTS,
    FIELD_BATIMENTS_HORS_ANNEXE,
    FIELD_ETAGES,
    FIELD_HAUTEUR,
    FIELD_RNB,
    MOTIF_AUCUN_BATIMENT,
)
from app.adresse.schemas import ValeurTheme
from app.rapport.partage import batiment_parcelle, champ
from app.rapport.schemas import Chiffre, Rattachement, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    en_entier,
    en_reel,
    etat_de_valeur,
    granularite,
    nombre,
    parcelle_unique,
    pluriel,
    sans_reponse,
    source,
    theme,
)

ENTITE = "batiment_du_point"


def _rnb(valeur: ValeurTheme | None) -> dict:
    if valeur is None:
        return {"etat": None}
    return {"etat": valeur.etat, "identifiant": valeur.valeur, "motif": valeur.motif}


def _description(hauteur: ValeurTheme | None, etages: ValeurTheme | None) -> str:
    morceaux = []
    if hauteur is not None and hauteur.etat == "value":
        morceaux.append(f"{nombre(en_reel(hauteur.valeur))} m")
    if etages is not None and etages.etat == "value":
        morceaux.append(pluriel(en_entier(etages.valeur), "étage"))
    return f"Bâtiment de {' et '.join(morceaux)}" if morceaux else "Un bâtiment"


async def lire(contexte: ContexteRapport) -> Theme:
    relation = contexte.identite.parcelle
    lu = await batiment_parcelle(contexte)
    champ_rnb = champ(lu, FIELD_RNB)
    rnb = _rnb(champ_rnb)
    panne_rnb = (
        ["rnb"] if champ_rnb is not None and champ_rnb.etat == "temporary_error" else []
    )
    id_parcelle = parcelle_unique(THEME, relation)
    if isinstance(id_parcelle, Theme):
        return id_parcelle.model_copy(
            update={"details": {"rnb": rnb}, "pannes_partielles": panne_rnb}
        )
    compte = champ(lu, FIELD_BATIMENTS)
    if compte is None or compte.etat != "value":
        etat = etat_de_valeur(compte.etat) if compte is not None else "manque"
        return sans_reponse(
            THEME,
            etat,
            compte.motif if compte else None,
            details={"rnb": rnb},
            pannes_partielles=panne_rnb,
        )
    total = en_entier(compte.valeur)
    hors_annexe = champ(lu, FIELD_BATIMENTS_HORS_ANNEXE)
    hauteur = champ(lu, FIELD_HAUTEUR)
    etages = champ(lu, FIELD_ETAGES)
    millesime = compte.periode
    details = {
        "batiments_parcelle": total,
        "batiments_parcelle_hors_annexe": hors_annexe.valeur if hors_annexe else None,
        "hauteur_etat": hauteur.etat if hauteur else None,
        "hauteur_motif": hauteur.motif if hauteur else None,
        "rnb": rnb,
    }
    # The RNB identifier is read in the imported RNB links export.
    sources = [
        source("batiments", millesime),
        source("rnb", champ_rnb.periode if champ_rnb else None, mode="import"),
    ]
    pannes = [
        cle
        for cle, valeur in (("hauteur", hauteur), ("rnb", champ_rnb))
        if valeur is not None and valeur.etat == "temporary_error"
    ]
    if total == 0:
        return theme(
            THEME,
            etat="zero",
            ton="neutre",
            verdict="Aucun bâtiment recensé sur la parcelle de l'adresse.",
            details=details,
            rattachement=Rattachement(
                entite=ENTITE, confiance="incertaine", motif=MOTIF_AUCUN_BATIMENT
            ),
            sources=sources,
            pannes_partielles=pannes,
        )
    sur_parcelle = pluriel(total, "bâtiment")
    if hauteur is not None and hauteur.etat in ("value", "not_reported"):
        verdict = (
            f"{_description(hauteur, etages)} au point de l'adresse ; "
            f"{sur_parcelle} sur la parcelle."
        )
        rattachement = Rattachement(entite=ENTITE, confiance="sure")
    elif hauteur is not None and hauteur.motif == MOTIF_AUCUN_BATIMENT:
        verdict = (
            "Aucun bâtiment ne contient le point de l'adresse ; "
            f"{sur_parcelle} sur la parcelle."
        )
        rattachement = Rattachement(
            entite=ENTITE, confiance="incertaine", motif=MOTIF_AUCUN_BATIMENT
        )
    else:
        verdict = (
            "Le bâtiment de l'adresse n'est pas identifié ; "
            f"{sur_parcelle} sur la parcelle."
        )
        rattachement = Rattachement(
            entite=ENTITE,
            confiance="incertaine",
            motif=hauteur.motif if hauteur is not None else None,
        )
    chiffres = [
        Chiffre(
            id="batiments_parcelle",
            libelle="Bâtiments sur la parcelle",
            valeur=total,
            unite="count",
            millesime=millesime,
        )
    ]
    if hauteur is not None and hauteur.etat == "value":
        chiffres.append(
            Chiffre(
                id="hauteur",
                libelle="Hauteur",
                valeur=en_reel(hauteur.valeur),
                unite="metre",
                millesime=millesime,
            )
        )
    if etages is not None and etages.etat == "value":
        chiffres.append(
            Chiffre(
                id="etages",
                libelle="Étages",
                valeur=en_entier(etages.valeur),
                unite="count",
                millesime=millesime,
            )
        )
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=verdict,
        chiffres=chiffres,
        details=details,
        rattachement=rattachement,
        sources=sources,
        pannes_partielles=pannes,
    )


THEME = DefinitionTheme(
    id="batiment",
    section="batiment",
    ordre=20,
    titre="Bâtiment",
    granularite=granularite("batiment"),
    sources=("batiments", "rnb"),
    lire=lire,
    rattachement_entite=ENTITE,
)
