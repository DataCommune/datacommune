"""``POST /v1/signalements``: an error report sent by the form of the site.

A public write, bounded on every side (framing of 2026-11-10):

- off unless ``SIGNALEMENT_ENABLED`` is set: the route answers 404;
- not reachable from the internet on the public instance: Caddy answers 404 on
  ``api.datacommune.fr`` and only the server of the site calls it, over the
  internal network, with the visitor's address (``Caddyfile.public``);
- two buckets per visitor (ten minutes, one day), spent before the body is
  read, and a daily ceiling for the whole site counted in the database;
- a filled honeypot gets the answer of a success and stores nothing;
- the answer carries a random reference only; there is no route to read.
"""

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ErreurApiError
from app.common.schemas import ProblemDetail
from app.config import settings
from app.database import get_db
from app.middleware import _get_client_ip, cle_visiteur
from app.rate_limit import _check_rate
from app.signalement.schemas import SignalementDepot, SignalementRecu
from app.signalement.service import compter_24h, enregistrer

logger = logging.getLogger(__name__)

signalement_router = APIRouter(prefix="/v1", tags=["Signalement"])

BUCKET_10_MIN = "signalement:ip10:"
BUCKET_JOUR = "signalement:ipjour:"
_DIX_MINUTES = 600
_UN_JOUR = 86_400
_NO_STORE = {"Cache-Control": "no-store"}
_PROBLEME = {"model": ProblemDetail}


def _trop_de_demandes(reset: int) -> ErreurApiError:
    return ErreurApiError(
        429,
        "Trop de signalements",
        "Reessayez plus tard.",
        headers={**_NO_STORE, "Retry-After": str(reset)},
    )


async def signalement_actif() -> None:
    """The route does not exist until the setting is on."""
    if not settings.signalement_enabled:
        raise ErreurApiError(404, "Not Found")


async def limite_signalement(request: Request) -> None:
    """Both buckets of the visitor, spent whatever the payload is worth."""
    visiteur = cle_visiteur(_get_client_ip(request))
    for prefixe, limite, fenetre in (
        (BUCKET_10_MIN, settings.signalement_par_ip_10_min, _DIX_MINUTES),
        (BUCKET_JOUR, settings.signalement_par_ip_jour, _UN_JOUR),
    ):
        allowed, _, reset = _check_rate(f"{prefixe}{visiteur}", limite, fenetre)
        if not allowed:
            logger.warning("signalement_rate_limited")
            raise _trop_de_demandes(reset)


def _reference() -> str:
    """Ten hexadecimal characters drawn at random: quotable, not enumerable."""
    return secrets.token_hex(5).upper()


@signalement_router.post(
    "/signalements",
    status_code=201,
    response_model=SignalementRecu,
    # Order matters: a switched-off route spends no bucket.
    dependencies=[Depends(signalement_actif), Depends(limite_signalement)],
    responses={404: _PROBLEME, 422: _PROBLEME, 429: _PROBLEME, 503: _PROBLEME},
    summary="Deposer un signalement d'erreur",
    # Not part of the public data contract: on the public instance the route is
    # closed to the internet and called by the server of the site alone.
    include_in_schema=False,
)
async def deposer_signalement(
    depot: SignalementDepot,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SignalementRecu:
    """Store a report; the answer is the same whether a robot was filtered or not."""
    response.headers.update(_NO_STORE)
    reference = _reference()
    if depot.piege:
        logger.info("signalement_piege")
        return SignalementRecu(reference=reference)
    try:
        if await compter_24h(db) >= settings.signalement_plafond_jour:
            logger.warning("signalement_plafond_jour")
            raise _trop_de_demandes(_UN_JOUR // 24)
        await enregistrer(db, depot, reference)
    except ErreurApiError:
        raise
    except Exception:  # noqa: BLE001
        # Constant answer: neither the cause nor anything of the input.
        logger.error("signalement_enregistrement_echoue")  # noqa: TRY400
        raise ErreurApiError(
            503,
            "Signalement momentanement indisponible",
            headers={**_NO_STORE, "Retry-After": "60"},
        ) from None
    return SignalementRecu(reference=reference)
