"""Protected address-DPE inventory routes and logement context orchestration.

The inventory, a consulted document and attribution to a dwelling are separate
objects. Exact-BAN documents may inform the historical building-detection
signal, but they never become logement cards or identity evidence.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Annotated, NamedTuple

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response as StarletteResponse

from app.common.exceptions import ErreurApiError
from app.common.publication_readiness import describe_read_failure
from app.common.schemas import ProblemDetail, ReponseApi
from app.common.sources import get_meta_sources
from app.database import async_session, get_db
from app.dpe.attribution import classify_dpe_attribution
from app.dpe.inventory_service import (
    DPE_INVENTORY_DEFAULT_PAGE_SIZE,
    DpeInventoryServiceResult,
    assert_inventory_date_current,
    load_dpe_document,
    load_dpe_inventory,
    load_dpe_inventory_result,
)
from app.dpe.matching import compare_inventory
from app.dpe.matching_schemas import DpeMatchResult, DpeMatchTarget
from app.dpe.queries import dpe_par_identifiants_ban
from app.dpe.references import InvalidReferenceError, RefreshRequiredError
from app.dpe.schemas import (
    DpeAttribution,
    DpeBuildingContext,
    DpeConsultedDocument,
    DpeInventoryDistribution,
    DpeInventoryPage,
)
from app.dvf.queries import dvf_appartements_par_parcelle
from app.logements.detection import (
    calcule_signaux,
    construit_denominateur,
    determine_mode,
)
from app.logements.queries import cles_ban_batiment_rnb, copropriete_par_parcelle
from app.logements.schemas import (
    BlocDetection,
    Denominateur,
    RattachementDpe,
    ResultatLogements,
    VenteImmeuble,
)
from app.scope.dependencies import RouteHosting, hosted_datasets

logger = logging.getLogger(__name__)

_DPE_REFERENCE_MAX_LENGTH = 512
_NO_STORE = {"Cache-Control": "no-store"}
_EMPTY_DISTRIBUTION = DpeInventoryDistribution.model_validate(
    {**dict.fromkeys("ABCDEFG", 0), "unknown": 0}
)

# 04-13: the two legacy routes are deprecated by the decision of 2026-10-06
# (replacements /v1/adresse/{ban_id} and /v1/dpe/{numero}); they keep answering
# unchanged. Constant instants, never recomputed per request. The announcement
# reaches clients only when deployed: Sunset keeps well over the 30-day beta
# minimum (api/docs/api-deprecation-policy.md § 3) and is not a removal date.
_DEPRECATED_AT = datetime(2026, 10, 6, tzinfo=UTC)
_SUNSET_AT = datetime(2027, 1, 6, tzinfo=UTC)
_DEPRECATION_HEADERS = {
    # RFC 9745: structured-field date, epoch seconds.
    "Deprecation": f"@{int(_DEPRECATED_AT.timestamp())}",
    # RFC 8594: HTTP-date (IMF-fixdate).
    "Sunset": format_datetime(_SUNSET_AT, usegmt=True),
}
DEPRECATED_PATHS = frozenset({"/v1/bien/logements", "/v1/bien/dpe/document"})


class _NoStoreValidationRoute(APIRoute):
    """Keep documentary responses private and announce the deprecated routes.

    Dependency failures (authentication, publication refusal, validation) are
    raised inside this handler, before the endpoint body: both the no-store
    header of the documentary routes and the deprecation headers are attached
    here so that errors carry them too.
    """

    def get_route_handler(
        self,
    ) -> Callable[[Request], Awaitable[StarletteResponse]]:
        original = super().get_route_handler()

        async def route_handler(request: Request) -> StarletteResponse:
            path = request.url.path
            documentary = path.startswith("/v1/bien/dpe/")
            announced = _DEPRECATION_HEADERS if path in DEPRECATED_PATHS else {}
            try:
                response = await original(request)
            except RequestValidationError as exc:
                if documentary:
                    raise ErreurApiError(
                        422,
                        "Erreur de validation des données",
                        headers={**_NO_STORE, **announced},
                    ) from None
                if not announced:
                    raise
                # Same detailed body as any other route, announcement added.
                handler = request.app.exception_handlers[RequestValidationError]
                response = await handler(request, exc)
            except ErreurApiError as exc:
                if not documentary and not announced:
                    raise
                private = _NO_STORE if documentary else {}
                headers = {**(exc.headers or {}), **private, **announced}
                raise ErreurApiError(
                    exc.code,
                    exc.message,
                    exc.detail,
                    headers=headers,
                ) from None
            except StarletteHTTPException as exc:
                if not announced:
                    raise
                exc.headers = {**(exc.headers or {}), **announced}
                raise
            response.headers.update(announced)
            return response

        return route_handler


router = APIRouter(
    prefix="/v1",
    tags=["Bien"],
    route_class=_NoStoreValidationRoute,
)

_NO_STORE_HEADER_DOC = {
    "Cache-Control": {
        "description": "Cette réponse documentaire ne doit pas être stockée.",
        "schema": {"type": "string", "example": "no-store"},
    }
}
_DEPRECATION_HEADER_DOC = {
    "Deprecation": {
        "description": "Route dépréciée depuis le 6 octobre 2026 (RFC 9745).",
        "schema": {"type": "string", "example": _DEPRECATION_HEADERS["Deprecation"]},
    },
    "Sunset": {
        "description": (
            "Retrait possible à partir de cette date, jamais avant (RFC 8594)."
        ),
        "schema": {"type": "string", "example": _DEPRECATION_HEADERS["Sunset"]},
    },
}
_PROBLEM_DETAIL_OPENAPI_SCHEMA = ProblemDetail.model_json_schema()


def _dpe_problem_response(description: str) -> dict:
    """Describe the exact RFC 9457 media type emitted by the exception handler."""
    return {
        "description": description,
        "headers": _NO_STORE_HEADER_DOC,
        "content": {
            "application/problem+json": {
                "schema": _PROBLEM_DETAIL_OPENAPI_SCHEMA,
            }
        },
    }


_DPE_ROUTE_RESPONSES = {
    200: {"headers": _NO_STORE_HEADER_DOC},
    400: _dpe_problem_response("Référence opaque invalide."),
    409: _dpe_problem_response("Contexte à rafraîchir, ou DPE ou BAN hors couverture."),
    422: _dpe_problem_response("Paramètres invalides."),
    503: _dpe_problem_response("Lecture documentaire temporairement indisponible."),
}


def _announced(responses: dict) -> dict:
    """Document the deprecation headers on every response of a deprecated route."""
    return {
        status: {
            **response,
            "headers": {**response.get("headers", {}), **_DEPRECATION_HEADER_DOC},
        }
        for status, response in responses.items()
    }


def _dpe_http_error(exc: Exception) -> ErreurApiError:
    """Map service failures to constant, non-reflective Problem Details."""
    if isinstance(exc, InvalidReferenceError):
        return ErreurApiError(400, "Référence DPE invalide", headers=_NO_STORE)
    if isinstance(exc, RefreshRequiredError):
        return ErreurApiError(
            409,
            "Inventaire DPE à rafraîchir",
            headers=_NO_STORE,
        )
    logger.warning(
        "lecture documentaire DPE indisponible (%s)", describe_read_failure(exc)
    )
    return ErreurApiError(
        503,
        "Données DPE momentanément indisponibles",
        headers=_NO_STORE,
    )


def _refuse_unless_hosted(hosting: RouteHosting, keys: tuple[str, ...]) -> None:
    """Refuse (409) before any read unless every one of *keys* is hosted.

    The DPE documentary routes read the diagnostics and the BAN address that
    anchors their inventory (``dpe_inventory_target_by_*`` reads ``adresses``):
    without either source there is no honest answer (D-23). The composite
    ``/v1/bien/logements`` payload has no out-of-coverage state either (D-24).
    """
    out = {key: hosting.hostings[key] for key in keys if not hosting.hosts(key)}
    if out:
        motifs = ", ".join(f"{key}: {value.motif.value}" for key, value in out.items())
        raise ErreurApiError(
            409, "Données hors couverture", f"Hors couverture — {motifs}", _NO_STORE
        )


_DPE_HOSTING = Annotated[RouteHosting, Depends(hosted_datasets("dpe", "ban"))]


@router.get(
    "/bien/dpe/inventaire",
    response_model=DpeInventoryPage,
    responses=_DPE_ROUTE_RESPONSES,
    summary="Diagnostics DPE connus à une adresse",
)
async def get_dpe_inventory(
    hosting: _DPE_HOSTING,
    response: Response,
    contexte: Annotated[
        str,
        Query(min_length=1, max_length=_DPE_REFERENCE_MAX_LENGTH),
    ],
    curseur: Annotated[
        str | None,
        Query(min_length=1, max_length=_DPE_REFERENCE_MAX_LENGTH),
    ] = None,
    limite: Annotated[int, Query(ge=1, le=50)] = DPE_INVENTORY_DEFAULT_PAGE_SIZE,
) -> DpeInventoryPage:
    """Return one bounded page without attributing any document to a dwelling."""
    _refuse_unless_hosted(hosting, ("dpe", "ban"))
    try:
        page = await load_dpe_inventory(
            async_session,
            context_ref=contexte,
            cursor=curseur,
            limit=limite,
        )
    except Exception as exc:  # noqa: BLE001
        raise _dpe_http_error(exc) from None
    response.headers.update(_NO_STORE)
    return page


@router.get(
    "/bien/dpe/document",
    response_model=DpeConsultedDocument,
    responses=_announced(_DPE_ROUTE_RESPONSES),
    summary="Consulter un document DPE connu à une adresse",
    description=(
        "Dépréciée depuis le 6 octobre 2026 : utiliser `GET /v1/dpe/{numero}` "
        "(document DPE lu par son numéro ADEME). Chaque réponse porte les "
        "en-têtes `Deprecation` et `Sunset` ; retrait possible à partir du "
        "6 janvier 2027, jamais avant. La route répond sans changement d'ici là."
    ),
    deprecated=True,
)
async def get_dpe_document(
    hosting: _DPE_HOSTING,
    response: Response,
    contexte: Annotated[
        str,
        Query(min_length=1, max_length=_DPE_REFERENCE_MAX_LENGTH),
    ],
    reference: Annotated[
        str,
        Query(min_length=1, max_length=_DPE_REFERENCE_MAX_LENGTH),
    ],
) -> DpeConsultedDocument:
    """Return one allowlisted document after target and generation checks."""
    _refuse_unless_hosted(hosting, ("dpe", "ban"))
    try:
        document = await load_dpe_document(
            async_session,
            context_ref=contexte,
            document_ref=reference,
        )
    except Exception as exc:  # noqa: BLE001
        raise _dpe_http_error(exc) from None
    response.headers.update(_NO_STORE)
    return document


class DpeComparisonRequest(BaseModel):
    """Optional public criteria in a body, never in a documentary URL."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    contexte: str = Field(strict=True, min_length=1, max_length=512)
    curseur: str | None = Field(default=None, strict=True, min_length=1, max_length=512)
    limite: int = Field(default=20, strict=True, ge=1, le=50)
    cible: DpeMatchTarget = Field(default_factory=DpeMatchTarget)


