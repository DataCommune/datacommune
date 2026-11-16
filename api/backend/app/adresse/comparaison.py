"""Addresses compared with the commune rule (04-07, CMP-01, D-102, D-116).

Two to four addresses, one column each, in four groups of rows: « Documents à
l'adresse », « Ventes », « Lieu », then « Commune de l'adresse (référence) »,
always last: the values of the commune never mix with those of the place.
« Ventes » ends with the sales declared at the address (C40), counted by the
reader of the C37 route in the same transaction, never by a copy of its query,
under the rule the page reads (number and suffix, C44, plan 04.5-08).

Each column is read by the readers of the address page (04-02 to 04-06), never
by a second engine: identity, then the upstreams of every column in parallel
outside any transaction (API CR-01), then the guarded blocks of every column
in ONE transaction. The shared publication lock taken by the first guarded
block (``guard_publication``, transaction-level) holds until its end: every
column reads the same generation of DPE, DVF and broadband (N-01). A refused
publication turns only the rows of its source into ``temporary_error``, in
every column; nothing masks the whole comparison.

Gaps, reasons and verdicts come from :func:`app.commune.comparison.compare_cell`
(the 03-09 rule): the reason of a column out of coverage names the address, a
comparable non-numeric value (an exposure, a zone, an eligibility, a date) gets
the verdict ``identique`` / ``different`` without a figure, and two absences of
the same state, reason and period stay identical (D-116). Nothing is
attached to a dwelling.

Designation (04-11, D-102): the visitor may designate, in the link only, one
DPE per address (``designes=<ban_id>:<numero>``). A pair is kept only when the
number is declared at this address (:func:`valider_designations`, read in the
DPE block of the column, same population as the list of the address page);
the document is then read by the reader of the document page and its rows,
values, gaps and reasons come from the single documentary projection
(``app.dpe.comparison_projection``, 04-10): the same couple of documents gets
the same answer here and on ``/v1/dpe/comparaison``. A refused DPE
publication or a failed read keeps the pair, its rows in ``temporary_error``:
an unread fact is never called « not declared ». No designation is stored.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.blocs import MOTIF_HORS_PERIMETRE, Bloc, lire_bloc, source_valeur
from app.adresse.documents import departement_de_cle, methode_document
from app.adresse.lieu import (
    FIELD_ARRETS,
    FIELD_BATIMENTS,
    FIELD_CONTENANCE,
    FIELD_FIBRE,
    FIELD_HAUTEUR,
    FIELD_ZONE_PLU,
)
from app.adresse.relations import PointBan
from app.adresse.schemas import (
    CelluleComparaisonAdresse,
    CleGroupeComparaison,
    ColonneAdresse,
    ComparaisonAdresses,
    DesignationDpe,
    DesignationEcartee,
    FicheAdresse,
    GroupeComparaisonAdresse,
    LigneComparaisonAdresse,
    SelectionPlusRecent,
    ValeurLieu,
    ValeurTheme,
)
from app.adresse.service import (
    AdresseInconnueError,
    VoieSansNumeroError,
    lire_amonts,
    lire_identite,  # the identity reader of the page itself (04-02), no copy
    lire_lieu,
)
from app.adresse.ventes import (
    FIELD_VENTES_ADRESSE_NOMBRE,
    RegleVentesAdresse,
    compte_ventes_adresse,
)
from app.common.semantic_contract import load_registry
from app.commune.comparison import ValeurServie, Verdict, compare_cell
from app.dpe import comparison_projection
from app.dpe.comparison_projection import ColonneSansDocument
from app.dpe.document_public import (
    DocumentDpePublic,
    DocumentIndisponibleError,
    DpeInconnuError,
    lire_document_public,
)
from app.dpe.queries import DOCUMENTS_ADRESSE_POPULATION_SQL
from app.resolve import telemetry
from app.scope.dependencies import RouteHosting, commune_departement, route_hosting
from app.scope.model import InstanceScope

logger = logging.getLogger(__name__)

# Keys served by the address page (``/v1/adresse/{ban_id}``), hence by each
# column of the comparison; hosting computed per column (N-07).
CLES_FICHE = (
    "ban",
    "cadastre",
    "dpe",
    "dvf",
    "georisques",
    "commune",
    "batiments",
    "rnb",
    "copropriete",
    "urbanisme",
    "connectivite",
    "transports",
    "gares",
)

FIELD_PLUS_RECENT = "adresse.dpe.plus_recent"

TITRES_GROUPES: dict[CleGroupeComparaison, str] = {
    "documents": "Documents à l'adresse",
    "ventes": "Ventes",
    "lieu": "Lieu",
    "commune_reference": "Commune de l'adresse (référence)",
}

# Compared value of a nearest-stop row without any stop within the radius: a
# category, never a distance (D-15 API WR-01).
AUCUN_ARRET_DANS_LE_RAYON = ("aucun_arret_dans_le_rayon",)

# Reason of a categorical row whose list is read in part on one side (zones of
# the plan): the verdict is not known (D-15 API WR-04).
MOTIF_LISTE_INCOMPLETE = "liste_incomplete"

# A column without designation, on the rows of the designated documents: not
# a state of a datum, the page says « Aucun DPE désigné » without a badge.
MOTIF_AUCUN_DPE_DESIGNE = "aucun_dpe_designe"
# Rows of the designated document, after the two rows of the documents group.
PREFIXE_DESIGNE = "designe_"
# The number is declared at the address: the population of the address page
# (equality on the source key within its partition, tombstones excluded).
_DECLARE_A_L_ADRESSE = text(f"""
    SELECT 1
    {DOCUMENTS_ADRESSE_POPULATION_SQL}
      AND d.numero_dpe = :numero
    LIMIT 1
