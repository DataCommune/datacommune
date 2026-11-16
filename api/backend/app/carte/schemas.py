"""Models of the four routes of the interactive map: the frozen contract of 04.10.

Under ``/v1/carte/``, reached by the browser on the site origin as ``/carte/…``:

- ``{ban_id}/contexte`` -> :class:`ContexteCarte` (plan 02);
- ``parcelles/{z}/{x}/{y}`` -> :class:`TuileParcelles` (plan 02);
- ``fond/{z}/{x}/{y}.png`` -> a PNG, no model (plan 04);
- ``{ban_id}/parcelle/{id_parcelle}/ventes`` -> :class:`VentesParcelleCarte`
  (plan 05).

Zero, missing, out of coverage and outage stay four answers (I-4): a state is
``value`` even when it counts nothing; what was not read is null, never zero.
Any answer carrying a ``temporary_error`` state is served ``no-store``.
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.adresse.schemas import CarteVente

# Longitude then latitude, WGS 84, as GeoJSON orders them.
LonLat = tuple[float, float]


class EtatParcelles(BaseModel):
    """Whether the parcels of the asked place are served.

    ``value``: every parcel of our base there is in the answer, zero included.
    With a ``motif``, part of the place lies in a departement we do not host:
    the served parcels are those of the hosted side only. ``out_of_coverage``:
    the cadastre is not hosted there (``motif`` says why); nothing was read.
    A failed read is never a state: the route answers 503.
    """

    etat: Literal["value", "out_of_coverage"]
    motif: str | None = None


class EtatVentes(BaseModel):
    """Whether the sales of the served parcels are counted, and over which period.

    ``value``: the counts are read; ``periode`` is the DVF period as the import
    declared it and ``annee_debut`` the year of the first imported sale (both
    null when unknown, never invented). With a ``motif``, DVF does not cover
    every departement of the place: the parcels it does not cover carry no
    count. ``out_of_coverage``: DVF is not hosted there. ``temporary_error``:
    refused publication or failed read; the parcels are served without counts.
    """

    etat: Literal["value", "out_of_coverage", "temporary_error"]
    motif: str | None = None
    periode: str | None = None
    annee_debut: int | None = None


class CentreCarte(BaseModel):
    """The BAN point of the address: where the map opens."""

    lon: float
    lat: float


class EmpriseCarte(BaseModel):
    """Bounds the map may be moved within, in degrees to six decimals.

    A square in the projection of the map, centred on the address: it holds the
    widest view of the dialog at zoom 16, whatever the latitude.
    """

    ouest: float
    sud: float
    est: float
    nord: float


class ParcelleAdresse(BaseModel):
    """The parcel that contains the BAN point; never the nearest one.

    ``value``: one parcel (``id_parcelle``). ``not_available``: the point
    designates no single parcel (``motif`` ``point_hors_parcelle``, or
    ``parcelles_multiples`` with ``nombre_parcelles``). ``out_of_coverage``:
    cadastre not hosted for the departement of the address, not read.
    ``temporary_error``: the cadastre read failed (``motif``
    ``lecture_indisponible``).
    """

    etat: Literal["value", "not_available", "out_of_coverage", "temporary_error"]
    id_parcelle: str | None = None
    motif: str | None = None
    nombre_parcelles: int | None = None


class FondCarte(BaseModel):
    """Credit of the base map, as the licence asks for it."""

    attribution: str
    licence: str


class ContexteCarte(BaseModel):
    """What the map of one address opens on.

    ``parcelles`` and ``ventes`` are the states for the departement of the
    address, with the period and the vintage a tile of that place serves.
    """

    centre: CentreCarte
    emprise: EmpriseCarte
    parcelle_adresse: ParcelleAdresse
    parcelles: EtatParcelles
    ventes: EtatVentes
    cadastre_millesime: str | None = None
    fond: FondCarte


class GeometrieMultiPolygone(BaseModel):
    """GeoJSON geometry of a parcel, whole, to six decimals."""

    type: Literal["MultiPolygon"] = "MultiPolygon"
    coordinates: list[list[list[list[float]]]]


class ProprietesParcelle(BaseModel):
    """What the map says of one parcel.

    ``contenance``: the cadastral surface in square metres, null when the
    cadastre gives none (missing, never zero). ``etiquette``: a point inside
    the parcel. ``ventes``: its number of distinct DVF sales over the period,
    zero included; null when it was not counted (the ``ventes`` state of the
    answer is not ``value``, or DVF does not cover the departement of this
    parcel). Never a price, a date or a sale identifier.
    """

    id_parcelle: str
    contenance: int | None = None
    etiquette: LonLat
    ventes: int | None = None


class EntiteParcelle(BaseModel):
    """GeoJSON feature of one parcel; ``id`` is its ``id_parcelle``."""

    type: Literal["Feature"] = "Feature"
    id: str
    geometry: GeometrieMultiPolygone
    properties: ProprietesParcelle


class CollectionParcelles(BaseModel):
    """GeoJSON collection, ordered by ``id_parcelle``, without any cap."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[EntiteParcelle] = Field(default_factory=list)