@router.post(
    "/bien/dpe/comparaison",
    response_model=DpeMatchResult,
    responses=_DPE_ROUTE_RESPONSES,
    summary="Comparer des caractéristiques aux diagnostics d'une page d'inventaire",
    description=(
        "Comparaison factuelle non attributive. Tous les documents de la page "
        "restent présents, avec leurs limites et leur cycle de vie. Une surface "
        "seule, une égalité ou un diagnostic unique ne prouve jamais l'identité "
        "du logement. Les autres pages ne sont pas réputées comparées."
    ),
)
async def compare_dpe_documents(
    hosting: _DPE_HOSTING, body: DpeComparisonRequest, response: Response
) -> DpeMatchResult:
    """Reuse signed context and snapshot checks without widening SQL or projection."""
    _refuse_unless_hosted(hosting, ("dpe", "ban"))
    try:
        page = await load_dpe_inventory(
            async_session,
            context_ref=body.contexte,
            cursor=body.curseur,
            limit=body.limite,
        )
        result = compare_inventory(page, body.cible)
        assert_inventory_date_current(page.as_of_date)
    except Exception as exc:  # noqa: BLE001
        raise _dpe_http_error(exc) from None
    response.headers.update(_NO_STORE)
    return result


class ContexteBatimentCharge(NamedTuple):
    """Bounded documents from other BAN keys, exposed only as an aggregate."""

    contexte: DpeBuildingContext | None
    lignes_batiment: list[dict]
    tronque: bool


