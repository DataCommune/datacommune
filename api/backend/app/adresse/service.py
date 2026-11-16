"""Identity of one BAN address and the landmarks of the place (04-02, 04-03).

Four outcomes: served address (200), out of coverage (200,
``couverture.etat=hors_couverture``, nothing read in the BAN nor the
cadastre), unknown key (404 ``adresse_inconnue``) and street form (404
``voie_sans_numero``). A key carried by several BAN rows is never resolved
by an arbitrary ``LIMIT 1``: rows that disagree give an indeterminate
identity.

The four landmarks (D-113) are read each in its own block
(:func:`app.adresse.blocs.lire_bloc`): ① DPE declared at the address,
② sales of the parcel of the point, ③ clay exposure at the point,
④ the commune median as a reference.
"""

import asyncio
import logging
import re
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.blocs import (
    faits_de_provenance,
    lire_bloc,
    periode_de_source,
    source_valeur,
)
from app.adresse.documents import repere_documents
from app.adresse.lieu import AmontLieu, lire_amont_lieu, lire_themes_lieu
from app.adresse.recherche import noms_departements
from app.adresse.relations import PointBan, resoudre_parcelle
from app.adresse.risques import repere_argiles_au_point
from app.adresse.schemas import (
    CommuneRef,
    CouvertureAdresse,
    DepartementRef,
    FicheAdresse,
    IdentiteAdresse,
    ReferenceCommune,
    RelationParcelle,
    RepereReferenceCommune,
    ReperesAdresse,
    RepereVentes,
    ValeurLieu,
)
from app.ban.queries import (
    GeocodageDefautError,
    GeocodageIndisponibleError,
    _base_indisponible,
)
from app.common.geo import expand_code_insee, normalize_code_insee
from app.common.source_catalog import DATASET_CATALOG
from app.commune.queries import commune_synthese_par_codes, fenetre_mediane_commune
from app.commune.schemas import (
    apply_commune_coverage,
    commune_coverage,
    hosted_commune_sources,
)
from app.dvf.queries import dvf_compte_par_id_parcelle
from app.resolve import telemetry
from app.scope.dependencies import RouteHosting, commune_departement
from app.scope.model import InstanceScope

logger = logging.getLogger(__name__)

# INSEE code then street segment of four to six characters (block E).
_RE_VOIE = re.compile(r"^[0-9][0-9AB][0-9]{3}_[0-9A-Za-z]{4,6}$")

_IDENTITE = ("numero", "rep", "nom_voie", "code_postal", "code_commune", "nom_commune")


class AdresseInconnueError(Exception):
    """Well-formed key absent from the BAN, or INSEE prefix unknown."""


class VoieSansNumeroError(Exception):
    """Street form of a key: an address page needs a number (D-107)."""


class AdresseIndisponibleError(Exception):
    """A read of the identity failed on an outage (BAN or national referential).

    Raised detached from the database error (C59 B1): never ``from exc``.
    """


def forme_de_voie(ban_id: str) -> bool:
    """True for a street key (INSEE code and street segment only)."""
    return _RE_VOIE.fullmatch(ban_id) is not None


_COMMUNE = text("""
    SELECT code, nom, departement_code
    FROM geo_communes
    WHERE code = ANY(CAST(:codes AS text[]))
    ORDER BY code = :code DESC
""")

# Every row carrying the key (index idx_adresses_ban_id), bounded to the
# partition of its departement; no LIMIT. The point is selected for the
# parcel relation only and never leaves this module.
_ADRESSES_PAR_CLE = text("""
    SELECT numero, rep, nom_voie, code_postal, code_commune, nom_commune,
           ST_X(position) AS lon, ST_Y(position) AS lat
    FROM adresses
    WHERE ban_id = :ban_id AND code_departement = :departement
    ORDER BY id
""")


