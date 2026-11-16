"""Risks of the address page: what is measured at the point, and nothing else.

Landmark ③ (decision of Ianis, 2026-10-02, N-03): « Argiles au point :
exposition {niveau} ». Géorisques answers several phenomena with different
scopes; only a phenomenon measured AT THE POINT feeds a landmark of the
place. The communal risks (GASPAR, radon) and the installations read by
radius (ICPE) belong to the « Risques » theme of 04-06, labelled commune.
The landmark is never a count of risks and never a « 0 ».

The « Risques » theme (04-06) reads the communal phenomena by INSEE code
(:func:`lire_risques_commune`), each with its own coverage: they are served
in their own group, attached to the commune (``commune_reference``), and
never merged with what is measured at the point.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal

import httpx

from app.adresse.blocs import source_valeur
from app.adresse.relations import PointBan
from app.adresse.schemas import ValeurLieu
from app.common.exceptions import UpstreamUnavailableError
from app.common.http import CONTEXTE_TLS
from app.common.proxy_coverage import ProxySource, bounded_rows, unavailable
from app.georisques.client import (
    GEORISQUES_BASE,
    GEORISQUES_HEADERS,
    RGA_DEADLINE_S,
    RGA_TIMEOUT,
    _safe_json,
    query_rga,
)
from app.resolve import telemetry
from app.scope.dependencies import RouteHosting

logger = logging.getLogger(__name__)

FIELD_ID_ARGILES = "adresse.risques.argiles_au_point"

Portee = Literal["au_point", "commune", "proximite"]


@dataclass(frozen=True)
class Phenomene:
    """One phenomenon of Géorisques, its key in the client and its scopes.

    ``field_id`` is the value of the « Risques » theme that serves it;
    ``chemin`` the Géorisques path read by INSEE code for a communal one.
    """

    cle_client: str
    portees: tuple[Portee, ...]
    field_id: str | None = None
    chemin: str | None = None


FIELD_ID_GASPAR = "adresse.risques.liste_commune"
FIELD_ID_RADON = "adresse.risques.radon_commune"
FIELD_ID_ICPE = "adresse.risques.icpe_commune"

# Closed table phenomenon -> scope (reused by the « Risques » theme, 04-06).
# ICPE is read by commune here (by radius on ``/v1/risques`` only).
PHENOMENES: dict[str, Phenomene] = {
    "argiles": Phenomene(
        cle_client="argiles", portees=("au_point",), field_id=FIELD_ID_ARGILES
    ),
    "gaspar": Phenomene(
        cle_client="risques_naturels",
        portees=("commune",),
        field_id=FIELD_ID_GASPAR,
        chemin="/gaspar/risques",
    ),
    "radon": Phenomene(
        cle_client="radon",
        portees=("commune",),
        field_id=FIELD_ID_RADON,
        chemin="/radon",
    ),
    "icpe": Phenomene(
        cle_client="icpe",
        portees=("commune", "proximite"),
        field_id=FIELD_ID_ICPE,
        chemin="/installations_classees",
    ),
}
# Rows read per communal sub-source: a first page, its coverage served.
LIGNES_COMMUNE = 50

# Fields of the RGA block holding the exposure, in order of preference.
_CHAMPS_EXPOSITION = ("exposition", "niveau", "libelle")
# An RGA answer is read as an answer only when it carries one of these keys
# (same frontier as ``query_all_risks``); otherwise it is an outage.
_CHAMPS_RGA = ("exposition", "code", "libelle", "niveau")


def phenomenes_au_point() -> tuple[str, ...]:
    """The phenomena allowed to feed a landmark of the place."""
    return tuple(cle for cle, phen in PHENOMENES.items() if "au_point" in phen.portees)


def phenomenes_commune() -> tuple[str, ...]:
    """The phenomena read by INSEE code, served as a reference of the commune."""
    return tuple(
        cle
        for cle, phen in PHENOMENES.items()
        if "commune" in phen.portees and phen.chemin is not None
    )


@dataclass(frozen=True)
class RisquesCommune:
    """First page of each communal phenomenon and its coverage.

    ``lignes[cle]`` is None when the sub-source did not answer
    (``couverture[cle].statut == "indisponible"``).
    """

    lignes: dict[str, list[dict] | None]
    couverture: dict[str, ProxySource]
    total: dict[str, int | None]


async def lire_risques_commune(code_insee: str) -> RisquesCommune:
    """GASPAR, radon and ICPE of the commune, by INSEE code (never by point).

    One client, the three paths in parallel, bounded by the deadline of
    landmark ③ (API CR-01): called with no database transaction open. A
    sub-source down is unavailable on its own; every sub-source down, or the
    deadline reached, raises :class:`UpstreamUnavailableError`.
    """
    cles = phenomenes_commune()
    try:
        async with asyncio.timeout(RGA_DEADLINE_S):
            async with httpx.AsyncClient(
                verify=CONTEXTE_TLS,
                base_url=GEORISQUES_BASE,
                timeout=RGA_TIMEOUT,
                headers=GEORISQUES_HEADERS,
            ) as client:
                reponses = await asyncio.gather(
                    *(
                        client.get(
                            PHENOMENES[cle].chemin,
                            params={
                                "code_insee": code_insee,
                                "page_size": LIGNES_COMMUNE,
                            },
                        )
                        for cle in cles
                    ),
                    return_exceptions=True,
                )
    except (TimeoutError, httpx.HTTPError) as exc:
        raise UpstreamUnavailableError("Georisques commune", "request failed") from exc
    lignes: dict[str, list[dict] | None] = {}
    couverture: dict[str, ProxySource] = {}
    total: dict[str, int | None] = {}
    for cle, reponse in zip(cles, reponses, strict=True):
        bloc = None if isinstance(reponse, BaseException) else _safe_json(reponse)
        lignes[cle], couverture[cle], total[cle] = None, unavailable(), None
        if bloc is None or not isinstance(bloc.get("data"), list):
            continue
        try:
            lues = bounded_rows(
                bloc["data"],
                limit=LIGNES_COMMUNE,
                total=bloc.get("results"),
                next_page=bloc.get("next"),
            )
        except UpstreamUnavailableError:
            continue
        lignes[cle], couverture[cle] = list(lues), lues.coverage
        total[cle] = lues.coverage.total
    if all(lu is None for lu in lignes.values()):
        raise UpstreamUnavailableError("Georisques commune", "all sub-sources down")
    return RisquesCommune(lignes=lignes, couverture=couverture, total=total)


def exposition_argiles(bloc: dict | None) -> str | None:
    """Exposure served by the RGA block, as written; None when absent."""
    if not isinstance(bloc, dict):
        return None
    for champ in _CHAMPS_EXPOSITION:
        valeur = bloc.get(champ)
        if isinstance(valeur, str) and valeur.strip():
            return valeur.strip()
    return None


async def repere_argiles_au_point(
    point: PointBan | None, hosting: RouteHosting, as_of: date
) -> ValeurLieu:
    """Landmark ③: clay shrink-swell exposure at the BAN point (RGA).

    Géorisques out of coverage: ``out_of_coverage`` without a call. No
    single point: a gap. Only ``/rga`` is called (API CR-01, IN-05), with no
    database transaction open: the caller ends it before this call. The RGA
    down or an answer without its keys: ``temporary_error``. An empty answer
    or one without exposure: ``not_reported``. ``periode`` is the day of the
    consultation.
    """
    commun = {
        "field_id": FIELD_ID_ARGILES,
        "source": source_valeur("georisques"),
        "rattachement": "au_point",
        "unite": "none",
    }
    hebergement = hosting.hostings["georisques"]
    if hebergement.etat != "heberge":
        return ValeurLieu(etat="out_of_coverage", motif=hebergement.motif, **commun)
    if point is None:
        return ValeurLieu(etat="not_available", motif="position_indeterminee", **commun)
    try:
        bloc = await query_rga(point.lat, point.lon)
    except UpstreamUnavailableError as exc:
        # Bounded code only (PRIV-01): never the point.
        telemetry.emit_resolve_event(
            logger,
            "address_block_unavailable",
            level=logging.WARNING,
            dataset="georisques",
            outcome="amont_indisponible",
            error_code=telemetry.error_code_for(exc),
        )
        return ValeurLieu(etat="temporary_error", motif="amont_indisponible", **commun)
    if bloc is not None and not any(champ in bloc for champ in _CHAMPS_RGA):
        return ValeurLieu(etat="temporary_error", motif="amont_indisponible", **commun)
    exposition = exposition_argiles(bloc)
    if exposition is None:
        return ValeurLieu(etat="not_reported", periode=as_of.isoformat(), **commun)
    return ValeurLieu(
        etat="value", valeur=exposition, periode=as_of.isoformat(), **commun
    )
