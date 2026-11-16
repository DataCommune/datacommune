"""Legacy source attribution helpers, derived from the canonical catalog.

``DATASET_SOURCES`` is no longer a parallel registry: it is projected from
:data:`app.common.source_catalog.DATASET_CATALOG` (single authority, D-01/D-03).
The public names and signatures (``DATASET_SOURCES``, ``get_meta_source``,
``get_meta_sources``) are preserved so existing routers keep working unchanged.
"""

from app.common.schemas import MetaReponse, SourceAttribution
from app.common.source_catalog import DATASET_CATALOG

# Projected legacy attribution: the four historical fields, one per catalog
# entry. Built via model_validate so no parallel SourceAttribution registry
# survives in this module.
DATASET_SOURCES: dict[str, SourceAttribution] = {
    key: SourceAttribution.model_validate(
        {
            "nom": entry.nom,
            "licence": entry.licence,
            "url": entry.url,
            "producteur": entry.producteur,
        }
    )
    for key, entry in DATASET_CATALOG.items()
}


def get_meta_source(key: str) -> dict:
    """Build meta dict with single-source attribution.

    Used by endpoints that serve a single dataset (e.g., /ban, /dvf).
    """
    return MetaReponse(source=DATASET_SOURCES[key]).model_dump()


def get_meta_sources(keys: list[str]) -> dict:
    """Build meta dict with multi-source attribution.

    Used by endpoints that combine multiple datasets
    (e.g., /transports, /resolve).
    """
    return MetaReponse(source=[DATASET_SOURCES[k] for k in keys]).model_dump()
