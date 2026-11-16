"""« Argiles » : clay shrink-swell exposure at the BAN point (Géorisques RGA).

Granularity: address (measured at the point, live). Landmark ③ of the
address page (:func:`app.adresse.risques.repere_argiles_au_point`), on the
shared upstream read.

Tone rule: vigilance for a strong or medium exposure; rassurant for a null
exposure; neutre for a weak exposure or any other label.
"""

from app.adresse.schemas import ValeurLieu
from app.rapport.partage import argiles
from app.rapport.schemas import Theme, Ton
from app.rapport.socle import (
    ContexteAmont,
    ContexteRapport,
    DefinitionTheme,
    etat_de_valeur,
    granularite,
    sans_reponse,
    sources_de,
    theme,
)


def ton_argiles(exposition: str) -> Ton:
    texte = exposition.strip().lower()
    if any(
        mot in texte for mot in ("nul", "pas d", "aucun", "non expos", "sans expos")
    ):
        return "rassurant"
    if "fort" in texte or "moyen" in texte:
        return "vigilance"
    return "neutre"


async def lire(contexte: ContexteRapport) -> Theme:
    valeur = contexte.amonts.get("argiles")
    sources = sources_de(THEME, {"georisques": contexte.as_of.isoformat()})
    if not isinstance(valeur, ValeurLieu):
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    if valeur.etat != "value":
        verdict = (
            "Géorisques ne publie pas d'exposition aux argiles à ce point."
            if valeur.etat == "not_reported"
            else None
        )
        return sans_reponse(
            THEME,
            etat_de_valeur(valeur.etat),
            valeur.motif,
            verdict=verdict,
            sources=sources,
        )
    exposition = str(valeur.valeur).strip()
    libelle = exposition.lower()
    if not libelle.startswith("exposition"):
        libelle = f"exposition {libelle}"
    return theme(
        THEME,
        etat="ok",
        ton=ton_argiles(exposition),
        verdict=f"Retrait-gonflement des argiles au point de l'adresse : {libelle}.",
        details={"exposition": exposition},
        sources=sources,
    )


async def amont(contexte: ContexteAmont) -> ValeurLieu:
    return await argiles(contexte)


THEME = DefinitionTheme(
    id="argiles",
    section="risques",
    ordre=10,
    titre="Argiles",
    granularite=granularite("adresse"),
    sources=("georisques",),
    lire=lire,
    amont=amont,
)
