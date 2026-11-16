"""« Copropriété » : condominiums registered on the parcel of the point (RNIC).

Granularity: parcel. Exact join of the registry on the parcel that contains
the BAN point (``app.logements.queries.copropriete_par_parcelle``), never a
text match on the address, never the nearest parcel (N-02). A registration
declares up to three parcels and is found from any of them; what the
registry cannot know (a fourth parcel, a condominium never registered) is
said in the detail, found or not. No lot is tied
to a dwelling. Each registration says its manager (04.7-08): the name of a
professional manager (a company) only, never the one of a volunteer or
cooperative manager (a co-owner, a person), and its mandate as the registry
gives it. Mandate columns empty on every row mean « not imported yet ».

Tone rule: neutre (a registration says how the building is held, no valence).
"""

from datetime import date

from app.adresse.blocs import faits_de_provenance, lire_bloc, periode_de_source
from app.copropriete.syndic import nom_syndic_public, type_syndic
from app.logements.queries import copropriete_par_parcelle
from app.rapport.schemas import Chiffre, Rattachement, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    parcelle_unique,
    pluriel,
    sans_reponse,
    sources_de,
    theme,
)

ENTITE = "parcelle_du_point"
# One more registration is read: only a longer list is partial.
REGISTRES_MAX = 5
# The limits of the registry, beside every answer: a zero is « none
# registered on this parcel », never « no condominium ».
MENTION = (
    "Le registre cite au plus trois parcelles par copropriété ; "
    "une copropriété non immatriculée n'y figure pas."
)


# Mandate statuses of the registry, as the ETL writes them (lower case).
STATUTS_MANDAT = {
    "mandat en cours": "en_cours",
    "pas de mandat en cours": "aucun",
    "mandat expiré sans successeur déclaré": "expire_sans_successeur",
    "mandat expiré avec successeur déclaré": "expire_avec_successeur",
}


def _mandat(statut: object, fin: object) -> dict:
    """``hors_couverture`` while not imported (NULL), ``manque`` when the
    registry leaves it empty or says something else, else the status."""
    if statut is None:
        return {"etat": "hors_couverture", "motif": "mandat_non_importe"}
    code = STATUTS_MANDAT.get(str(statut))
    if code is None:
        motif = "mandat_non_renseigne" if statut == "non_connu" else "statut_inconnu"
        return {"etat": "manque", "motif": motif}
    return {
        "etat": "ok",
        "statut": code,
        "fin_dernier_mandat": fin.isoformat() if isinstance(fin, date) else None,
    }


def syndic_de(registre: dict) -> dict:
    """The manager of one registration, and its mandate."""
    return {
        "type": type_syndic(registre.get("type_syndic")),
        "nom": nom_syndic_public(
            registre.get("type_syndic"), registre.get("nom_syndic")
        ),
        "mandat": _mandat(
            registre.get("mandat_en_cours"), registre.get("date_fin_dernier_mandat")
        ),
    }


async def lire(contexte: ContexteRapport) -> Theme:
    hebergement = contexte.hosting.hostings["copropriete"]
    if hebergement.etat != "heberge":
        return sans_reponse(THEME, "hors_couverture", hebergement.motif)
    relation = contexte.identite.parcelle
    id_parcelle = parcelle_unique(THEME, relation)
    if isinstance(id_parcelle, Theme):
        return id_parcelle
    session = contexte.session
    bloc = await lire_bloc(
        session,
        ["copropriete"],
        lambda: copropriete_par_parcelle(
            session, id_parcelle, REGISTRES_MAX + 1, avec_gestion=True
        ),
    )
    if bloc.en_panne or bloc.valeur is None:
        return sans_reponse(THEME, "panne", bloc.panne)
    periode = periode_de_source(await faits_de_provenance(session, "copropriete"))
    sources = sources_de(THEME, {"copropriete": periode})
    rattachement = Rattachement(entite=ENTITE, cle=id_parcelle, confiance="sure")
    registres = bloc.valeur[:REGISTRES_MAX]
    details = {
        "registres": [
            {
                "numero_immatriculation": r.get("numero_immatriculation"),
                "nom": r.get("nom_copropriete"),
                "lots_total": r.get("nb_lots_total"),
                "lots_habitation": r.get("nb_lots_habitation_strict"),
                "periode_construction": r.get("periode_construction"),
                "syndic": syndic_de(r),
            }
            for r in registres
        ],
        "liste_incomplete": len(bloc.valeur) > REGISTRES_MAX,
        "mention": MENTION,
    }
    if not registres:
        return theme(
            THEME,
            etat="zero",
            ton="neutre",
            verdict="Aucune copropriété immatriculée sur la parcelle de l'adresse.",
            details=details,
            rattachement=rattachement,
            sources=sources,
        )
    chiffres = []
    if len(registres) == 1:
        lots, habitation = (
            registres[0].get("nb_lots_total"),
            registres[0].get("nb_lots_habitation_strict"),
        )
        if lots is not None and habitation is not None:
            verdict = (
                f"Copropriété immatriculée de {pluriel(int(lots), 'lot')}, "
                f"dont {int(habitation)} d'habitation."
            )
        else:
            verdict = "Copropriété immatriculée sur la parcelle de l'adresse."
        if lots is not None:
            chiffres.append(
                Chiffre(
                    id="lots",
                    libelle="Lots",
                    valeur=int(lots),
                    unite="count",
                    millesime=periode,
                )
            )
    else:
        verdict = (
            f"{pluriel(len(registres), 'copropriété', 'copropriétés')} "
            f"{'immatriculée' if len(registres) == 1 else 'immatriculées'}"
            " sur la parcelle de l'adresse."
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
    )


THEME = DefinitionTheme(
    id="copropriete",
    section="batiment",
    ordre=40,
    titre="Copropriété",
    granularite=granularite("parcelle"),
    sources=("copropriete",),
    lire=lire,
    rattachement_entite=ENTITE,
)
