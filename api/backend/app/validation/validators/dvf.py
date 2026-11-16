"""DVF (Demande de Valeurs Foncières) validator.

Since phase 04.4 (D-01) the ETL loads every line of the geo-DVF files of the
hosted departements, located or not, and a run refuses to publish unless it
staged exactly the hosted departements (``DvfPipeline``). The served
``mutations`` must therefore hold exactly the lines the published run read:
``etl_runs.total_rows_loaded`` of the latest successful run.
No tolerance (IN-06 of the 04.4 review): the former 15 % threshold against
the tabular API of data.gouv.fr let a loss of 14 % of the lines pass, and
that national resource does not count the population served (hosted
departements, every year of ``YEARS``). Truncated producer files are refused
at import by the per-departement floors (``app.scope.floors``).
"""

import logging

import psycopg

from app.validation.base import BaseValidator, ValidationResult, register_validator

logger = logging.getLogger(__name__)

# Lines read by the latest successful DVF run. The runner journals
# ``success`` only once the publication is done (``PipelineRunner._publish``);
# a dry run is not journaled, a run stopped earlier is ``failed``. The stored
# ``stages`` do not carry the swap step: never filter on them.
PUBLISHED_RUN_LINES_SQL = """
    SELECT total_rows_loaded
    FROM etl_runs
    WHERE dataset_name = 'dvf' AND status = 'success'
    ORDER BY completed_at DESC NULLS LAST, id DESC
    LIMIT 1
"""


@register_validator("dvf")
class DvfValidator(BaseValidator):
    """Validates DVF mutations against the lines of the published import run."""

    def __init__(self) -> None:
        self._dsn: str | None = None

    @property
    def dataset_name(self) -> str:
        return "dvf"

    @property
    def production_table_name(self) -> str:
        # The ETL pipeline stores DVF data in "mutations", not "dvf".
        return "mutations"

    @property
    def discrepancy_threshold(self) -> float:
        return 0.0

    def run(self, dsn: str) -> ValidationResult:
        self._dsn = dsn
        return super().run(dsn)

    def get_source_row_count(self) -> int | None:
        """Lines read by the published run, None when no such run is known."""
        if self._dsn is None:
            return None
        try:
            with psycopg.connect(self._dsn, connect_timeout=5) as conn:
                conn.set_read_only(True)
                row = conn.execute(PUBLISHED_RUN_LINES_SQL).fetchone()
        except psycopg.Error:
            logger.warning("DVF published run unavailable", exc_info=True)
            return None
        total = None if row is None else row[0]
        logger.info("DVF lines of the published run: %s", total)
        return total
