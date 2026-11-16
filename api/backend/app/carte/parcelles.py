"""Parcels of one tile and context of an address, for the interactive map (04.10-02).

A tile is asked by its indices and served whole: every parcel that touches it,
each one entire, without any cap. Its size is bounded by the zoom (16 at the
widest) and by the real density, measured before this reader was written
(``04.10-DENSITE.md``: 1 683 parcels at most in a zoom-16 tile).

The read is pruned by the departements the tile touches, bound as a parameter:
``parcelles`` and ``mutations_grain`` are partitioned by departement, and a
partition taken from a join or a CTE would leave the planner with all of them
(see ``app/adresse/relations.py``). A tile can touch several departements.

Four answers that never merge (I-4): out of coverage is decided from the scope
before any read of a dataset table; a tile without parcel is ``value`` with an
empty collection; a missing surface is null; a failed cadastre read is
:class:`ParcellesIndisponiblesError` (503), never an empty tile. The sales
count is read in its own guarded block: its outage leaves the parcels served,
without counts.

The context of an address (:func:`lire_contexte`) is where its map opens: the
BAN point read by the server, the bounds the map may be moved within
(:func:`emprise_de_deplacement`, a pure function shared with the sales route)
and the parcel that contains the point, never the nearest one.
"""

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.blocs import faits_de_provenance, lire_bloc, periode_de_source
from app.adresse.relations import resoudre_parcelle
from app.adresse.schemas import RelationParcelle
from app.adresse.service import point_de_la_cle
from app.carte.schemas import (
    CentreCarte,
    CollectionParcelles,
    ContexteCarte,
    EmpriseCarte,
    EntiteParcelle,
    EtatParcelles,
    EtatVentes,
    FondCarte,
    GeometrieMultiPolygone,
    ParcelleAdresse,
    ProprietesParcelle,
    TuileParcelles,
)
from app.common.schemas import DatasetHosting
from app.config import settings
from app.dvf.queries import POSITION_CONNUE_SQL
from app.rapport.carte import LICENCE
from app.scope.dependencies import RouteHosting, route_hosting
from app.scope.model import InstanceScope

# Decimals of every served coordinate: about 0.1 m.
DECIMALES = 6
# Side of the world at zoom 16 in the projection of the map, in pixels (tiles
# of 256 px).
_MONDE_PX_Z16 = 256 * 2**16
# Credit of the base map of the interactive map: the Plan IGN alone (the
# parcels drawn on it come from our base, not from the IGN parcel layer).
ATTRIBUTION_FOND = "© IGN (Plan IGN)"


class ParcellesIndisponiblesError(Exception):
    """Failed read of the cadastre or of the departements of a tile."""


# The tile in the coordinates of the tables. Its edges follow meridians and
# parallels, so its four transformed corners are the exact tile.
_ENVELOPPE_SQL = """ST_Transform(
            ST_TileEnvelope(CAST(:z AS integer), CAST(:x AS integer),
                            CAST(:y AS integer)), 4326)"""

# Departements a tile touches, from the referential (never from a dataset
# table): they qualify the coverage and prune the two reads below.
_DEPARTEMENTS_DE_LA_TUILE = text(f"""
    SELECT g.code
    FROM geo_departements g
    WHERE ST_Intersects(g.geometry, {_ENVELOPPE_SQL})
    ORDER BY g.code
""")

# Every parcel touching the tile, whole (the geometry is never cut to the
# tile), with a label point inside it. No row cap: a tile is served whole or not
# at all. The partitions come from the bound list, the rows from the spatial
# index of each remaining partition.
_PARCELLES_DE_LA_TUILE = text(f"""
    SELECT p.id_parcelle, p.code_departement, p.contenance,
           ST_AsGeoJSON(p.geometrie, {DECIMALES}) AS geometrie,
           ST_X(e.point) AS lon, ST_Y(e.point) AS lat
    FROM parcelles p
    CROSS JOIN LATERAL (SELECT ST_PointOnSurface(p.geometrie) AS point) e
    WHERE p.code_departement = ANY(CAST(:departements AS text[]))
      AND ST_Intersects(p.geometrie, {_ENVELOPPE_SQL})
    ORDER BY p.id_parcelle
""")

# Number of distinct sales of each parcel: the population of
# ``_MUTATIONS_DE_LA_PARCELLE_SQL`` (app/dvf/queries.py), for many parcels at
# once, so that the dot on the map and the list of the panel agree. A parcel
# without sale has no row. The second departement predicate is the one of the
# single-parcel statement; the first prunes the partitions of
# ``mutations_grain`` with the bound list.
_VENTES_DES_PARCELLES = text(f"""
    SELECT mp.id_parcelle, COUNT(DISTINCT mg.id_mutation) AS nombre
    FROM mutation_parcelles mp
    JOIN mutations_grain mg ON mg.id_mutation = mp.id_mutation
    WHERE mp.id_parcelle = ANY(CAST(:ids AS text[]))
      AND mg.code_departement = ANY(CAST(:departements AS text[]))
      AND mg.code_departement = CASE
          WHEN LEFT(mp.id_parcelle, 2) = '97' THEN LEFT(mp.id_parcelle, 3)
          ELSE LEFT(mp.id_parcelle, 2)
      END
      AND {POSITION_CONNUE_SQL}
    GROUP BY mp.id_parcelle
""")

