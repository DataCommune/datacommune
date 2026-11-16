"""« Bruit des avions » : the zone of an airport noise exposure plan (PEB).

Granularity: address (the zone is read at the BAN point, from the PEB zones
of the DGAC served by the vector WMS of the Géoplateforme). Where two plans
overlap, the strongest zone counts. The noise annoyance plan (PGS, zones 1
to 3, where residents may receive help to soundproof their home) goes in
the detail.

Tone rule: vigilance in any zone, A to D (the arrêté recognises the
aircraft noise; zones A and B are of strong noise, C of moderate noise, D
imposes acoustic insulation on new buildings); neutre outside any PEB: a
plan only covers the surroundings of an airport, and no open source maps
the overflights at altitude, so « no plan » never reads « no aircraft noise ».
"""

from app.rapport.partage import BruitAerien, ZoneBruitAerien, bruit_aerien_au_point
from app.rapport.schemas import VERDICT_MAX, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    date_longue,
    granularite,
    sans_reponse,
    sources_de,
    theme,
)

PORTEE = {
    "A": "bruit fort",
    "B": "bruit fort",
    "C": "bruit modéré",
    "D": "isolation acoustique imposée aux constructions neuves",
}


def _aerodrome(zone: ZoneBruitAerien) -> str:
    return zone.aerodrome or zone.code_oaci or "un aérodrome"


def verdict_zone(zone: ZoneBruitAerien) -> str:
    debut = f"Zone {zone.zone} du plan d'exposition au bruit de {_aerodrome(zone)}"
    portee = PORTEE[zone.zone]
    candidats = [f"{debut} : {portee}."]
    if zone.date_arrete:
        candidats.insert(
            0, f"{debut} (arrêté du {date_longue(zone.date_arrete)}) : {portee}."
        )
    # Always within the bound: the airport by its code, its name in the detail.
    code = (zone.code_oaci or "aérodrome")[:8]
    court = f"Zone {zone.zone} du plan d'exposition au bruit ({code}) : {portee}."
    return next((v for v in candidats if len(v) <= VERDICT_MAX), court)


def _zone(z: ZoneBruitAerien) -> dict:
    return {
        "zone": z.zone,
        "aerodrome": z.aerodrome,
        "code_oaci": z.code_oaci,
        "date_arrete": z.date_arrete,
        "document": z.document,
    }


async def lire(contexte: ContexteRapport) -> Theme:
    sources = sources_de(THEME, {"peb": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["peb"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources
        )
    if contexte.point is None:
        return sans_reponse(THEME, "manque", "position_indeterminee", sources=sources)
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, BruitAerien):
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    details = {
        "zones_peb": [_zone(z) for z in lu.peb],
        "zones_pgs": [_zone(z) for z in lu.pgs],
    }
    if not lu.peb:
        return theme(
            THEME,
            etat="zero",
            ton="neutre",
            verdict=(
                "Hors de tout plan d'exposition au bruit d'aérodrome ; "
                "le survol en altitude n'est pas cartographié."
            ),
            details=details,
            sources=sources,
        )
    # Zones are sorted A to D: the first is the strongest.
    return theme(
        THEME,
        etat="ok",
        ton="vigilance",
        verdict=verdict_zone(lu.peb[0]),
        details=details,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="peb",
    section="vivre",
    ordre=54,
    titre="Bruit des avions",
    granularite=granularite("adresse"),
    sources=("peb",),
    lire=lire,
    amont=bruit_aerien_au_point,
)
