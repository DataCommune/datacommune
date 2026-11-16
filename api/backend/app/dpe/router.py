"""FastAPI router for DPE energy diagnostic endpoints."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import build_page, decode_pagination
from app.common.schemas import PaginationMeta, ReponseApi, ReponsePaginee
from app.common.sources import get_meta_source, get_meta_sources
from app.database import get_db
from app.dpe.comparaison_public import ComparaisonDocuments, comparer_documents
from app.dpe.document_public import (
    NUMERO_DPE_PATTERN,
    NUMERO_DPE_RE,
    CouvertureDocument,
    DocumentDpePublic,
    DocumentIndisponibleError,
    DpeInconnuError,
    ProblemeDocumentDpe,
    lire_document_public,
)
from app.dpe.queries import dpe_par_point
from app.dpe.references import paris_today
from app.dpe.schemas import ResultatDpe
from app.scope.dependencies import (
    RouteHosting,
    get_instance_scope,
    hosted_datasets,
    hosting_at_point,
)
from app.scope.model import InstanceScope

router = APIRouter(prefix="/v1", tags=["Parcelle"])

# Valid DPE etiquette: single char A-G
_ETIQUETTE_PATTERN = re.compile(r"^[A-Ga-g]$")


@router.get(
    "/dpe",
    response_model=ReponsePaginee[ResultatDpe],
    summary="Diagnostics énergétiques autour d'un point (DPE)",
    description=(
        "Recherche de diagnostics autour d'un point : renvoie les diagnostics "
        "de performance énergétique situés dans le rayon demandé, ordonnés par "
        "distance croissante. Cette liste décrit le voisinage du point ; elle "
        "ne rattache aucun diagnostic au bien interrogé. Pour le diagnostic "
        "attribué à une adresse résolue, utiliser `/v1/resolve?datasets=dpe`. "
        "Filtrable par classe énergétique minimale (A-G). "
        "Pagination par curseur."
    ),
)
async def get_dpe(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("dpe"))],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
    db: Annotated[AsyncSession, Depends(get_db)],
    lat: Annotated[
        float,
        Query(ge=-90, le=90, description="Latitude WGS-84", example=48.8566),
    ],
    lon: Annotated[
        float,
        Query(ge=-180, le=180, description="Longitude WGS-84", example=2.3522),
    ],
    rayon_m: Annotated[
        float,
        Query(ge=50, le=5000, description="Rayon de recherche en metres", example=500),
    ] = 500.0,
    etiquette_dpe: Annotated[
        str | None,
        Query(
            description="Etiquette energetique : A (meilleur) a G (passoire thermique)",
            example="F",
        ),
    ] = None,
    annee_construction_min: Annotated[
        int | None,
        Query(description="Annee de construction minimum (incluse)", example=1970),
    ] = None,
    limite: Annotated[
        int,
        Query(ge=1, le=100, description="Nombre de resultats par page", example=20),
    ] = 20,
    curseur: Annotated[
        str | None,
        Query(description="Curseur de pagination (reponse precedente)"),
    ] = None,
) -> ReponsePaginee[ResultatDpe]:
    """Search DPE energy diagnostics NEAR a GPS point -- proximity only (D-16).

    This route answers "which diagnostics sit around this point", which is a
    legitimate public capability and never a proof of ownership: a row returned
    here describes a POSITION, not the property the caller asked about. The
    decision about a resolved address is served by ``/v1/resolve?datasets=dpe``,
    which reads the ADEME interoperability key instead of a radius.

    Supports spatial filtering (radius), energy label filtering (A-G),
    and construction year filtering. Results are paginated via cursor-based
    pagination and ordered by distance from query point.
    """
    # Validate etiquette format
    if etiquette_dpe is not None and not _ETIQUETTE_PATTERN.match(etiquette_dpe):
        raise HTTPException(
            status_code=422,
            detail="etiquette_dpe doit etre une lettre unique entre A et G",
        )

    offset = decode_pagination(curseur)

    # C52 (B2): the departement of the commune covering the point qualifies
    # the coverage before any read.
    if not hosting.none_hosted:
        hosting = await hosting_at_point(db, scope, hosting, lat, lon)
    if hosting.none_hosted:
        return ReponsePaginee(
            donnees=[],
            pagination=PaginationMeta(curseur_suivant=None, taille=0, limite=limite),
            meta=hosting.meta(get_meta_source("dpe")),
        )

    rows = await dpe_par_point(
        db,
        lat,
        lon,
        rayon_m=rayon_m,
        etiquette_dpe=etiquette_dpe,
        annee_min=annee_construction_min,
        limit=limite + 1,
        offset=offset,
    )

    resultats, pagination = build_page(rows, ResultatDpe, limite, offset)

    meta = get_meta_source("dpe")
    return ReponsePaginee(donnees=resultats, pagination=pagination, meta=meta)


# The documentary answers are never cached (same rule as the document routes
# of ``/v1/bien/dpe``); the problems carry the route template as instance, so
# that neither a malformed input nor a number is reflected (contract V3).
_NO_STORE = {"Cache-Control": "no-store"}
_INSTANCE_DOCUMENT = "/v1/dpe/{numero}"
_INSTANCE_COMPARAISON = "/v1/dpe/comparaison"
_PROBLEME_DOCUMENT_SCHEMA = ProblemeDocumentDpe.model_json_schema()


def _probleme_document_doc(description: str) -> dict:
    return {
        "description": description,
        "content": {"application/problem+json": {"schema": _PROBLEME_DOCUMENT_SCHEMA}},
    }


def _probleme_document(
    status: int,
    code: str,
    title: str,
    departements: list[str] | None = None,
    instance: str = _INSTANCE_DOCUMENT,
) -> JSONResponse:
    body = ProblemeDocumentDpe(
        title=title,
        status=status,
        code=code,
        instance=instance,
        couverture=None
        if departements is None
        else CouvertureDocument(departements=departements),
    ).model_dump(exclude_none=True)
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers=_NO_STORE,
    )


# 04-10 (CMP-02, T-04-10-01): 2 to 4 numbers of the ADEME form, duplicates
# dropped, checked before any document is read; the problem never quotes one.
# The raw bound leaves room for a space on each side of a comma, as ``ids``
# tolerates them (D-15 API IN-09); each part is checked after its strip.
MIN_DOCUMENTS = 2
MAX_DOCUMENTS = 4
_NUMEROS_MAX_LENGTH = MAX_DOCUMENTS * (13 + 3)


def numeros_compares(numeros: str | None) -> list[str] | None:
    """The requested numbers in column order, or None for a malformed request."""
    if not numeros or len(numeros) > _NUMEROS_MAX_LENGTH:
        return None
    parts = list(dict.fromkeys(part.strip() for part in numeros.split(",")))
    if not MIN_DOCUMENTS <= len(parts) <= MAX_DOCUMENTS:
        return None
    if any(NUMERO_DPE_RE.fullmatch(part) is None for part in parts):
        return None
    return parts


# Declared before ``/dpe/{numero}``: the static path is never read as a number.
@router.get(
    "/dpe/comparaison",
    response_model=ReponseApi[ComparaisonDocuments],
    responses={
        422: _probleme_document_doc(
            "Moins de 2 ou plus de 4 numeros, ou numero mal forme "
            "(`numeros_invalides`)."
        ),
        503: _probleme_document_doc(
            "Comparaison indisponible (publication non validee ou lecture en echec)."
        ),
    },
    summary="Comparaison de 2 a 4 documents DPE par leur numero ADEME",
    description=(
        "Rend, pour 2 a 4 documents DPE lus par leur numero ADEME (comme la "
        "page du document), une colonne par document et quatre groupes de "
        "lignes : etiquettes, logement decrit, cout et validite, adresse. "
        "Pour les colonnes 2 a 4, l'API sert l'ecart a la premiere colonne, "
        "le motif pour lequel il n'est pas calcule (dont methodes de calcul "
        "differentes, avant et apres juillet 2021), ou pour une valeur non "
        "numerique le verdict identique / different. Un numero inconnu est "
        "rendu dans numeros_ecartes. Aucun document n'est rattache a un "
        "logement."
    ),
)
async def comparer_documents_dpe(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("dpe", "ban"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    numeros: Annotated[
        str | None,
        Query(
            description=(
                "2 a 4 numeros ADEME separes par des virgules ; le premier "
                "document est la reference des ecarts. Un doublon est ecarte."
            ),
            examples=["2385E0123456A,2185E0000001A"],
        ),
    ] = None,
) -> ReponseApi[ComparaisonDocuments] | JSONResponse:
    """Compare documents; no log line carries a number (route template only)."""
    lus = numeros_compares(numeros)
    if lus is None:
        return _probleme_document(
            422,
            "numeros_invalides",
            "Numeros DPE invalides",
            instance=_INSTANCE_COMPARAISON,
        )
    try:
        comparaison = await comparer_documents(db, lus, hosting, paris_today())
    except DocumentIndisponibleError:
        return _probleme_document(
            503,
            "comparaison_dpe_indisponible",
            "Comparaison de documents DPE momentanement indisponible",
            instance=_INSTANCE_COMPARAISON,
        )
    return JSONResponse(
        content=ReponseApi(
            donnees=comparaison, meta=get_meta_sources(["dpe"])
        ).model_dump(mode="json"),
        headers=_NO_STORE,
    )


@router.get(
    "/dpe/{numero}",
    response_model=ReponseApi[DocumentDpePublic],
    responses={
        404: _probleme_document_doc(
            "Aucun document ne porte ce numero dans les departements servis "
            "(`dpe_inconnu`, couverture de l'instance jointe)."
        ),
        422: _probleme_document_doc("Numero mal forme (`numero_mal_forme`)."),
        503: _probleme_document_doc(
            "Document indisponible (publication non validee ou lecture en echec)."
        ),
    },
    summary="Document DPE par son numero ADEME",
    description=(
        "Ce que declare un document DPE, lu par son numero source ADEME tel "
        "quel : identite source (numero et jeux ADEME avec leur page de "
        "catalogue), methode, dates et reperes declares. Le document n'est "
        "rattache a aucun logement ; aucune donnee de logement, de vente ou "
        "de commune n'est servie. Un document retire par la source garde son "
        "numero et ses jeux, sans descriptif."
    ),
)
async def lire_document_dpe(
    hosting: Annotated[RouteHosting, Depends(hosted_datasets("dpe", "ban"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    numero: Annotated[
        str,
        Path(
            description="Numero ADEME du document (13 caracteres, ex. 2385E0123456A)",
            # Documented here, checked by the handler with the same pattern:
            # the generic validation problem would reflect the input path.
            json_schema_extra={"pattern": NUMERO_DPE_PATTERN},
        ),
    ],
) -> ReponseApi[DocumentDpePublic] | JSONResponse:
    """One document by its source number; no log line carries the number."""
    if NUMERO_DPE_RE.fullmatch(numero) is None:
        return _probleme_document(422, "numero_mal_forme", "Numero DPE mal forme")
    try:
        document = await lire_document_public(db, numero, hosting, paris_today())
    except DpeInconnuError as exc:
        return _probleme_document(404, "dpe_inconnu", "DPE inconnu", exc.departements)
    except DocumentIndisponibleError:
        return _probleme_document(
            503, "document_dpe_indisponible", "Document DPE momentanement indisponible"
        )
    return JSONResponse(
        content=ReponseApi(donnees=document, meta=get_meta_sources(["dpe"])).model_dump(
            mode="json"
        ),
        headers=_NO_STORE,
    )
