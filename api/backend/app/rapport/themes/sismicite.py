"""« Sismicité » : seismic zone of the commune (Géorisques, decree zoning).

Granularity: commune: the zone is set by decree for the whole commune and
read by INSEE code (``/zonage_sismique``), never through a parcel (04.7-04:
the ERRIAL parcel could resolve a neighbouring commune). Zones 1 to 5. An
answer for another commune, or none, is a gap; an outage is never a zone.

Tone rule: vigilance for zones 3 to 5 (construction rules apply to houses),
neutre for zone 2, rassurant for zone 1; any other label is neutre.
"""

from app.common.geo import normalize_code_insee
from app.rapport.partage import LignesLues, lignes_commune
from app.rapport.schemas import Theme, Ton
from app.rapport.socle import (
    ContexteAmont,
    ContexteRapport,
    DefinitionTheme,
    granularite,
    sans_reponse,
    sources_de,
    theme,
)

LIBELLES = {
    "1": "très faible",
    "2": "faible",
    "3": "modérée",
    "4": "moyenne",
    "5": "forte",
}

_TONS: dict[str, Ton] = {"2": "neutre", "1": "rassurant"}


def ton_sismicite(zone: str) -> Ton:
    if zone in ("3", "4", "5"):
        return "vigilance"
    return _TONS.get(zone, "neutre")


async def lire(contexte: ContexteRapport) -> Theme:
    sources = sources_de(THEME, {"georisques": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["georisques"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources
        )
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, LignesLues):
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    code = normalize_code_insee(contexte.identite.commune.code_insee)
    zones = {
        str(ligne.get("code_zone") or "").strip()
        for ligne in lu.lignes
        if normalize_code_insee(str(ligne.get("code_insee") or "")) == code
    } - {""}
    if len(zones) != 1:
        return sans_reponse(
            THEME,
            "manque",
            "zone_non_publiee" if not zones else "zones_multiples",
            sources=sources,
        )
    zone = zones.pop()
    libelle = LIBELLES.get(zone)
    verdict = (
        f"Commune en zone de sismicité {zone} ({libelle})."
        if libelle
        else f"Zone de sismicité de la commune : {zone}."
    )
    return theme(
        THEME,
        etat="ok",
        ton=ton_sismicite(zone),
        verdict=verdict,
        details={"zone": zone},
        sources=sources,
    )


async def amont(contexte: ContexteAmont) -> object:
    return await lignes_commune(contexte, "/zonage_sismique")


THEME = DefinitionTheme(
    id="sismicite",
    section="risques",
    ordre=50,
    titre="Sismicité",
    granularite=granularite("commune"),
    sources=("georisques",),
    lire=lire,
    amont=amont,
)