async def _charge_contexte_batiment(
    id_parcelle: str,
    ban_id: str | None,
    limite: int,
) -> ContexteBatimentCharge:
    """Load an optional RNB context after the exact-address snapshot is closed."""
    if not ban_id:
        return ContexteBatimentCharge(None, [], False)
    try:
        async with async_session() as rnb_db:
            cles = await cles_ban_batiment_rnb(rnb_db, id_parcelle, ban_id)
            autres = [cle for cle in cles if cle and cle != ban_id]
            if not autres:
                return ContexteBatimentCharge(None, [], False)
            lignes, tronque = await dpe_par_identifiants_ban(
                rnb_db,
                autres,
                limit=limite,
            )
    except Exception:  # noqa: BLE001
        logger.warning("logements: contexte RNB secondaire indisponible")
        return ContexteBatimentCharge(None, [], False)
    decision = classify_dpe_attribution(
        ban_id,
        (),
        building_rows=lignes,
        building_truncated=tronque,
    )
    return ContexteBatimentCharge(decision.building_context, lignes, tronque)


def _unavailable_inventory() -> DpeInventoryServiceResult:
    page = DpeInventoryPage(
        state="unavailable",
        total=None,
        distribution=_EMPTY_DISTRIBUTION,
        page_size=DPE_INVENTORY_DEFAULT_PAGE_SIZE,
        has_more=False,
        read_state="partial",
    )
    return DpeInventoryServiceResult(page=page, apartment_document_count=None)


