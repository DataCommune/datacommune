"""``GET /v1/adresse/{ban_id}/rapport``: the address report (04.7-01).

Mounted on the protected router with the existing authentication, beside the
routes of the address page, with their key pattern and their constant
problems. No route-wide publication guard: each journaled source is guarded
in its own block, as on the address page (N-01).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.router import (
    BAN_ID_MAX_LENGTH,
    BAN_ID_PATTERN,
    NO_STORE,
    _probleme_doc,
    departement_de_la_cle,
    probleme,
)
from app.adresse.service import (
    AdresseInconnueError,
    AdresseIndisponibleError,
    VoieSansNumeroError,
)
from app.common.schemas import MetaReponse, ReponseApi
from app.common.sources import get_meta_sources
from app.database import get_db
from app.dpe.references import paris_today
from app.rapport.registre import CLES_RAPPORT
from app.rapport.schemas import RapportAdresse
from app.rapport.service import Attente, lire_rapport
from app.scope.dependencies import RouteHosting, get_instance_scope, hosted_datasets
from app.scope.model import InstanceScope

rapport_router = APIRouter(prefix="/v1", tags=["Adresse"])


@rapport_router.get(
    "/adresse/{ban_id}/rapport",
    response_model=ReponseApi[RapportAdresse],
    responses={
        404: _probleme_doc("Adresse inconnue ou voie sans numero."),
        503: _probleme_doc("Lecture de l'adresse indisponible (panne SQL)."),
    },
    summary="Rapport d'une adresse : resume et themes",
    description=(
        "Pour une adresse BAN : l'adresse resolue, un resume (points a verifier "
        "a l'adresse, points a l'echelle de la commune, points rassurants, "
        "quatre chiffres cles) et une liste de themes au meme format. Chaque "
        "theme porte son etat (ok, zero, manque, hors_couverture, panne, "
        "non_concerne), son ton (vigilance, rassurant, neutre, info), sa "
        "granularite reelle, un verdict d'une phrase, au plus trois chiffres, "
        "son detail, son rattachement a l'adresse avec sa confiance et ses "
        "sources (millesime, mode direct ou import). Une source en panne ne "
        "rend que son theme en panne, jamais un zero. Aucun DPE n'est rattache "
        "a un batiment ou a un logement ; la position du point BAN (deja "
        "publique) et la carte de l'en-tete, servie par le site ; aucun prix de "
        "vente individuelle. Parametre `attente` (facultatif) : `courte` "
        "rend le rapport apres une attente breve des sources lues en direct ; "
        "un theme dont la lecture n'a pas fini porte alors l'etat `en_cours` "
        "(ni panne, ni manque) et son id figure dans `lectures_en_cours` ; "
        "`complete` attend ces lectures et rend le rapport definitif. Sans le "
        "parametre, l'etat `en_cours` n'est jamais servi."
    ),
)
async def lire_rapport_adresse(
    request: Request,
    hosting: Annotated[
        RouteHosting,
        Depends(hosted_datasets(*CLES_RAPPORT, departement=departement_de_la_cle)),
    ],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    ban_id: Annotated[
        str,
        Path(
            max_length=BAN_ID_MAX_LENGTH,
            pattern=BAN_ID_PATTERN,
            description="Cle BAN servie (ex. 85191_0420_00012)",
        ),
    ],
    attente: Annotated[
        Attente | None,
        Query(
            description=(
                "`courte` : reponse apres une attente breve, les lectures "
                "lentes servies `en_cours` ; `complete` : attend ces lectures."
            ),
        ),
    ] = None,
) -> ReponseApi[RapportAdresse] | JSONResponse:
    """The report of one BAN key; constant problems (T-04-02-03)."""
    try:
        if attente is None:
            rapport = await lire_rapport(db, ban_id, hosting, scope, paris_today())
        else:
            rapport = await lire_rapport(
                db, ban_id, hosting, scope, paris_today(), attente=attente
            )
    except VoieSansNumeroError:
        return probleme(request, 404, "voie_sans_numero", "Voie sans numero")
    except AdresseInconnueError:
        return probleme(request, 404, "adresse_inconnue", "Adresse inconnue")
    except AdresseIndisponibleError:
        return probleme(
            request, 503, "adresse_indisponible", "Adresse momentanement indisponible"
        )
    meta = MetaReponse.model_validate(get_meta_sources(["ban", "cadastre"]))
    corps = ReponseApi(donnees=rapport, meta=meta)
    return JSONResponse(content=corps.model_dump(mode="json"), headers=NO_STORE)
