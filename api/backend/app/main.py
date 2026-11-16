"""FastAPI application factory with lifespan management."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from scalar_fastapi import get_scalar_api_reference
from sqlalchemy import text

from app.admin import api_keys as admin_api_keys
from app.admin import audit as admin_audit
from app.admin import etl as admin_etl
from app.admin import overview as admin_overview
from app.admin import requests as admin_requests
from app.admin import subscriptions as admin_subscriptions
from app.admin import system as admin_system
from app.admin import users as admin_users
from app.adresse.router import adresse_router
from app.antennes.router import router as antennes_router
from app.auth.dependencies import check_quota_hybrid
from app.auth.router import router as auth_router
from app.ban.router import router as ban_router
from app.batiments.router import router as batiments_router
from app.bdnb.router import router as bdnb_router
from app.billing.router import router as billing_router
from app.cadastre.router import router as cadastre_router
from app.carte.router import carte_router
from app.common.exceptions import ErrorHandlingMiddleware, register_exception_handlers
from app.common.migration_check import check_migrations_current
from app.common.publication_readiness import (
    require_publication,
    shutdown_admissions,
)
from app.commune.router import router as commune_synthese_router
from app.config import settings
from app.connectivite.router import router as connectivite_router
from app.copropriete.router import router as copropriete_router
from app.database import async_session, engine
from app.datasets.router import router as datasets_router
from app.demographie.router import router as demographie_router
from app.dpe.router import router as dpe_router
from app.dvf.router import router as dvf_router
from app.encadrement_loyers.router import router as encadrement_loyers_router
from app.equipements.router import router as equipements_router
from app.errial.router import router as errial_router
from app.fiscalite.router import router as fiscalite_router
from app.foncier_pm.router import router as foncier_pm_router
from app.georisques.router import router as georisques_router
from app.health.router import router as health_router
from app.logements.router import router as logements_router
from app.logging_config import configure_logging
from app.loyers.router import router as loyers_router
from app.mcp.protocole import mcp_router
from app.middleware import NoIndexMiddleware, RequestLoggingMiddleware
from app.monuments.router import router as monuments_router
from app.openapi_sources import install_openapi_sources
from app.permis.router import router as permis_router
from app.rapport.partage import prechauffer_delinquance
from app.rapport.router import rapport_router
from app.rate_limit import RateLimitMiddleware
from app.resolve.router import router as resolve_router
from app.revenus.router import router as revenus_router
from app.rnb.router import router as rnb_router
from app.scolaire.router import router as scolaire_router
from app.scope.coverage import hosting_for
from app.scope.store import load_scope
from app.scoring.router import router as scoring_router
from app.signalement.router import signalement_router
from app.sirene.router import router as sirene_router
from app.transport.router import router as transport_router
from app.urbanisme.router import router as urbanisme_router
from app.vacance.router import router as vacance_router
from app.validation_reports.router import router as validation_reports_router
from app.zonage_abc.router import router as zonage_abc_router
from app.zone.router import router as zone_router

logger = logging.getLogger(__name__)


async def _prechauffer_amonts() -> asyncio.Task | None:
    """Start reading the communal SSMSI file in the background when the
    instance hosts the delinquency: read whole (16 MB, seconds), it would
    otherwise be read by the first address page, past that page's deadline."""
    if not settings.prechauffage_amonts:
        return None
    async with async_session() as session:
        scope = await load_scope(session)
    if hosting_for("delinquance", scope, None).etat != "heberge":
        return None
    return asyncio.create_task(prechauffer_delinquance())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown lifecycle.

    Startup: configure structured logging, verify database connectivity.
    Shutdown: dispose of the database engine connection pool.
    """
    configure_logging(json_output=settings.log_format == "json")

    if "*" in settings.cors_origins:
        # CORE-01 fail-fast: wildcard CORS with allow_credentials=True makes
        # Starlette reflect any Origin with Access-Control-Allow-Credentials,
        # i.e. generalized CSRF on the JWT session cookie. Aligned with the
        # fail-fast policy of config.py: refuse to boot instead of warning.
        raise RuntimeError(
            "CORS_ORIGINS contient '*' — incompatible avec allow_credentials=True"
            " (vol de session cross-origin). Définissez des domaines explicites"
            ' dans .env : CORS_ORIGINS=["https://datacommune.fr",'
            '"https://dev.datacommune.fr"]'
        )

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Connexion a la base de donnees verifiee")

    # ADR-0003 D2: refuse to boot on blocking schema drift (DB behind code),
    # turning the /v1/dpe 500 incident class into a franc, alerted boot failure.
    await check_migrations_current(engine)

    prechauffage = await _prechauffer_amonts()

    yield

    if prechauffage is not None:
        prechauffage.cancel()

    # Background admission tasks still hold pool connections: drain them first.
    await shutdown_admissions()
    await engine.dispose()
    logger.info("Pool de connexions ferme")


tags_metadata = [
    {
        "name": "Parcelle",
        "description": (
            "Donnees a la parcelle ou adresse — cadastre, DPE, DVF, urbanisme, "
            "BDNB, RNB, ERRIAL. Encadrement des loyers indisponible (quarantaine). "
            "Recherche par coordonnees GPS ou reference cadastrale."
        ),
    },
    {
        "name": "Commune",
        "description": (
            "Indicateurs agreges au niveau commune — demographie, revenus, "
            "fiscalite, loyers, vacance, zonage ABC, coproprietes, "
            "permis de construire, carte scolaire, connectivite. "
            "Scoring renovation indisponible (quarantaine). "
            "Recherche par coordonnees GPS ou code INSEE."
        ),
    },
    {
        "name": "Proximite",
        "description": (
            "Recherche spatiale par rayon autour d'un point GPS — "
            "transports, monuments, batiments, entreprises, risques, "
            "pollutions, antennes, equipements. "
            "Parametres lat/lon + rayon en metres."
        ),
    },
    {
        "name": "auth",
        "description": (
            "Authentification, creation de compte, gestion des cles API "
            "et sessions JWT. Endpoints publics (pas de X-API-Key requis)."
        ),
    },
    {
        "name": "billing",
        "description": (
            "Abonnements et facturation via Stripe Checkout. "
            "Plans : Decouverte (500 req/mois), Pro, Business, Enterprise."
        ),
    },
    {
        "name": "datasets",
        "description": (
            "Metadonnees des datasets ingeres : statut, fraicheur, "
            "nombre de lignes, prochaine mise a jour prevue."
        ),
    },
    {
        "name": "sante",
        "description": (
            "Probes de sante pour monitoring (liveness + readiness). "
            "Retourne 503 si la base est deconnectee ou si des datasets sont obsoletes."
        ),
    },
]

app = FastAPI(
    title="DataCommune API",
    description=(
        "API unifiee d'acces aux donnees publiques francaises geolocalisees.\n\n"
        # OWD-03 (C4): governance banner. What is published WITHOUT a stability
        # notice becomes frozen by accident — this banner is the notice. Guarded
        # by tests/test_openapi_contract_governance.py; the reference policy
        # lives in api/docs/api-deprecation-policy.md. Keep the two consistent.
        "## Statut du contrat : beta publique\n"
        "`/v1` est en **beta publique** : le contrat (noms de champs, formes de "
        "reponse, parametres) peut changer sans garantie de compatibilite "
        "ascendante tant que ce statut est affiche.\n"
        "- Tout retrait ou renommage passe par une depreciation annoncee : "
        "`deprecated` dans ce document, puis en-tetes `Deprecation` et `Sunset` "
        "(RFC 8594) sur les reponses concernees, avec un delai minimal de "
        "30 jours avant retrait.\n"
        "- **Contrat d'erreur stable**, fiable des maintenant : le code de "
        "statut HTTP, l'en-tete `Retry-After` (present sur 429 et 503), et le "
        "champ `status` du corps `application/problem+json` (RFC 9457). "
        "Les textes `title` et `detail` sont destines aux humains et peuvent "
        "changer sans preavis — ne les parsez pas.\n\n"
        "## Authentification\n"
        "Toutes les routes `/v1/*` requierent une cle API dans le header `X-API-Key`.\n"
        "La creation de cle n'est pas ouverte pour le moment.\n\n"
        "## Recherche par localisation\n"
        "La plupart des endpoints acceptent **coordonnees GPS** (`lat` + `lon`) "
        "ou **code INSEE** de la commune.\n"
        "Les coordonnees sont en WGS-84 (systeme standard GPS).\n\n"
        "## Point d'entree unifie\n"
        "`GET /v1/resolve?q=...` detecte automatiquement si l'entree est une adresse, "
        "des coordonnees GPS ou une reference cadastrale, et peut enrichir avec "
        "plusieurs datasets en parallele.\n\n"
        "## Exemples de coordonnees\n"
        "- **Paris** : lat=48.8566, lon=2.3522\n"
        "- **Nantes** : lat=47.2184, lon=-1.5536\n"
        "- **Lyon** : lat=45.7640, lon=4.8357\n"
        "- **Bordeaux** : lat=44.8378, lon=-0.5792\n\n"
        "## Pagination\n"
        "Les endpoints pagines retournent un champ `pagination.curseur_suivant`. "
        "Passez-le dans le parametre `curseur` pour obtenir la page suivante."
    ),
    version="3.0.0",
    lifespan=lifespan,
    openapi_tags=tags_metadata,
    docs_url=None,
    redoc_url=None,
)

register_exception_handlers(app)

# Middleware stack (executed bottom-to-top: Error -> Logging -> RateLimit -> CORS
# -> NoIndex)
# ErrorHandlingMiddleware MUST be inside CORS so error responses get CORS headers.
app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type", "Authorization"],
    allow_credentials=True,
    expose_headers=[
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        # D-11: cross-origin JS (datacommune.fr -> api.datacommune.fr) must read
        # Retry-After to distinguish the shared-key per-IP rate limit from a
        # quota/account 429 (see frontend client.ts isAnonymous429).
        "Retry-After",
    ],
)
# Outermost: every response, CORS preflights and 429 refusals included.
app.add_middleware(NoIndexMiddleware)

# Public routes (no auth required)
app.include_router(health_router)
# Map images (04.7-17): public, an <img> tag carries no API key.
app.include_router(carte_router)
app.include_router(auth_router)
app.include_router(billing_router)
# Error reports of the site: off by default, closed by Caddy on the public API host.
app.include_router(signalement_router)
# MCP server for agents (D-143): off by default (MCP_ENABLED); its tools replay
# the protected /v1 routes below in process, keyless on the visitor's buckets.
app.include_router(mcp_router)

# Protected routes (API key + quota enforcement)
protected_router = APIRouter(dependencies=[Depends(check_quota_hybrid)])
# BAN and cadastre are not journaled sources: no publication guard (N-10).
protected_router.include_router(adresse_router)
protected_router.include_router(rapport_router)
protected_router.include_router(ban_router)
protected_router.include_router(bdnb_router)
protected_router.include_router(batiments_router)
protected_router.include_router(cadastre_router)
protected_router.include_router(commune_synthese_router)
protected_router.include_router(
    connectivite_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(dpe_router, dependencies=[Depends(require_publication)])
protected_router.include_router(dvf_router, dependencies=[Depends(require_publication)])
protected_router.include_router(errial_router)
protected_router.include_router(georisques_router)
protected_router.include_router(
    loyers_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(
    monuments_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(
    scolaire_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(sirene_router)
protected_router.include_router(transport_router)
protected_router.include_router(urbanisme_router)
protected_router.include_router(resolve_router)
protected_router.include_router(
    logements_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(rnb_router)
protected_router.include_router(encadrement_loyers_router)
protected_router.include_router(
    fiscalite_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(
    foncier_pm_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(
    zonage_abc_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(
    revenus_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(
    demographie_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(copropriete_router)
protected_router.include_router(permis_router)
protected_router.include_router(antennes_router)
protected_router.include_router(
    equipements_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(
    vacance_router, dependencies=[Depends(require_publication)]
)
protected_router.include_router(datasets_router)
protected_router.include_router(scoring_router)
protected_router.include_router(zone_router)
app.include_router(protected_router)

# Admin routes — require_admin dependency (is_admin=True + 50 req/min per admin)
app.include_router(admin_users.router)
app.include_router(admin_audit.router)
app.include_router(admin_requests.router)
app.include_router(admin_api_keys.router)
app.include_router(admin_subscriptions.router)
app.include_router(admin_overview.router)
app.include_router(admin_etl.router)
app.include_router(admin_system.router)

# Validation reports — admin-only, JWT auth at endpoint level
app.include_router(validation_reports_router)
install_openapi_sources(app)


@app.get("/docs", include_in_schema=False)
async def scalar_html():
    """Serve Scalar API reference instead of the default Swagger UI."""
    return get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title="DataCommune API",
    )