# First imported DVF date, beside the last one (``_FIN_DVF_SQL`` of
# app/adresse/ventes.py): before its year, no sale is known here.
_DEBUT_DVF_SQL = text("SELECT min(date_mutation) AS debut FROM mutations_grain")


@dataclass(frozen=True)
class _Couverture:
    """Coverage of one dataset over the departements of a place.

    ``couverts``: the departements where it is hosted. ``motif``: why the
    others are not (the first one, in the order of the codes); None when
    every departement is covered. ``hors``: nothing is covered.
    """

    couverts: tuple[str, ...]
    motif: str | None
    hors: bool


def _couverture(
    cle: str,
    scope: InstanceScope,
    hosting: RouteHosting,
    departements: Sequence[str] | None,
) -> _Couverture:
    """Coverage of *cle* over *departements*, by the rule of every route.

    Without any departement (a tile at sea, or a dataset hosted nowhere, for
    which none is read) the hosting of the route stands: no departement is a
    proof of « hors couverture » (02-07 rule).
    """
    if not departements:
        return _de_base(hosting.hostings[cle])
    hebergements = {
        code: route_hosting((cle,), scope, code).hostings[cle] for code in departements
    }
    couverts = tuple(
        code for code in departements if hebergements[code].etat == "heberge"
    )
    motifs = [
        _motif(hebergements[code]) for code in departements if code not in couverts
    ]
    return _Couverture(couverts, motifs[0] if motifs else None, not couverts)


def _de_base(hebergement: DatasetHosting) -> _Couverture:
    """Coverage as the route computed it, for the place it was asked about."""
    return _Couverture((), _motif(hebergement), hebergement.etat != "heberge")


def _motif(hebergement: DatasetHosting) -> str | None:
    return None if hebergement.motif is None else hebergement.motif.value


def _entite(ligne: Mapping, ventes: int | None) -> EntiteParcelle:
    geometrie = json.loads(ligne["geometrie"])
    return EntiteParcelle(
        id=ligne["id_parcelle"],
        geometry=GeometrieMultiPolygone(coordinates=geometrie["coordinates"]),
        properties=ProprietesParcelle(
            id_parcelle=ligne["id_parcelle"],
            contenance=ligne["contenance"],
            etiquette=(round(ligne["lon"], DECIMALES), round(ligne["lat"], DECIMALES)),
            ventes=ventes,
        ),
    )


async def _etat_ventes(
    session: AsyncSession, couverture: _Couverture, ids: Sequence[str]
) -> tuple[EtatVentes, dict[str, int]]:
    """State of the sales and the count of each parcel of *ids* that has some.

    DVF is read in its own guarded block: a refused publication or a failed
    read is ``temporary_error`` and no count, never zero sale.
    """
    if couverture.hors:
        return EtatVentes(etat="out_of_coverage", motif=couverture.motif), {}

    async def lecteur():
        comptes: dict[str, int] = {}
        if ids:
            lignes = await session.execute(
                _VENTES_DES_PARCELLES,
                {"ids": list(ids), "departements": list(couverture.couverts)},
            )
            comptes = {ligne.id_parcelle: int(ligne.nombre) for ligne in lignes}
        debut = (await session.execute(_DEBUT_DVF_SQL)).scalar_one()
        return comptes, debut, await faits_de_provenance(session, "dvf")

    bloc = await lire_bloc(session, ["dvf"], lecteur)
    if bloc.valeur is None:
        return EtatVentes(etat="temporary_error", motif=bloc.panne), {}
    comptes, debut, faits = bloc.valeur
    etat = EtatVentes(
        etat="value",
        motif=couverture.motif,
        periode=periode_de_source(faits),
        annee_debut=None if debut is None else debut.year,
    )
    return etat, comptes


