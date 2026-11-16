"""BAN (Base Adresse Nationale) validator — drift-only.

BAN distributes data as 96 department-level files with no national
aggregate row count API. Validation relies on drift detection between
successive runs rather than source comparison.
"""

import logging

from app.validation.base import BaseValidator, register_validator

logger = logging.getLogger(__name__)


@register_validator("ban")
class BanValidator(BaseValidator):
    """Validates BAN adresses via drift detection (no source count API)."""

    source_comparison_applicable = False

    @property
    def dataset_name(self) -> str:
        return "ban"

    @property
    def production_table_name(self) -> str:
        return "adresses"

    @property
    def discrepancy_threshold(self) -> float:
        return 5.0

    def get_source_row_count(self) -> int | None:
        # No single API endpoint for BAN aggregate row count.
        # BAN uses 96 department files; no national count API exists.
        # Rely on drift detection between successive runs.
        logger.info("BAN: no source count API — drift-only validation")
        return None