async def _lire(session: AsyncSession, statement, params: dict) -> list[dict]:
    """Rows of *statement* in a savepoint.

    Classified as the readers of the geocoder (C59 I3): an availability
    failure is :class:`AdresseIndisponibleError` (the 503), any other error
    the bounded :class:`GeocodageDefautError` (the 500). Neither keeps the
    key nor the database error: raised after the ``except`` block (C59 B1).
    """
    try:
        async with session.begin_nested():
            result = await session.execute(statement, params)
            return [dict(row._mapping) for row in result]
    except Exception as exc:  # noqa: BLE001 - re-raised bounded, below
        # Bounded code only (PRIV-01): params carry the key.
        telemetry.emit_resolve_event(
            logger,
            "address_identity_read_failed",
            level=logging.WARNING,
            error_code=telemetry.error_code_for(exc),
        )
        erreur: Exception = (
            AdresseIndisponibleError()
            if _base_indisponible(exc)
            else GeocodageDefautError(exc)
        )
    raise erreur from None


def libelle_adresse(row: dict) -> str:
    """« {numéro}{indice} {voie}, {CP} {Commune} »."""
    numero = f"{row['numero'] or ''}{row['rep'] or ''}"
    voie = " ".join(part for part in (numero, row["nom_voie"] or "") if part)
    commune = " ".join(
        part for part in (row["code_postal"] or "", row["nom_commune"] or "") if part
    )
    return f"{voie}, {commune}"


def _commun(rows: list[dict]) -> dict:
    """Identity fields shared by every row; a field they disagree on is None."""
    return {
        field: rows[0][field] if len({row[field] for row in rows}) == 1 else None
        for field in _IDENTITE
    }


FIELD_VENTES_NOMBRE = "adresse.dvf.ventes_parcelle.nombre"
FIELD_VENTES_DERNIERE = "adresse.dvf.ventes_parcelle.derniere"
# Landmark ④: (key of the landmark, field_id, column of the synthesis).
_REFERENCES = (
    (
        "maisons",
        "adresse.reference_commune.prix_median_maison",
        "prix_m2_median_maison",
    ),
    (
        "appartements",
        "adresse.reference_commune.prix_median_appartement",
        "prix_m2_median_appartement",
    ),
)
_MOTIFS_PARCELLE = {
    "point_hors_parcelle": "point_hors_parcelle",
    "ambigue": "parcelles_multiples",
}


async def repere_ventes(
    session: AsyncSession, relation: RelationParcelle | None, hosting: RouteHosting
) -> RepereVentes:
    """Landmark ②: distinct mutations of the parcel of the point (N-02).

    Read only for a ``unique`` parcel. No parcel under the point, several
    parcels, contradictory points: a gap with its reason, never a zero and
    never a sale attributed to one parcel among several.
    """
    commun = {"source": source_valeur("dvf"), "rattachement": "parcelle_du_point"}

    def paire(**etat) -> RepereVentes:
        return RepereVentes(
            nombre=ValeurLieu(
                field_id=FIELD_VENTES_NOMBRE, unite="count", **commun, **etat
            ),
            derniere=ValeurLieu(
                field_id=FIELD_VENTES_DERNIERE, unite="none", **commun, **etat
            ),
        )

    dvf = hosting.hostings["dvf"]
    if dvf.etat != "heberge":
        return paire(etat="out_of_coverage", motif=dvf.motif)
    if relation is None:
        return paire(etat="not_available", motif="parcelle_indeterminee")
    if relation.etat == "hors_couverture":
        return paire(etat="out_of_coverage", motif=relation.motif)
    if relation.etat == "indisponible":
        return paire(etat="temporary_error", motif="lecture_indisponible")
    if relation.etat != "unique" or relation.id_parcelle is None:
        motif = _MOTIFS_PARCELLE.get(relation.etat, relation.motif)
        return paire(etat="not_available", motif=motif)
    id_parcelle = relation.id_parcelle

    async def lecteur():
        compte = await dvf_compte_par_id_parcelle(session, id_parcelle)
        return compte, await faits_de_provenance(session, "dvf")

    bloc = await lire_bloc(session, ["dvf"], lecteur)
    if bloc.en_panne:
        return paire(etat="temporary_error", motif=bloc.panne)
    compte, faits = bloc.valeur
    periode = periode_de_source(faits)
    nombre = ValeurLieu(
        field_id=FIELD_VENTES_NOMBRE,
        etat="value",
        valeur=compte["nombre"],
        unite="count",
        periode=periode,
        **commun,
    )
    if compte["derniere"] is None:
        derniere = ValeurLieu(
            field_id=FIELD_VENTES_DERNIERE,
            etat="not_available",
            motif="aucune_vente",
            unite="none",
            periode=periode,
            **commun,
        )
    else:
        derniere = ValeurLieu(
            field_id=FIELD_VENTES_DERNIERE,
            etat="value",
            valeur=compte["derniere"].isoformat(),
            unite="none",
            periode=periode,
            **commun,
        )
    return RepereVentes(nombre=nombre, derniere=derniere)


