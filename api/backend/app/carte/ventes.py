"""Sales of one parcel of the interactive map, and the parcels sold with it
(04.10-05, LIEU-02).

``GET /v1/carte/{ban_id}/parcelle/{id_parcelle}/ventes``: the sales of the
parcel a visitor picked on the map of an address, five per page, and for each
sale that took several parcels, what is needed to show them together.

One price rule. The page is read by
:func:`app.adresse.ventes.lire_page_parcelle`, the reader of the address page,
with another page size: same population, same order, same cards, and the price
of a recent isolated sale withheld by the same function. Nothing of that rule
is written here.

Only around the address of the path (C6, T-04.10-11). The server reads the BAN
point of the key, computes the bounds its map may be moved within
(:func:`app.carte.parcelles.emprise_de_deplacement`) and answers only when the
asked parcel is in our cadastre and touches these bounds. An unknown key, an
unknown parcel and a parcel elsewhere are the same answer (``None``, one
constant 404): the route cannot be walked parcel by parcel across a
departement, and it does not say whether a key or a parcel exists. The visitor
supplies no bounds.

Sold together is a dated fact, never an owner nor a land unit. A group is
keyed by the rank of its sale in the page: the identifier of a mutation serves
the computation and never leaves the API, under any form. The parcels of a
group are drawn within the same bounds; those outside are counted, those the
cadastre no longer holds too, and the total surface is whole or null, computed
here and never by the front. Beyond ``carte_groupe_geometries_max`` parcels to
draw, a group serves its counts only.

Three answers that never merge (I-4): zero sale is ``value`` with ``total`` 0;
DVF not hosted for the departement of the parcel is ``out_of_coverage``, said
before any sale is read; a refused publication or a failed read is
:class:`app.adresse.ventes.VentesIndisponiblesError` (503), never an empty list.
"""

import json
from collections.abc import Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.blocs import lire_bloc, periode_de_source
from app.adresse.relations import departement_de_parcelle
from app.adresse.service import (
    AdresseInconnueError,
    AdresseIndisponibleError,
    VoieSansNumeroError,
    point_de_la_cle,
)
from app.adresse.ventes import VentesIndisponiblesError, lire_page_parcelle
from app.carte.parcelles import (
    _DEBUT_DVF_SQL,
    DECIMALES,
    _couverture,
    emprise_de_deplacement,
)
from app.carte.schemas import (
    EmpriseCarte,
    GeometrieMultiPolygone,
    GroupeVendu,
    ParcelleDuGroupe,
    VentesParcelleCarte,
)
from app.config import settings
from app.scope.dependencies import RouteHosting
from app.scope.model import InstanceScope

# The bounds of the address in the coordinates of the tables.
_EMPRISE_SQL = """ST_MakeEnvelope(
            CAST(:ouest AS double precision), CAST(:sud AS double precision),
            CAST(:est AS double precision), CAST(:nord AS double precision), 4326)"""

# The asked parcel, read by its identifier in the partition of its departement
# (bound as a parameter: a partition taken from a join would leave the planner
# with all of them, see ``app/adresse/relations.py``), and whether it touches
# the bounds of the address. No row: the cadastre does not hold this parcel.
# The bounds are compared in the output, not in the filter, so that the read
# is the one of the identifier index whatever the planner thinks of the box.
_PARCELLE_DANS_L_EMPRISE = text(f"""
    SELECT ST_Intersects(p.geometrie, {_EMPRISE_SQL}) AS dedans
    FROM parcelles p
    WHERE p.id_parcelle = :id_parcelle
      AND p.code_departement = :departement
""")


# Every parcel of the sales of one page. ``mutation_parcelles`` has no index on
# ``id_mutation`` alone since migration 107: the read is the one of the index
# of its unique constraint (id_mutation, id_parcelle), which leads with it.
# The mutation identifiers are bound and group the rows; they are not served.
_PARCELLES_DES_VENTES = text("""
    SELECT mp.id_mutation, mp.id_parcelle
    FROM mutation_parcelles mp
    WHERE mp.id_mutation = ANY(CAST(:ids AS text[]))
    ORDER BY mp.id_mutation, mp.id_parcelle
""")

