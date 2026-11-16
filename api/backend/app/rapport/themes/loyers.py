"""« Loyers » : asking rent per m² of the commune (ANIL rent map).

Granularity: commune. ``app.loyers.queries.loyers_par_codes``: the latest
year per housing type. The value is served for the commune but may be
estimated from a wider area (``type_prediction``: commune, EPCI or mesh):
the verdict then says so, and the details carry the scale and the mesh
(``id_zone``) of each housing type. Mandatory credit of the producer in
``details``.

Tone rule: neutre (a reference rent, no valence).
"""

from app.loyers.queries import loyers_par_codes
from app.rapport.libelles import arrondi
from app.rapport.schemas import VERDICT_MAX, Chiffre, Theme
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

_TYPES = (("appartement", "un appartement"), ("maison", "une maison"))
MENTION = "Estimations ANIL, à partir des données du Groupe SeLoger et de leboncoin"


def _loyer(ligne: dict) -> float:
    """The rent the commune fiche writes: one decimal, the precision of its
    contract (``loyers.loyer_m2_*``), rounded the same way."""
    return float(arrondi(ligne["loyer_m2"], 1))


def _verdict(parties: list[str], lignes: list[dict], lieu: str) -> str:
    """Says when a value is estimated on a wider area than *lieu*; the first
    wording within the length of a verdict."""
    echelles = {ligne.get("type_prediction") for ligne in lignes}
    valeurs = ", ".join(parties)
    if echelles <= {"commune", None}:
        candidats = [
            f"Loyer d'annonce estimé dans {lieu} : {valeurs}.",
            f"Loyer d'annonce estimé dans {lieu}.",
        ]
    else:
        candidats = [
            f"Loyer d'annonce estimé pour {lieu} à partir d'un territoire plus "
            f"large : {valeurs}.",
            f"Loyer d'annonce estimé pour {lieu} à partir d'un territoire plus large.",
        ]
    return next(c for c in candidats if len(c) <= VERDICT_MAX)


async def lire(contexte: ContexteRapport) -> Theme:
    lu = await lignes_communales(contexte, THEME, "loyers", loyers_par_codes)
    if isinstance(lu, Theme):
        return lu
    return lu.situer(await _servir(contexte, lu))


async def _servir(contexte: ContexteRapport, lu: LignesCommunales) -> Theme:
    par_type = {
        ligne["type_habitat"]: ligne
        for ligne in lu.lignes
        if ligne.get("loyer_m2") is not None
    }
    servis = [(cle, article) for cle, article in _TYPES if cle in par_type]
    if not servis:
        return sans_reponse(
            THEME,
            "manque",
            "loyer_non_publie",
            verdict=f"Aucun loyer estimé n'est publié pour {lu.lieu}.",
            sources=sources_de(THEME, {"loyers": lu.periode}),
        )
    annee = str(par_type[servis[0][0]]["annee"])
    chiffres = [
        Chiffre(
            id=cle,
            libelle=f"Loyer {cle}",
            valeur=_loyer(par_type[cle]),
            unite="euro_per_m2_mois",
            millesime=str(par_type[cle]["annee"]),
        )
        for cle, _ in servis
    ]
    parties = [
        f"{nombre(_loyer(par_type[cle]))} €/m² pour {article}"
        for cle, article in servis
    ]
    details = {
        "territoire": lu.territoire,
        "mention": MENTION,
        "types": {
            cle: {
                champ: ligne.get(champ)
                for champ in (
                    "loyer_m2_bas",
                    "loyer_m2_haut",
                    "type_prediction",
                    "nb_observations",
                    "r2_ajuste",
                    "charges_comprises",
                    "annee",
                    "id_zone",
                )
            }
            for cle, ligne in par_type.items()
        },
    }
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=_verdict(parties, [par_type[cle] for cle, _ in servis], lu.lieu),
        chiffres=chiffres,
        details=details,
        sources=sources_de(THEME, {"loyers": lu.periode or annee}),
    )


THEME = DefinitionTheme(
    id="loyers",
    section="marche",
    ordre=50,
    titre="Loyers",
    granularite=granularite("commune"),
    sources=("loyers",),
    lire=lire,
)