async def repere_reference_commune(
    session: AsyncSession,
    commune: CommuneRef,
    scope: InstanceScope,
    hosting: RouteHosting,
) -> RepereReferenceCommune:
    """Landmark ④: the commune medians, read as ``/v1/commune`` reads them.

    ``commune_synthese_par_codes`` (its own publication barrier) then
    ``apply_commune_coverage``: the same values and the same absence states
    as the commune page. Nothing of the place is read.
    """
    source = source_valeur("dvf")
    commun = {
        "unite": "euro_per_m2",
        "source": source,
        "rattachement": "commune_reference",
    }

    def toutes(**etat) -> RepereReferenceCommune:
        return RepereReferenceCommune(
            **{
                cle: ValeurLieu(field_id=fid, **commun, **etat)
                for cle, fid, _ in _REFERENCES
            }
        )

    hebergement = hosting.hostings["commune"]
    if hebergement.etat != "heberge":
        return toutes(etat="out_of_coverage", motif=hebergement.motif)
    sources = hosted_commune_sources(scope, commune_departement(commune.code_insee))

    async def lecteur():
        rows = await commune_synthese_par_codes(
            session, expand_code_insee(commune.code_insee), sources
        )
        # DVF not hosted for the commune: no median, no sale table read.
        fenetre = await fenetre_mediane_commune(session) if "dvf" in sources else None
        return rows, fenetre, await faits_de_provenance(session, "dvf")

    bloc = await lire_bloc(session, ["commune"], lecteur)
    if bloc.en_panne:
        return toutes(etat="temporary_error", motif=bloc.panne)
    rows, fenetre, faits = bloc.valeur
    row = next((r for r in rows if r.get("code_insee") == commune.code_insee), None)
    servie = (
        apply_commune_coverage(row, commune_coverage(scope, commune.code_insee))
        if row is not None
        else None
    )
    if servie is None:
        return toutes(etat="not_reported", motif="commune_absente_de_la_synthese")
    territoire = CommuneRef(
        code_insee=commune.code_insee, nom=servie.get("nom_commune") or commune.nom
    )
    # The months the medians are computed on (C29), never the whole source
    # window; no sale to compute on: the period of the source (C14).
    millesime = fenetre or periode_de_source(faits)
    etats = servie.get("etats") or {}
    valeurs = {}
    for cle, field_id, colonne in _REFERENCES:
        valeur = servie.get(colonne)
        reference = ReferenceCommune(
            valeur=valeur,
            unite="euro_per_m2",
            territoire=territoire,
            source=source,
            millesime=millesime,
            url=DATASET_CATALOG["dvf"].url,
        )
        if valeur is not None:
            valeurs[cle] = ValeurLieu(
                field_id=field_id,
                etat="value",
                valeur=valeur,
                periode=millesime,
                reference=reference,
                **commun,
            )
        else:
            absence = etats.get(colonne) or {"etat": "not_reported", "motif": None}
            valeurs[cle] = ValeurLieu(
                field_id=field_id,
                etat=absence["etat"],
                motif=absence.get("motif"),
                periode=millesime,
                reference=reference,
                **commun,
            )
    return RepereReferenceCommune(**valeurs)