# What the cadastre holds of these parcels: the surface of each one, and, for
# those that touch the bounds of the address only, the shape and a label point
# as a tile serves them. Read by identifier in the partitions of the bound
# departements (those of the identifiers); a parcel without row here is absent
# from our cadastre. The bounds are compared in the output, as above.
_PARCELLES_DU_GROUPE = text(f"""
    SELECT p.id_parcelle, p.contenance, e.dedans,
           CASE WHEN e.dedans THEN ST_AsGeoJSON(p.geometrie, {DECIMALES}) END
               AS geometrie,
           ST_X(s.point) AS lon, ST_Y(s.point) AS lat
    FROM parcelles p
    CROSS JOIN LATERAL (
        SELECT ST_Intersects(p.geometrie, {_EMPRISE_SQL}) AS dedans
    ) e
    CROSS JOIN LATERAL (
        SELECT CASE WHEN e.dedans THEN ST_PointOnSurface(p.geometrie) END AS point
    ) s
    WHERE p.code_departement = ANY(CAST(:departements AS text[]))
      AND p.id_parcelle = ANY(CAST(:ids AS text[]))
    ORDER BY p.id_parcelle
""")


def _dessinee(ligne: Mapping) -> ParcelleDuGroupe:
    geometrie = json.loads(ligne["geometrie"])
    return ParcelleDuGroupe(
        id_parcelle=ligne["id_parcelle"],
        geometry=GeometrieMultiPolygone(coordinates=geometrie["coordinates"]),
        etiquette=(round(ligne["lon"], DECIMALES), round(ligne["lat"], DECIMALES)),
    )


def groupe_vendu(
    liees: Sequence[str], cadastre: Mapping[str, Mapping], plafond: int
) -> GroupeVendu | None:
    """The parcels sold in one sale, or None for a sale of one parcel.

    *liees*: the parcels DVF links to the sale, the asked one included, in the
    order they are served. *cadastre*: the rows of ``_PARCELLES_DU_GROUPE`` by
    parcel; a linked parcel without row is absent from our cadastre. The total
    surface is whole or null: one parcel absent, or without surface, and no
    sum is served. Beyond *plafond* parcels to draw, none is served and the
    counts still are.
    """
    if len(liees) < 2:
        return None
    presentes = [cadastre[parcelle] for parcelle in liees if parcelle in cadastre]
    dessinees = [ligne for ligne in presentes if ligne["dedans"]]
    absentes = len(liees) - len(presentes)
    surfaces = [ligne["contenance"] for ligne in presentes]
    entiere = absentes == 0 and None not in surfaces
    return GroupeVendu(
        total=len(liees),
        autres=len(liees) - 1,
        absentes_du_cadastre=absentes,
        hors_emprise=len(presentes) - len(dessinees),
        sans_surface=surfaces.count(None),
        surface_totale_m2=sum(surfaces) if entiere else None,
        parcelles=None
        if len(dessinees) > plafond
        else [_dessinee(ligne) for ligne in dessinees],
    )


async def _groupes(
    session: AsyncSession,
    scope: InstanceScope,
    hosting: RouteHosting,
    emprise: EmpriseCarte,
    lignes: Sequence[Mapping],
) -> list[GroupeVendu | None]:
    """The group of each sale of a page, in the order of *lignes*.

    Two statements at most, whatever the page: the parcels of its sales, then
    what the cadastre holds of those sold with another one. The rank in the
    page is the only key of a group: no identifier of a sale leaves here.
    """
    ventes = [ligne["id_mutation"] for ligne in lignes]
    if not ventes:
        return []
    liens = (
        (await session.execute(_PARCELLES_DES_VENTES, {"ids": ventes})).mappings().all()
    )
    liees: dict[str, list[str]] = {}
    for lien in liens:
        liees.setdefault(lien["id_mutation"], []).append(lien["id_parcelle"])
    a_lire = sorted(
        {
            parcelle
            for parcelles in liees.values()
            if len(parcelles) > 1
            for parcelle in parcelles
        }
    )
    cadastre: dict[str, Mapping] = {}
    if a_lire:
        # The departements come from the identifiers, and the cadastre is read
        # where it is hosted only: a parcel of another departement is found
        # there, one of a departement we do not host is absent for us.
        departements = _couverture(
            "cadastre",
            scope,
            hosting,
            sorted({departement_de_parcelle(parcelle) for parcelle in a_lire}),
        ).couverts
        if departements:
            resultat = await session.execute(
                _PARCELLES_DU_GROUPE,
                {
                    "ids": a_lire,
                    "departements": list(departements),
                    **_bornes(emprise),
                },
            )
            cadastre = {
                ligne["id_parcelle"]: ligne for ligne in resultat.mappings().all()
            }
    plafond = settings.carte_groupe_geometries_max
    return [groupe_vendu(liees.get(vente, []), cadastre, plafond) for vente in ventes]


