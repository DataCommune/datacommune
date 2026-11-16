"""« Ventes de la parcelle » : DVF mutations of the parcel of the point.

Granularity: parcel. Landmark ② of the address page
(:func:`app.adresse.service.repere_ventes`): distinct mutations of the parcel
that contains the BAN point, read only for a ``unique`` parcel. The count and
the date of the last sale only: no price is served here (no price of an
isolated recent sale, C6), no sale is tied to a dwelling.

Tone rule: neutre (a count of sales, no valence).
"""

from typing import Any

from app.adresse.service import repere_ventes
from app.rapport.schemas import Chiffre, Rattachement, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    en_entier,
    etat_de_valeur,
    granularite,
    mois_annee,
    parcelle_unique,
    pluriel,
    sans_reponse,
    source,
    theme,
)


async def lire(contexte: ContexteRapport) -> Theme:
    dvf = contexte.hosting.hostings["dvf"]
    if dvf.etat != "heberge":
        return sans_reponse(THEME, "hors_couverture", dvf.motif)
    relation = contexte.identite.parcelle
    id_parcelle = parcelle_unique(THEME, relation)
    if isinstance(id_parcelle, Theme):
        return id_parcelle
    repere = await repere_ventes(contexte.session, relation, contexte.hosting)
    nombre_, derniere = repere.nombre, repere.derniere
    sources = [source("dvf", nombre_.periode)]
    rattachement = Rattachement(
        entite="parcelle_du_point", cle=id_parcelle, confiance="sure"
    )
    if nombre_.etat != "value":
        return sans_reponse(
            THEME,
            etat_de_valeur(nombre_.etat),
            nombre_.motif,
            rattachement=rattachement,
            sources=sources,
        )
    compte = en_entier(nombre_.valeur)
    details: dict[str, Any] = {"periode": nombre_.periode, "nombre": compte}
    if compte == 0:
        return theme(
            THEME,
            etat="zero",
            ton="neutre",
            verdict="Aucune vente enregistrée sur la parcelle de l'adresse dans DVF.",
            details=details,
            rattachement=rattachement,
            sources=sources,
        )
    chiffres = [
        Chiffre(
            id="nombre",
            libelle="Ventes sur la parcelle",
            valeur=compte,
            unite="count",
            millesime=nombre_.periode,
        )
    ]
    verdict = (
        f"{pluriel(compte, 'vente enregistrée', 'ventes enregistrées')} "
        "sur la parcelle de l'adresse"
    )
    if derniere.etat == "value":
        chiffres.append(
            Chiffre(
                id="derniere",
                libelle="Dernière vente",
                valeur=str(derniere.valeur),
                unite="date",
                millesime=derniere.periode,
            )
        )
        details["derniere"] = derniere.valeur
        verdict += f", la dernière en {mois_annee(str(derniere.valeur))}"
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=f"{verdict}.",
        chiffres=chiffres,
        details=details,
        rattachement=rattachement,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="ventes_parcelle",
    section="marche",
    ordre=20,
    titre="Ventes de la parcelle",
    granularite=granularite("parcelle"),
    sources=("dvf",),
    lire=lire,
    rattachement_entite="parcelle_du_point",
)