async def lire_tuile(
    session: AsyncSession,
    scope: InstanceScope,
    hosting: RouteHosting,
    z: int,
    x: int,
    y: int,
) -> TuileParcelles:
    """Every parcel of our base touching the tile *z*/*x*/*y*, with its sales count.

    *hosting* carries ``cadastre`` and ``dvf`` as the route declares them;
    *scope* requalifies both for the departements of the tile. Raises
    :class:`ParcellesIndisponiblesError` when the cadastre cannot be read.
    """
    tuile = {"z": z, "x": x, "y": y}
    departements: list[str] | None = None
    lignes: Sequence[Mapping] = []
    cadastre = _couverture("cadastre", scope, hosting, None)
    if not cadastre.hors:

        async def lecteur():
            codes = (
                (await session.execute(_DEPARTEMENTS_DE_LA_TUILE, tuile))
                .scalars()
                .all()
            )
            couverture = _couverture("cadastre", scope, hosting, codes)
            if not couverture.couverts:
                return codes, couverture, []
            resultat = await session.execute(
                _PARCELLES_DE_LA_TUILE,
                {**tuile, "departements": list(couverture.couverts)},
            )
            return codes, couverture, resultat.mappings().all()

        bloc = await lire_bloc(session, ["cadastre"], lecteur)
        if bloc.valeur is None:
            raise ParcellesIndisponiblesError
        departements, cadastre, lignes = bloc.valeur

    if cadastre.hors:
        parcelles = EtatParcelles(etat="out_of_coverage", motif=cadastre.motif)
        millesime = None
    else:
        parcelles = EtatParcelles(etat="value", motif=cadastre.motif)
        millesime = periode_de_source(await faits_de_provenance(session, "cadastre"))

    dvf = _couverture("dvf", scope, hosting, departements)
    comptables = [
        ligne["id_parcelle"]
        for ligne in lignes
        if ligne["code_departement"] in dvf.couverts
    ]
    ventes, comptes = await _etat_ventes(session, dvf, comptables)
    comptees = set(comptables) if ventes.etat == "value" else set()
    return TuileParcelles(
        parcelles=parcelles,
        ventes=ventes,
        cadastre_millesime=millesime,
        collection=CollectionParcelles(
            features=[
                _entite(
                    ligne,
                    comptes.get(ligne["id_parcelle"], 0)
                    if ligne["id_parcelle"] in comptees
                    else None,
                )
                for ligne in lignes
            ]
        ),
    )


def emprise_de_deplacement(lon: float, lat: float) -> EmpriseCarte:
    """Bounds the map of the address at (*lon*, *lat*) may be moved within (C6).

    A square centred on the point in the projection of the map (Web Mercator),
    ``carte_emprise_demi_cote_px_z16`` pixels on each side at zoom 16, given
    back in degrees. Counted in pixels, not in metres: the widest view of the
    dialog at the lowest zoom fits inside whatever the latitude, where a square
    in metres would be too small far from the equator.
    """
    demi = settings.carte_emprise_demi_cote_px_z16
    px = (lon + 180) / 360 * _MONDE_PX_Z16
    py = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * _MONDE_PX_Z16

    def longitude(x: float) -> float:
        return round(x / _MONDE_PX_Z16 * 360 - 180, DECIMALES)

    def latitude(y: float) -> float:
        angle = math.atan(math.sinh(math.pi * (1 - 2 * y / _MONDE_PX_Z16)))
        return round(math.degrees(angle), DECIMALES)

    return EmpriseCarte(
        ouest=longitude(px - demi),
        sud=latitude(py + demi),
        est=longitude(px + demi),
        nord=latitude(py - demi),
    )


def _parcelle_adresse(relation: RelationParcelle) -> ParcelleAdresse:
    """The parcel relation of the address page, in the states of the map."""
    if relation.etat == "hors_couverture":
        return ParcelleAdresse(etat="out_of_coverage", motif=relation.motif)
    if relation.etat == "indisponible":
        return ParcelleAdresse(etat="temporary_error", motif="lecture_indisponible")
    if relation.etat == "ambigue":
        return ParcelleAdresse(
            etat="not_available",
            motif="parcelles_multiples",
            nombre_parcelles=relation.nombre_candidats,
        )
    if relation.etat != "unique" or relation.id_parcelle is None:
        return ParcelleAdresse(etat="not_available", motif="point_hors_parcelle")
    return ParcelleAdresse(etat="value", id_parcelle=relation.id_parcelle)


async def lire_contexte(
    session: AsyncSession, hosting: RouteHosting, ban_id: str
) -> ContexteCarte | None:
    """Where the map of *ban_id* opens: point, bounds, parcel, states.

    *hosting* carries ``ban``, ``cadastre`` and ``dvf`` for the departement of
    the key. The point is read by the server, as for the static map; None
    without a single BAN point (BAN out of coverage, row without position,
    rows that disagree): there is no centre to open a map on. Raises the
    errors of :func:`app.adresse.service.point_de_la_cle`. Nothing of the
    cadastre nor of DVF is read where it is not hosted.
    """
    point = await point_de_la_cle(session, ban_id, hosting)
    if point is None:
        return None
    relation = await resoudre_parcelle(session, point, hosting)
    cadastre = _de_base(hosting.hostings["cadastre"])
    if cadastre.hors:
        parcelles = EtatParcelles(etat="out_of_coverage", motif=cadastre.motif)
        millesime = None
    else:
        parcelles = EtatParcelles(etat="value")
        millesime = periode_de_source(await faits_de_provenance(session, "cadastre"))
    ventes, _ = await _etat_ventes(session, _de_base(hosting.hostings["dvf"]), [])
    return ContexteCarte(
        centre=CentreCarte(lon=point.lon, lat=point.lat),
        emprise=emprise_de_deplacement(point.lon, point.lat),
        parcelle_adresse=_parcelle_adresse(relation),
        parcelles=parcelles,
        ventes=ventes,
        cadastre_millesime=millesime,
        fond=FondCarte(attribution=ATTRIBUTION_FOND, licence=LICENCE),
    )
