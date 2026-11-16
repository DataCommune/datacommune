"""Readers shared by several themes, read once per request (04.7-01).

Upstream readers (outside any transaction, API CR-01) and database readers
(inside the transaction of the themes) that more than one theme needs: each
goes through the :class:`~app.rapport.socle.Memo` of the request, so the
themes that share it trigger one read and get one answer.

They reuse the readers of the address page (``app.adresse.lieu``,
``app.adresse.risques``) and the clients of the live sources; nothing is
rewritten. Later plans import them and add their own below.
"""

import asyncio
import csv
import io
import math
import re
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import TypeGuard
from xml.etree import ElementTree

import httpx
from cachetools import TTLCache  # type: ignore[import-untyped]

from app.adresse import lieu, risques
from app.adresse.blocs import lire_bloc, rayon_deborde
from app.adresse.relations import PointBan
from app.adresse.schemas import ThemeLieu, ValeurLieu, ValeurTheme
from app.common.exceptions import UpstreamUnavailableError
from app.common.geo import normalize_code_insee
from app.common.http import CONTEXTE_TLS
from app.config import settings
from app.errial.client import (
    extract_avis_summary,
    query_avis,
    resolve_parcelle_by_coords,
)
from app.georisques.client import (
    GEORISQUES_BASE,
    GEORISQUES_HEADERS,
    _safe_json,
)
from app.rapport.libelles import arrondi
from app.rapport.schemas import NiveauGranularite, SourceTheme, Theme, Ton
from app.rapport.socle import (
    ContexteAmont,
    ContexteRapport,
    DefinitionTheme,
    PanneAmont,
    etat_de_valeur,
    granularite,
    sans_reponse,
    source,
    sources_de,
    theme,
)
from app.transport.queries import gare_la_plus_proche

# Deadline of one live source of the report: the one of the address page.
DEADLINE_S = lieu.AMONT_DEADLINE_S
# A view served in two steps (``app.rapport.suite``) gives its reads a later
# deadline: the page no longer waits for them, so a read slower than the
# deadline of a page is not an outage yet. Tasks inherit it at their creation.
ECHEANCE: ContextVar[float | None] = ContextVar("echeance_amont", default=None)


def echeance_s() -> float:
    """Deadline of one live source, for the view that started the read."""
    return ECHEANCE.get() or DEADLINE_S


_UPSTREAM_ERRORS = (UpstreamUnavailableError, TimeoutError, httpx.HTTPError)

# Communal risks of Géorisques, by INSEE code: shared by every address of
# the commune. Kept a day (the proxy TTL), only when every sub-source
# answered: a partial answer is never kept, so an outage never outlives its
# source.
_risques_commune_cache: TTLCache = TTLCache(
    maxsize=4096, ttl=settings.proxy_cache_ttl_s
)


async def risques_commune(code_insee: str) -> risques.RisquesCommune:
    garde = _risques_commune_cache.get(code_insee)
    if garde is not None:
        return garde
    lu = await risques.lire_risques_commune(code_insee)
    if all(lignes is not None for lignes in lu.lignes.values()):
        _risques_commune_cache[code_insee] = lu
    return lu


def vider_caches() -> None:
    _risques_commune_cache.clear()
    for cache in _CACHES:
        cache.vider()
    for fichier in _FICHIERS:
        fichier.vider()


async def amont_lieu(contexte: ContexteAmont) -> lieu.AmontLieu:
    """Communal risks (cached) and planning at the point, as the address page."""

    async def communes():
        if contexte.hosting.hostings["georisques"].etat != "heberge":
            return None
        try:
            return await risques_commune(contexte.identite.commune.code_insee)
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("georisques", exc)

    async def lire() -> lieu.AmontLieu:
        commune, urbanisme = await asyncio.gather(
            communes(), lieu._urbanisme(contexte.point, contexte.hosting)
        )
        return lieu.AmontLieu(risques_commune=commune, urbanisme=urbanisme)

    return await contexte.memo.obtenir("lieu.amont", lire)


async def risques_commune_amont(contexte: ContexteAmont) -> object:
    """The communal Géorisques answer of the shared upstream read."""
    return (await amont_lieu(contexte)).risques_commune


