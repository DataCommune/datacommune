"""Explicit coverage for bounded proxy reads, independent from dataset presence."""

from typing import Literal

from pydantic import BaseModel, Field

from app.common.exceptions import UpstreamUnavailableError


class ProxySource(BaseModel):
    statut: Literal["disponible", "vide", "partiel", "indisponible"]
    nombre_retournes: int | None = Field(
        default=None,
        description="Nombre de lignes effectivement rendues ; NULL si non consultable.",
    )
    total: int | None = Field(
        default=None,
        description=(
            "Total annoncé par la source ; NULL si inconnu, "
            "jamais la longueur tronquée."
        ),
    )
    complet: bool | None = Field(
        default=None,
        description=(
            "True seulement si complétude attestée ; "
            "False si tronqué, NULL si non établie."
        ),
    )
    limite: int | None = None
    pages_lues: int = 0


class ProxyRows(list[dict]):
    """Keep list callers compatible while retaining first-page metadata."""

    def __init__(self, rows: list[dict], coverage: ProxySource):
        super().__init__(rows)
        self.coverage = coverage


def bounded_rows(
    rows: list,
    *,
    limit: int,
    total: object = None,
    next_page: object = None,
    pagination_known: bool = False,
) -> ProxyRows:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise UpstreamUnavailableError("Proxy source", "invalid rows")
    known_total = total if type(total) is int and total >= 0 else None
    visible = rows[:limit]
    if known_total is not None and known_total < len(rows):
        raise UpstreamUnavailableError("Proxy source", "inconsistent total")
    if (
        next_page
        or len(rows) > limit
        or (known_total is not None and known_total > len(visible))
    ):
        complete = False
    elif known_total is not None:
        complete = known_total == len(visible)
    elif pagination_known:
        complete = True
    else:
        # No pagination evidence: a full page is not an exhaustive inventory.
        complete = True if not rows else None
    return ProxyRows(
        visible,
        ProxySource(
            statut=("vide" if not visible else "disponible") if complete else "partiel",
            nombre_retournes=len(visible),
            total=known_total,
            complet=complete,
            limite=limit,
            pages_lues=1,
        ),
    )


def unavailable() -> ProxySource:
    return ProxySource(statut="indisponible")


def proxy_sources(data: object) -> dict[str, ProxySource]:
    if isinstance(data, ProxyRows):
        return {"batiments": data.coverage}
    if isinstance(data, dict) and isinstance(data.get("sources"), dict):
        return {
            key: ProxySource.model_validate(value)
            for key, value in data["sources"].items()
        }
    return {}


def is_partial(sources: dict[str, ProxySource]) -> bool:
    return any(
        value.statut in {"partiel", "indisponible"} for value in sources.values()
    )