""")


@dataclass(frozen=True)
class Lue:
    """One served cell and the value the rule compares (None for an absence)."""

    cellule: ValeurTheme
    comparee: Any = None
    selection: SelectionPlusRecent | None = None
    # Unit of the compared value when it is not the unit of the field (the
    # distance of the nearest stop, read in metres on a count field).
    unite: str | None = None
    # Calculation method of a compared DPE class (« Le plus récent »): before
    # or after July 2021 (R06-09/7, D-111); None for a value without method.
    methode: str | None = None
    # A list read in part (zones of the plan): its verdict is unknown.
    liste_incomplete: bool = False


@dataclass(frozen=True)
class Ligne:
    """A row of the comparison: its key, field, source and reader."""

    cle: str
    field_id: str
    source: str
    lire: Callable[[FicheAdresse], Lue]


# --- Readers of one row in one served page ------------------------------------


def _theme_valeur(valeur: ValeurLieu) -> ValeurTheme:
    return ValeurTheme(**valeur.model_dump(exclude={"selection"}))


def _servie(valeur: ValeurLieu) -> Lue:
    cellule = _theme_valeur(valeur)
    return Lue(cellule=cellule, comparee=cellule.valeur)


def _absente(field_id: str, source: str, etat: str, motif: str | None) -> ValeurTheme:
    contrat = load_registry()[field_id]
    return ValeurTheme(
        field_id=field_id,
        etat=etat,
        motif=motif,
        unite=contrat.unit,
        source=source_valeur(source),
        rattachement=contrat.rattachement,
    )


def _champ_du_theme(
    fiche: FicheAdresse,
    theme_cle: str,
    field_id: str,
    source: str,
    repli_field_id: str | None = None,
) -> ValeurTheme:
    """The value *field_id* of a theme, or the state of the theme itself.

    A theme served without value (out of coverage, outage, point outside the
    parcel…) gives its state and reason to its rows, as the page shows it.
    A field the page does not serve takes the served absence of
    *repli_field_id* -- a field of the same source read before it, whose
    absence is why it was not read -- or else stays ``not_reported``
    without reason: never a reason the reader did not serve (D-15 API CR-01).
    """
    theme = next((t for t in fiche.themes if t.cle == theme_cle), None)
    if theme is None or not theme.applicable:
        return _absente(field_id, source, "not_applicable", None)
    if theme.etat != "value":
        return _absente(field_id, source, theme.etat, theme.motif)
    champs = {champ.field_id: champ for g in theme.groupes for champ in g.champs}
    if field_id in champs:
        return champs[field_id]
    repli = champs.get(repli_field_id) if repli_field_id else None
    if repli is not None and repli.etat != "value":
        return _absente(field_id, source, repli.etat, repli.motif)
    return _absente(field_id, source, "not_reported", None)


def _nombre_dpe(fiche: FicheAdresse) -> Lue:
    return _servie(fiche.reperes.dpe)


def _plus_recent(fiche: FicheAdresse) -> Lue:
    """The most recent document of ①: unique, a tie or undetermined (D-109).

    The served value is the date; the selection travels with it, so the page
    names the class as ① does. A tie names no document: the compared value
    is the whole tie (date and classes), never one document chosen here.
    """
    dpe = fiche.reperes.dpe
    commun = {
        "field_id": FIELD_PLUS_RECENT,
        "unite": "none",
        "periode": dpe.periode,
        "source": dpe.source,
        "rattachement": dpe.rattachement,
    }
    selection = dpe.selection
    if dpe.etat != "value":
        return Lue(cellule=ValeurTheme(**commun, etat=dpe.etat, motif=dpe.motif))
    if selection is None or selection.etat == "indeterminee" or selection.date is None:
        motif = selection.motif if selection is not None else None
        cellule = ValeurTheme(**commun, etat="not_available", motif=motif)
        return Lue(cellule=cellule, selection=selection)
    documents = (
        [selection.document]
        if selection.etat == "unique" and selection.document is not None
        else selection.documents_ex_aequo
    )
    classes = tuple(sorted(d.etiquette_dpe or "" for d in documents))
    comparee = (selection.etat, selection.date.isoformat(), classes, selection.motif)
    cellule = ValeurTheme(**commun, etat="value", valeur=selection.date.isoformat())
    # The row shows a class: two classes of different methods get no verdict,
    # as on the rows of a designated document (D-15 API WR-02, R06-09/7).
    return Lue(
        cellule=cellule,
        comparee=comparee,
        selection=selection,
        methode=methode_document(selection.date),
    )


def _ventes(cle: str) -> Callable[[FicheAdresse], Lue]:
    return lambda fiche: _servie(getattr(fiche.reperes.ventes, cle))


def _argiles(fiche: FicheAdresse) -> Lue:
    return _servie(fiche.reperes.argiles)


def _reference(cle: str) -> Callable[[FicheAdresse], Lue]:
    return lambda fiche: _servie(getattr(fiche.reperes.reference_commune, cle))


def _theme(
    theme_cle: str, field_id: str, source: str, repli_field_id: str | None = None
) -> Callable[[FicheAdresse], Lue]:
    def lire(fiche: FicheAdresse) -> Lue:
        cellule = _champ_du_theme(fiche, theme_cle, field_id, source, repli_field_id)
        return Lue(cellule=cellule, comparee=cellule.valeur)

    return lire


def _zone(fiche: FicheAdresse) -> Lue:
    """The zones of the plan at the point, compared by their served labels."""
    cellule = _champ_du_theme(fiche, "urbanisme", FIELD_ZONE_PLU, "urbanisme")
    if cellule.etat != "value":
        return Lue(cellule=cellule)
    libelles = tuple(sorted(element.libelle for element in cellule.elements))
    return Lue(
        cellule=cellule,
        comparee=(libelles, cellule.liste_incomplete),
        liste_incomplete=cellule.liste_incomplete,
    )


def _arret(fiche: FicheAdresse) -> Lue:
    """The nearest stop: the distance of the first served stop, in metres.

    No stop within the radius is an observed zero (D-113), never a zero
    distance nor a missing datum (D-15 API WR-01): it is compared as a
    category, so two such columns are « Identique » and a column with a stop
    is « Différent », without a figure. A disc that crosses a departement not
    hosted may hide a nearer stop: no verdict (C16). The three nearest of a
    longer list (API IN-01) keep theirs: the nearest is known.
    """
    cellule = _champ_du_theme(fiche, "reseaux_transports", FIELD_ARRETS, "transports")
    comparee = None
    if cellule.etat == "value":
        distances = [e.distance_m for e in cellule.elements if e.distance_m is not None]
        if distances:
            comparee = min(distances)
        elif not cellule.elements:
            comparee = AUCUN_ARRET_DANS_LE_RAYON
    return Lue(
        cellule=cellule,
        comparee=comparee,
        unite="metre",
        liste_incomplete=cellule.liste_incomplete
        and cellule.motif == MOTIF_HORS_PERIMETRE,
    )


GROUPES: tuple[tuple[CleGroupeComparaison, tuple[Ligne, ...]], ...] = (
    (
        "documents",
        (
            Ligne("dpe_declares", "adresse.dpe.nombre_declares", "dpe", _nombre_dpe),
            Ligne("dpe_plus_recent", FIELD_PLUS_RECENT, "dpe", _plus_recent),
        ),
    ),
    (
        "ventes",
        (
            Ligne(
                "ventes_parcelle",
                "adresse.dvf.ventes_parcelle.nombre",
                "dvf",
                _ventes("nombre"),
            ),
            Ligne(
                "derniere_vente",
                "adresse.dvf.ventes_parcelle.derniere",
                "dvf",
                _ventes("derniere"),
            ),
            # C40: read beside the page (``annexes``), by the C37 reader.
            Ligne(
                "ventes_declarees",
                FIELD_VENTES_ADRESSE_NOMBRE,
                "dvf",
                lambda fiche: _non_lue(FIELD_VENTES_ADRESSE_NOMBRE, "dvf"),
            ),
        ),
    ),
    (
        "lieu",
        (
            Ligne(
                "hauteur",
                FIELD_HAUTEUR,
                "batiments",
                # The page serves the height typed by its cause (zero, several,
                # outage of the point); out of coverage, an outage of the
                # count or a parcel off the cadastre leave it unread, said by
                # the count of the same source.
                _theme(
                    "batiment_parcelle",
                    FIELD_HAUTEUR,
                    "batiments",
                    FIELD_BATIMENTS,
                ),
            ),
            Ligne(
                "contenance",
                FIELD_CONTENANCE,
                "cadastre",
                _theme("batiment_parcelle", FIELD_CONTENANCE, "cadastre"),
            ),
            Ligne(
                "argiles", "adresse.risques.argiles_au_point", "georisques", _argiles
            ),
            Ligne("zone_plu", FIELD_ZONE_PLU, "urbanisme", _zone),
            Ligne(
                "fibre",
                FIELD_FIBRE,
                "connectivite",
                _theme("reseaux_transports", FIELD_FIBRE, "connectivite"),
            ),
            Ligne("arret_proche", FIELD_ARRETS, "transports", _arret),
        ),
    ),
    (
        "commune_reference",
        (
            Ligne(
                "prix_median_maison",
                "adresse.reference_commune.prix_median_maison",
                "dvf",
                _reference("maisons"),
            ),
            Ligne(
                "prix_median_appartement",
                "adresse.reference_commune.prix_median_appartement",
                "dvf",
                _reference("appartements"),
            ),
        ),
    ),
)


# --- The comparison of the served pages (pure) --------------------------------


@dataclass(frozen=True)
class Designee:
    """A kept designation: the document read, or why it could not be read."""

    numero: str
    document: DocumentDpePublic | None = None
    etat: Literal["designe", "temporary_error", "out_of_coverage"] = "designe"
    motif: str | None = None

    def servie(self) -> DesignationDpe:
        """The header of the column: the class and date of the document page."""
        document = self.document
        classe = None
        etablissement = None
        if document is not None and document.reperes is not None:
            servie = document.reperes.etiquette_energie.classe
            if servie.etat == "value" and isinstance(servie.valeur, str):
                classe = servie.valeur
        if document is not None and document.dates is not None:
            etablissement = document.dates.etablissement
        return DesignationDpe(
            numero=self.numero,
            etat=self.etat,
            motif=self.motif,
            classe_energie=classe,
            date_etablissement=etablissement,
        )


def _colonne(fiche: FicheAdresse, designee: Designee | None = None) -> ColonneAdresse:
    identite = fiche.identite
    numero = f"{identite.numero or ''}{identite.rep or ''}"
    numero_voie = (
        f"{numero} {identite.nom_voie}" if numero and identite.nom_voie else None
    )
    return ColonneAdresse(
        ban_id=identite.ban_id,
        libelle=identite.libelle,
        numero_voie=numero_voie,
        code_postal=identite.code_postal,
        commune=identite.commune,
        departement=identite.departement,
        couverture=fiche.couverture,
        identite_etat=identite.etat,
        designation=designee.servie() if designee else None,
    )


def _non_lue(field_id: str, source: str) -> Lue:
    """A value read beside the page that is missing: an outage, never a zero."""
    return Lue(cellule=_absente(field_id, source, "temporary_error", None))


# Values of a column read beside its page (C40), by address key then field.
Annexes = Mapping[str, Mapping[str, ValeurLieu]]

# C40 counts the declared sales by the rule the address page asks for
# (``regle=numero_et_suffixe``, plan 04.5-08, R8 « comparateur aligné »):
# « 2 bis » counts its « 2 B » sales, « 2 » its sales without suffix.
REGLE_VENTES_DECLAREES: RegleVentesAdresse = "numero_et_suffixe"


def _lue(fiche: FicheAdresse, ligne: Ligne, annexes: Annexes) -> Lue:
    if fiche.couverture.etat != "heberge" or fiche.reperes is None:
        # Nothing of the place is read out of coverage: the column stays, empty.
        motif = fiche.couverture.motif
        cellule = _absente(ligne.field_id, ligne.source, "out_of_coverage", motif)
        return Lue(cellule=cellule)
    annexe = annexes.get(fiche.identite.ban_id, {}).get(ligne.field_id)
    if annexe is not None:
        return _servie(annexe)
    return ligne.lire(fiche)


def _comparable(ligne: Ligne, fiche: FicheAdresse, lue: Lue) -> ValeurServie:
    cellule = lue.cellule
    contrat = load_registry()[ligne.field_id]
    return ValeurServie(
        etat=cellule.etat,
        valeur=lue.comparee if cellule.etat == "value" else None,
        periode=cellule.periode,
        # The served unit, the contract's when none is served (D-15 API IN-11).
        unite=lue.unite or cellule.unite or contrat.unit,
        definition=(ligne.field_id,),
        hors_couverture=fiche.couverture.etat != "heberge",
        motif=cellule.motif if cellule.etat != "value" else None,
        methode=lue.methode if cellule.etat == "value" else None,
    )


def _ligne(
    ligne: Ligne, fiches: Sequence[FicheAdresse], annexes: Annexes
) -> LigneComparaisonAdresse:
    contrat = load_registry()[ligne.field_id]
    lues = [_lue(fiche, ligne, annexes) for fiche in fiches]
    comparables = [
        _comparable(ligne, fiche, lue) for fiche, lue in zip(fiches, lues, strict=True)
    ]
    identiques = not any(valeur.hors_couverture for valeur in comparables)
    cellules = []
    for index, (fiche, lue) in enumerate(zip(fiches, lues, strict=True)):
        verdict = (
            compare_cell(
                contrat,
                comparables[0],
                comparables[index],
                hors_couverture="adresse_hors_couverture",
                categoriel=True,
            )
            if index
            else None
        )
        if (
            verdict is not None
            and verdict.motif is None
            and (lues[0].liste_incomplete or lue.liste_incomplete)
        ):
            # Zones not read, or a stop beyond the hosted departements, may
            # differ: no verdict nor gap on a list read in part, never
            # « Identique » (D-15 API WR-04, C16).
            verdict = Verdict(ecart=None, motif=MOTIF_LISTE_INCOMPLETE, identique=False)
        identiques = identiques and (verdict is None or verdict.identique)
        cellules.append(
            CelluleComparaisonAdresse(
                **lue.cellule.model_dump(),
                ban_id=fiche.identite.ban_id,
                selection=lue.selection,
                ecart=verdict.ecart if verdict else None,
                motif_non_calcul=verdict.motif if verdict else None,
                verdict=verdict.categorie if verdict else None,
                identique_a_la_reference=verdict.identique if verdict else None,
            )
        )
    return LigneComparaisonAdresse(
        cle=ligne.cle, field_id=ligne.field_id, identiques=identiques, cellules=cellules
    )


def _colonne_documentaire(
    fiche: FicheAdresse, designee: Designee | None
) -> DocumentDpePublic | ColonneSansDocument:
    """What a column compares on the rows of the designated documents."""
    if fiche.couverture.etat != "heberge" or fiche.reperes is None:
        motif = fiche.couverture.motif
        return ColonneSansDocument("out_of_coverage", motif, hors_couverture=True)
    if designee is None:
        return ColonneSansDocument("not_applicable", MOTIF_AUCUN_DPE_DESIGNE)
    if designee.document is None:
        return ColonneSansDocument(designee.etat, designee.motif)
    return designee.document


def _lignes_designees(
    fiches: Sequence[FicheAdresse], designations: Mapping[str, Designee]
) -> list[LigneComparaisonAdresse]:
    """The rows of the designated documents, by the projection of 04-10.

    Values, gaps, reasons and verdicts are those of the direct comparison of
    the same documents; a column without document says its absence.
    """
    colonnes = [
        _colonne_documentaire(fiche, designations.get(fiche.identite.ban_id))
        for fiche in fiches
    ]
    groupes = comparison_projection.comparer_projections(
        colonnes, hors_couverture="adresse_hors_couverture"
    )
    lignes = []
    for groupe in groupes:
        for ligne in groupe.lignes:
            cellules = []
            for fiche, comparee in zip(fiches, ligne.cellules, strict=True):
                verdict = comparee.verdict
                cellules.append(
                    CelluleComparaisonAdresse(
                        **comparee.valeur.cellule.model_dump(),
                        ban_id=fiche.identite.ban_id,
                        ecart=verdict.ecart if verdict else None,
                        motif_non_calcul=verdict.motif if verdict else None,
                        verdict=verdict.categorie if verdict else None,
                        identique_a_la_reference=(
                            verdict.identique if verdict else None
                        ),
                    )
                )
            lignes.append(
                LigneComparaisonAdresse(
                    cle=f"{PREFIXE_DESIGNE}{ligne.cle}",
                    field_id=ligne.field_id,
                    identiques=ligne.identiques,
                    designe=True,
                    cellules=cellules,
                )
            )
    return lignes


def construire_comparaison(
    fiches: Sequence[FicheAdresse],
    ids_ecartes: Sequence[str] = (),
    designations: Mapping[str, Designee] | None = None,
    designations_ecartees: Sequence[tuple[str, str]] = (),
    annexes: Annexes | None = None,
) -> ComparaisonAdresses:
    """The comparison of the served pages *fiches* (column 1 first). Pure.

    *designations*: the kept designations by address key (04-11); their rows
    follow the two rows of « Documents à l'adresse » when one column at least
    has one. *designations_ecartees*: the pairs set aside, named as served.
    *annexes*: the values read beside the page of each column (C40, the
    declared sales); a hosted column without its value gets an outage.
    """
    annexes = annexes or {}
    cles = {fiche.identite.ban_id for fiche in fiches}
    designations = {
        ban_id: designee
        for ban_id, designee in (designations or {}).items()
        if ban_id in cles
    }
    groupes = []
    if fiches:
        groupes = [
            GroupeComparaisonAdresse(
                cle=cle,
                titre=TITRES_GROUPES[cle],
                lignes=[_ligne(ligne, fiches, annexes) for ligne in lignes]
                + (
                    _lignes_designees(fiches, designations)
                    if cle == "documents" and designations
                    else []
                ),
            )
            for cle, lignes in GROUPES
        ]
    return ComparaisonAdresses(
        colonnes=[
            _colonne(fiche, designations.get(fiche.identite.ban_id)) for fiche in fiches
        ],
        groupes=groupes,
        ids_ecartes=list(ids_ecartes),
        designations_ecartees=[
            DesignationEcartee(ban_id=ban_id, numero=numero)
            for ban_id, numero in designations_ecartees
        ],
    )


# --- Reading the columns -----------------------------------------------------------


@dataclass(frozen=True)
class _Identite:
    fiche: FicheAdresse
    point: PointBan | None
    hosting: RouteHosting


async def _declare_a_l_adresse(session: AsyncSession, ban_id: str, numero: str) -> bool:
    result = await session.execute(
        _DECLARE_A_L_ADRESSE,
        {
            "ban_id": ban_id,
            "department": departement_de_cle(ban_id),
            "numero": numero,
        },
    )
    return result.first() is not None


async def valider_designations(
    session: AsyncSession,
    paires: Sequence[tuple[str, str]],
    colonnes: Mapping[str, tuple[FicheAdresse, RouteHosting]],
    as_of: date,
) -> tuple[dict[str, Designee], list[tuple[str, str]]]:
    """Keep each pair whose number is declared at its address (T-04-11-01).

    *paires*: validated ``(ban_id, numero)``, one per address at most. A pair
    whose address has no column (unknown key) is dropped with it. Each pair is
    read in the DPE block of its column (``lire_bloc(session, ["dpe"], …)``,
    N-01): a refused publication or a failed read keeps the pair, typed
    ``temporary_error``; an address whose DPE is not hosted keeps it, typed
    ``out_of_coverage``. Only a number read as absent from the population of
    the address is set aside (« Lien altéré »).
    """
    designations: dict[str, Designee] = {}
    ecartees: list[tuple[str, str]] = []
    for ban_id, numero in paires:
        if ban_id not in colonnes:
            continue
        fiche, hosting = colonnes[ban_id]
        dpe = hosting.hostings["dpe"]
        if fiche.couverture.etat != "heberge" or dpe.etat != "heberge":
            motif = fiche.couverture.motif or dpe.motif
            designations[ban_id] = Designee(numero, etat="out_of_coverage", motif=motif)
            continue

        async def lecteur(
            ban_id: str = ban_id, numero: str = numero, hosting: RouteHosting = hosting
        ) -> DocumentDpePublic | None:
            if not await _declare_a_l_adresse(session, ban_id, numero):
                return None
            return await lire_document_public(session, numero, hosting, as_of)

        try:
            bloc = await lire_bloc(session, ["dpe"], lecteur)
        except (DocumentIndisponibleError, DpeInconnuError) as exc:
            # Declared at the address but unreadable by its number: never
            # « not declared » (the savepoint of the block is rolled back).
            # Logged as a block in outage, bounded codes only (D-15 API IN-10).
            telemetry.emit_resolve_event(
                logger,
                "address_block_unavailable",
                level=logging.WARNING,
                dataset="dpe",
                outcome="lecture_indisponible",
                error_code=telemetry.error_code_for(exc),
            )
            bloc = Bloc(panne="lecture_indisponible")
        if bloc.en_panne:
            designations[ban_id] = Designee(
                numero, etat="temporary_error", motif=bloc.panne
            )
        elif bloc.valeur is None:
            ecartees.append((ban_id, numero))
        else:
            designations[ban_id] = Designee(numero, document=bloc.valeur)
    return designations, ecartees


async def comparer_adresses(
    session: AsyncSession,
    ids: Sequence[str],
    scope: InstanceScope,
    as_of: date,
    designes: Sequence[tuple[str, str]] = (),
) -> ComparaisonAdresses:
    """Compare the addresses *ids* (validated, distinct, column 1 first).

    A key the BAN does not know, or a street without number, is named in
    ``ids_ecartes`` and gets no column. A failed identity read raises
    :class:`app.adresse.service.AdresseIndisponibleError` (the whole
    comparison is unavailable: its columns cannot be named). *designes*: the
    validated designations, read after the blocks of every column, in the
    same transaction (one generation of the DPE for the whole comparison).
    """
    identites: list[_Identite] = []
    ecartes: list[str] = []
    for ban_id in ids:
        departement = commune_departement(ban_id.split("_", 1)[0])
        hosting = route_hosting(CLES_FICHE, scope, departement)
        try:
            fiche, point = await lire_identite(session, ban_id, hosting)
        except (AdresseInconnueError, VoieSansNumeroError):
            ecartes.append(ban_id)
            continue
        identites.append(_Identite(fiche=fiche, point=point, hosting=hosting))
    # Read-only so far, no publication lock taken yet: give the connection back
    # before the upstreams answer (API CR-01).
    await session.rollback()
    servies = [i for i in identites if i.fiche.couverture.etat == "heberge"]
    amonts = await asyncio.gather(
        *(lire_amonts(i.fiche, i.point, i.hosting, as_of) for i in servies)
    )
    amont_de = {id(i): amont for i, amont in zip(servies, amonts, strict=True)}
    fiches = []
    annexes: dict[str, dict[str, ValeurLieu]] = {}
    for identite in identites:
        if id(identite) not in amont_de:
            fiches.append(identite.fiche)
            continue
        ban_id = identite.fiche.identite.ban_id
        fiches.append(
            await lire_lieu(
                session,
                ban_id,
                identite.fiche,
                identite.point,
                identite.hosting,
                scope,
                amont_de[id(identite)],
                as_of,
            )
        )
        # C40: in the same transaction, under the same DVF generation, by the
        # rule the address page reads (number and suffix, 04.5-08, R8).
        annexes[ban_id] = {
            FIELD_VENTES_ADRESSE_NOMBRE: await compte_ventes_adresse(
                session, ban_id, identite.hosting, regle=REGLE_VENTES_DECLAREES
            )
        }
    designations: dict[str, Designee] = {}
    designations_ecartees: list[tuple[str, str]] = []
    if designes:
        colonnes = {
            fiche.identite.ban_id: (fiche, identite.hosting)
            for fiche, identite in zip(fiches, identites, strict=True)
        }
        designations, designations_ecartees = await valider_designations(
            session, designes, colonnes, as_of
        )
    return construire_comparaison(
        fiches, ecartes, designations, designations_ecartees, annexes
    )
