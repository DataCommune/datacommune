"""« Équipements » : shops, health and schools within a kilometre (INSEE BPE).

Granularity: radius of 1 km around the BAN point (geodesic). The facilities
of the permanent facilities base are counted by domain, without LIMIT, with
the rule of the commune view (``v_commune_equipements``): the source-row
weights (``nb_equipements``) of the latest year of each commune, and no total
when a weight is unknown or negative (a gap, never a guess). A radius that
reaches a departement the instance does not host is a partial count (C16),
never a complete one.

Tone rule: rassurant when shops, health and teaching facilities are all
present within the radius; neutre otherwise, and when none is (a zero).
"""

from sqlalchemy import text

from app.adresse.blocs import (
    MOTIF_HORS_PERIMETRE,
    faits_de_provenance,
    lire_bloc,
    periode_de_source,
    rayon_deborde,
)
from app.rapport.schemas import Chiffre, Theme, Ton
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    nombre,
    sans_reponse,
    sources_de,
    theme,
)

RAYON_M = 1000
ESSENTIELS = (
    ("commerces", "commerces"),
    ("sante_social", "santé"),
    ("enseignement", "enseignement"),
)

# Bounding box on the geometry index first, then the geodesic distance; the
# margin (degrees) covers the radius up to 60° of latitude (as C16). The
# latest year is read per commune of the radius (index on code_commune).
_COMPTES_PAR_DOMAINE = text("""
    WITH proches AS (
        SELECT code_commune, annee, coalesce(domaine, '') AS domaine,
               nb_equipements
        FROM equipements
        WHERE geom IS NOT NULL
          AND geom && ST_Expand(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :marge)
          AND ST_DWithin(
              geom::geography,
              ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
              :rayon
          )
    ), derniere AS (
        SELECT code_commune, max(annee) AS annee
        FROM equipements
        WHERE code_commune IN (SELECT code_commune FROM proches)
        GROUP BY code_commune
    )
    SELECT p.domaine,
           count(*) AS lignes,
           count(p.nb_equipements) AS poids_connus,
           min(p.nb_equipements) AS poids_min,
           coalesce(sum(p.nb_equipements), 0) AS nombre
    FROM proches p
    JOIN derniere d ON d.code_commune = p.code_commune AND d.annee = p.annee
    GROUP BY p.domaine
""")


def ton_equipements(comptes: dict[str, int]) -> Ton:
    if all(comptes.get(domaine, 0) > 0 for domaine, _ in ESSENTIELS):
        return "rassurant"
    return "neutre"


async def lire(contexte: ContexteRapport) -> Theme:
    hebergement = contexte.hosting.hostings["equipements"]
    if hebergement.etat != "heberge":
        return sans_reponse(THEME, "hors_couverture", hebergement.motif)
    point = contexte.point
    if point is None:
        return sans_reponse(THEME, "manque", "position_indeterminee")
    session = contexte.session

    async def lecteur():
        result = await session.execute(
            _COMPTES_PAR_DOMAINE,
            {
                "lon": point.lon,
                "lat": point.lat,
                "rayon": RAYON_M,
                "marge": RAYON_M / 50_000,
            },
        )
        lignes = result.all()
        complets = all(
            ligne.lignes == ligne.poids_connus and ligne.poids_min >= 0
            for ligne in lignes
        )
        comptes = (
            {ligne.domaine: int(ligne.nombre) for ligne in lignes} if complets else None
        )
        deborde = await rayon_deborde(
            session, point.lon, point.lat, RAYON_M, hebergement.departements
        )
        return comptes, deborde

    bloc = await lire_bloc(session, ["equipements"], lecteur)
    if bloc.en_panne or bloc.valeur is None:
        return sans_reponse(THEME, "panne", bloc.panne)
    comptes, deborde = bloc.valeur
    periode = periode_de_source(await faits_de_provenance(session, "equipements"))
    sources = sources_de(THEME, {"equipements": periode})
    if comptes is None:
        return sans_reponse(THEME, "manque", "poids_incomplets", sources=sources)
    total = sum(comptes.values())
    details = {
        "par_domaine": comptes,
        "liste_incomplete": deborde,
        "motif": MOTIF_HORS_PERIMETRE if deborde else None,
    }
    if total == 0:
        if deborde:
            return sans_reponse(
                THEME,
                "hors_couverture",
                MOTIF_HORS_PERIMETRE,
                details=details,
                sources=sources,
            )
        return theme(
            THEME,
            etat="zero",
            ton="neutre",
            verdict="Aucun équipement recensé à moins d'1 km.",
            details=details,
            sources=sources,
        )
    parties = ", ".join(
        f"{nombre(comptes.get(domaine, 0))} {libelle}"
        for domaine, libelle in ESSENTIELS
    )
    prefixe = "Au moins " if deborde else ""
    return theme(
        THEME,
        etat="ok",
        ton=ton_equipements(comptes),
        verdict=f"{prefixe}{nombre(total)} équipements à moins d'1 km, dont {parties}.",
        chiffres=[
            Chiffre(
                id=domaine,
                libelle=libelle.capitalize(),
                valeur=comptes.get(domaine, 0),
                unite="count",
                millesime=periode,
            )
            for domaine, libelle in ESSENTIELS
        ],
        details=details,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="equipements",
    section="vivre",
    ordre=30,
    titre="Équipements",
    granularite=granularite("rayon", RAYON_M),
    sources=("equipements",),
    lire=lire,
)