async def _charge_inventaire(ban_id: str | None) -> DpeInventoryServiceResult:
    try:
        return await load_dpe_inventory_result(
            async_session,
            ban_id=ban_id,
            limit=DPE_INVENTORY_DEFAULT_PAGE_SIZE,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "logements: inventaire DPE indisponible (%s)", describe_read_failure(exc)
        )
        return _unavailable_inventory()


def _decision_inventaire(
    page: DpeInventoryPage,
    contexte: DpeBuildingContext | None,
) -> DpeAttribution:
    common = {"building_context": contexte, "graph_state": page.graph_state}
    if page.state == "ready":
        return DpeAttribution(
            status="unassigned",
            reason="dwelling_identity_unproven",
            **common,
        )
    if page.state == "absent":
        return DpeAttribution(status="absent", reason="not_reported", **common)
    if page.state == "identity_unavailable":
        return DpeAttribution(status="absent", reason="identity_unavailable")
    return DpeAttribution(status="error", reason="temporary_error")


def _signaux_detection(
    *,
    copro_present: bool,
    inventaire: DpeInventoryServiceResult,
    ventes: int,
) -> dict:
    count = inventaire.apartment_document_count
    signaux = calcule_signaux(copro_present, count or 0, ventes)
    if count is not None:
        return signaux
    state = inventaire.page.state
    signaux["dpe_appart"] = {
        "positif": False,
        "presence": "error" if state == "unavailable" else "absent",
        "absence_reason": (
            "temporary_error" if state == "unavailable" else "not_applicable"
        ),
        "compte": 0,
    }
    return signaux


