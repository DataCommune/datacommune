"""« Parcelle » : the cadastral parcel that contains the BAN point.

Granularity: parcel. The parcel relation of the identity (one parcel
containing the point, never the nearest one, N-02) and its area, read by
« Bâtiment et parcelle » of the address page. A point outside every parcel or
on several parcels attributes nothing and says so.

Tone rule: neutre (a cadastral fact, no valence).
"""

from typing import Any

from app.adresse.lieu import FIELD_CONTENANCE
from app.rapport.partage import batiment_parcelle, champ
from app.rapport.schemas import Chiffre, Rattachement, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    en_entier,
    granularite,
    nombre,
    parcelle_unique,
    sources_de,
    theme,
)


async def lire(contexte: ContexteRapport) -> Theme:
    relation = contexte.identite.parcelle
    id_parcelle = parcelle_unique(THEME, relation)
    if isinstance(id_parcelle, Theme):
        return id_parcelle
    contenance = champ(await batiment_parcelle(contexte), FIELD_CONTENANCE)
    rattachement = Rattachement(
        entite="parcelle_du_point", cle=id_parcelle, confiance="sure"
    )
    millesime = contenance.periode if contenance is not None else None
    details: dict[str, Any] = {"identifiant": id_parcelle}
    chiffres = []
    if contenance is not None and contenance.etat == "value":
        surface = en_entier(contenance.valeur)
        details["contenance_m2"] = surface
        chiffres.append(
            Chiffre(
                id="contenance",
                libelle="Surface cadastrale",
                valeur=surface,
                unite="m2",
                millesime=millesime,
            )
        )
        # A plot often gathers several parcels and no open source says which
        # ones belong together: the area is never read as the one of the plot.
        verdict = (
            "Le point de l'adresse est sur une parcelle cadastrale de "
            f"{nombre(surface)} m² ; un terrain peut en réunir plusieurs."
        )
    else:
        details["contenance_etat"] = contenance.etat if contenance else None
        verdict = "Le point de l'adresse est sur une seule parcelle cadastrale."
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=verdict,
        chiffres=chiffres,
        details=details,
        rattachement=rattachement,
        sources=sources_de(THEME, {"cadastre": millesime}),
    )


THEME = DefinitionTheme(
    id="parcelle",
    section="marche",
    ordre=30,
    titre="Parcelle",
    granularite=granularite("parcelle"),
    sources=("cadastre",),
    lire=lire,
    rattachement_entite="parcelle_du_point",
)
