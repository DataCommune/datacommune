"""« Fibre » : broadband eligibility published for exactly this address (ARCEP).

Granularity: address. « Réseaux et transports » of the address page
(:func:`app.adresse.lieu.theme_reseaux_transports`), read once: the offers
published for this number, suffix and street of the commune, never a nearby
address. A guarded journaled source: a refused publication is an outage of
this row only.

Fibre is the ``FO`` technology of the file only. A cable network (``COAX``)
of 100 Mbit/s or more, often sold as « fibre » by a local operator, is said
as what the file declares: no fibre to the home, a fast cable network. Its
offers are read a second time, only for an address without fibre.

Tone rule: rassurant when the address is eligible to fibre; neutre when it
has no fibre but a cable network of 100 Mbit/s or more; vigilance when
offers are published for it with neither.
"""

from app.adresse.blocs import lire_bloc
from app.adresse.lieu import FIELD_DEBIT, FIELD_FIBRE
from app.connectivite.queries import eligibilite_adresse_exacte
from app.rapport.partage import champ, reseaux_transports
from app.rapport.schemas import Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    etat_de_valeur,
    granularite,
    sans_reponse,
    sources_de,
    theme,
)

VERDICTS_MANQUE = {
    "adresse_non_verifiable": (
        "L'adresse ne peut pas être vérifiée dans les données d'éligibilité."
    ),
}
# ARCEP speed classes of 100 Mbit/s or more.
DEBITS_CABLE_RAPIDE = frozenset({"THD100", "THD1G"})


async def _cable_rapide(contexte: ContexteRapport) -> bool:
    """Whether a cable offer of 100 Mbit/s or more is published for exactly
    this address; False on an outage (an unread offer lifts no vigilance)."""
    session, identite = contexte.session, contexte.identite
    numero, nom_voie = identite.numero, identite.nom_voie
    # Same guard as the fibre field of the address page: without a number or a
    # street the address cannot be matched, so no cable offer is established.
    if not numero or not nom_voie:
        return False
    bloc = await lire_bloc(
        session,
        ["connectivite"],
        lambda: eligibilite_adresse_exacte(
            session,
            identite.commune.code_insee,
            numero=numero,
            rep=identite.rep,
            nom_voie=nom_voie,
        ),
    )
    if bloc.en_panne or bloc.valeur is None:
        return False
    return any(
        ligne["technologie"] == "COAX" and ligne["debit_max"] in DEBITS_CABLE_RAPIDE
        for ligne in bloc.valeur.lignes
    )


async def lire(contexte: ContexteRapport) -> Theme:
    lu = await reseaux_transports(contexte)
    fibre = champ(lu, FIELD_FIBRE)
    if fibre is None:
        return sans_reponse(THEME, "panne", lu.motif)
    sources = sources_de(THEME, {"connectivite": fibre.periode})
    if fibre.etat == "not_reported":
        return sans_reponse(
            THEME,
            "manque",
            "aucune_offre_publiee",
            verdict="Aucune offre internet fixe n'est publiée pour cette adresse.",
            sources=sources,
        )
    if fibre.etat != "value":
        return sans_reponse(
            THEME,
            etat_de_valeur(fibre.etat),
            fibre.motif,
            verdict=VERDICTS_MANQUE.get(fibre.motif or ""),
            sources=sources,
        )
    debit = champ(lu, FIELD_DEBIT)
    details = {"debit_max": debit.valeur if debit is not None else None}
    if fibre.valeur:
        return theme(
            THEME,
            etat="ok",
            ton="rassurant",
            verdict="Adresse éligible à la fibre optique.",
            details=details,
            sources=sources,
        )
    if await _cable_rapide(contexte):
        return theme(
            THEME,
            etat="ok",
            ton="neutre",
            verdict=(
                "Pas de fibre jusqu'au logement publiée pour cette adresse ; "
                "un réseau câblé à très haut débit l'est."
            ),
            details=details,
            sources=sources,
        )
    return theme(
        THEME,
        etat="ok",
        ton="vigilance",
        verdict=(
            "Pas d'offre fibre publiée pour cette adresse ; "
            "d'autres technologies le sont."
        ),
        details=details,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="fibre",
    section="vivre",
    ordre=10,
    titre="Fibre",
    granularite=granularite("adresse"),
    sources=("connectivite",),
    lire=lire,
)
