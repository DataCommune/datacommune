"""« Taxe foncière » : communal rates of the local taxes (DGFiP).

Granularity: commune. ``app.fiscalite.queries.fiscalite_par_codes``: the
latest year. The global rate on built property (commune, intercommunality
and additional taxes: ``Taux_Global_TFB`` of the source, never the rate the
commune votes alone) is the figure. The details hold the plain mean of the
same rate over the communes of the departement (04.7-08), served only when
the whole departement is hosted and every commune of the year has a rate,
the surcharge of the tax on secondary
residences, the household waste rate (TEOM) and the other rates.

Tone rule: neutre (comparing a rate is not judging it).
"""

from app.adresse.blocs import lire_bloc
from app.fiscalite.queries import fiscalite_par_codes, moyenne_departement
from app.rapport.libelles import arrondi, nombre_fr
from app.rapport.schemas import Chiffre, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    LignesCommunales,
    granularite,
    lignes_communales,
    sans_reponse,
    sources_de,
    theme,
)


async def _moyenne(contexte: ContexteRapport, ligne: dict, taux: float) -> dict:
    """The plain mean of the departement, with its state."""
    departement = ligne.get("code_departement")
    hebergement = contexte.hosting.hostings["fiscalite"]
    if not departement or departement not in hebergement.departements:
        return {"etat": "hors_couverture", "motif": "departement_non_heberge"}
    session = contexte.session
    annee = int(ligne["annee"])
    bloc = await lire_bloc(
        session,
        ["fiscalite"],
        lambda: moyenne_departement(session, departement, annee),
    )
    if bloc.en_panne or bloc.valeur is None:
        return {"etat": "panne", "motif": bloc.panne or "lecture_indisponible"}
    communes = int(bloc.valeur.get("communes") or 0)
    if communes < 2:
        return {"etat": "non_concerne", "motif": "seule_commune_du_departement"}
    if int(bloc.valeur.get("taux") or 0) < communes or bloc.valeur["moyenne"] is None:
        # A mean over part of the communes would not be the departement's.
        return {"etat": "manque", "motif": "taux_manquants_au_departement"}
    moyenne = float(arrondi(bloc.valeur["moyenne"], 2))
    return {
        "etat": "ok",
        "departement": departement,
        "nom_departement": ligne.get("nom_departement"),
        "moyenne_simple": moyenne,
        "communes": communes,
        "ecart_points": float(arrondi(taux - moyenne, 2)),
        "annee": annee,
    }


def _majoration(ligne: dict) -> dict:
    majoration = ligne.get("majoration_thrs")
    if majoration is None:
        return {"etat": "hors_couverture", "motif": "majoration_non_importee"}
    taux = ligne.get("taux_majoration_thrs")
    return {
        "etat": "ok",
        "majoree": bool(majoration),
        "taux": float(taux) if majoration and taux is not None else None,
    }


async def lire(contexte: ContexteRapport) -> Theme:
    lu = await lignes_communales(contexte, THEME, "fiscalite", fiscalite_par_codes)
    if isinstance(lu, Theme):
        return lu
    return lu.situer(await _servir(contexte, lu))


def _dans(lieu: str) -> str:
    """« », « à Paris », « dans l'arrondissement »: where the rate is read."""
    if lieu == "la commune":
        return ""
    return " dans l'arrondissement" if lieu == "l'arrondissement" else f" à {lieu}"


async def _servir(contexte: ContexteRapport, lu: LignesCommunales) -> Theme:
    ligne = lu.lignes[0] if lu.lignes else None
    if ligne is None or ligne.get("taux_tfpb") is None:
        return sans_reponse(
            THEME,
            "manque",
            "taux_non_publie",
            verdict=f"Aucun taux de taxe foncière n'est publié pour {lu.lieu}.",
            sources=sources_de(THEME, {"fiscalite": lu.periode}),
        )
    annee = str(ligne["annee"])
    taux = float(arrondi(ligne["taux_tfpb"], 2))
    moyenne = await _moyenne(contexte, ligne, taux)
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=(
            f"Taux global de taxe foncière sur le bâti{_dans(lu.lieu)} (commune, "
            "intercommunalité "
            f"et taxes spéciales) : {nombre_fr(taux)} % en {annee}."
        ),
        chiffres=[
            Chiffre(
                id="taux_tfpb",
                libelle="Taux global de taxe foncière (bâti)",
                valeur=taux,
                unite="pourcent",
                millesime=annee,
            )
        ],
        details={
            "territoire": lu.territoire,
            "taux_tfpb": ligne.get("taux_tfpb"),
            "taux_tfpnb": ligne.get("taux_tfpnb"),
            "taux_th_rs": ligne.get("taux_th_rs"),
            "taux_teom": ligne.get("taux_teom"),
            "nom_epci": ligne.get("nom_epci"),
            "annee": ligne.get("annee"),
            "moyenne_departement": moyenne,
            "majoration_thrs": _majoration(ligne),
        },
        sources=sources_de(THEME, {"fiscalite": annee}),
        pannes_partielles=["fiscalite"] if moyenne["etat"] == "panne" else [],
    )


THEME = DefinitionTheme(
    id="fiscalite",
    section="marche",
    ordre=70,
    titre="Taxe foncière",
    granularite=granularite("commune"),
    sources=("fiscalite",),
    lire=lire,
)