@router.get(
    "/bien/logements",
    response_model=ReponseApi[ResultatLogements],
    responses=_announced(
        {
            200: {"headers": _NO_STORE_HEADER_DOC},
            409: _dpe_problem_response("Un de DPE, DVF, copropriété hors couverture."),
            422: {
                "description": "Paramètres invalides.",
                "content": {
                    "application/problem+json": {
                        "schema": _PROBLEM_DETAIL_OPENAPI_SCHEMA,
                    }
                },
            },
        }
    ),
    summary="Logements d'un immeuble multi-logements (FICHE-06)",
    description=(
        "Détection multi-logements (copro RNC + documents DPE déclarés "
        "appartement + DVF appartement), inventaire exact-BAN et décision "
        "DPE V2 non attributive.\n\n"
        "Dépréciée depuis le 6 octobre 2026 : utiliser `GET /v1/adresse/{ban_id}` "
        "(page de l'adresse : documents DPE déclarés, ventes, lieu). Chaque "
        "réponse porte les en-têtes `Deprecation` et `Sunset` ; retrait possible "
        "à partir du 6 janvier 2027, jamais avant. La route répond sans "
        "changement d'ici là."
    ),
    deprecated=True,
)
async def get_logements(
    # rnb only widens the DPE context through rnb_liens: never read unhosted.
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets("dpe", "dvf", "copropriete", "rnb", "ban")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
    id_parcelle: Annotated[
        str,
        Query(
            min_length=14,
            max_length=14,
            pattern=r"^[0-9A-Z]{14}$",
            description="Identifiant cadastral canonique (14 caracteres)",
            example="85047000AB0042",
        ),
    ],
    ban_id: Annotated[
        str | None,
        Query(
            max_length=50,
            pattern=r"^[0-9A-Za-z_\-]{5,50}$",
            description=(
                "Cle d'interoperabilite BAN de l'adresse (INSEE_FANTOIR_NUMERO) "
                "-- seule voie d'attribution d'un DPE. Sans elle, la reponse est "
                "`absent/identity_unavailable` (D-04)"
            ),
            example="44109_zz01_00002",
        ),
    ] = None,
    adresse: Annotated[
        str | None,
        Query(
            max_length=200,
            description=(
                "Complement d'adresse optionnel (batiment/porte). Conserve pour "
                "compatibilite : depuis la Phase 51, il n'intervient plus dans "
                "l'attribution, qui repose uniquement sur `ban_id` (D-13/D-18)"
            ),
        ),
    ] = None,
    limite: Annotated[
        int,
        Query(ge=1, le=200, description="Borne des lectures DPE", example=120),
    ] = 120,
) -> ReponseApi[ResultatLogements]:
    """Assemble address inventory and parcel context without creating identities."""
    # The detection signals and the inventory have no out-of-coverage state:
    # one unhosted source would read as a false absent or 0 (SEM-01). Refuse
    # before any read (decision of 2026-09-26, WINDOWS 37).
    _refuse_unless_hosted(hosting, ("dpe", "dvf", "copropriete"))
    # The inventory target is the BAN address (``adresses``): without the ban
    # source, the identity is unavailable and nothing is read (D-23).
    inventaire = await _charge_inventaire(ban_id if hosting.hosts("ban") else None)
    if inventaire.page.state in {"ready", "absent"} and hosting.hosts("rnb"):
        charge_batiment = await _charge_contexte_batiment(
            id_parcelle,
            ban_id,
            limite,
        )
    else:
        # Without a usable exact-address target, an RNB aggregate cannot be
        # carried by the attribution state. Skipping it also prevents a
        # secondary neighbour read from turning a localized inventory failure
        # into an internally inconsistent response. Without rnb hosted, its
        # link table is never read (D-23).
        charge_batiment = ContexteBatimentCharge(None, [], False)
    decision = _decision_inventaire(inventaire.page, charge_batiment.contexte)
    distribution_dpe = (
        dict(charge_batiment.contexte.distribution)
        if charge_batiment.contexte is not None
        else {}
    )
    copro_rows = await copropriete_par_parcelle(db, id_parcelle)
    ventes_rows = await dvf_appartements_par_parcelle(db, id_parcelle)
    signaux = _signaux_detection(
        copro_present=bool(copro_rows),
        inventaire=inventaire,
        ventes=len(ventes_rows),
    )
    mode = determine_mode(signaux, preuve_identites=False)
    signaux_positifs = sum(1 for signal in signaux.values() if signal["positif"])

    lots_declares = copro_rows[0]["nb_lots_habitation_strict"] if copro_rows else None
    denominateur = construit_denominateur(0, lots_declares, 0)
    exact_count = inventaire.page.total or 0
    building_count = len(charge_batiment.lignes_batiment)

    resultat = ResultatLogements(
        id_parcelle=id_parcelle,
        detection=BlocDetection(
            signaux=signaux,
            signaux_positifs=signaux_positifs,
            mode=mode,
            preuve_identites=False,
        ),
        denominateur=Denominateur(**denominateur),
        distribution_dpe=distribution_dpe,
        ventes_immeuble=[VenteImmeuble(**row) for row in ventes_rows],
        dpe_inventory=inventaire.page,
        dpe_attribution=decision,
        diagnostics_charges=exact_count + building_count,
        diagnostics_exclus_adresse=0,
        rattachement=RattachementDpe(
            exact=exact_count,
            elargi_batiment=building_count,
            spatial=0,
        ),
        tronque=charge_batiment.tronque,
    )
    response.headers.update(_NO_STORE)
    return ReponseApi(
        donnees=resultat,
        meta=hosting.meta(get_meta_sources(["dpe", "dvf", "copropriete"])),
    )
