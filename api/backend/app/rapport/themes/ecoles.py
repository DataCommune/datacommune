"""« Écoles » : the nearest public schools by level, their social profile
(IPS) and added value, and the sector college of the address.

Granularity: distance to the BAN point, within 10 km (Annuaire de
l'éducation, open public schools). The IPS (indice de position sociale) is
the one of the latest school year served, the added value the one of the
latest exam session (brevet for a college, baccalauréat for a lycée). The
sector college comes from the school map of the department (``scolaire``),
matched on the street and number of the address.

Tone rule: neutre (an IPS or an added value describes a public, never a
quality grade); a gap when no public school of any level is within 10 km.
"""

import re

from app.adresse.blocs import lire_bloc
from app.rapport.partage import EcolesLues, Etablissement, ecoles_au_point
from app.rapport.schemas import Chiffre, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    nombre,
    sans_reponse,
    sources_de,
    theme,
)
from app.scolaire.queries import colleges_du_secteur

RAYON_M = 10_000
# Level -> its name and the word for « none » of its gender.
NIVEAUX = {
    "elementaire": ("école élémentaire", "aucune"),
    "college": ("collège", "aucun"),
    "lycee": ("lycée général", "aucun"),
}


def distance(metres: int) -> str:
    if metres < 1000:
        return f"{nombre(metres)} m"
    return f"{nombre(metres / 1000)} km"


def _etablissement(e: Etablissement) -> dict:
    return {
        "uai": e.uai,
        "nom": e.nom,
        "distance_m": e.distance_m,
        "ips": e.ips,
        "ips_rentree": e.ips_rentree,
        "ips_national_public": e.ips_national_public,
        "taux_reussite": e.taux_reussite,
        "valeur_ajoutee": e.valeur_ajoutee,
        "session": e.session,
    }


async def college_de_secteur_lu(contexte: ContexteRapport) -> tuple[str | None, bool]:
    """The UAI of the sector college of the address when the school map
    names exactly one (None otherwise: not hosted, no number, no match,
    several), and whether the school map was in outage."""
    if contexte.hosting.hostings["scolaire"].etat != "heberge":
        return None, False
    identite = contexte.identite
    code_insee, voie = identite.commune.code_insee, identite.nom_voie
    numero = re.match(r"\d+", identite.numero or "")
    if not (code_insee and voie and numero):
        return None, False
    numero_voie = int(numero.group())

    async def lecteur():
        return await colleges_du_secteur(
            contexte.session, code_insee, voie, numero_voie
        )

    bloc = await lire_bloc(contexte.session, ["scolaire"], lecteur)
    if bloc.en_panne:
        return None, True
    uais = bloc.valeur or []
    return (uais[0] if len(uais) == 1 else None), False


def verdict_ecoles(plus_proches: dict[str, Etablissement | None]) -> str:
    """At most 112 characters: every level fits whatever is missing."""
    parties = [
        f"{libelle} à {distance(e.distance_m)}"
        if (e := plus_proches.get(niveau))
        else f"{libelle} : {aucun} à 10 km"
        for niveau, (libelle, aucun) in NIVEAUX.items()
    ]
    return "Publics les plus proches : " + ", ".join(parties) + "."


async def lire(contexte: ContexteRapport) -> Theme:
    sources = sources_de(THEME, {"annuaire_education": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["annuaire_education"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources
        )
    if contexte.point is None:
        return sans_reponse(THEME, "manque", "position_indeterminee", sources=sources)
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, EcolesLues):
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    plus_proches: dict[str, Etablissement | None] = {
        niveau: next(iter(lu.par_niveau.get(niveau, [])), None) for niveau in NIVEAUX
    }
    secteur, panne_secteur = await college_de_secteur_lu(contexte)
    # An outage of the school map is said and keeps the report out of cache.
    pannes = ["scolaire"] if panne_secteur else []
    colleges = {e.uai: e for e in lu.par_niveau.get("college", [])}
    details = {
        niveau: _etablissement(e) if e else None for niveau, e in plus_proches.items()
    } | {
        "college_de_secteur": {
            "uai": secteur,
            "parmi_les_plus_proches": secteur in colleges,
            **(_etablissement(colleges[secteur]) if secteur in colleges else {}),
        }
        if secteur
        else None,
    }
    if not any(plus_proches.values()):
        return sans_reponse(
            THEME,
            "manque",
            "aucune_ecole_publique",
            verdict=(
                "Aucune école, aucun collège ni lycée général public à moins de 10 km."
            ),
            details=details,
            sources=sources,
            pannes_partielles=pannes,
        )
    elementaire = plus_proches["elementaire"]
    return theme(
        THEME,
        etat="ok",
        ton="neutre",
        verdict=verdict_ecoles(plus_proches),
        chiffres=[
            Chiffre(
                id="ecole_elementaire",
                libelle="École élémentaire publique la plus proche",
                valeur=elementaire.distance_m,
                unite="metre",
                millesime=contexte.as_of.isoformat(),
            )
        ]
        if elementaire
        else [],
        details=details,
        sources=sources,
        pannes_partielles=pannes,
    )


THEME = DefinitionTheme(
    id="ecoles",
    section="vivre",
    ordre=25,
    titre="Écoles publiques",
    granularite=granularite("rayon", RAYON_M),
    sources=("annuaire_education", "scolaire"),
    lire=lire,
    amont=ecoles_au_point,
)