async def lire_fiche_adresse(
    session: AsyncSession,
    ban_id: str,
    hosting: RouteHosting,
    scope: InstanceScope,
    as_of: date,
) -> FicheAdresse:
    """Identity of *ban_id*, the landmarks and the themes of the place.

    Each landmark reads its source in its own guarded block: an outage of
    one source never breaks the identity nor another landmark (N-01, N-14).
    Nothing of the place is read when the BAN is out of coverage.

    Géorisques (③, and the communal risks of the themes) and the planning
    of the themes are called between two transactions (API CR-01, D-15): the
    identity transaction is ended first, so that neither the shared
    publication lock of the guarded blocks nor a pool connection is held
    while an upstream answers; ①, ②, ④ and the themes then read in a new
    transaction.
    """
    fiche, point = await lire_identite(session, ban_id, hosting)
    if fiche.couverture.etat != "heberge":
        return fiche
    # Read-only so far, no publication lock taken yet: give the connection back.
    await session.rollback()
    amonts = await lire_amonts(fiche, point, hosting, as_of)
    return await lire_lieu(session, ban_id, fiche, point, hosting, scope, amonts, as_of)


async def lire_amonts(
    fiche: FicheAdresse, point: PointBan | None, hosting: RouteHosting, as_of: date
) -> tuple[ValeurLieu, AmontLieu]:
    """Every upstream of the page: ③, the communal risks, the planning.

    Read in parallel and bounded, outside any transaction, before any guarded
    block (API CR-01). Shared by the address comparison (04-07).
    """
    argiles, amont = await asyncio.gather(
        repere_argiles_au_point(point, hosting, as_of),
        lire_amont_lieu(point, fiche.identite.commune.code_insee, hosting),
    )
    return argiles, amont


async def lire_lieu(
    session: AsyncSession,
    ban_id: str,
    fiche: FicheAdresse,
    point: PointBan | None,
    hosting: RouteHosting,
    scope: InstanceScope,
    amonts: tuple[ValeurLieu, AmontLieu],
    as_of: date,
) -> FicheAdresse:
    """①, ②, ④ and the themes of a served identity, each in its guarded block.

    Called once the upstreams are read (:func:`lire_amonts`). The address
    comparison calls it for every column in one transaction: the shared
    publication lock of the first guarded block holds until its end, so every
    column reads the same generation (N-01, 04-07).
    """
    argiles, amont = amonts
    commune = fiche.identite.commune
    reperes = ReperesAdresse(
        dpe=await repere_documents(session, ban_id, hosting, as_of),
        ventes=await repere_ventes(session, fiche.identite.parcelle, hosting),
        argiles=argiles,
        reference_commune=await repere_reference_commune(
            session, commune, scope, hosting
        ),
    )
    themes = await lire_themes_lieu(
        session, fiche.identite, point, hosting, argiles, amont, as_of
    )
    return fiche.model_copy(update={"reperes": reperes, "themes": themes})


