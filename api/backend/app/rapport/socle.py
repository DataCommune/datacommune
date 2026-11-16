"""What every theme file of the report imports (04.7-01).

A theme file exports ``THEME``, a :class:`DefinitionTheme`: its id, section,
order, title, declared granularity, catalogue sources, its database reader
``lire`` and, for a live source, its upstream reader ``amont``. The registry
(:mod:`app.rapport.registre`) discovers the files; nothing central is edited
to add one.

Readers get a context: :class:`ContexteAmont` (outside any transaction, API
CR-01) or :class:`ContexteRapport` (inside the transaction of the themes).
Both share one :class:`Memo` per request, so that several themes reading the
same upstream or the same block of the address page read it once.

The builders below (:func:`theme`, :func:`sans_reponse`) fill the title,
section, sources and attachment of the definition, and give the states
without answer their tone and their plain verdict.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.blocs import faits_de_provenance, lire_bloc, periode_de_source
from app.adresse.lieu import Contexte as ContexteLieu
from app.adresse.lieu import PanneAmont as PanneAmont
from app.adresse.relations import PointBan
from app.adresse.schemas import IdentiteAdresse, RelationParcelle
from app.common.geo import normalize_code_insee
from app.common.source_catalog import DATASET_CATALOG
from app.rapport.libelles import NNBSP, arrondi
from app.rapport.schemas import (
    ArrondissementRef,
    Chiffre,
    EtatTheme,
    Granularite,
    ModeSource,
    NiveauGranularite,
    Rattachement,
    Section,
    SourceTheme,
    Theme,
    Ton,
)
from app.scope.dependencies import RouteHosting
from app.scope.model import InstanceScope


class Memo:
    """Results shared by the themes of one request, computed once.

    Each key holds the task of its first caller; later callers await the
    same task. Never shared between requests.
    """

    def __init__(self) -> None:
        self._taches: dict[str, asyncio.Future] = {}

    async def obtenir[T](self, cle: str, fabrique: Callable[[], Awaitable[T]]) -> T:
        tache = self._taches.get(cle)
        if tache is None:
            tache = self._taches[cle] = asyncio.ensure_future(fabrique())
        return await tache


@dataclass(frozen=True)
class ContexteAmont:
    """What an upstream reader gets: no session, no transaction open."""

    identite: IdentiteAdresse
    point: PointBan | None
    hosting: RouteHosting
    as_of: date
    memo: Memo


@dataclass(frozen=True)
class ContexteRapport:
    """What a database reader gets, inside the transaction of the themes.

    ``amonts`` maps a theme id to the answer of its upstream reader, or to
    :class:`PanneAmont` (the outage of the address page) when it raised.
    """

    session: AsyncSession
    identite: IdentiteAdresse
    point: PointBan | None
    hosting: RouteHosting
    scope: InstanceScope
    as_of: date
    memo: Memo
    amonts: dict[str, Any] = field(default_factory=dict)

    @property
    def lieu(self) -> ContexteLieu:
        """The context of the readers of the address page (``app.adresse.lieu``)."""
        return ContexteLieu(
            identite=self.identite, point=self.point, hosting=self.hosting
        )


@dataclass(frozen=True)
class DefinitionTheme:
    """One theme of the report, as its file declares it.

    ``rattachement_entite`` set means the attachment is mandatory (DPE,
    building, condominium, parcel): every state carries it, the states
    without answer with the confidence ``incertaine``.
    """

    id: str
    section: Section
    ordre: int
    titre: str
    granularite: Granularite
    sources: tuple[str, ...]
    lire: Callable[[ContexteRapport], Awaitable[Theme]]
    amont: Callable[[ContexteAmont], Awaitable[Any]] | None = None
    rattachement_entite: str | None = None


# Plain verdicts of the states without answer: the badge says the state.
VERDICTS_SANS_REPONSE: dict[str, str] = {
    "manque": "Information non publiée pour cette adresse.",
    "hors_couverture": "Source non couverte ici par DataCommune.",
    "panne": "Source momentanément indisponible : réessayez plus tard.",
    "en_cours": "La source n\u2019a pas encore répondu.",
}

# Closed map of the value states of the address page (D-04) to the states of
# a theme: a gap of any kind is a gap, an outage is never a zero.
_ETATS_VALEUR: dict[str, EtatTheme] = {
    "value": "ok",
    "not_reported": "manque",
    "not_available": "manque",
    "insufficient_sample": "manque",
    "suppressed": "manque",
    "not_qualified": "manque",
    "zero_denominator": "manque",
    "out_of_coverage": "hors_couverture",
    "temporary_error": "panne",
    "not_applicable": "non_concerne",
}


def etat_de_valeur(etat_valeur: str) -> EtatTheme:
    """State of a theme for a value state of the address page."""
    return _ETATS_VALEUR[etat_valeur]


def granularite(niveau: NiveauGranularite, rayon_m: int | None = None) -> Granularite:
    return Granularite(niveau=niveau, rayon_m=rayon_m)


# Sources the report reads otherwise than their catalogue policy says: the
# RNB through its imported departmental links export (``rnb_liens``), the
# BDNB through its imported open export (``bdnb_batiment_parcelle``), never
# their live API.
_MODES_LUS: dict[str, ModeSource] = {"rnb": "import", "bdnb": "import"}


def source(
    cle: str, millesime: str | None = None, mode: ModeSource | None = None
) -> SourceTheme:
    """A catalogue source; ``upstream`` sources are read live (``direct``)
    unless the reader says otherwise (the RNB links are an imported export).
    """
    entree = DATASET_CATALOG[cle]
    return SourceTheme(
        cle=cle,
        nom=entree.nom,
        producteur=entree.producteur,
        url=entree.url,
        licence=entree.licence,
        millesime=millesime,
        mode=mode
        or _MODES_LUS.get(cle)
        or ("direct" if entree.coverage_policy == "upstream" else "import"),
    )


def sources_de(
    definition: DefinitionTheme, millesimes: dict[str, str | None] | None = None
) -> list[SourceTheme]:
    """Every source of *definition*, each with its millésime when known."""
    millesimes = millesimes or {}
    return [source(cle, millesimes.get(cle)) for cle in definition.sources]


def theme(
    definition: DefinitionTheme,
    *,
    etat: EtatTheme,
    ton: Ton,
    verdict: str,
    chiffres: Iterable[Chiffre] = (),
    details: dict[str, Any] | None = None,
    rattachement: Rattachement | None = None,
    granularite: Granularite | None = None,
    sources: list[SourceTheme] | None = None,
    motif: str | None = None,
    pannes_partielles: Iterable[str] = (),
) -> Theme:
    """A theme of *definition*; its declared granularity unless overridden."""
    return Theme(
        id=definition.id,
        section=definition.section,
        titre=definition.titre,
        etat=etat,
        ton=ton,
        granularite=granularite or definition.granularite,
        verdict=verdict,
        chiffres=list(chiffres),
        details=details or {},
        rattachement=rattachement,
        sources=sources or sources_de(definition),
        motif=motif,
        pannes_partielles=list(pannes_partielles),
    )


def sans_reponse(
    definition: DefinitionTheme,
    etat: EtatTheme,
    motif: str | None = None,
    *,
    verdict: str | None = None,
    details: dict[str, Any] | None = None,
    rattachement: Rattachement | None = None,
    sources: list[SourceTheme] | None = None,
    pannes_partielles: Iterable[str] = (),
    granularite: Granularite | None = None,
) -> Theme:
    """A theme without answer (``manque``, ``hors_couverture``, ``panne``,
    ``en_cours``).

    Tone ``info``, no figure. A mandatory attachment the reader could not
    establish is served with the confidence ``incertaine`` and the reason.
    """
    if rattachement is None and definition.rattachement_entite is not None:
        rattachement = Rattachement(
            entite=definition.rattachement_entite, confiance="incertaine", motif=motif
        )
    return theme(
        definition,
        etat=etat,
        ton="info",
        verdict=verdict or VERDICTS_SANS_REPONSE[etat],
        details=details,
        rattachement=rattachement,
        sources=sources,
        motif=motif,
        pannes_partielles=pannes_partielles,
        granularite=granularite,
    )


# Reasons of a parcel relation that is not ``unique`` (N-02): never a zero,
# never the nearest parcel. Same reasons as the address page themes.
MOTIFS_RELATION: dict[str, str] = {
    "point_hors_parcelle": "point_hors_parcelle",
    "ambigue": "parcelles_multiples",
    "indeterminee": "parcelle_indeterminee",
}
VERDICTS_RELATION: dict[str, str] = {
    "point_hors_parcelle": (
        "Le point de l'adresse n'est dans aucune parcelle cadastrale."
    ),
    "parcelles_multiples": (
        "Le point de l'adresse touche plusieurs parcelles : aucune n'est retenue."
    ),
    "parcelle_indeterminee": "La parcelle de l'adresse n'est pas déterminée.",
}


def parcelle_unique(
    definition: DefinitionTheme, relation: RelationParcelle | None
) -> str | Theme:
    """The parcel containing the point when it is ``unique``; otherwise the
    theme of the relation, whose attachment says the reason and the number
    of candidates.
    """
    if relation is not None and relation.etat == "unique" and relation.id_parcelle:
        return relation.id_parcelle
    entite = definition.rattachement_entite or "parcelle_du_point"
    motif: str | None
    if relation is None:
        etat: EtatTheme = "manque"
        motif = "parcelle_indeterminee"
    elif relation.etat == "hors_couverture":
        etat, motif = "hors_couverture", relation.motif
    elif relation.etat == "indisponible":
        etat, motif = "panne", "lecture_indisponible"
    else:
        etat = "manque"
        motif = MOTIFS_RELATION.get(relation.etat, relation.motif)
    rattachement = Rattachement(
        entite=entite,
        confiance="incertaine",
        candidats=relation.nombre_candidats if relation is not None else None,
        motif=motif,
    )
    return sans_reponse(
        definition,
        etat,
        motif,
        verdict=VERDICTS_RELATION.get(motif or "") if etat == "manque" else None,
        rattachement=rattachement,
    )


# Cities of the arrondissements, and the number of their first arrondissement.
VILLES_PLM = {"75056": ("Paris", 75100), "69123": ("Lyon", 69380),
              "13055": ("Marseille", 13200)}  # fmt: skip


def nom_arrondissement(code: str) -> str:
    """« 12e arrondissement de Paris », « 1er arrondissement de Lyon »."""
    ville, base = VILLES_PLM[normalize_code_insee(code)]
    rang = int(code) - base
    return f"{'1er' if rang == 1 else f'{rang}e'} arrondissement de {ville}"


def granularite_arrondissement(code: str) -> Granularite:
    """The granularity of a value read at the arrondissement *code*."""
    return Granularite(
        niveau="arrondissement",
        arrondissement=ArrondissementRef(code=code, nom=nom_arrondissement(code)),
    )


@dataclass(frozen=True)
class LignesCommunales:
    """Rows of a communal source for the address: its own code first, else
    the parent commune of an arrondissement; ``territoire`` says which, and
    ``code_adresse`` is the code of the address."""

    lignes: list[dict]
    territoire: str
    periode: str | None
    code_adresse: str = ""

    def situer(self, lu: Theme) -> Theme:
        """*lu* at the real scale of the rows read (04.7-09): an
        arrondissement read at the arrondissement; for an address in an
        arrondissement read at the commune, the value is said communal (its
        verdict names the city through :attr:`lieu`). A commune outside
        Paris, Lyon and Marseille is unchanged."""
        commune = normalize_code_insee(self.code_adresse)
        if commune == self.code_adresse:
            return lu
        details = dict(lu.details)
        details["valeur_communale"] = self.territoire != self.code_adresse
        if not details["valeur_communale"]:
            niveau = granularite_arrondissement(self.territoire)
            return lu.model_copy(update={"granularite": niveau, "details": details})
        return lu.model_copy(update={"details": details})

    @property
    def lieu(self) -> str:
        """Where the rows were read, for a verdict: « la commune »,
        « l'arrondissement », or the city when an address in an arrondissement
        is read at its commune (the value is then said of the city)."""
        commune = normalize_code_insee(self.code_adresse)
        if commune == self.code_adresse:
            return "la commune"
        if self.territoire == self.code_adresse:
            return "l'arrondissement"
        return VILLES_PLM[commune][0]


async def lignes_communales(
    contexte: ContexteRapport,
    definition: DefinitionTheme,
    dataset: str,
    requete: Callable[[AsyncSession, list[str]], Awaitable[list[dict]]],
) -> LignesCommunales | Theme:
    """Rows of *requete* for the commune of the address, in a guarded block.

    Returns the theme without answer when *dataset* is not hosted or the
    read fails (never an empty list then).
    """
    hebergement = contexte.hosting.hostings[dataset]
    if hebergement.etat != "heberge":
        return sans_reponse(definition, "hors_couverture", hebergement.motif)
    code = contexte.identite.commune.code_insee
    codes = list(dict.fromkeys([code, normalize_code_insee(code)]))
    session = contexte.session
    bloc = await lire_bloc(session, [dataset], lambda: requete(session, codes))
    if bloc.en_panne or bloc.valeur is None:
        return sans_reponse(definition, "panne", bloc.panne)
    periode = periode_de_source(await faits_de_provenance(session, dataset))
    for territoire in codes:
        lignes = [
            ligne for ligne in bloc.valeur if ligne.get("code_insee") == territoire
        ]
        if lignes:
            return LignesCommunales(lignes, territoire, periode, code)
    return LignesCommunales([], code, periode, code)


_MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def mois_annee(jour: date | str) -> str:
    """« mars 2023 » for a date or an ISO date."""
    if isinstance(jour, str):
        jour = date.fromisoformat(jour[:10])
    return f"{_MOIS[jour.month - 1]} {jour.year}"


def date_longue(jour: date | str) -> str:
    """« 17 septembre 2004 », « 1er mars 2011 », for a date or an ISO date."""
    if isinstance(jour, str):
        jour = date.fromisoformat(jour[:10])
    quantieme = "1er" if jour.day == 1 else str(jour.day)
    return f"{quantieme} {mois_annee(jour)}"


def en_entier(valeur: object) -> int:
    """A served count; anything else is malformed (the theme's outage)."""
    if isinstance(valeur, bool) or not isinstance(valeur, int | float | str):
        raise TypeError("not a number")
    return int(valeur)


def en_reel(valeur: object) -> float:
    if isinstance(valeur, bool) or not isinstance(valeur, int | float | str):
        raise TypeError("not a number")
    return float(valeur)


def nombre(valeur: float | int) -> str:
    """French writing of a number: thousands split by a narrow space, one
    decimal at most, never padded, rounded as ``nombre_fr``."""
    texte = f"{arrondi(valeur, 1).normalize():,f}"
    return texte.replace(",", NNBSP).replace(".", ",")


def pluriel(n: int, singulier: str, pluriel_: str | None = None) -> str:
    """« 1 vente », « 3 ventes »."""
    mot = singulier if n == 1 else (pluriel_ or f"{singulier}s")
    return f"{nombre(n)} {mot}"