def _bornes(emprise: EmpriseCarte) -> dict[str, float]:
    return {
        "ouest": emprise.ouest,
        "sud": emprise.sud,
        "est": emprise.est,
        "nord": emprise.nord,
    }


async def _dans_l_emprise(
    session: AsyncSession, id_parcelle: str, departement: str, emprise: EmpriseCarte
) -> bool:
    """Whether *id_parcelle* is in our cadastre and touches *emprise*.

    Read in its own block: a failed cadastre read is an outage of the sales
    (:class:`VentesIndisponiblesError`), never « not there ».
    """

    async def lecteur() -> bool:
        lignes = await session.execute(
            _PARCELLE_DANS_L_EMPRISE,
            {
                "id_parcelle": id_parcelle,
                "departement": departement,
                **_bornes(emprise),
            },
        )
        return any(ligne.dedans for ligne in lignes)

    bloc = await lire_bloc(session, ["cadastre"], lecteur)
    if bloc.valeur is None:
        raise VentesIndisponiblesError
    return bloc.valeur


async def lire_ventes_de_la_parcelle(
    session: AsyncSession,
    scope: InstanceScope,
    hosting: RouteHosting,
    ban_id: str,
    id_parcelle: str,
    page: int,
    generation: str | None,
) -> VentesParcelleCarte | None:
    """Page *page* of the sales of *id_parcelle*, for the map of *ban_id*.

    *hosting* carries ``ban``, ``cadastre`` and ``dvf`` for the departement of
    the key; *scope* requalifies the cadastre and DVF for the departement of
    the parcel, which may be another one. Returns None when the pair is not
    served: no single BAN point for the key (unknown, street key, BAN out of
    coverage), parcel absent from our cadastre, or parcel that does not touch
    the bounds of the address; no sale is read then. Raises
    :class:`GenerationChangeeError`, :class:`PageHorsLimitesError` or
    :class:`VentesIndisponiblesError`.
    """
    try:
        point = await point_de_la_cle(session, ban_id, hosting)
    except (VoieSansNumeroError, AdresseInconnueError):
        return None
    except AdresseIndisponibleError:
        raise VentesIndisponiblesError from None
    if point is None:
        return None
    departement = departement_de_parcelle(id_parcelle)
    # A departement whose cadastre we do not host: the parcel is on no map of
    # ours, and its table is not read.
    if _couverture("cadastre", scope, hosting, [departement]).hors:
        return None
    emprise = emprise_de_deplacement(point.lon, point.lat)
    if not await _dans_l_emprise(session, id_parcelle, departement, emprise):
        return None

    par_page = settings.carte_ventes_par_page
    dvf = _couverture("dvf", scope, hosting, [departement])
    if dvf.hors:
        return VentesParcelleCarte(
            etat="out_of_coverage", motif=dvf.motif, page=page, par_page=par_page
        )

    async def lecteur():
        lue = await lire_page_parcelle(session, id_parcelle, page, generation, par_page)
        debut = (await session.execute(_DEBUT_DVF_SQL)).scalar_one()
        groupes = await _groupes(session, scope, hosting, emprise, lue.lignes)
        return lue, debut, groupes

    bloc = await lire_bloc(session, ["dvf"], lecteur)
    if bloc.valeur is None:
        raise VentesIndisponiblesError
    lue, debut, groupes = bloc.valeur
    return VentesParcelleCarte(
        etat="value",
        periode=periode_de_source(lue.faits),
        annee_debut=None if debut is None else debut.year,
        total=lue.total,
        page=page,
        pages=lue.pages,
        par_page=par_page,
        generation=lue.generation,
        ventes=lue.cartes,
        groupes=groupes,
    )
