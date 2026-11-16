"""The static map of the head of the report (04.7-17).

The report serves the addresses of two images, relative to the site: Caddy
relays ``/carte/*`` to the cached proxy of the API (``app.carte``), which
alone calls the IGN Géoplateforme. The visitor's browser never reaches the
IGN, and the Content-Security-Policy of the site is unchanged. Moving to a
direct call would only change :data:`BASE`.
"""

from urllib.parse import quote

from app.adresse.relations import PointBan
from app.rapport.schemas import CarteEntete, ImagesCarte

BASE = "/carte"
# Width of the map on the ground, as the proxy computes its extent.
EMPRISE_M = 300
ATTRIBUTION = "© IGN (Plan IGN, Parcellaire Express)"
LICENCE = "Licence Ouverte 2.0"
GEOPORTAIL = (
    "https://www.geoportail.gouv.fr/carte?c={lon},{lat}&z=18"
    "&l0=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2::GEOPORTAIL:OGC:WMTS(1)"
    "&l1=CADASTRALPARCELS.PARCELLAIRE_EXPRESS::GEOPORTAIL:OGC:WMTS(1)"
    "&permalink=yes"
)


def carte_de(ban_id: str, point: PointBan | None) -> CarteEntete | None:
    """The map of *ban_id*; None without a BAN point."""
    if point is None:
        return None
    cle = quote(ban_id, safe="")
    return CarteEntete(
        images=ImagesCarte(
            plan=f"{BASE}/{cle}/plan.png", parcelles=f"{BASE}/{cle}/parcelles.png"
        ),
        emprise_m=EMPRISE_M,
        attribution=ATTRIBUTION,
        licence=LICENCE,
        geoportail=GEOPORTAIL.format(lon=point.lon, lat=point.lat),
    )