class TuileParcelles(BaseModel):
    """Every parcel of our base that touches one tile, each one whole.

    A parcel astride two tiles is served by both, with the same geometry: the
    reader of several tiles deduplicates on ``id``. An empty collection under
    ``parcelles.etat`` ``value`` is a place without any parcel.
    """

    parcelles: EtatParcelles
    ventes: EtatVentes
    cadastre_millesime: str | None = None
    collection: CollectionParcelles


class ParcelleDuGroupe(BaseModel):
    """One parcel of a sale, drawn with the group it was sold in."""

    id_parcelle: str
    geometry: GeometrieMultiPolygone
    etiquette: LonLat


class GroupeVendu(BaseModel):
    """The parcels sold together in one sale: a dated fact, never an owner.

    ``total`` counts the parcels of the sale, the asked one included;
    ``autres`` is ``total`` minus one. ``absentes_du_cadastre``: those of them
    the current cadastre no longer holds. ``hors_emprise``: those in the
    cadastre but outside the bounds of the address, never drawn.
    ``sans_surface``: those in the cadastre without a surface, said to the
    visitor when the total is null for them alone (review of 2026-11-15).
    ``surface_totale_m2`` is null as soon as one parcel is absent from the
    cadastre or has no surface: never a partial sum. ``parcelles``: those in
    the cadastre and inside the bounds, the asked one included; null beyond
    the cap of the sales route.
    """

    total: int
    autres: int
    absentes_du_cadastre: int
    hors_emprise: int
    sans_surface: int
    surface_totale_m2: int | None = None
    parcelles: list[ParcelleDuGroupe] | None = None


class VentesParcelleCarte(BaseModel):
    """One page of the sales of a parcel of the map, five per page.

    ``value``: the distinct sales of the parcel, latest first, over the whole
    imported DVF period (zero is ``total`` 0). ``out_of_coverage``: DVF not
    hosted there. An outage is a 503, never a state. ``ventes`` are the cards
    of the address page, unchanged and unidentified: a withheld price never
    leaves the API. ``groupes`` has the length and the order of ``ventes``:
    null for a sale of this parcel alone. ``generation`` is carried by the
    page links.
    """

    etat: Literal["value", "out_of_coverage"]
    motif: str | None = None
    periode: str | None = None
    annee_debut: int | None = None
    total: int | None = None
    page: int
    pages: int | None = None
    par_page: int
    generation: str | None = None
    ventes: list[CarteVente] = Field(default_factory=list)
    groupes: list[GroupeVendu | None] = Field(default_factory=list)

    @model_validator(mode="after")
    def _un_groupe_par_vente(self) -> Self:
        if len(self.groupes) != len(self.ventes):
            raise ValueError("groupes has one entry per sale, in the same order")
        return self
