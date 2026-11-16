"""Address report: identity, upstream reads, themes, summary (04.7-01).

The sequence of the address page (``app.adresse.service``), applied to every
registered theme:

1. the identity of the BAN key (404, 503 and out of coverage as the page);
2. the identity transaction ends, then every upstream reader runs in
   parallel, outside any transaction (API CR-01): no publication lock and no
   pool connection is held while Géorisques, the GPU or ERRIAL answer (the
   BDNB is read in its import since 04.7-19, never live);
3. each theme reads the database in its own guarded blocks, in the order of
   the registry, in one transaction: the shared publication lock of the
   first guarded block holds until its end, so the themes read one
   generation (N-01).

A theme that raises is served as an outage of its own row, never as a
broken report; the logs carry bounded codes only (PRIV-01).

A view in two steps (R-07, ``app.rapport.suite``): asked with a short wait,
step 2 stops waiting after ``suite.ATTENTE_COURTE_S`` and the themes whose
read has not ended are served ``en_cours`` without reading the database for
them; the reads go on, and the same report asked with the complete wait
joins them. Without the parameter, step 2 is the one above, unchanged.
"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import date
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.service import lire_identite
from app.rapport import cache, suite
from app.rapport.alignement import aligner, phrase_rattachement
from app.rapport.carte import carte_de
from app.rapport.registre import THEMES
from app.rapport.resume import resume_hors_couverture, resumer
from app.rapport.schemas import Position, RapportAdresse, Theme
from app.rapport.socle import (
    ContexteAmont,
    ContexteRapport,
    DefinitionTheme,
    Memo,
    PanneAmont,
    sans_reponse,
)
from app.resolve import telemetry
from app.scope.dependencies import RouteHosting
from app.scope.model import InstanceScope

logger = logging.getLogger(__name__)

Attente = Literal["courte", "complete"]


def _signaler(definition: DefinitionTheme, outcome: str, exc: BaseException) -> None:
    # Bounded code only (PRIV-01): never the key, the point or a message.
    telemetry.emit_resolve_event(
        logger,
        "address_report_theme_failed",
        level=logging.WARNING,
        dataset=definition.id,
        outcome=outcome,
        error_code=telemetry.error_code_for(exc),
    )


async def lire_amonts(
    definitions: Sequence[DefinitionTheme], contexte: ContexteAmont
) -> dict[str, object]:
    """The answer of every upstream reader, by theme id, read in parallel."""
    avec_amont = [d for d in definitions if d.amont is not None]
    reponses = await asyncio.gather(
        *(d.amont(contexte) for d in avec_amont if d.amont is not None),
        return_exceptions=True,
    )
    amonts: dict[str, object] = {}
    for definition, reponse in zip(avec_amont, reponses, strict=True):
        if isinstance(reponse, BaseException):
            if not isinstance(reponse, Exception):
                raise reponse
            _signaler(definition, "amont_indisponible", reponse)
            reponse = PanneAmont()
        amonts[definition.id] = reponse
    return amonts


async def lire_theme(definition: DefinitionTheme, contexte: ContexteRapport) -> Theme:
    """The theme of *definition*; an error of its reader is its outage only."""
    try:
        theme = await definition.lire(contexte)
    except Exception as exc:  # noqa: BLE001 - one row in outage, never the report
        _signaler(definition, "erreur_theme", exc)
        return sans_reponse(definition, "panne", "erreur_theme")
    if theme.id != definition.id or (
        definition.rattachement_entite is not None and theme.rattachement is None
    ):
        _signaler(definition, "theme_non_conforme", ValueError(definition.id))
        return sans_reponse(definition, "panne", "erreur_theme")
    return theme


async def lire_rapport(
    session: AsyncSession,
    ban_id: str,
    hosting: RouteHosting,
    scope: InstanceScope,
    as_of: date,
    definitions: Sequence[DefinitionTheme] = THEMES,
    *,
    attente: Attente | None = None,
) -> RapportAdresse:
    """The report of *ban_id*, from the cache when it holds it.

    *attente*: ``courte`` serves the report after a short wait of the
    upstream reads, ``complete`` waits for the reads a short wait left;
    None waits for every read within the deadline of a page, as ever.

    Raises the errors of the identity (:func:`lire_identite`): unknown key,
    street key, identity outage.
    """
    cle = cache.cle_cache(ban_id, as_of, hosting)
    if (garde := cache.lire(cle)) is not None:
        return garde
    fiche, point = await lire_identite(session, ban_id, hosting)
    if fiche.couverture.etat != "heberge":
        rapport = RapportAdresse(
            adresse=fiche.identite,
            couverture=fiche.couverture,
            date=as_of,
            resume=resume_hors_couverture(),
            themes=[],
        )
        cache.ecrire(cle, rapport)
        return rapport
    # Read-only so far, no publication lock taken: give the connection back.
    await session.rollback()
    memo = Memo()
    contexte_amont = ContexteAmont(
        identite=fiche.identite,
        point=point,
        hosting=hosting,
        as_of=as_of,
        memo=memo,
    )
    if attente is None:
        amonts = await lire_amonts(definitions, contexte_amont)
    else:
        lectures = suite.obtenir(cle, definitions, contexte_amont)
        amonts = await lectures.attendre(
            suite.ATTENTE_COURTE_S if attente == "courte" else None
        )
        if suite.EN_COURS not in amonts.values():
            suite.oublier(cle)
    contexte = ContexteRapport(
        session=session,
        identite=fiche.identite,
        point=point,
        hosting=hosting,
        scope=scope,
        as_of=as_of,
        memo=memo,
        amonts=amonts,
    )
    themes = [
        aligner(
            sans_reponse(definition, "en_cours")
            if amonts.get(definition.id) is suite.EN_COURS
            else await lire_theme(definition, contexte)
        )
        for definition in definitions
    ]
    rapport = RapportAdresse(
        adresse=fiche.identite,
        couverture=fiche.couverture,
        date=as_of,
        resume=resumer(themes),
        themes=themes,
        rattachement_phrase=phrase_rattachement(themes),
        position=Position(lon=point.lon, lat=point.lat) if point else None,
        carte=carte_de(ban_id, point),
        lectures_en_cours=[t.id for t in themes if t.etat == "en_cours"],
    )
    cache.ecrire(cle, rapport)
    return rapport
