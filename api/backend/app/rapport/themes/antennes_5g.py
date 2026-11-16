"""« Réseau mobile 5G » : 5G antenna supports within a kilometre (ANFR).

Granularity: radius of 1 km around the BAN point (geodesic). Supports are
counted once (``numero_support``), every operator and band together, without
LIMIT. A support is not a coverage measure: the operators' coverage maps
come later. A radius that reaches a departement the instance does not host
is a partial count (C16).

Tone rule: rassurant when at least one 5G support is within the radius;
neutre otherwise (a zero when none is).
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
    pluriel,
    sans_reponse,
    sources_de,
    theme,
)

RAYON_M = 1000

# Bounding box on the geometry index first, then the geodesic distance.
_SUPPORTS = text("""
    SELECT count(DISTINCT numero_support) AS supports,
           count(DISTINCT numero_support) FILTER (
               WHERE generation ILIKE '5G%'
           ) AS supports_5g
    FROM antennes
    WHERE geom IS NOT NULL
      AND geom && ST_Expand(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :marge)
      AND ST_DWithin(
          geom::geography,
          ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
          :rayon
      )
""")


def ton_antennes(supports_5g: int) -> Ton:
    return "rassurant" if supports_5g > 0 else "neutre"


async def lire(contexte: ContexteRapport) -> Theme:
    hebergement = contexte.hosting.hostings["antennes"]
    if hebergement.etat != "heberge":
        return sans_reponse(THEME, "hors_couverture", hebergement.motif)
    point = contexte.point
    if point is None:
        return sans_reponse(THEME, "manque", "position_indeterminee")
    session = contexte.session

    async def lecteur():
        ligne = (
            await session.execute(
                _SUPPORTS,
                {
                    "lon": point.lon,
                    "lat": point.lat,
                    "rayon": RAYON_M,
                    "marge": RAYON_M / 50_000,
                },
            )
        ).one()
        deborde = await rayon_deborde(
            session, point.lon, point.lat, RAYON_M, hebergement.departements
        )
        return int(ligne.supports), int(ligne.supports_5g), deborde

    bloc = await lire_bloc(session, ["antennes"], lecteur)
    if bloc.en_panne or bloc.valeur is None:
        return sans_reponse(THEME, "panne", bloc.panne)
    supports, supports_5g, deborde = bloc.valeur
    periode = periode_de_source(await faits_de_provenance(session, "antennes"))
    sources = sources_de(THEME, {"antennes": periode})
    details = {
        "supports": supports,
        "supports_5g": supports_5g,
        "liste_incomplete": deborde,
        "motif": MOTIF_HORS_PERIMETRE if deborde else None,
    }
    if supports_5g == 0:
        if deborde:
            return sans_reponse(
                THEME,
                "hors_couverture",
                MOTIF_HORS_PERIMETRE,
                details=details,
                sources=sources,
            )
        autres = (
            f" ({pluriel(supports, 'support')} d'autres générations)"
            if supports
            else ""
        )
        return theme(
            THEME,
            etat="zero",
            ton="neutre",
            verdict=f"Aucun support d'antenne 5G à moins d'1 km{autres}.",
            details=details,
            sources=sources,
        )
    prefixe = "Au moins " if deborde else ""
    return theme(
        THEME,
        etat="ok",
        ton=ton_antennes(supports_5g),
        verdict=(
            f"{prefixe}{nombre(supports_5g)} "
            f"{'support' if supports_5g == 1 else 'supports'} d'antennes 5G"
            " à moins d'1 km."
        ),
        chiffres=[
            Chiffre(
                id="supports_5g",
                libelle="Supports 5G",
                valeur=supports_5g,
                unite="count",
                millesime=periode,
            )
        ],
        details=details,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="antennes_5g",
    section="vivre",
    ordre=40,
    titre="Réseau mobile 5G",
    granularite=granularite("rayon", RAYON_M),
    sources=("antennes",),
    lire=lire,
)