def risque_communal(
    contexte: ContexteRapport, definition: DefinitionTheme, phenomene: str
) -> ValeurTheme | Theme:
    """One communal phenomenon (GASPAR, radon, ICPE) read by INSEE code, as
    the « Risques » theme of the address page serves it; or the theme without
    answer (not hosted, outage, nothing published)."""
    sources = sources_de(definition, {"georisques": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["georisques"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            definition, "hors_couverture", hebergement.motif, sources=sources
        )
    lu = contexte.amonts.get(definition.id)
    if not isinstance(lu, risques.RisquesCommune):
        return sans_reponse(definition, "panne", "amont_indisponible", sources=sources)
    valeur = lieu._champ_commune(phenomene, lu, contexte.as_of)
    if valeur.etat != "value":
        return sans_reponse(
            definition, etat_de_valeur(valeur.etat), valeur.motif, sources=sources
        )
    return valeur


async def argiles(contexte: ContexteAmont) -> ValeurLieu:
    """Clay exposure at the BAN point: landmark ③ of the address page."""
    return await contexte.memo.obtenir(
        "georisques.rga",
        lambda: risques.repere_argiles_au_point(
            contexte.point, contexte.hosting, contexte.as_of
        ),
    )


@dataclass(frozen=True)
class ErrialLu:
    """ERRIAL assessment of the parcel ERRIAL finds at the BAN point.

    ``concordance`` compares that parcel with the parcel of the address
    (``unique`` relation): True, False, or None when the address has no
    single parcel.
    """

    parcelle: str
    concordance: bool | None
    resume: dict
    # TRI flood extents ERRIAL finds on the parcel, with their hazard (04.7-04).
    tris: list[dict] = field(default_factory=list)


def _tris_errial(brut: dict) -> list[dict]:
    """ERRIAL serves one TRI as an object, several as a list."""
    tris = brut.get("tris")
    if isinstance(tris, dict):
        tris = [tris]
    if not isinstance(tris, list):
        return []
    return [
        {"libelle": tri.get("libelle_tri"), "aleas": list(tri.get("aleas") or [])}
        for tri in tris
        if isinstance(tri, dict)
    ]


def _parcelle_errial_concorde(code_errial: str, id_parcelle: str | None) -> bool | None:
    """« 000-LB-369@44109 » against « 44109000LB0369 »; None without parcel."""
    if not id_parcelle or len(id_parcelle) != 14:
        return None
    code, _, commune = code_errial.partition("@")
    morceaux = code.split("-")
    if len(morceaux) != 3:
        return False
    prefixe, section, numero = morceaux
    try:
        return (
            commune == id_parcelle[:5]
            and prefixe.zfill(3) == id_parcelle[5:8]
            and section.zfill(2) == id_parcelle[8:10]
            and int(numero) == int(id_parcelle[10:14])
        )
    except ValueError:
        return False


async def errial(contexte: ContexteAmont) -> ErrialLu | PanneAmont | None:
    """ERRIAL at the BAN point; None when not hosted, without a single point
    or without a parcel at the point for ERRIAL."""
    if contexte.hosting.hostings["errial"].etat != "heberge" or contexte.point is None:
        return None
    point = contexte.point
    relation = contexte.identite.parcelle
    id_parcelle = (
        relation.id_parcelle
        if relation is not None and relation.etat == "unique"
        else None
    )

    async def lire() -> ErrialLu | PanneAmont | None:
        try:
            async with asyncio.timeout(echeance_s()):
                trouvee = await resolve_parcelle_by_coords(point.lat, point.lon)
                if trouvee is None:
                    return None
                code_insee, parcelle = trouvee
                brut = await query_avis(code_insee, parcelle)
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("errial", exc)
        return ErrialLu(
            parcelle=parcelle,
            concordance=_parcelle_errial_concorde(parcelle, id_parcelle),
            resume=extract_avis_summary(brut),
            tris=_tris_errial(brut),
        )

    return await contexte.memo.obtenir("errial", lire)


_AMONT_DU_THEME = object()


def errial_lu(
    contexte: ContexteRapport,
    definition: DefinitionTheme,
    amont: object = _AMONT_DU_THEME,
) -> ErrialLu | Theme:
    """The ERRIAL assessment of the address, or the theme without answer.

    *amont* is the ERRIAL answer when the theme's upstream reader returned
    it among others; by default, the theme's own upstream answer.
    """
    sources = sources_de(definition, {"errial": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["errial"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            definition, "hors_couverture", hebergement.motif, sources=sources
        )
    if contexte.point is None:
        return sans_reponse(
            definition, "manque", "position_indeterminee", sources=sources
        )
    lu = contexte.amonts.get(definition.id) if amont is _AMONT_DU_THEME else amont
    if lu is None:
        return sans_reponse(
            definition,
            "manque",
            "parcelle_errial_absente",
            verdict="ERRIAL ne trouve aucune parcelle au point de l'adresse.",
            sources=sources,
        )
    if not isinstance(lu, ErrialLu):
        return sans_reponse(definition, "panne", "amont_indisponible", sources=sources)
    return lu


async def batiment_parcelle(contexte: ContexteRapport) -> ThemeLieu:
    """« Bâtiment et parcelle » of the address page, read once."""
    return await contexte.memo.obtenir(
        "lieu.batiment_parcelle",
        lambda: lieu.theme_batiment_parcelle(contexte.session, contexte.lieu),
    )


async def reseaux_transports(contexte: ContexteRapport) -> ThemeLieu:
    """« Réseaux et transports » of the address page, read once."""
    return await contexte.memo.obtenir(
        "lieu.reseaux_transports",
        lambda: lieu.theme_reseaux_transports(contexte.session, contexte.lieu),
    )


def champ(theme: ThemeLieu, field_id: str) -> ValeurTheme | None:
    """The value *field_id* of a theme of the address page, if served."""
    return next(
        (
            c
            for groupe in theme.groupes
            for c in groupe.champs
            if c.field_id == field_id
        ),
        None,
    )


# --- 04.7-04: risks read live from Géorisques, cached ------------------------

# A dropped connection is retried once (observed 2026-10-30 on the risk
# report: ReadError in 0.2 s, then 200 on the same call).
_REESSAYABLES = (httpx.ReadError, httpx.RemoteProtocolError)
_ENTETES = {**GEORISQUES_HEADERS, "Accept": "application/json"}
PAGE = 100
# A status is read at the address only when Géorisques' address is the BAN point.
CONCORDANCE_ADRESSE_M = 20.0
_CACHES: list["CacheAmont"] = []


# Bound of a read once its page stopped waiting for it (the proxy read bound).
DELAI_FOND_S = settings.proxy_read_timeout_s


def _lecture_finie(lecture: asyncio.Future) -> None:
    # Retrieved here: the page that started it may have stopped waiting.
    if not lecture.cancelled():
        lecture.exception()


class CacheAmont:
    """A day of an upstream answer (the proxy TTL). An outage is never kept:
    a reader that raises leaves nothing behind.

    One read per key at a time: the views that need it await the same one,
    each within its own deadline. A deadline ends the wait, never the read
    (R-07, 2026-11-10: each view started a read it abandoned, and a source
    slower than the deadline stayed an outage view after view). The read ends
    in the background within ``DELAI_FOND_S``, and the next view finds it.
    """

    def __init__(self, taille: int = 4096, ttl_s: int | None = None) -> None:
        self._cache: TTLCache = TTLCache(
            maxsize=taille, ttl=ttl_s or settings.proxy_cache_ttl_s
        )
        self._lectures: dict[object, asyncio.Future] = {}
        _CACHES.append(self)

    async def obtenir(self, cle, lecteur):
        garde = self._cache.get(cle)
        if garde is not None:
            return garde
        lecture = self._lectures.get(cle)
        if (
            lecture is None
            or lecture.done()
            or lecture.get_loop() is not asyncio.get_running_loop()
        ):
            lecture = asyncio.ensure_future(self._lire(cle, lecteur))
            lecture.add_done_callback(_lecture_finie)
            self._lectures[cle] = lecture
        return await asyncio.shield(lecture)

    async def _lire(self, cle, lecteur):
        try:
            async with asyncio.timeout(DELAI_FOND_S):
                valeur = await lecteur()
            self._cache[cle] = valeur
            return valeur
        finally:
            if self._lectures.get(cle) is asyncio.current_task():
                del self._lectures[cle]

    def oublier(self, cle) -> None:
        """Forget one answer, kept although it must not outlive the request."""
        self._cache.pop(cle, None)

    def vider(self) -> None:
        self._cache.clear()
        lectures, self._lectures = self._lectures, {}
        for lecture in lectures.values():
            if not lecture.get_loop().is_closed():
                lecture.cancel()


# The deadline of the page ends its wait (5 s); these bounds end the read it
# leaves in the background. A read bound of 4 s cut the risk report of an
# address never opened, view after view (2026-11-10: 4.5 s, 4.2 s, then 0.1 s).
_TIMEOUT_GEORISQUES = httpx.Timeout(DELAI_FOND_S, connect=2.0)


async def get_georisques(chemin: str, params: dict) -> dict:
    """One GET to Géorisques API v1, retried once on a dropped connection.

    Raises :class:`UpstreamUnavailableError` on any failure or on an answer
    that is not a JSON object.
    """
    for essai in (1, 2):
        try:
            async with httpx.AsyncClient(
                verify=CONTEXTE_TLS,
                base_url=GEORISQUES_BASE,
                timeout=_TIMEOUT_GEORISQUES,
                headers=_ENTETES,
            ) as client:
                reponse = await client.get(chemin, params=params)
        except _REESSAYABLES as exc:
            if essai == 2:
                raise UpstreamUnavailableError(
                    "Georisques", "connexion coupée"
                ) from exc
            continue
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableError("Georisques", "request failed") from exc
        donnees = _safe_json(reponse)
        if donnees is None:
            raise UpstreamUnavailableError("Georisques", "invalid response")
        return donnees
    raise UpstreamUnavailableError("Georisques", "unreachable")  # pragma: no cover


@dataclass(frozen=True)
class LignesLues:
    """Rows of a paginated Géorisques list; ``complet`` is False when pages
    were left unread (bound reached) or the announced count is not met."""

    lignes: list[dict]
    complet: bool


async def lignes_georisques(
    chemin: str, params: dict, pages_max: int = 1
) -> LignesLues:
    lignes: list[dict] = []
    page = 1
    while True:
        donnees = await get_georisques(
            chemin, {**params, "page": page, "page_size": PAGE}
        )
        rangs = donnees.get("data")
        if not isinstance(rangs, list):
            raise UpstreamUnavailableError("Georisques", "invalid rows")
        lignes.extend(rang for rang in rangs if isinstance(rang, dict))
        total_pages = donnees.get("total_pages")
        pages_annoncees = total_pages if isinstance(total_pages, int) else None
        derniere = not donnees.get("next") or (
            pages_annoncees is not None and page >= pages_annoncees
        )
        if derniere:
            # Complete only when every metadata agrees that nothing is left:
            # no ``next``, no page announced beyond this one, no count above
            # the rows read. Any contradiction is a partial read.
            annonce = donnees.get("results")
            complet = (
                not donnees.get("next")
                and (pages_annoncees is None or page >= pages_annoncees)
                and (not isinstance(annonce, int) or annonce <= len(lignes))
            )
            return LignesLues(lignes=lignes, complet=complet)
        if page >= pages_max:
            return LignesLues(lignes=lignes, complet=False)
        page += 1


_LIGNES_CACHE = CacheAmont()


async def lignes_commune(
    contexte: ContexteAmont, chemin: str, pages_max: int = 1
) -> LignesLues | lieu.PanneAmont | None:
    """A Géorisques list by INSEE code of the address; None when not hosted."""
    if contexte.hosting.hostings["georisques"].etat != "heberge":
        return None
    code = contexte.identite.commune.code_insee

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                return await _LIGNES_CACHE.obtenir(
                    (chemin, code),
                    lambda: lignes_georisques(chemin, {"code_insee": code}, pages_max),
                )
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("georisques", exc)

    return await contexte.memo.obtenir(f"georisques{chemin}", lire)


async def lignes_au_point(
    contexte: ContexteAmont, chemin: str, rayon_m: int, pages_max: int = 1
) -> LignesLues | lieu.PanneAmont | None:
    """A Géorisques list in a radius of the BAN point; None when not hosted
    or without a single point."""
    if (
        contexte.hosting.hostings["georisques"].etat != "heberge"
        or contexte.point is None
    ):
        return None
    lon, lat = round(contexte.point.lon, 5), round(contexte.point.lat, 5)
    params = {"latlon": f"{lon},{lat}", "rayon": rayon_m}

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                return await _LIGNES_CACHE.obtenir(
                    (chemin, lon, lat, rayon_m),
                    lambda: lignes_georisques(chemin, params, pages_max),
                )
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("georisques", exc)

    return await contexte.memo.obtenir(f"georisques{chemin}:{rayon_m}", lire)


def distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres (sphere of 6 371 008.8 m)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_008.8 * math.asin(math.sqrt(a))


def distance_au_point(point: PointBan, ligne: dict) -> int | None:
    """Distance from the BAN point to a row carrying ``longitude``/``latitude``."""
    lon, lat = ligne.get("longitude"), ligne.get("latitude")
    if isinstance(lon, bool) or isinstance(lat, bool):
        return None
    if not isinstance(lon, int | float) or not isinstance(lat, int | float):
        return None
    return round(distance_m(point.lon, point.lat, float(lon), float(lat)))


@dataclass(frozen=True)
class StatutRisque:
    """One risk of the Géorisques risk report: listed in the commune or not,
    and its status there and at the address Géorisques geocoded."""

    present: bool
    commune: str | None
    adresse: str | None


@dataclass(frozen=True)
class RapportRisques:
    """The risk report; ``adresse_concordante`` says whether the address
    Géorisques read the statuses at is the BAN point (20 m)."""

    statuts: dict[str, StatutRisque]
    adresse_concordante: bool
    distance_m: float | None
    code_insee: str | None


# Risks the themes read: a report that lacks one is incomplete (an outage,
# never kept in cache), not a report without that risk.
RISQUES_LUS = (
    "inondation",
    "remonteeNappe",
    "ruptureBarrage",
    "reculTraitCote",
    "risqueCotier",
    "nucleaire",
    "feuForet",
)


def rapport_de(donnees: dict, point: PointBan) -> RapportRisques:
    """The risk report of Géorisques; its address is compared with the point.

    Raises :class:`UpstreamUnavailableError` for a report without the INSEE
    code of its commune or without one of :data:`RISQUES_LUS`: nothing may
    then be concluded on the commune.
    """
    adresse = donnees.get("adresse")
    groupes = [donnees.get("risquesNaturels"), donnees.get("risquesTechnologiques")]
    if not isinstance(adresse, dict) or not all(isinstance(g, dict) for g in groupes):
        raise UpstreamUnavailableError("Georisques rapport", "invalid report")
    commune = donnees.get("commune")
    code_insee = commune.get("codeInsee") if isinstance(commune, dict) else None
    if not isinstance(code_insee, str) or not code_insee.strip():
        raise UpstreamUnavailableError("Georisques rapport", "commune not identified")
    statuts: dict[str, StatutRisque] = {}
    for groupe in (g for g in groupes if isinstance(g, dict)):
        for cle, statut in groupe.items():
            if isinstance(statut, dict) and isinstance(statut.get("present"), bool):
                statuts[cle] = StatutRisque(
                    present=statut["present"],
                    commune=statut.get("libelleStatutCommune"),
                    adresse=statut.get("libelleStatutAdresse"),
                )
    if any(cle not in statuts for cle in RISQUES_LUS):
        raise UpstreamUnavailableError("Georisques rapport", "incomplete report")
    ecart = distance_au_point(point, adresse)
    return RapportRisques(
        statuts=statuts,
        adresse_concordante=ecart is not None and ecart <= CONCORDANCE_ADRESSE_M,
        distance_m=ecart,
        code_insee=code_insee.strip(),
    )


_RAPPORT_CACHE = CacheAmont()


async def rapport_risques(
    contexte: ContexteAmont,
) -> RapportRisques | lieu.PanneAmont | None:
    """The Géorisques risk report at the BAN point, read once per request
    and kept a day; None when Géorisques is not hosted or without a point."""
    if (
        contexte.hosting.hostings["georisques"].etat != "heberge"
        or contexte.point is None
    ):
        return None
    point = contexte.point
    lon, lat = round(point.lon, 5), round(point.lat, 5)

    async def lire_donnees() -> dict:
        donnees = await get_georisques(
            "/resultats_rapport_risque", {"latlon": f"{lon},{lat}"}
        )
        # Validated before it is kept: an incomplete report raises, never cached.
        rapport_de(donnees, point)
        return donnees

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                donnees = await _RAPPORT_CACHE.obtenir((lon, lat), lire_donnees)
            # The raw answer is shared by close points; the concordance of the
            # address is computed for this very point.
            return rapport_de(donnees, point)
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("georisques", exc)

    return await contexte.memo.obtenir("georisques.rapport", lire)


@dataclass(frozen=True)
class AmontInondation:
    """Upstream answers of the flood theme (each one may be an outage)."""

    rapport: object
    azi: object
    tri: object
    errial: object
    gaspar: object = None


@dataclass(frozen=True)
class AmontDebroussaillement:
    errial: object
    rapport: object


@dataclass(frozen=True)
class AmontTerrain:
    cavites: object
    mouvements: object


def est_panne(valeur: object) -> bool:
    return isinstance(valeur, lieu.PanneAmont)


@dataclass(frozen=True)
class PhrasesRisque:
    """The verdicts of one risk of the report, by case of the common rule."""

    adresse: str
    hors_zone: str
    commune: str
    non_concerne: str


def _expose(statut: str) -> bool:
    texte = statut.strip().lower()
    return texte.startswith("risque existant") or texte == "risque concerne"


def _hors_zone(statut: str) -> bool:
    return statut.strip().lower() == "risque non concerne"


def _niveau(statut: str | None) -> str | None:
    if statut and " - " in statut:
        return statut.split(" - ", 1)[1].strip()
    return None


def _sans_lecture_au_point(
    contexte: ContexteRapport, definition: DefinitionTheme, sources: list
) -> Theme | None:
    """Géorisques not hosted, or no single point: the theme without answer."""
    hebergement = contexte.hosting.hostings["georisques"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            definition, "hors_couverture", hebergement.motif, sources=sources
        )
    if contexte.point is None:
        return sans_reponse(
            definition, "manque", "position_indeterminee", sources=sources
        )
    return None


def risque_du_rapport(
    contexte: ContexteRapport,
    definition: DefinitionTheme,
    lu: object,
    cle: str,
    phrases: PhrasesRisque,
    *,
    details: dict | None = None,
    vigilance_a_l_adresse: bool = False,
    pannes_partielles: list[str] | None = None,
    gaspar: object = None,
    libelles_gaspar: tuple[str, ...] = (),
    sources_preuve: list[SourceTheme] | None = None,
) -> Theme:
    """The common rule of a risk of the Géorisques risk report (04.7-04).

    Not listed in the commune: ``non_concerne``, neutral, at the commune.
    Listed, and the address Géorisques read is the BAN point: exposed is a
    point to check at the address, « not concerned » is neutral at the
    address (outside the mapped zones, not a proof of absence). Listed with
    an unknown or distant address: a point to check at the commune.
    ``vigilance_a_l_adresse`` adds another proof of exposure at the address.

    The risk report often drops its connection (observed 2026-10-30). In
    its outage the commune's GASPAR list answers instead (*gaspar*, the
    communal Géorisques read of the address page, matched on
    *libelles_gaspar*): a fact of the commune, with the status at the
    address declared down (``statut_adresse`` in ``pannes_partielles``).
    A proof of exposure at the address (``vigilance_a_l_adresse``, from
    *sources_preuve*) prevails over the fallback: it is never contradicted
    by the commune's list.
    """
    sources = [source("georisques", contexte.as_of.isoformat())]
    pannes = list(pannes_partielles or [])
    prealable = _sans_lecture_au_point(contexte, definition, sources)
    if prealable is not None:
        return prealable
    if vigilance_a_l_adresse:
        # A proof of exposure at the address is never contradicted, neither by
        # the commune status of the report nor by the commune's list.
        return _preuve_a_l_adresse(
            contexte,
            definition,
            phrases,
            lu,
            cle,
            [*sources, *(sources_preuve or [])],
            details,
            pannes,
        )
    if not isinstance(lu, RapportRisques):
        return _repli_gaspar(
            definition, gaspar, libelles_gaspar, phrases, sources, details, pannes
        )
    if not _meme_commune(contexte, lu):
        return sans_reponse(
            definition, "manque", "commune_georisques_differente", sources=sources
        )
    statut = lu.statuts.get(cle)
    if statut is None:
        return sans_reponse(definition, "panne", "amont_indisponible", sources=sources)
    tout = {**_statuts(lu, statut), **(details or {})}
    if not statut.present:
        return theme(
            definition,
            etat="non_concerne",
            ton="neutre",
            verdict=phrases.non_concerne,
            details=tout,
            granularite=granularite("commune"),
            sources=sources,
            pannes_partielles=pannes,
        )
    adresse = statut.adresse if lu.adresse_concordante else None
    verdict, ton, niveau = _lecture_recensee(phrases, adresse)
    return theme(
        definition,
        etat="ok",
        ton=ton,
        verdict=verdict,
        details=tout,
        granularite=granularite(niveau),
        sources=sources,
        pannes_partielles=pannes,
    )


def _lecture_recensee(
    phrases: PhrasesRisque, adresse: str | None
) -> tuple[str, Ton, NiveauGranularite]:
    """Verdict, tone and scale of a risk listed in the commune, by the status
    at the address (None: unknown, or Géorisques read another address)."""
    if adresse is not None and _expose(adresse):
        return phrases.adresse, "vigilance", "adresse"
    if adresse is not None and _hors_zone(adresse):
        return phrases.hors_zone, "neutre", "adresse"
    return phrases.commune, "vigilance", "commune"


def _meme_commune(contexte: ContexteRapport, lu: RapportRisques) -> bool:
    code = contexte.identite.commune.code_insee
    return normalize_code_insee(lu.code_insee or "") == normalize_code_insee(code)


def _statuts(lu: RapportRisques, statut: StatutRisque) -> dict:
    return {
        "statut_commune": statut.commune,
        "statut_adresse": statut.adresse,
        "niveau": _niveau(statut.adresse) or _niveau(statut.commune),
        "adresse_concordante": lu.adresse_concordante,
        "ecart_adresse_georisques_m": lu.distance_m,
    }


def _preuve_a_l_adresse(
    contexte: ContexteRapport,
    definition: DefinitionTheme,
    phrases: PhrasesRisque,
    lu: object,
    cle: str,
    sources: list,
    details: dict | None,
    pannes: list[str],
) -> Theme:
    """A proof of exposure at the address (``vigilance_a_l_adresse``).

    The report read for the commune of the address keeps its statuses beside
    the proof; only the report down makes the status at the address an
    outage.
    """
    tout = {**(details or {}), "statut_adresse": None}
    if not isinstance(lu, RapportRisques):
        pannes = [*pannes, "statut_adresse"]
    elif (statut := lu.statuts.get(cle)) is not None and _meme_commune(contexte, lu):
        tout = {**_statuts(lu, statut), **(details or {})}
    return theme(
        definition,
        etat="ok",
        ton="vigilance",
        verdict=phrases.adresse,
        details=tout,
        granularite=granularite("adresse"),
        sources=sources,
        pannes_partielles=pannes,
    )


def _repli_gaspar(
    definition: DefinitionTheme,
    gaspar: object,
    libelles: tuple[str, ...],
    phrases: PhrasesRisque,
    sources: list,
    details: dict | None,
    pannes: list[str],
) -> Theme:
    """The risk at the commune from the GASPAR list, the risk report being down."""
    if not isinstance(gaspar, risques.RisquesCommune):
        return sans_reponse(definition, "panne", "amont_indisponible", sources=sources)
    lignes = gaspar.lignes.get("gaspar")
    if lignes is None or not libelles:
        return sans_reponse(definition, "panne", "amont_indisponible", sources=sources)
    recenses = sorted(
        {
            str(detail.get("libelle_risque_long")).strip()
            for ligne in lignes
            for detail in (ligne.get("risques_detail") or [])
            if isinstance(detail, dict) and detail.get("libelle_risque_long")
        }
    )
    trouves = [r for r in recenses if any(m in r.lower() for m in libelles)]
    pannes = [*pannes, "statut_adresse"]
    tout = {**(details or {}), "risques_gaspar": trouves, "statut_adresse": None}
    sources = [*sources]
    if trouves:
        return theme(
            definition,
            etat="ok",
            ton="vigilance",
            verdict=phrases.commune,
            details=tout,
            granularite=granularite("commune"),
            sources=sources,
            pannes_partielles=pannes,
        )
    # « Not listed » needs a list read entirely and legible: at least one
    # risk read with its label, every row carrying its list of risks.
    lisible = bool(recenses) and all(
        isinstance(ligne.get("risques_detail"), list)
        and all(
            isinstance(detail, dict)
            and isinstance(detail.get("libelle_risque_long"), str)
            and detail["libelle_risque_long"].strip()
            for detail in ligne["risques_detail"]
        )
        for ligne in lignes
    )
    if gaspar.couverture["gaspar"].complet is not True or not lisible:
        motif = "liste_incomplete" if lisible else "liste_illisible"
        return sans_reponse(definition, "manque", motif, sources=sources)
    return theme(
        definition,
        etat="non_concerne",
        ton="neutre",
        verdict=phrases.non_concerne,
        details=tout,
        granularite=granularite("commune"),
        sources=sources,
        pannes_partielles=pannes,
    )


@dataclass(frozen=True)
class AmontRisque:
    """The risk report and, for its outage, the commune's GASPAR list."""

    rapport: object
    gaspar: object


async def amont_risque(contexte: ContexteAmont) -> AmontRisque:
    rapport, gaspar = await asyncio.gather(
        rapport_risques(contexte), risques_commune_amont(contexte)
    )
    return AmontRisque(rapport=rapport, gaspar=gaspar)


def amont_risque_lu(
    contexte: ContexteRapport, definition: DefinitionTheme
) -> AmontRisque:
    """The theme's upstream answer; an outage of its reader is an outage of both."""
    amont = contexte.amonts.get(definition.id)
    if isinstance(amont, AmontRisque):
        return amont
    return AmontRisque(rapport=amont, gaspar=amont)


# --- 04.7-04 lot B: altitude (IGN) and lithology (BRGM) at the point ---------

ALTI_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
ALTI_RESSOURCE = "ign_rge_alti_wld"
# The service writes this value where the RGE ALTI has no data.
ALTI_SANS_DONNEE = -99999.0
GEOLOGIE_URL = "https://geoservices.brgm.fr/geologie"
GEOLOGIE_COUCHE = "LITHO_1M_SIMPLIFIEE"
_TIMEOUT_TERRAIN = httpx.Timeout(4.0, connect=2.0)
# A refused connection too: the Géoplateforme refused the sixth and seventh
# connections a first view opens at once, in 0.6 s (2026-11-10). A connection
# that times out is not tried again: it already spent its bound.
_TERRAIN_REESSAYABLES = (*_REESSAYABLES, httpx.ConnectError)


async def _get_terrain(url: str, params: dict) -> httpx.Response:
    """One GET to a terrain service, retried once on a dropped or refused
    connection."""
    for essai in (1, 2):
        try:
            async with httpx.AsyncClient(
                verify=CONTEXTE_TLS, timeout=_TIMEOUT_TERRAIN
            ) as client:
                reponse = await client.get(url, params=params)
        except _TERRAIN_REESSAYABLES as exc:
            if essai == 2:
                raise UpstreamUnavailableError("Terrain", "connexion coupée") from exc
            continue
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableError("Terrain", "request failed") from exc
        if reponse.status_code != 200:
            raise UpstreamUnavailableError("Terrain", f"HTTP {reponse.status_code}")
        return reponse
    raise UpstreamUnavailableError("Terrain", "unreachable")  # pragma: no cover


def altitude_de(donnees: object) -> float | None:
    """The elevation of the IGN answer; None where the RGE ALTI has no data.

    Raises :class:`UpstreamUnavailableError` on an answer of another shape.
    """
    valeurs = donnees.get("elevations") if isinstance(donnees, dict) else None
    if not isinstance(valeurs, list) or len(valeurs) != 1:
        raise UpstreamUnavailableError("IGN altimetrie", "invalid answer")
    valeur = valeurs[0]
    if isinstance(valeur, dict):
        valeur = valeur.get("z")
    if isinstance(valeur, bool) or not isinstance(valeur, int | float):
        raise UpstreamUnavailableError("IGN altimetrie", "invalid elevation")
    return None if float(valeur) <= ALTI_SANS_DONNEE else float(valeur)


@dataclass(frozen=True)
class Lithologie:
    """The dominant rock of the 1:1 000 000 map at the point."""

    description: str
    type_roche: str | None
    code: str | None


def _texte(element: ElementTree.Element, nom: str) -> str | None:
    enfant = element.find(nom)
    texte = (enfant.text or "").strip() if enfant is not None else ""
    return texte or None


def lithologies_de(gml: bytes | str) -> list[Lithologie]:
    """The features of a GetFeatureInfo answer (GML of MapServer), as read.

    Decoded as XML (entities and CDATA included): bytes in the encoding their
    declaration names, text as already decoded, never re-encoded. Raises
    :class:`UpstreamUnavailableError` when the answer is not well-formed or
    not the GML output of MapServer (an exception report, a cut answer): an
    outage, never an empty list kept in cache. A feature without its
    description is malformed too.
    """
    try:
        racine = ElementTree.fromstring(gml)
    except ElementTree.ParseError as exc:
        raise UpstreamUnavailableError("BRGM geologie", "malformed xml") from exc
    if racine.tag != "msGMLOutput":
        raise UpstreamUnavailableError("BRGM geologie", "invalid answer")
    lithologies = []
    for feature in racine.iter(f"{GEOLOGIE_COUCHE}_feature"):
        description = _texte(feature, "DESCR")
        if description is None:
            raise UpstreamUnavailableError("BRGM geologie", "feature without DESCR")
        lithologies.append(
            Lithologie(
                description=description,
                type_roche=_texte(feature, "TYPE"),
                code=_texte(feature, "CODE_GEOL"),
            )
        )
    return lithologies


_TERRAIN_CACHE = CacheAmont()


async def altitude_au_point(contexte: ContexteAmont) -> object:
    """Elevation of the BAN point (IGN RGE ALTI); None when not hosted or
    without a point; a value of None inside ``AltitudeLue`` is « no data »."""
    if (
        contexte.hosting.hostings["altimetrie"].etat != "heberge"
        or contexte.point is None
    ):
        return None
    lon, lat = round(contexte.point.lon, 6), round(contexte.point.lat, 6)

    async def lire_altitude() -> AltitudeLue:
        reponse = await _get_terrain(
            ALTI_URL,
            {"lon": lon, "lat": lat, "resource": ALTI_RESSOURCE, "zonly": "true"},
        )
        try:
            donnees = reponse.json()
        except ValueError as exc:
            raise UpstreamUnavailableError("IGN altimetrie", "invalid json") from exc
        return AltitudeLue(metres=altitude_de(donnees))

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                return await _TERRAIN_CACHE.obtenir(("alti", lon, lat), lire_altitude)
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("altimetrie", exc)

    return await contexte.memo.obtenir("ign.altitude", lire)


@dataclass(frozen=True)
class AltitudeLue:
    metres: float | None


async def geologie_au_point(contexte: ContexteAmont) -> object:
    """Lithology of the 1:1 000 000 map at the BAN point (BRGM); None when
    not hosted or without a point."""
    if (
        contexte.hosting.hostings["geologie"].etat != "heberge"
        or contexte.point is None
    ):
        return None
    lon, lat = round(contexte.point.lon, 5), round(contexte.point.lat, 5)
    demi = 0.0005
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetFeatureInfo",
        "LAYERS": GEOLOGIE_COUCHE,
        "QUERY_LAYERS": GEOLOGIE_COUCHE,
        "SRS": "EPSG:4326",
        "BBOX": f"{lon - demi},{lat - demi},{lon + demi},{lat + demi}",
        "WIDTH": 3,
        "HEIGHT": 3,
        "X": 1,
        "Y": 1,
        "INFO_FORMAT": "application/vnd.ogc.gml",
        "STYLES": "",
    }

    async def lire_geologie() -> list[Lithologie]:
        reponse = await _get_terrain(GEOLOGIE_URL, params)
        # An explicit HTTP charset prevails (RFC 7303), decoded strictly: a
        # byte it cannot decode is an outage, never a replaced character
        # kept in cache. Without it, the bytes as served, in the encoding
        # their XML declaration names.
        charset = reponse.charset_encoding
        if not charset:
            return lithologies_de(reponse.content)
        try:
            texte = reponse.content.decode(charset)
        except (LookupError, UnicodeDecodeError) as exc:
            raise UpstreamUnavailableError("BRGM geologie", "undecodable") from exc
        return lithologies_de(texte)

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                return await _TERRAIN_CACHE.obtenir(("geol", lon, lat), lire_geologie)
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("geologie", exc)

    return await contexte.memo.obtenir("brgm.geologie", lire)


# --- 04.7-05: noise (BD TOPO network, DGAC airport noise, PLU noise sectors) --

WFS_URL = "https://data.geopf.fr/wfs/ows"
WMS_VECTEUR_URL = "https://data.geopf.fr/wms-v/ows"
GPU_INFO_COUCHE = "wfs_du:info_surf"
# One WFS page; a read reaching it is never complete.
TRONCONS_MAX = 1000
_WGS84_A = 6_378_137.0
_WGS84_E2 = 0.00669437999014


def _metres_par_degre(lat: float) -> tuple[float, float]:
    """Metres per degree of longitude and of latitude at *lat*, on the WGS84
    ellipsoid (a sphere errs by 1.5 m at 500 m, review 2026-10-30)."""
    phi = math.radians(lat)
    w = 1 - _WGS84_E2 * math.sin(phi) ** 2
    est = _WGS84_A / math.sqrt(w) * math.cos(phi) * math.pi / 180
    nord = _WGS84_A * (1 - _WGS84_E2) / w**1.5 * math.pi / 180
    return est, nord


def _entier(valeur: object) -> TypeGuard[int]:
    return isinstance(valeur, int) and not isinstance(valeur, bool)


def _collection(donnees: object, source: str) -> list:
    """The features of a GeoJSON feature collection, its returned count, when
    served, matching them. Raises :class:`UpstreamUnavailableError` on any
    other answer: an outage, never an empty list kept in cache."""
    features = donnees.get("features") if isinstance(donnees, dict) else None
    if (
        not isinstance(donnees, dict)
        or donnees.get("type") != "FeatureCollection"
        or not isinstance(features, list)
    ):
        raise UpstreamUnavailableError(source, "invalid answer")
    retour = donnees.get("numberReturned")
    if retour is not None and (not _entier(retour) or retour != len(features)):
        raise UpstreamUnavailableError(source, "contradictory counts")
    return features


def _collection_entiere(donnees: object, source: str) -> list:
    """A collection read at a point, every feature announced served: a count
    above the features read (``numberMatched``, ``totalFeatures``) would make
    a missing zone an absence. A count that is not a number (GeoServer writes
    ``"unknown"``) announces nothing."""
    features = _collection(donnees, source)
    for cle in ("numberMatched", "totalFeatures"):
        annonce = donnees.get(cle) if isinstance(donnees, dict) else None
        if _entier(annonce) and annonce != len(features):
            raise UpstreamUnavailableError(source, "incomplete answer")
    return features


@dataclass(frozen=True)
class CoucheWfs:
    """A BD TOPO layer read around the point, its filter and its attributes."""

    nom: str
    filtre: str
    proprietes: tuple[str, ...]
    rayon_m: int


@dataclass(frozen=True)
class Troncon:
    distance_m: int
    proprietes: dict


@dataclass(frozen=True)
class TronconsLus:
    """Sections within the radius, nearest first; ``complet`` when every
    section matching the filter was read."""

    troncons: list[Troncon]
    complet: bool


def _distance_segment(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance from the origin to the segment [a, b]."""
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    longueur = dx * dx + dy * dy
    t = 0.0 if longueur == 0 else -(ax * dx + ay * dy) / longueur
    t = max(0.0, min(1.0, t))
    return math.hypot(ax + t * dx, ay + t * dy)


def distance_ligne_m(point: PointBan, geometrie: object) -> float:
    """Distance from the point to a (multi)line, on a local plane: below a
    metre of error within a few kilometres. Raises ``ValueError`` or
    ``TypeError`` on any other geometry."""
    if not isinstance(geometrie, dict):
        raise TypeError("geometry")
    coordonnees = geometrie.get("coordinates")
    if geometrie.get("type") == "LineString":
        lignes: object = [coordonnees]
    elif geometrie.get("type") == "MultiLineString":
        lignes = coordonnees
    else:
        raise ValueError("geometry type")
    if not isinstance(lignes, list):
        raise TypeError("coordinates")
    echelle_x, echelle_y = _metres_par_degre(point.lat)
    meilleure = math.inf
    for ligne in lignes:
        sommets = [
            (
                (float(c[0]) - point.lon) * echelle_x,
                (float(c[1]) - point.lat) * echelle_y,
            )
            for c in ligne
        ]
        for a, b in zip(sommets, sommets[1:] or sommets, strict=False):
            meilleure = min(meilleure, _distance_segment(a, b))
    if meilleure == math.inf:
        raise ValueError("empty geometry")
    return meilleure


def _sous_le_sol(proprietes: dict) -> bool:
    return str(proprietes.get("position_par_rapport_au_sol") or "0").startswith("-")


def troncons_de(donnees: object, point: PointBan, rayon_m: int) -> TronconsLus:
    """The sections of a WFS answer within *rayon_m* of the point, nearest
    first, tunnels and fictive sections left out.

    Complete only when the server counted exactly the features it returned,
    below one page. Raises :class:`UpstreamUnavailableError` on an answer
    that is not a feature collection of lines, or whose counts contradict
    the features read.
    """
    features = _collection(donnees, "BD TOPO WFS")
    annonce = donnees.get("numberMatched") if isinstance(donnees, dict) else None
    if _entier(annonce) and annonce < len(features):
        raise UpstreamUnavailableError("BD TOPO WFS", "contradictory counts")
    troncons = []
    for feature in features:
        proprietes = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(proprietes, dict):
            raise UpstreamUnavailableError("BD TOPO WFS", "invalid feature")
        try:
            distance = distance_ligne_m(point, feature.get("geometry"))
        except (ValueError, TypeError, IndexError) as exc:
            raise UpstreamUnavailableError("BD TOPO WFS", "invalid geometry") from exc
        if _sous_le_sol(proprietes) or proprietes.get("fictif") is True:
            continue
        if distance <= rayon_m:
            troncons.append(Troncon(distance_m=round(distance), proprietes=proprietes))
    troncons.sort(key=lambda t: t.distance_m)
    complet = _entier(annonce) and annonce == len(features) < TRONCONS_MAX
    return TronconsLus(troncons=troncons, complet=complet)


_BRUIT_CACHE = CacheAmont()


def _bbox(lon: float, lat: float, rayon_m: int) -> str:
    # A margin of 10 m covers the rounding of the point in the cache key.
    est, nord = _metres_par_degre(lat)
    dlat, dlon = (rayon_m + 10) / nord, (rayon_m + 10) / est
    return (
        f"BBOX(geometrie,{lon - dlon:.6f},{lat - dlat:.6f},"
        f"{lon + dlon:.6f},{lat + dlat:.6f},'CRS:84')"
    )


async def troncons_au_point(contexte: ContexteAmont, couche: CoucheWfs) -> object:
    """Sections of *couche* around the BAN point (BD TOPO WFS); None when
    ``axes_transport`` is not hosted or without a point."""
    if (
        contexte.hosting.hostings["axes_transport"].etat != "heberge"
        or contexte.point is None
    ):
        return None
    point = contexte.point
    lon, lat = round(point.lon, 5), round(point.lat, 5)
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": couche.nom,
        "OUTPUTFORMAT": "application/json",
        "SRSNAME": "CRS:84",
        "CQL_FILTER": f"{_bbox(lon, lat, couche.rayon_m)} AND {couche.filtre}",
        "PROPERTYNAME": ",".join((*couche.proprietes, "geometrie")),
        "COUNT": TRONCONS_MAX,
    }

    async def lire_wfs() -> dict:
        reponse = await _get_terrain(WFS_URL, params)
        try:
            donnees = reponse.json()
        except ValueError as exc:
            raise UpstreamUnavailableError("BD TOPO WFS", "invalid json") from exc
        # An answer that cannot be read is an outage, never kept.
        troncons_de(donnees, point, couche.rayon_m)
        return donnees

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                donnees = await _BRUIT_CACHE.obtenir(
                    (couche.nom, couche.filtre, lon, lat), lire_wfs
                )
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("axes_transport", exc)
        return troncons_de(donnees, point, couche.rayon_m)

    return await contexte.memo.obtenir(f"bdtopo.{couche.nom}", lire)


PEB_COUCHE = "dgac_peb_plan_wmsv"
PGS_COUCHE = "dgac_pgs_plan_wmsv"
ZONES_PEB = ("A", "B", "C", "D")
ZONES_PGS = ("1", "2", "3")
# Zones asked at the point; an answer reaching it may be cut.
ZONES_MAX = 50


def sld_rempli(*couches: str) -> str:
    """A style filling each zone: the served style only draws the outlines,
    and a GetFeatureInfo then answers on the edges only (probe 2026-10-30)."""
    regle = (
        "<UserStyle><FeatureTypeStyle><Rule><PolygonSymbolizer><Fill>"
        '<CssParameter name="fill">#000000</CssParameter>'
        "</Fill></PolygonSymbolizer></Rule></FeatureTypeStyle></UserStyle>"
    )
    return (
        '<StyledLayerDescriptor version="1.0.0" '
        'xmlns="http://www.opengis.net/sld" xmlns:ogc="http://www.opengis.net/ogc">'
        + "".join(f"<NamedLayer><Name>{c}</Name>{regle}</NamedLayer>" for c in couches)
        + "</StyledLayerDescriptor>"
    )


@dataclass(frozen=True)
class ZoneBruitAerien:
    """A zone of a noise exposure plan (PEB, A to D) or of a noise annoyance
    plan (PGS, 1 to 3) at the point."""

    zone: str
    aerodrome: str | None
    code_oaci: str | None
    date_arrete: str | None
    document: str | None


@dataclass(frozen=True)
class BruitAerien:
    peb: list[ZoneBruitAerien]
    pgs: list[ZoneBruitAerien]


def _texte_ou_rien(valeur: object) -> str | None:
    if not isinstance(valeur, str):
        return None
    return valeur.strip() or None


def _date_iso(valeur: object) -> str | None:
    texte = _texte_ou_rien(valeur)
    try:
        return date.fromisoformat(texte[:10]).isoformat() if texte else None
    except ValueError:
        return None


def _zone_aerienne(proprietes: dict, zone: str, cle_date: str) -> ZoneBruitAerien:
    return ZoneBruitAerien(
        zone=zone,
        aerodrome=_texte_ou_rien(proprietes.get("nom")),
        code_oaci=_texte_ou_rien(proprietes.get("code_oaci")),
        date_arrete=_date_iso(proprietes.get(cle_date)),
        document=_texte_ou_rien(proprietes.get("ref_doc")),
    )


def bruit_aerien_de(donnees: object) -> BruitAerien:
    """PEB and PGS zones of a GetFeatureInfo answer (GeoJSON).

    Raises :class:`UpstreamUnavailableError` on an answer that is not a
    feature collection, on a full page (a zone may be missing), or on a zone
    of neither plan.
    """
    features = _collection_entiere(donnees, "DGAC PEB")
    if len(features) >= ZONES_MAX:
        raise UpstreamUnavailableError("DGAC PEB", "full page")
    peb, pgs = [], []
    for feature in features:
        proprietes = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(proprietes, dict):
            raise UpstreamUnavailableError("DGAC PEB", "invalid feature")
        zone = _texte_ou_rien(proprietes.get("zone"))
        if "indldenext" in proprietes and zone in ZONES_PEB:
            peb.append(_zone_aerienne(proprietes, zone, "date_arret"))
        elif "indice_lde" in proprietes and zone in ZONES_PGS:
            pgs.append(_zone_aerienne(proprietes, zone, "date_arrete"))
        else:
            raise UpstreamUnavailableError("DGAC PEB", "unknown zone")
    return BruitAerien(
        peb=sorted(peb, key=lambda z: z.zone), pgs=sorted(pgs, key=lambda z: z.zone)
    )


async def bruit_aerien_au_point(contexte: ContexteAmont) -> object:
    """PEB and PGS zones at the BAN point (DGAC, WMS of the Géoplateforme);
    None when ``peb`` is not hosted or without a point."""
    if contexte.hosting.hostings["peb"].etat != "heberge" or contexte.point is None:
        return None
    lon, lat = round(contexte.point.lon, 6), round(contexte.point.lat, 6)
    demi = 0.0001
    couches = f"{PEB_COUCHE},{PGS_COUCHE}"
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetFeatureInfo",
        "LAYERS": couches,
        "QUERY_LAYERS": couches,
        "CRS": "CRS:84",
        "BBOX": f"{lon - demi:.6f},{lat - demi:.6f},{lon + demi:.6f},{lat + demi:.6f}",
        "WIDTH": 101,
        "HEIGHT": 101,
        "I": 50,
        "J": 50,
        "INFO_FORMAT": "application/json",
        "FEATURE_COUNT": ZONES_MAX,
        "SLD_BODY": sld_rempli(PEB_COUCHE, PGS_COUCHE),
    }

    async def lire_zones() -> BruitAerien:
        reponse = await _get_terrain(WMS_VECTEUR_URL, params)
        try:
            donnees = reponse.json()
        except ValueError as exc:
            raise UpstreamUnavailableError("DGAC PEB", "invalid json") from exc
        return bruit_aerien_de(donnees)

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                return await _BRUIT_CACHE.obtenir(("peb", lon, lat), lire_zones)
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("peb", exc)

    return await contexte.memo.obtenir("dgac.peb", lire)


# CNIG type of a PLU information area: « secteur affecté par le bruit des
# infrastructures de transports terrestres ».
TYPE_INFO_BRUIT = "14"


@dataclass(frozen=True)
class InformationsPlu:
    """The information areas of the published PLU at the point."""

    secteurs_bruit: list[str]
    informations: int


def informations_plu_de(donnees: object) -> InformationsPlu:
    """Raises :class:`UpstreamUnavailableError` on an answer that is not a
    feature collection with properties."""
    features = _collection_entiere(donnees, "GPU")
    if any(
        not isinstance(f, dict) or not isinstance(f.get("properties"), dict)
        for f in features
    ):
        raise UpstreamUnavailableError("GPU", "invalid feature")
    secteurs = [
        _texte_ou_rien(f["properties"].get("libelle")) or "secteur affecté par le bruit"
        for f in features
        if str(f["properties"].get("typeinf") or "").strip() == TYPE_INFO_BRUIT
    ]
    return InformationsPlu(
        secteurs_bruit=list(dict.fromkeys(secteurs)), informations=len(features)
    )


async def informations_plu_au_point(contexte: ContexteAmont) -> object:
    """PLU information areas at the BAN point; None when ``urbanisme`` is not
    hosted or without a point.

    Read from the GPU layer of the Géoplateforme WFS, the flux behind API
    Carto, without geometries: API Carto serves each area whole, up to 4 MB
    at one address of Nantes (probe 2026-10-30), for the same attributes.
    """
    if (
        contexte.hosting.hostings["urbanisme"].etat != "heberge"
        or contexte.point is None
    ):
        return None
    lon, lat = round(contexte.point.lon, 6), round(contexte.point.lat, 6)
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": GPU_INFO_COUCHE,
        "OUTPUTFORMAT": "application/json",
        "SRSNAME": "CRS:84",
        "CQL_FILTER": f"INTERSECTS(the_geom,SRID=4326;POINT({lon} {lat}))",
        "PROPERTYNAME": "typeinf,libelle",
        "COUNT": 500,
    }

    async def lire_informations() -> InformationsPlu:
        reponse = await _get_terrain(WFS_URL, params)
        try:
            donnees = reponse.json()
        except ValueError as exc:
            raise UpstreamUnavailableError("GPU", "invalid json") from exc
        return informations_plu_de(donnees)

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                return await _BRUIT_CACHE.obtenir(("gpu", lon, lat), lire_informations)
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("urbanisme", exc)

    return await contexte.memo.obtenir("gpu.info-surf", lire)


# --- 04.7-06: living here (SSMSI delinquency, Hub'Eau tap water, schools) ----

# « Bases statistiques communale, départementale et régionale de la
# délinquance enregistrée » (data.gouv.fr, Ministère de l'Intérieur), through
# the stable links data.gouv.fr redirects to their latest edition. Each file
# is read whole, once a day: the tabular API answers a query out of its cache
# in 7 to 12 s on the communal base (5.2 M rows scanned) and up to 4 s on the
# departmental one (probe 2026-11-09), past the deadline of the page, so the
# first page of every commune nobody asked about lately was an outage.
SSMSI_COMMUNES_FICHIER = (  # parquet, 16 MB
    "https://www.data.gouv.fr/fr/datasets/r/604d71b8-337d-4869-9226-49e01bae87df"
)
SSMSI_DEPARTEMENTS_FICHIER = (  # csv, 2 MB
    "https://www.data.gouv.fr/fr/datasets/r/2b27a675-e3bf-41ef-a852-5fb9ab483967"
)
_TIMEOUT_FICHIER = httpx.Timeout(60.0, connect=5.0)
_VIVRE_CACHE = CacheAmont()


async def _get_json(url: str, params: dict, source: str) -> dict:
    reponse = await _get_terrain(url, params)
    try:
        donnees = reponse.json()
    except ValueError as exc:
        raise UpstreamUnavailableError(source, "invalid json") from exc
    if not isinstance(donnees, dict):
        raise UpstreamUnavailableError(source, "invalid answer")
    return donnees


@dataclass(frozen=True)
class IndicateurDelinquance:
    """One indicator of one year; ``nombre`` and ``taux`` are None when the
    SSMSI does not publish it (under its diffusion threshold)."""

    indicateur: str
    annee: int
    nombre: int | None
    taux_pour_mille: float | None
    diffuse: bool


@dataclass(frozen=True)
class DelinquanceLue:
    annee: int | None
    commune: list[IndicateurDelinquance]
    departement: list[IndicateurDelinquance]
    # The code whose rows were read: an arrondissement or the commune.
    zone: str | None = None

    @property
    def arrondissement(self) -> bool:
        return self.zone is not None and normalize_code_insee(self.zone) != self.zone


def _indicateur_de(ligne: dict) -> IndicateurDelinquance:
    """One row of an SSMSI file. Raises :class:`UpstreamUnavailableError`
    on a row of another shape."""
    diffusion = ligne.get("est_diffuse")
    nom, annee = ligne.get("indicateur"), ligne.get("annee")
    if diffusion not in ("diff", "ndiff", None) or not isinstance(nom, str):
        raise UpstreamUnavailableError("SSMSI", "invalid row")
    if not _entier(annee):
        raise UpstreamUnavailableError("SSMSI", "invalid year")
    nombre, taux = ligne.get("nombre"), ligne.get("taux_pour_mille")
    diffuse = diffusion != "ndiff" and _entier(nombre)
    return IndicateurDelinquance(
        indicateur=nom.strip(),
        annee=annee,
        nombre=nombre if diffuse else None,
        taux_pour_mille=float(taux)
        if diffuse and isinstance(taux, int | float) and not isinstance(taux, bool)
        else None,
        diffuse=diffuse,
    )


_COLONNES_COMMUNES = (
    "CODGEO_2026",
    "annee",
    "indicateur",
    "nombre",
    "taux_pour_mille",
    "est_diffuse",
)


class BaseCommunaleSsmsi:
    """The rows of the latest year of each commune of the communal file,
    sorted by code, with the place of each code: about 35 MB of Arrow
    columns where Python objects would take several times that."""

    def __init__(self, table, places: dict[str, tuple[int, int]]) -> None:
        self._table = table
        self._places = places

    def indicateurs(self, code: str) -> list[IndicateurDelinquance]:
        place = self._places.get(code)
        if place is None:
            return []
        debut, nombre = place
        lignes = self._table.slice(debut, nombre).to_pylist()
        return [_indicateur_de(ligne) for ligne in lignes]


def base_communale_de(contenu: bytes) -> BaseCommunaleSsmsi:
    """The communal file (parquet), read whole: blocking, run in a thread.

    Raises :class:`UpstreamUnavailableError` on a file that is not a parquet
    file of the expected columns.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    try:
        table = pq.read_table(
            pa.BufferReader(contenu), columns=list(_COLONNES_COMMUNES)
        )
        # The latest year of each commune, as the communal query read it.
        derniere = table.group_by("CODGEO_2026").aggregate([("annee", "max")])
        table = table.join(derniere, "CODGEO_2026")
        table = table.filter(pc.equal(table["annee"], table["annee_max"]))
        table = table.select(list(_COLONNES_COMMUNES)).sort_by("CODGEO_2026")
        table = table.set_column(
            table.schema.get_field_index("indicateur"),
            "indicateur",
            table["indicateur"].cast(pa.string()),
        )
    except (pa.ArrowException, KeyError, ValueError) as exc:
        raise UpstreamUnavailableError("SSMSI", "invalid file") from exc
    places: dict[str, tuple[int, int]] = {}
    for rang, code in enumerate(table["CODGEO_2026"].to_pylist()):
        if not isinstance(code, str):
            raise UpstreamUnavailableError("SSMSI", "invalid row")
        debut, nombre = places.get(code, (rang, 0))
        places[code] = (debut, nombre + 1)
    return BaseCommunaleSsmsi(table, places)


type BaseDepartementaleSsmsi = dict[tuple[str, int], list[IndicateurDelinquance]]
_COLONNES_DEPARTEMENTS = {"Code_departement", "annee", "indicateur", "nombre",
                          "taux_pour_mille"}  # fmt: skip


def _nombre_csv(texte: str | None, type_):
    """A number of the departmental file: quoted, with a decimal comma; an
    empty cell is no value. Raises ValueError on anything else."""
    if texte is None or not texte.strip():
        return None
    return type_(texte.strip().replace(",", "."))


def base_departementale_de(contenu: bytes) -> BaseDepartementaleSsmsi:
    """The departmental file (csv), by department and year.

    Raises :class:`UpstreamUnavailableError` on a file of another shape. It
    has no diffusion column: a department is always over the threshold, and
    an empty cell stays a gap.
    """
    try:
        lecteur = csv.DictReader(
            io.StringIO(contenu.decode("utf-8-sig")), delimiter=";"
        )
        if not set(lecteur.fieldnames or ()) >= _COLONNES_DEPARTEMENTS:
            raise UpstreamUnavailableError("SSMSI", "invalid file")
        base: BaseDepartementaleSsmsi = {}
        for ligne in lecteur:
            annee = _nombre_csv(ligne["annee"], int)
            indicateur = _indicateur_de(
                {
                    "indicateur": ligne["indicateur"],
                    "annee": annee,
                    "nombre": _nombre_csv(ligne["nombre"], int),
                    "taux_pour_mille": _nombre_csv(ligne["taux_pour_mille"], float),
                }
            )
            code = (ligne["Code_departement"] or "").strip()
            base.setdefault((code, indicateur.annee), []).append(indicateur)
    except (UnicodeDecodeError, ValueError, csv.Error) as exc:
        raise UpstreamUnavailableError("SSMSI", "invalid file") from exc
    if not base:
        raise UpstreamUnavailableError("SSMSI", "empty file")
    return base


class FichierAmont[T]:
    """An upstream file read whole, once a day, and kept in memory.

    Past its day, the base still answers while a new reading runs in the
    background: the SSMSI publishes twice a year. Without a base (a fresh
    process), a page awaits the one reading in flight within its own
    deadline; the reading outlives that deadline, so the next page finds
    the base. A failed reading is never kept: the next page tries again.
    """

    def __init__(self, url: str, lecteur: Callable[[bytes], T]) -> None:
        self._url, self._lecteur = url, lecteur
        self._base: T | None = None
        self._lue_a = 0.0
        self._lecture: asyncio.Future[T] | None = None
        _FICHIERS.append(self)

    async def base(self) -> T:
        maintenant = asyncio.get_running_loop().time()
        if (
            self._base is not None
            and maintenant - self._lue_a < settings.proxy_cache_ttl_s
        ):
            return self._base
        if self._lecture is None or self._lecture.done():
            self._lecture = asyncio.ensure_future(self._lire())
            self._lecture.add_done_callback(_lecture_terminee)
        if self._base is not None:
            return self._base
        return await asyncio.shield(self._lecture)

    async def _lire(self) -> T:
        try:
            async with httpx.AsyncClient(
                verify=CONTEXTE_TLS, timeout=_TIMEOUT_FICHIER, follow_redirects=True
            ) as client:
                reponse = await client.get(self._url)
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableError("SSMSI", "request failed") from exc
        if reponse.status_code != 200:
            raise UpstreamUnavailableError("SSMSI", f"HTTP {reponse.status_code}")
        base = await asyncio.to_thread(self._lecteur, reponse.content)
        self._base, self._lue_a = base, asyncio.get_running_loop().time()
        return base

    def vider(self) -> None:
        self._base, self._lecture = None, None


_FICHIERS: list[FichierAmont] = []


def _lecture_terminee(lecture: asyncio.Future) -> None:
    # Retrieved here so that a background reading that fails is logged once,
    # never left as an exception nobody awaited.
    if not lecture.cancelled() and (exc := lecture.exception()) is not None:
        lieu._panne_amont("delinquance", exc)


_SSMSI_COMMUNES = FichierAmont(SSMSI_COMMUNES_FICHIER, base_communale_de)
_SSMSI_DEPARTEMENTS = FichierAmont(SSMSI_DEPARTEMENTS_FICHIER, base_departementale_de)


async def prechauffer_delinquance() -> None:
    """Start reading the SSMSI files, so that the first pages after a start
    find them read. Never raises: a failure is the next page's to try."""
    await asyncio.gather(
        _SSMSI_COMMUNES.base(), _SSMSI_DEPARTEMENTS.base(), return_exceptions=True
    )


async def delinquance_au_point(contexte: ContexteAmont) -> object:
    """Indicators of the commune for its latest year, and of its department
    for the same year (SSMSI); None when ``delinquance`` is not hosted."""
    if contexte.hosting.hostings["delinquance"].etat != "heberge":
        return None
    # The SSMSI publishes the arrondissements of Paris, Lyon and Marseille:
    # the code as served first, the whole commune when it has no row.
    brut = (contexte.identite.commune.code_insee or "").strip()
    codes = list(dict.fromkeys((brut, normalize_code_insee(brut))))
    departement = (
        contexte.identite.departement.code if contexte.identite.departement else None
    )

    async def lire_delinquance() -> DelinquanceLue:
        communes, departements = await asyncio.gather(
            _SSMSI_COMMUNES.base(), _SSMSI_DEPARTEMENTS.base()
        )
        commune: list[IndicateurDelinquance] = []
        for zone in codes:
            commune = communes.indicateurs(zone)
            if commune:
                break
        if not commune:
            return DelinquanceLue(annee=None, commune=[], departement=[])
        annee = max(i.annee for i in commune)
        dep = departements.get((departement, annee), []) if departement else []
        return DelinquanceLue(
            annee=annee,
            commune=[i for i in commune if i.annee == annee],
            departement=list(dep),
            zone=zone,
        )

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                return await _VIVRE_CACHE.obtenir(
                    ("ssmsi", brut, departement), lire_delinquance
                )
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("delinquance", exc)

    return await contexte.memo.obtenir("ssmsi", lire)


HUBEAU_URL = "https://hubeau.eaufrance.fr/api/v1/qualite_eau_potable"
# One parameter per sample carries the conformity of the whole sample.
TRACEUR_BACTERIOLOGIQUE = "1449"  # Escherichia coli
TRACEUR_PHYSICO_CHIMIQUE = "1302"  # pH
TITRE_HYDROTIMETRIQUE = "1345"  # hardness, in French degrees
PRELEVEMENTS_MAX = 5000


# Hub'Eau answers in 0.1 to 1 s, but one batch in 20 stalled beyond 4 s
# (2026-10-30): a short wait, then one more try, within the report deadline.
_TIMEOUT_HUBEAU = httpx.Timeout(2.0, connect=1.5)
_HUBEAU_REESSAYABLES = (*_REESSAYABLES, httpx.TimeoutException)


async def _get_hubeau(chemin: str, params: dict) -> tuple[dict, bool]:
    """A Hub'Eau page and whether it holds every row (200) or a part (206)."""
    for essai in (1, 2):
        try:
            async with httpx.AsyncClient(
                verify=CONTEXTE_TLS, timeout=_TIMEOUT_HUBEAU
            ) as client:
                reponse = await client.get(f"{HUBEAU_URL}/{chemin}", params=params)
        except _HUBEAU_REESSAYABLES as exc:
            if essai == 2:
                raise UpstreamUnavailableError("Hub'Eau", "connexion coupée") from exc
            continue
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableError("Hub'Eau", "request failed") from exc
        if reponse.status_code not in (200, 206):
            raise UpstreamUnavailableError("Hub'Eau", f"HTTP {reponse.status_code}")
        try:
            donnees = reponse.json()
        except ValueError as exc:
            raise UpstreamUnavailableError("Hub'Eau", "invalid json") from exc
        if not isinstance(donnees, dict) or not isinstance(donnees.get("data"), list):
            raise UpstreamUnavailableError("Hub'Eau", "invalid answer")
        complet = reponse.status_code == 200 and donnees.get("count") == len(
            donnees["data"]
        )
        return donnees, complet
    raise UpstreamUnavailableError("Hub'Eau", "unreachable")  # pragma: no cover


@dataclass(frozen=True)
class Reseau:
    code: str
    nom: str | None


# The ARS conclusion of a sample on the limits; any other value (absent,
# « S », unknown) concludes nothing and is never read as a conformity.
CONFORME, NON_CONFORME = "C", "N"


@dataclass(frozen=True)
class Conformite:
    """Samples of 12 months for one nature, by the ARS conclusion on the
    limits: conforming, breaking a limit (day and code of each, latest
    first), or without conclusion. ``dernier`` is the instant of the latest
    concluded sample, ``dernier_lu`` the instant of the latest sample read."""

    prelevements: int
    conformes: int
    non_conformes: tuple[tuple[str, str], ...]
    dernier: datetime | None
    dernier_conforme: bool | None
    complet: bool
    dernier_lu: datetime | None = None

    @property
    def conclus(self) -> int:
        return self.conformes + len(self.non_conformes)

    @property
    def sans_conclusion(self) -> int:
        return self.prelevements - self.conclus

    @property
    def dates_non_conformes(self) -> tuple[str, ...]:
        return tuple(date_ for date_, _ in self.non_conformes)


@dataclass(frozen=True)
class EauLue:
    reseaux: list[Reseau]
    annee_reseaux: str | None
    bacteriologique: Conformite
    physico_chimique: Conformite
    durete_f: float | None
    durete_date: str | None
    # Concluded samples of either nature, each counted once (most carry both).
    prelevements: int = 0
    # False when the units of the commune came on a partial page.
    reseaux_complets: bool = True


def reseaux_de(donnees: dict) -> tuple[list[Reseau], str | None]:
    """The distribution units of the latest year of a ``communes_udi`` page."""
    lignes = [x for x in donnees["data"] if isinstance(x, dict)]
    annees = [str(x["annee"]) for x in lignes if x.get("annee")]
    if not annees:
        return [], None
    annee = max(annees)
    reseaux = {
        str(x["code_reseau"]): Reseau(
            str(x["code_reseau"]), _texte_ou_rien(x.get("nom_reseau"))
        )
        for x in lignes
        if str(x.get("annee")) == annee and x.get("code_reseau")
    }
    return sorted(reseaux.values(), key=lambda r: r.code), annee


def _prelevements(donnees: dict) -> dict[str, dict]:
    """The rows of a ``resultats_dis`` page by sample code, each once."""
    return {
        str(ligne["code_prelevement"]): ligne
        for ligne in donnees["data"]
        if isinstance(ligne, dict) and ligne.get("code_prelevement")
    }


def _instant(valeur: object) -> datetime:
    """The instant of a sample, comparable whatever its writing (``Z``, an
    offset, a fraction of a second). A sample without a readable one makes
    the answer unreadable: an outage, never kept."""
    try:
        instant = datetime.fromisoformat(str(valeur))
    except ValueError as exc:
        raise UpstreamUnavailableError("Hub'Eau", "invalid date") from exc
    return instant if instant.tzinfo else instant.replace(tzinfo=UTC)


def conformite_de(donnees: dict, cle: str, complet: bool) -> Conformite:
    """Samples of a tracer parameter, each counted once by its code. The
    latest control is ordered by its instant: a breach in the morning and a
    conforming control in the evening of one day end conforming."""
    prelevements = _prelevements(donnees)
    instants = {
        code: _instant(p.get("date_prelevement")) for code, p in prelevements.items()
    }
    conclus = {
        code: p[cle]
        for code, p in prelevements.items()
        if p.get(cle) in (CONFORME, NON_CONFORME)
    }
    non_conformes = [code for code, c in conclus.items() if c == NON_CONFORME]
    dernier = max((instants[code] for code in conclus), default=None)
    return Conformite(
        prelevements=len(prelevements),
        conformes=len(conclus) - len(non_conformes),
        non_conformes=tuple(
            sorted(
                ((instants[code].date().isoformat(), code) for code in non_conformes),
                reverse=True,
            )
        ),
        dernier=dernier,
        dernier_conforme=None
        if dernier is None
        else not any(instants[code] == dernier for code in non_conformes),
        dernier_lu=max(instants.values(), default=None),
        complet=complet,
    )


async def eau_au_point(contexte: ContexteAmont) -> object:
    """Distribution units of the commune and the conformity of 12 months of
    samples (Hub'Eau, ARS sanitary control); None when ``eau_potable`` is
    not hosted."""
    if contexte.hosting.hostings["eau_potable"].etat != "heberge":
        return None
    code = normalize_code_insee(contexte.identite.commune.code_insee or "")
    depuis = (contexte.as_of - timedelta(days=365)).isoformat()

    def traceur(parametre: str, champs: str) -> dict:
        return {
            "code_commune": code,
            "code_parametre": parametre,
            "date_min_prelevement": depuis,
            "fields": champs,
            "size": PRELEVEMENTS_MAX,
        }

    async def lire_eau() -> EauLue:
        # One row per unit and year since 2016: Marseille has 176 (16 units).
        udi_lues = _get_hubeau(
            "communes_udi", {"code_commune": code, "size": PRELEVEMENTS_MAX}
        )
        cle_bact = "conformite_limites_bact_prelevement"
        cle_pc = "conformite_limites_pc_prelevement"
        (udi, udi_ok), (bact, bact_ok), (pc, pc_ok), (th, _) = await asyncio.gather(
            udi_lues,
            _get_hubeau(
                "resultats_dis",
                traceur(
                    TRACEUR_BACTERIOLOGIQUE,
                    f"code_prelevement,date_prelevement,{cle_bact}",
                ),
            ),
            _get_hubeau(
                "resultats_dis",
                traceur(
                    TRACEUR_PHYSICO_CHIMIQUE,
                    f"code_prelevement,date_prelevement,{cle_pc}",
                ),
            ),
            _get_hubeau(
                "resultats_dis",
                {
                    **traceur(
                        TITRE_HYDROTIMETRIQUE, "date_prelevement,resultat_numerique"
                    ),
                    "size": 200,
                    "sort": "desc",
                },
            ),
        )
        reseaux, annee = reseaux_de(udi)
        mesures = sorted(
            (str(x.get("date_prelevement") or "")[:10], x.get("resultat_numerique"))
            for x in th["data"]
            if isinstance(x, dict)
            and isinstance(x.get("resultat_numerique"), int | float)
            and not isinstance(x.get("resultat_numerique"), bool)
        )
        conclus = {
            prelevement
            for donnees, cle in ((bact, cle_bact), (pc, cle_pc))
            for prelevement, p in _prelevements(donnees).items()
            if p.get(cle) in (CONFORME, NON_CONFORME)
        }
        return EauLue(
            reseaux=reseaux,
            annee_reseaux=annee,
            bacteriologique=conformite_de(bact, cle_bact, bact_ok),
            physico_chimique=conformite_de(pc, cle_pc, pc_ok),
            durete_f=_reel(mesures[-1][1]) if mesures else None,
            durete_date=mesures[-1][0] if mesures else None,
            prelevements=len(conclus),
            reseaux_complets=udi_ok,
        )

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                lu = await _VIVRE_CACHE.obtenir(("eau", code, depuis), lire_eau)
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("eau_potable", exc)
        if not (
            lu.bacteriologique.complet
            and lu.physico_chimique.complet
            and lu.reseaux_complets
        ):
            # A partial page is said once, never kept for a day.
            _VIVRE_CACHE.oublier(("eau", code, depuis))
        return lu

    return await contexte.memo.obtenir("hubeau.eau", lire)


EDUCATION_URL = (
    "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/{jeu}/records"
)
ECOLES_RAYON_KM = 10
# Level -> filter of the directory (public schools open today).
NIVEAUX_SCOLAIRES = {
    "elementaire": "type_etablissement='Ecole' AND ecole_elementaire=1",
    # A SEGPA (nature 390) is a section of a college, not a college.
    "college": "type_etablissement='Collège' AND code_nature='340'",
    "lycee": "type_etablissement='Lycée' AND voie_generale='1'",
}
COLLEGES_LUS = 5


@dataclass(frozen=True)
class Etablissement:
    uai: str
    nom: str | None
    niveau: str
    distance_m: int
    ips: float | None = None
    ips_rentree: str | None = None
    ips_national_public: float | None = None
    taux_reussite: float | None = None
    valeur_ajoutee: float | None = None
    session: str | None = None


@dataclass(frozen=True)
class EcolesLues:
    """The nearest public schools by level (colleges: the few nearest, to
    find the sector college among them)."""

    par_niveau: dict[str, list[Etablissement]]


def _reel(valeur: object) -> float | None:
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, int | float):
        return float(valeur)
    try:
        return (
            float(str(valeur).replace(",", ".")) if valeur not in (None, "") else None
        )
    except ValueError:
        return None


def _resultats(donnees: dict, source: str) -> list[dict]:
    lignes = donnees.get("results")
    if not isinstance(lignes, list) or any(not isinstance(x, dict) for x in lignes):
        raise UpstreamUnavailableError(source, "invalid answer")
    return lignes


def etablissements_de(
    donnees: dict, niveau: str, point: PointBan
) -> list[Etablissement]:
    """Schools of a directory answer, nearest first, with their distance."""
    etablissements = []
    for ligne in _resultats(donnees, "Annuaire de l'éducation"):
        lon, lat = _reel(ligne.get("longitude")), _reel(ligne.get("latitude"))
        uai = _texte_ou_rien(ligne.get("identifiant_de_l_etablissement"))
        if uai is None or lon is None or lat is None:
            continue
        etablissements.append(
            Etablissement(
                uai=uai,
                nom=_texte_ou_rien(ligne.get("nom_etablissement")),
                niveau=niveau,
                distance_m=round(distance_m(point.lon, point.lat, lon, lat)),
            )
        )
    return sorted(etablissements, key=lambda e: e.distance_m)


def _plus_recent(lignes: list[dict], cle_annee: str) -> dict[str, dict]:
    """By UAI, the row of the latest year (rentrée or session)."""
    par_uai: dict[str, dict] = {}
    for ligne in sorted(lignes, key=lambda x: str(x.get(cle_annee) or "")):
        uai = _texte_ou_rien(ligne.get("uai"))
        if uai:
            par_uai[uai] = ligne
    return par_uai


async def _jeu_education(jeu: str, ou: str, limite: int = 100) -> list[dict]:
    return _resultats(
        await _get_json(
            EDUCATION_URL.format(jeu=jeu),
            {"where": ou, "limit": limite},
            "Éducation nationale",
        ),
        "Éducation nationale",
    )


def _liste_uai(uais: list[str]) -> str:
    return ", ".join(f"'{u}'" for u in uais if u.isalnum())


async def _profils(
    par_niveau: dict[str, list[Etablissement]],
) -> dict[str, list[Etablissement]]:
    """IPS and added value of the schools read, by their UAI."""
    uais = {n: [e.uai for e in liste] for n, liste in par_niveau.items()}
    # The schools' set runs from 2022 to its latest rentrée; colleges and
    # lycées have their own from 2023 (those « 2022 » stop at 2022-2023).
    jeux = {
        "elementaire": ("fr-en-ips-ecoles-ap2022", None),
        "college": (
            "fr-en-ips-colleges-ap2023",
            "fr-en-indicateurs-valeur-ajoutee-colleges",
        ),
        "lycee": (
            "fr-en-ips-lycees-ap2023",
            "fr-en-indicateurs-de-resultat-des-lycees-gt_v2",
        ),
    }
    lectures = {}
    for niveau, (jeu_ips, jeu_va) in jeux.items():
        dans = _liste_uai(uais.get(niveau, []))
        if not dans:
            continue
        lectures[(niveau, "ips")] = _jeu_education(jeu_ips, f"uai IN ({dans})")
        if jeu_va:
            lectures[(niveau, "va")] = _jeu_education(jeu_va, f"uai IN ({dans})")
    lus = dict(zip(lectures, await asyncio.gather(*lectures.values()), strict=True))
    profils = {}
    for niveau, liste in par_niveau.items():
        ips = _plus_recent(lus.get((niveau, "ips"), []), "rentree_scolaire")
        va = _plus_recent(
            lus.get((niveau, "va"), []), "annee" if niveau == "lycee" else "session"
        )
        complets = []
        for e in liste:
            i, v = ips.get(e.uai, {}), va.get(e.uai, {})
            complets.append(
                Etablissement(
                    uai=e.uai,
                    nom=e.nom,
                    niveau=e.niveau,
                    distance_m=e.distance_m,
                    ips=_reel(
                        i.get("ips") if niveau != "lycee" else i.get("ips_voie_gt")
                    ),
                    ips_rentree=_texte_ou_rien(i.get("rentree_scolaire")),
                    # A lycée against the public lycées of its own type.
                    ips_national_public=_reel(
                        i.get("ips_national_public")
                        if niveau != "lycee"
                        else i.get(
                            f"ips_national_{str(i.get('type_de_lycee') or '').lower()}"
                            "_public"
                        )
                    ),
                    taux_reussite=_reel(
                        v.get("taux_reu_total")
                        if niveau == "lycee"
                        else v.get("taux_de_reussite_g")
                    ),
                    valeur_ajoutee=_reel(
                        v.get("va_reu_total")
                        if niveau == "lycee"
                        else v.get("va_du_taux_de_reussite_g")
                    ),
                    session=_texte_ou_rien(
                        str(v.get("annee" if niveau == "lycee" else "session") or "")
                    ),
                )
            )
        profils[niveau] = complets
    return profils


async def ecoles_au_point(contexte: ContexteAmont) -> object:
    """Nearest public schools by level within 10 km, with their IPS and added
    value (Éducation nationale); None when ``annuaire_education`` is not
    hosted or without a point."""
    if (
        contexte.hosting.hostings["annuaire_education"].etat != "heberge"
        or contexte.point is None
    ):
        return None
    point = contexte.point
    lon, lat = round(point.lon, 5), round(point.lat, 5)
    geom = f"geom'POINT({lon} {lat})'"

    async def lire_annuaire() -> dict[str, dict]:
        requetes = {
            niveau: _get_json(
                EDUCATION_URL.format(jeu="fr-en-annuaire-education"),
                {
                    "where": (
                        f"within_distance(position, {geom}, {ECOLES_RAYON_KM}km) "
                        "AND statut_public_prive='Public' AND etat='OUVERT' "
                        f"AND {filtre}"
                    ),
                    "order_by": f"distance(position, {geom})",
                    "select": "identifiant_de_l_etablissement,nom_etablissement,"
                    "latitude,longitude",
                    "limit": COLLEGES_LUS if niveau == "college" else 1,
                },
                "Annuaire de l'éducation",
            )
            for niveau, filtre in NIVEAUX_SCOLAIRES.items()
        }
        reponses = await asyncio.gather(*requetes.values())
        for reponse in reponses:
            _resultats(reponse, "Annuaire de l'éducation")
        return dict(zip(requetes, reponses, strict=True))

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                annuaire = await _VIVRE_CACHE.obtenir(
                    ("ecoles", lon, lat), lire_annuaire
                )
                par_niveau = {
                    niveau: etablissements_de(donnees, niveau, point)
                    for niveau, donnees in annuaire.items()
                }
                # Profiles are kept by the schools they describe; the order
                # and distance are those of this point.
                uais = tuple(
                    (niveau, tuple(sorted(e.uai for e in liste)))
                    for niveau, liste in sorted(par_niveau.items())
                )
                profils = await _VIVRE_CACHE.obtenir(
                    ("ecoles-profils", uais), lambda: _profils(par_niveau)
                )
                par_uai = {
                    (e.niveau, e.uai): e for liste in profils.values() for e in liste
                }
                return EcolesLues(
                    par_niveau={
                        niveau: [
                            replace(par_uai[(niveau, e.uai)], distance_m=e.distance_m)
                            for e in liste
                        ]
                        for niveau, liste in par_niveau.items()
                    }
                )
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("annuaire_education", exc)

    return await contexte.memo.obtenir("education.ecoles", lire)


# --- 04.7-07: living here part 2 (ATMO index, Filosofi cells, IRIS, station) --

ATMO_WFS_URL = "https://data.atmo-france.org/geoserver/ind/ows"
ATMO_COUCHE = "ind:ind_atmo_2021"
GEO_API_COMMUNE_URL = "https://geo.api.gouv.fr/communes/{code}"
# Codes of the 2021 index: 1 to 6, plus 0 « absent » and 7 « événement ».
CODES_ATMO = range(8)
SOUS_INDICES_ATMO = ("no2", "o3", "pm10", "pm25", "so2")
# A code sent in a CQL filter: an INSEE code (Corsica included) or a SIREN.
_CODE_ZONE = re.compile(r"(?:\d[\dAB]\d{3}|\d{9})")
# An AASQA may revise the index of the day (``date_maj``): kept an hour.
_AIR_CACHE = CacheAmont(ttl_s=3600)


@dataclass(frozen=True)
class IndiceAtmo:
    code: int
    date: str
    zone_code: str
    zone_nom: str | None
    zone_type: str  # « commune » or « epci »
    aasqa: str | None
    sous_indices: dict[str, int | None]


@dataclass(frozen=True)
class AirLu:
    """The index of the day; None with the reason when there is none."""

    indice: IndiceAtmo | None
    motif: str | None = None


def _code_atmo(valeur: object) -> int:
    if not _entier(valeur) or valeur not in CODES_ATMO:
        raise UpstreamUnavailableError("ATMO", "invalid index")
    return valeur


def _ligne_atmo(feature: object, jour: str) -> tuple:
    p = feature.get("properties") if isinstance(feature, dict) else None
    if not isinstance(p, dict):
        raise UpstreamUnavailableError("ATMO", "invalid feature")
    type_zone = p.get("type_zone")
    type_zone = type_zone.lower() if isinstance(type_zone, str) else None
    if type_zone not in ("commune", "epci") or p.get("date_ech") != jour:
        raise UpstreamUnavailableError("ATMO", "unexpected zone or day")
    zone = p.get("code_zone")
    if not isinstance(zone, str):
        raise UpstreamUnavailableError("ATMO", "invalid zone")
    sous_indices = tuple(
        (
            nom,
            None if p.get(f"code_{nom}") is None else _code_atmo(p[f"code_{nom}"]),
        )
        for nom in SOUS_INDICES_ATMO
    )
    return (
        _code_atmo(p.get("code_qual")),
        zone,
        _texte_ou_rien(p.get("lib_zone")),
        type_zone,
        _texte_ou_rien(p.get("source")),
        sous_indices,
    )


def indices_atmo_de(donnees: object, jour: str) -> dict[str, IndiceAtmo | None]:
    """The index of each zone of a WFS answer for one day; None for a zone
    whose rows disagree (no index is chosen). Raises
    :class:`UpstreamUnavailableError` on any unreadable answer."""
    lignes: dict[str, set[tuple]] = {}
    for feature in _collection_entiere(donnees, "ATMO"):
        ligne = _ligne_atmo(feature, jour)
        lignes.setdefault(ligne[1], set()).add(ligne)
    indices: dict[str, IndiceAtmo | None] = {}
    for zone, distinctes in lignes.items():
        if len(distinctes) > 1:
            indices[zone] = None
            continue
        code, _, nom, type_zone, aasqa, sous_indices = distinctes.pop()
        indices[zone] = IndiceAtmo(
            code=code,
            date=jour,
            zone_code=zone,
            zone_nom=nom,
            zone_type=type_zone,
            aasqa=aasqa,
            sous_indices=dict(sous_indices),
        )
    return indices


async def _indices_des_zones(
    codes: list[str], jour: str
) -> dict[str, IndiceAtmo | None]:
    """One WFS read of the index of the day of every zone of *codes*."""
    liste = ",".join(f"'{code}'" for code in codes)
    donnees = await _get_json(
        ATMO_WFS_URL,
        {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": ATMO_COUCHE,
            "OUTPUTFORMAT": "application/json",
            "CQL_FILTER": f"code_zone IN ({liste}) AND date_ech='{jour}'",
        },
        "ATMO",
    )
    indices = indices_atmo_de(donnees, jour)
    if not set(indices) <= set(codes):
        raise UpstreamUnavailableError("ATMO", "zone not asked")
    return indices


async def _epci_de(code_insee: str) -> str | None:
    """The SIREN of the EPCI of a commune (geo.api.gouv.fr); None when the
    commune is unknown there or belongs to none."""
    try:
        async with httpx.AsyncClient(
            verify=CONTEXTE_TLS, timeout=_TIMEOUT_TERRAIN
        ) as client:
            reponse = await client.get(
                GEO_API_COMMUNE_URL.format(code=code_insee),
                params={"fields": "codeEpci"},
            )
    except httpx.HTTPError as exc:
        raise UpstreamUnavailableError("geo.api", "request failed") from exc
    if reponse.status_code == 404:
        return None
    if reponse.status_code != 200:
        raise UpstreamUnavailableError("geo.api", f"HTTP {reponse.status_code}")
    try:
        donnees = reponse.json()
    except ValueError as exc:
        raise UpstreamUnavailableError("geo.api", "invalid json") from exc
    if not isinstance(donnees, dict):
        raise UpstreamUnavailableError("geo.api", "invalid answer")
    epci = donnees.get("codeEpci")
    if epci is not None and not (isinstance(epci, str) and _CODE_ZONE.fullmatch(epci)):
        raise UpstreamUnavailableError("geo.api", "invalid EPCI")
    return epci


def _air_lu(indices: dict[str, IndiceAtmo | None], zones: list[str]) -> AirLu | None:
    """The index of the first zone of *zones* that has a row."""
    for zone in zones:
        if zone in indices:
            indice = indices[zone]
            if indice is None:
                return AirLu(indice=None, motif="indices_contradictoires")
            return AirLu(indice=indice)
    return None


async def _air_du_jour(codes: list[str], commune: str, jour: str) -> AirLu:
    # The EPCI is looked up while the commune is read; it is awaited only
    # when the commune has no index, so that it never delays nor fails one.
    recherche_epci = asyncio.ensure_future(_epci_de(commune))
    try:
        lu = _air_lu(await _indices_des_zones(codes, jour), codes)
        if lu is not None:
            return lu
        epci = await recherche_epci
    finally:
        recherche_epci.cancel()
    lu = _air_lu(await _indices_des_zones([epci], jour), [epci]) if epci else None
    return lu or AirLu(indice=None, motif="aucun_indice_du_jour")


async def air_au_point(contexte: ContexteAmont) -> object:
    """The ATMO index of the day: the zone as served (an arrondissement),
    then its commune, then the EPCI of the commune. None when
    ``qualite_air`` is not hosted."""
    if contexte.hosting.hostings["qualite_air"].etat != "heberge":
        return None
    brut = (contexte.identite.commune.code_insee or "").strip()
    commune = normalize_code_insee(brut) if _CODE_ZONE.fullmatch(brut) else ""
    codes = [c for c in dict.fromkeys((brut, commune)) if _CODE_ZONE.fullmatch(c)]
    jour = contexte.as_of.isoformat()

    if not codes:
        return AirLu(indice=None, motif="commune_indeterminee")

    async def lire():
        cle = ("atmo", brut, jour)
        try:
            async with asyncio.timeout(echeance_s()):
                lu = await _AIR_CACHE.obtenir(
                    cle, lambda: _air_du_jour(codes, commune, jour)
                )
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("qualite_air", exc)
        if lu.indice is None or lu.indice.code == 0:
            # The index of the day may be published, or computed, later.
            _AIR_CACHE.oublier(cle)
        return lu

    return await contexte.memo.obtenir("atmo", lire)


FILOSOFI_COUCHE = "INSEE.FILOSOFI.INDICATORS:carreaux_200m"
# Millésime the layer serves: Filosofi 2015, every value of three cells equal
# to the Insee file of 2015 (and not to 2017, 2019 nor 2021), 2026-10-31.
MILLESIME_CARREAUX = "2015"
IRIS_COUCHE = "STATISTICALUNITS.IRIS:contours_iris"
MELODI_URL = "https://api.insee.fr/melodi/data/DS_RP_POPULATION_COMP_IRIS"
# Geography of the census served by Melodi (« 2025-IRIS-… », 2026-10-31).
MELODI_GEO_IRIS = "2025-IRIS-{code}"
_REVENUS_CACHE = CacheAmont()


def _nombre_positif(valeur: object, source: str) -> float:
    if isinstance(valeur, bool) or not isinstance(valeur, int | float) or valeur < 0:
        raise UpstreamUnavailableError(source, "invalid number")
    return float(valeur)


@dataclass(frozen=True)
class Carreau:
    """A 200 m cell of Filosofi. ``niveau_vie_moyen`` is a mean (the sum of
    the living standards over the individuals), never a median."""

    id: str
    individus: float
    menages: float
    niveau_vie_moyen: int
    part_menages_pauvres: float | None
    impute: bool


@dataclass(frozen=True)
class CarreauLu:
    carreau: Carreau | None
    motif: str | None = None


def carreau_de(donnees: object) -> CarreauLu:
    """The cell holding the point. No cell is a gap; a point on the edge of
    two cells chooses none."""
    features = _collection_entiere(donnees, "Filosofi")
    if not features:
        return CarreauLu(carreau=None, motif="aucun_carreau")
    if len(features) > 1:
        return CarreauLu(carreau=None, motif="carreaux_multiples")
    p = features[0].get("properties") if isinstance(features[0], dict) else None
    if not isinstance(p, dict) or not isinstance(p.get("id_inspire"), str):
        raise UpstreamUnavailableError("Filosofi", "invalid cell")
    individus = _nombre_positif(p.get("ind"), "Filosofi")
    menages = _nombre_positif(p.get("men"), "Filosofi")
    niveau_vie = _nombre_positif(p.get("ind_snv"), "Filosofi")
    if individus == 0 or p.get("i_car_est") not in (0, 1):
        raise UpstreamUnavailableError("Filosofi", "invalid cell")
    pauvres = p.get("men_pauv")
    part = (
        float(arrondi(_nombre_positif(pauvres, "Filosofi") / menages * 100, 1))
        if pauvres is not None and menages
        else None
    )
    return CarreauLu(
        carreau=Carreau(
            id=p["id_inspire"],
            individus=individus,
            menages=menages,
            niveau_vie_moyen=int(arrondi(niveau_vie / individus, 0)),
            part_menages_pauvres=part,
            impute=p["i_car_est"] == 1,
        )
    )


@dataclass(frozen=True)
class Iris:
    code: str
    nom: str | None
    type: str | None


_CODE_IRIS = re.compile(r"\d[\dAB]\d{7}")


def iris_de(donnees: object) -> "IrisLu":
    """The IRIS whose contour holds the point; none is chosen when the point
    is on the edge of two."""
    features = _collection_entiere(donnees, "IRIS")
    if not features:
        return IrisLu(iris=None, motif="aucun_iris")
    if len(features) > 1:
        return IrisLu(iris=None, motif="iris_multiples")
    p = features[0].get("properties") if isinstance(features[0], dict) else None
    if not isinstance(p, dict):
        raise UpstreamUnavailableError("IRIS", "invalid feature")
    code = p.get("code_iris")
    if not isinstance(code, str) or not _CODE_IRIS.fullmatch(code):
        raise UpstreamUnavailableError("IRIS", "invalid IRIS")
    return IrisLu(
        iris=Iris(
            code=code,
            nom=_texte_ou_rien(p.get("nom_iris")),
            type=_texte_ou_rien(p.get("type_iris")),
        )
    )


@dataclass(frozen=True)
class CompositionIris:
    """Population of 15 or more by socio-professional category (census):
    each share in percent of the total, by PCS code."""

    annee: str
    population_15_ans_plus: int
    parts: dict[str, float]


# The categories the census serves for an IRIS (Nantes and Paris, read on
# 2026-10-31): a make-up without one of them, or with another, is not read.
PCS_IRIS = frozenset({"1", "2", "3", "4", "5", "6", "7", "9"})


def _effectifs_iris(donnees: object, geo: str) -> tuple[dict[str, float], set]:
    """Population of 15 or more by PCS code (``_T``: the total), and the
    years of every observation. Every observation is of *geo*, and one
    category is served once."""
    observations = donnees.get("observations") if isinstance(donnees, dict) else None
    paging = donnees.get("paging") if isinstance(donnees, dict) else None
    if not isinstance(observations, list) or not isinstance(paging, dict):
        raise UpstreamUnavailableError("Melodi", "invalid answer")
    if paging.get("next"):
        raise UpstreamUnavailableError("Melodi", "incomplete page")
    effectifs: dict[str, float] = {}
    annees = set()
    for obs in observations:
        dims = obs.get("dimensions") if isinstance(obs, dict) else None
        mesures = obs.get("measures") if isinstance(obs, dict) else None
        mesure = mesures.get("OBS_VALUE_NIVEAU") if isinstance(mesures, dict) else None
        if not isinstance(dims, dict) or not isinstance(mesure, dict):
            raise UpstreamUnavailableError("Melodi", "invalid observation")
        if dims.get("GEO") != geo:
            raise UpstreamUnavailableError("Melodi", "another territory")
        annees.add(dims.get("TIME_PERIOD"))
        if dims.get("SEX") != "_T" or dims.get("AGE") != "Y_GE15":
            continue
        pcs = dims.get("PCS")
        if not isinstance(pcs, str):
            raise UpstreamUnavailableError("Melodi", "invalid PCS")
        if pcs in effectifs:
            raise UpstreamUnavailableError("Melodi", "category served twice")
        effectifs[pcs] = _nombre_positif(mesure.get("value"), "Melodi")
    return effectifs, annees


def composition_iris_de(donnees: object, geo: str) -> CompositionIris | None:
    """The make-up of a Melodi answer for the IRIS *geo*; None without
    population.

    Raises :class:`UpstreamUnavailableError` on a partial or unreadable
    answer, on rows of several years or of another territory, and on a
    category missing, unknown or served twice.
    """
    effectifs, annees = _effectifs_iris(donnees, geo)
    if len(annees) > 1 or not all(isinstance(a, str) for a in annees):
        raise UpstreamUnavailableError("Melodi", "several years")
    if not effectifs:
        return None
    if "_T" not in effectifs:
        raise UpstreamUnavailableError("Melodi", "no total")
    total = effectifs.pop("_T")
    if total == 0:
        return None
    if effectifs.keys() != PCS_IRIS:
        raise UpstreamUnavailableError("Melodi", "incomplete make-up")
    return CompositionIris(
        annee=annees.pop(),
        population_15_ans_plus=int(arrondi(total, 0)),
        parts={
            pcs: float(arrondi(n / total * 100, 1))
            for pcs, n in sorted(effectifs.items())
        },
    )


@dataclass(frozen=True)
class IrisLu:
    iris: Iris | None
    composition: CompositionIris | None = None
    # The IRIS is named while the census read failed: a partial outage.
    panne_composition: bool = False
    motif: str | None = None
    # Why the census gives no make-up (an IRIS the census does not know).
    motif_composition: str | None = None


@dataclass(frozen=True)
class RevenusAmont:
    """The cell and the IRIS of the point, each None when not hosted or
    without a point, or :class:`PanneAmont`."""

    carreau: CarreauLu | PanneAmont | None
    iris: IrisLu | PanneAmont | None


async def _wfs_au_point(couche: str, geometrie: str, lon: float, lat: float) -> dict:
    return await _get_json(
        WFS_URL,
        {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": couche,
            "OUTPUTFORMAT": "application/json",
            "CQL_FILTER": f"INTERSECTS({geometrie},SRID=4326;POINT({lon} {lat}))",
        },
        couche,
    )


async def _carreau_au_point(lon: float, lat: float) -> CarreauLu | PanneAmont:
    async def lire_carreau() -> CarreauLu:
        return carreau_de(await _wfs_au_point(FILOSOFI_COUCHE, "geom", lon, lat))

    try:
        async with asyncio.timeout(echeance_s()):
            return await _REVENUS_CACHE.obtenir(("carreau", lon, lat), lire_carreau)
    except _UPSTREAM_ERRORS as exc:
        return lieu._panne_amont("filosofi_carreaux", exc)


class IrisHorsRecensementError(Exception):
    """Melodi does not know the IRIS (« Aucun territoire trouvé »)."""


async def _composition_melodi(code: str) -> CompositionIris | None:
    """The make-up of one IRIS. Raises :class:`IrisHorsRecensementError` on
    the 400 of an IRIS the census geography does not hold, and
    :class:`UpstreamUnavailableError` on any other failure."""
    geo = MELODI_GEO_IRIS.format(code=code)
    try:
        async with httpx.AsyncClient(
            verify=CONTEXTE_TLS, timeout=_TIMEOUT_TERRAIN
        ) as client:
            reponse = await client.get(
                MELODI_URL, params={"GEO": geo, "maxResult": 200}
            )
    except httpx.HTTPError as exc:
        raise UpstreamUnavailableError("Melodi", "request failed") from exc
    if reponse.status_code == 400 and "Aucun territoire" in reponse.text:
        raise IrisHorsRecensementError
    if reponse.status_code != 200:
        raise UpstreamUnavailableError("Melodi", f"HTTP {reponse.status_code}")
    try:
        donnees = reponse.json()
    except ValueError as exc:
        raise UpstreamUnavailableError("Melodi", "invalid json") from exc
    return composition_iris_de(donnees, geo)


async def _iris_au_point(lon: float, lat: float) -> IrisLu | PanneAmont:
    """The IRIS of the point, then its census make-up, within one deadline:
    an outage of the census keeps the IRIS named."""
    fin = asyncio.get_running_loop().time() + echeance_s()

    async def lire_iris() -> IrisLu:
        return iris_de(await _wfs_au_point(IRIS_COUCHE, "geometrie", lon, lat))

    try:
        async with asyncio.timeout_at(fin):
            lu = await _REVENUS_CACHE.obtenir(("iris", lon, lat), lire_iris)
    except _UPSTREAM_ERRORS as exc:
        return lieu._panne_amont("iris", exc)
    if lu.iris is None:
        return lu
    code = lu.iris.code

    async def lire_composition() -> CompositionIris | str | None:
        try:
            return await _composition_melodi(code)
        except IrisHorsRecensementError:
            return "iris_hors_recensement"

    try:
        async with asyncio.timeout_at(fin):
            composition = await _REVENUS_CACHE.obtenir(
                ("melodi", code), lire_composition
            )
    except _UPSTREAM_ERRORS as exc:
        lieu._panne_amont("iris", exc)
        return replace(lu, panne_composition=True)
    if isinstance(composition, str):
        return replace(lu, motif_composition=composition)
    return replace(lu, composition=composition)


async def revenus_au_point(contexte: ContexteAmont) -> RevenusAmont:
    """The Filosofi cell and the IRIS holding the BAN point, read apart: the
    outage of one never hides the other. Each is None when not hosted or
    without a point."""
    point = contexte.point
    if point is None:
        return RevenusAmont(carreau=None, iris=None)
    hostings = contexte.hosting.hostings
    # Exact coordinates: rounded, a point may fall in the neighbouring cell.
    lon, lat = point.lon, point.lat

    async def aucun() -> None:
        return None

    async def lire() -> RevenusAmont:
        lu_carreau, lu_iris = await asyncio.gather(
            _carreau_au_point(lon, lat)
            if hostings["filosofi_carreaux"].etat == "heberge"
            else aucun(),
            _iris_au_point(lon, lat) if hostings["iris"].etat == "heberge" else aucun(),
        )
        return RevenusAmont(carreau=lu_carreau, iris=lu_iris)

    return await contexte.memo.obtenir("revenus.point", lire)


# The nearest station is looked for this far; the stops keep their radius.
RAYON_GARE_M = 20_000


@dataclass(frozen=True)
class GareProche:
    """The nearest SNCF station within :data:`RAYON_GARE_M`; ``complete`` is
    False when the disc read reaches a departement the instance does not
    host, so that a nearer station may exist there."""

    nom: str | None
    distance_m: int | None
    complete: bool


async def gare_proche(contexte: ContexteRapport) -> GareProche | PanneAmont | None:
    """The nearest station; None when ``gares`` is not hosted or without a
    point, :class:`PanneAmont` when the read fails."""
    hebergement = contexte.hosting.hostings["gares"]
    point = contexte.point
    if hebergement.etat != "heberge" or point is None:
        return None
    session = contexte.session

    async def lire() -> GareProche:
        ligne = await gare_la_plus_proche(session, point.lon, point.lat, RAYON_GARE_M)
        distance = float(ligne["distance_m"]) if ligne else float(RAYON_GARE_M)
        deborde = await rayon_deborde(
            session, point.lon, point.lat, distance, hebergement.departements
        )
        nom = ligne.get("nom_gare") if ligne else None
        return GareProche(
            nom=nom.strip() if isinstance(nom, str) and nom.strip() else None,
            distance_m=round(distance) if ligne else None,
            complete=not deborde,
        )

    bloc = await lire_bloc(session, ["gares"], lire)
    if bloc.en_panne or bloc.valeur is None:
        return PanneAmont()
    return bloc.valeur


# --- 04.7-08: housing (tense zone and rent control, read live) ---------------

# Simulator of the tense zones (DILA, service-public.gouv.fr), the list the
# vacant housing tax applies on: two disjoint lists of communes.
ZONE_TENDUE_URL = (
    "https://gitlab.com/pidila/sp-simulateurs-data/-/raw/master/"
    "donnees-de-reference/TaxeLogementVacant.json"
)
CATEGORIES_ZONE_TENDUE = {
    "PlusDe50000": "agglomeration",
    "DesequilibreOffreEtDemande": "desequilibre",
}
# 1 434 and 2 263 communes on 2026-10-31: a list far shorter is a truncated
# file, whose absences would be served as « not concerned ».
ZONE_TENDUE_MINIMUM = 1000
# Communes under rent control (loi ELAN, art. 140), gathered commune by
# commune from the decrees and prefectoral orders by Naistor (data.gouv.fr
# 6ae3c8c1252503d60d5c00df); the stable link of its data.gouv.fr copy.
ENCADREMENT_URL = (
    "https://www.data.gouv.fr/api/1/datasets/r/8249b6fe-db2c-42d2-875a-7efa1e083377"
)
# 68 communes on 2026-10-29: a far shorter list is truncated.
ENCADREMENT_MINIMUM = 40
PERIMETRES_ENCADREMENT = frozenset({"commune", "partie"})
COLONNES_ENCADREMENT = (
    "code_insee",
    "commune",
    "territoire",
    "zone",
    "decret_perimetre",
    "arrete_prefectoral",
    "arrete_date",
    "en_vigueur_du",
    "en_vigueur_au",
    "source_libelle",
    "source_url",
    "verifie_le",
)
_CODE_COMMUNE = re.compile(r"\d[\dAB]\d{3}")
# Two national files, kept a day; an outage is never kept.
_LOGEMENT_CACHE = CacheAmont(taille=8)


async def _get_fichier(url: str, source: str) -> httpx.Response:
    """One GET of a national file, following its redirections."""
    try:
        async with httpx.AsyncClient(
            verify=CONTEXTE_TLS, timeout=_TIMEOUT_TERRAIN, follow_redirects=True
        ) as client:
            reponse = await client.get(url)
    except httpx.HTTPError as exc:
        raise UpstreamUnavailableError(source, "request failed") from exc
    if reponse.status_code != 200:
        raise UpstreamUnavailableError(source, f"HTTP {reponse.status_code}")
    return reponse


def zones_tendues_de(donnees: object) -> dict[str, str]:
    """The category of every commune of the simulator, by INSEE code.

    Raises :class:`UpstreamUnavailableError` on a file without both lists,
    with an unreadable entry, a commune in both lists or a list too short.
    ``Ok`` is true on every entry (2026-10-31) and its meaning is not
    documented (« Ok ou non »): an entry not ok is an outage, never a
    commune silently dropped.
    """
    if not isinstance(donnees, dict):
        raise UpstreamUnavailableError("Zone tendue", "invalid answer")
    zones: dict[str, str] = {}
    for cle, categorie in CATEGORIES_ZONE_TENDUE.items():
        liste = donnees.get(cle)
        if not isinstance(liste, list):
            raise UpstreamUnavailableError("Zone tendue", f"no list {cle}")
        for entree in liste:
            code = entree.get("codeInsee") if isinstance(entree, dict) else None
            ok = entree.get("Ok") if isinstance(entree, dict) else None
            if not isinstance(code, str) or not _CODE_COMMUNE.fullmatch(code):
                raise UpstreamUnavailableError("Zone tendue", "invalid commune")
            if ok is not True:
                raise UpstreamUnavailableError("Zone tendue", "entry not ok")
            if code in zones:
                raise UpstreamUnavailableError("Zone tendue", "commune in two lists")
            zones[code] = categorie
        if len(liste) < ZONE_TENDUE_MINIMUM:
            raise UpstreamUnavailableError("Zone tendue", f"incomplete list {cle}")
    return zones


@dataclass(frozen=True)
class ZoneTendueLue:
    """The commune read (an arrondissement is read on its commune) and its
    category; None when the commune is not in a tense zone."""

    commune: str
    categorie: str | None


async def _zones_tendues() -> dict[str, str]:
    async def lire() -> dict[str, str]:
        reponse = await _get_fichier(ZONE_TENDUE_URL, "Zone tendue")
        try:
            donnees = reponse.json()
        except ValueError as exc:
            raise UpstreamUnavailableError("Zone tendue", "invalid json") from exc
        return zones_tendues_de(donnees)

    return await _LOGEMENT_CACHE.obtenir("zones_tendues", lire)


def _commune_de(contexte: ContexteAmont) -> str | None:
    brut = (contexte.identite.commune.code_insee or "").strip()
    commune = normalize_code_insee(brut)
    return commune if _CODE_COMMUNE.fullmatch(commune) else None


async def zone_tendue_au_point(contexte: ContexteAmont) -> object:
    """Whether the commune of the address is in a tense zone. None when
    ``zone_tendue`` is not hosted or the commune is unknown."""
    if contexte.hosting.hostings["zone_tendue"].etat != "heberge":
        return None
    commune = _commune_de(contexte)
    if commune is None:
        return None

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                zones = await _zones_tendues()
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("zone_tendue", exc)
        return ZoneTendueLue(commune=commune, categorie=zones.get(commune))

    return await contexte.memo.obtenir("zone_tendue", lire)


@dataclass(frozen=True)
class PeriodeEncadrement:
    """One prefectoral order of a commune of the list, as the list gives it."""

    territoire: str | None
    zone: str | None
    decret: str | None
    arrete: str | None
    arrete_date: date | None
    du: date | None
    au: date | None
    source: str | None
    source_url: str | None
    verifie_le: date | None


def _date_ou_rien(valeur: str | None) -> date | None:
    texte = (valeur or "").strip()
    if not texte:
        return None
    try:
        return date.fromisoformat(texte)
    except ValueError as exc:
        raise UpstreamUnavailableError("Encadrement", "invalid date") from exc


def encadrement_de(texte: str) -> dict[str, list[PeriodeEncadrement]]:
    """The orders of the list, by INSEE code of the commune.

    Raises :class:`UpstreamUnavailableError` on a file without the expected
    columns, too short, or with an unreadable code, date or perimeter.
    """
    lecteur = csv.DictReader(io.StringIO(texte.lstrip("﻿")))
    if not set(COLONNES_ENCADREMENT) <= set(lecteur.fieldnames or ()):
        raise UpstreamUnavailableError("Encadrement", "missing columns")
    communes: dict[str, list[PeriodeEncadrement]] = {}
    for ligne in lecteur:
        # A short row (a cut file) leaves None, a long one a None key.
        if None in ligne or None in ligne.values():
            raise UpstreamUnavailableError("Encadrement", "malformed row")
        code = (ligne.get("code_insee") or "").strip()
        if not _CODE_COMMUNE.fullmatch(code):
            raise UpstreamUnavailableError("Encadrement", "invalid commune")
        if (ligne.get("zone") or "").strip() not in PERIMETRES_ENCADREMENT:
            raise UpstreamUnavailableError("Encadrement", "invalid perimeter")
        communes.setdefault(code, []).append(
            PeriodeEncadrement(
                territoire=_texte_ou_rien(ligne.get("territoire")),
                zone=_texte_ou_rien(ligne.get("zone")),
                decret=_texte_ou_rien(ligne.get("decret_perimetre")),
                arrete=_texte_ou_rien(ligne.get("arrete_prefectoral")),
                arrete_date=_date_ou_rien(ligne.get("arrete_date")),
                du=_date_ou_rien(ligne.get("en_vigueur_du")),
                au=_date_ou_rien(ligne.get("en_vigueur_au")),
                source=_texte_ou_rien(ligne.get("source_libelle")),
                source_url=_texte_ou_rien(ligne.get("source_url")),
                verifie_le=_date_ou_rien(ligne.get("verifie_le")),
            )
        )
    if len(communes) < ENCADREMENT_MINIMUM:
        raise UpstreamUnavailableError("Encadrement", "incomplete list")
    return communes


@dataclass(frozen=True)
class EncadrementLu:
    """The orders of the commune read; empty when it is not in the list, of
    which ``liste_verifiee_le`` is the latest check by its producer."""

    commune: str
    periodes: tuple[PeriodeEncadrement, ...]
    liste_verifiee_le: date | None = None


async def _communes_encadrees() -> dict[str, list[PeriodeEncadrement]]:
    async def lire() -> dict[str, list[PeriodeEncadrement]]:
        reponse = await _get_fichier(ENCADREMENT_URL, "Encadrement")
        return encadrement_de(reponse.text)

    return await _LOGEMENT_CACHE.obtenir("encadrement", lire)


async def encadrement_au_point(contexte: ContexteAmont) -> object:
    """The rent control orders of the commune of the address. None when
    ``encadrement_communes`` is not hosted or the commune is unknown."""
    if contexte.hosting.hostings["encadrement_communes"].etat != "heberge":
        return None
    commune = _commune_de(contexte)
    if commune is None:
        return None

    async def lire():
        try:
            async with asyncio.timeout(echeance_s()):
                communes = await _communes_encadrees()
        except _UPSTREAM_ERRORS as exc:
            return lieu._panne_amont("encadrement_communes", exc)
        verifiees = [
            p.verifie_le for ps in communes.values() for p in ps if p.verifie_le
        ]
        return EncadrementLu(
            commune=commune,
            periodes=tuple(communes.get(commune, ())),
            liste_verifiee_le=max(verifiees, default=None),
        )

    return await contexte.memo.obtenir("encadrement", lire)
