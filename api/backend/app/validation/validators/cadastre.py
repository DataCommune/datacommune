"""Cadastre validator — drift-only.

Cadastre data is distributed as GeoJSON files. The tabular-api.data.gouv.fr
only indexes CSV resources, so no row count API is available for Cadastre.
Validation relies on drift detection between successive runs.
"""

import logging

from app.validation.base import BaseValidator, register_validator

logger = logging.getLogger(__name__)


@register_validator("cadastre")
class CadastreValidator(BaseValidator):
    """Validates cadastre parcelles via drift detection (no source count API)."""

    source_comparison_applicable = False

    @property
    def dataset_name(self) -> str:
        return "cadastre"

    @property
    def production_table_name(self) -> str:
        return "parcelles"

    @property
    def discrepancy_threshold(self) -> float:
        return 5.0

    def get_source_row_count(self) -> int | None:
        # Cadastre uses GeoJSON; tabular API only indexes CSV resources.
        logger.info("Cadastre: no source count API — drift-only validation")
        return None