async def lire_identite(
    session: AsyncSession, ban_id: str, hosting: RouteHosting
) -> tuple[FicheAdresse, PointBan | None]:
    """Identity of *ban_id* and its single BAN point, if any (never served).

    Raises :class:`VoieSansNumeroError` before any read for a street key,
    :class:`AdresseInconnueError` for an unknown key and
    :class:`AdresseIndisponibleError` when a read fails. Nothing is read in
    the BAN nor in the cadastre for a departement out of coverage.
    """
    if forme_de_voie(ban_id):
        raise VoieSansNumeroError
    code_insee = ban_id.split("_", 1)[0]
    departement = commune_departement(code_insee)
    if departement is None:
        raise AdresseInconnueError
    communes = await _lire(
        session,
        _COMMUNE,
        {"codes": [code_insee, normalize_code_insee(code_insee)], "code": code_insee},
    )
    if not communes:
        raise AdresseInconnueError
    try:
        noms: dict[str, str] | None = await noms_departements(session, [departement])
    except GeocodageIndisponibleError:
        noms = None
    if noms is None:
        raise AdresseIndisponibleError from None
    commune = CommuneRef(code_insee=code_insee, nom=communes[0]["nom"])
    departement_ref = DepartementRef(code=departement, nom=noms.get(departement))

    ban = hosting.hostings["ban"]
    if ban.etat != "heberge":
        fiche = FicheAdresse(
            identite=IdentiteAdresse(
                ban_id=ban_id, commune=commune, departement=departement_ref
            ),
            couverture=CouvertureAdresse(etat="hors_couverture", motif=ban.motif),
        )
        return fiche, None

    rows = await _lire(
        session, _ADRESSES_PAR_CLE, {"ban_id": ban_id, "departement": departement}
    )
    if not rows:
        raise AdresseInconnueError

    commun = _commun(rows)
    coherent = all(len({row[field] for row in rows}) == 1 for field in _IDENTITE)
    points = {(row["lon"], row["lat"]) for row in rows}
    point = None
    if len(points) > 1:
        parcelle = RelationParcelle(
            etat="indeterminee", motif="positions_contradictoires"
        )
    elif None in next(iter(points)):
        # A BAN row without position is a gap, not a cadastre outage.
        parcelle = RelationParcelle(etat="indeterminee", motif="position_absente")
    else:
        lon, lat = points.pop()
        point = PointBan(lon=lon, lat=lat)
        parcelle = await resoudre_parcelle(session, point, hosting)
    etablie = coherent and parcelle.etat != "indeterminee"
    libelle = libelle_adresse(commun) if coherent else None
    identite = IdentiteAdresse(
        etat="etablie" if etablie else "indeterminee",
        ban_id=ban_id,
        libelle=libelle,
        numero=commun["numero"],
        rep=commun["rep"],
        nom_voie=commun["nom_voie"],
        code_postal=commun["code_postal"],
        commune=commune,
        departement=departement_ref,
        parcelle=parcelle,
    )
    fiche = FicheAdresse(
        identite=identite, couverture=CouvertureAdresse(etat="heberge")
    )
    return fiche, point


async def point_de_la_cle(
    session: AsyncSession, ban_id: str, hosting: RouteHosting
) -> PointBan | None:
    """The BAN point of *ban_id* and nothing else: one read, no identity.

    For routes that need only the position (the map proxy): none of the commune,
    department names or cadastre reads of :func:`lire_identite`, whose parcel
    relation also needs the ``cadastre`` hosting such a route does not declare.
    Same errors: :class:`VoieSansNumeroError` before any read for a street key,
    :class:`AdresseInconnueError` for a key naming no territory or no row,
    :class:`AdresseIndisponibleError` when the read fails. None when the BAN is
    out of coverage (no read), the row has no position or its rows disagree.
    """
    if forme_de_voie(ban_id):
        raise VoieSansNumeroError
    departement = commune_departement(ban_id.split("_", 1)[0])
    if departement is None:
        raise AdresseInconnueError
    if hosting.hostings["ban"].etat != "heberge":
        return None
    rows = await _lire(
        session, _ADRESSES_PAR_CLE, {"ban_id": ban_id, "departement": departement}
    )
    if not rows:
        raise AdresseInconnueError
    points = {(row["lon"], row["lat"]) for row in rows}
    if len(points) > 1:
        return None
    lon, lat = points.pop()
    if lon is None or lat is None:
        return None
    return PointBan(lon=lon, lat=lat)
