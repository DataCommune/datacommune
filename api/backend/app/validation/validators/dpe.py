"""Validate each DPE source/department against its verified import manifest.

The pairs judged are those of the manifest's generation (its own scope, 04.1-07).
The versioned transition baseline replaces the incompatible single-source
population of 44/85 and keeps its tolerance there only; any other pair must
equal its manifest total exactly (national fidelity method, plan 04.1-03): no
threshold nor ratio is carried over from 44/85. No live HTTP count request is
performed by the scheduled validator.
"""

import logging
import os
from pathlib import Path

import psycopg

from app.etl_dpe.sources import DpeImportManifest
from app.validation.base import BaseValidator, ValidationResult, register_validator

logger = logging.getLogger(__name__)

# The old single-source global count is not comparable to this population.
# Counts are checked against the exact, versioned manifest, not this reference.
TRANSITION_BASELINE_VERSION = "58.2-two-sources-44-85-2026-09-05"
TRANSITION_BASELINE = {
    ("dpe03existant", "44"): 349361,
    ("dpe03existant", "85"): 128086,
    ("dpe02neuf", "44"): 41292,
    ("dpe02neuf", "85"): 22059,
}


@register_validator("dpe")
class DpeValidator(BaseValidator):
    """Check comparable populations without weakening the existing 5% threshold."""

    def __init__(self, manifest_path: Path | None = None) -> None:
        self.manifest_path = manifest_path

    @property
    def dataset_name(self) -> str:
        return "dpe"

    @property
    def production_table_name(self) -> str:
        return "diagnostics_dpe"

    @property
    def discrepancy_threshold(self) -> float:
        return 5.0

    def get_source_row_count(self) -> int | None:
        # Scheduled run uses the authenticated local manifest instead of HTTP.
        logger.info("DPE: source counts require the local import manifest")
        return None

    def load_manifest(self) -> DpeImportManifest:
        # The ETL download cache is deleted after import. Operators must retain
        # the manifest AND its private artifacts outside that disposable cache.
        path = self.manifest_path or os.environ.get("DPE_VALIDATION_MANIFEST")
        if not path:
            raise ValueError("DPE_VALIDATION_MANIFEST must select a durable manifest")
        return DpeImportManifest.load(Path(path))

    def compare_populations(self, manifest, observed: dict) -> list[str]:
        """Issues of the observed pairs against the generation's manifest.

        A pair the manifest counts and the base lacks is missing (never read
        as a zero count); a pair the manifest proves empty is a verified zero;
        an observed pair outside the generation is unexpected.
        """
        manifest.validate()
        expected = {entry.pair: entry for entry in manifest.files}
        issues = [
            f"Unexpected pair outside the generation: {pair}"
            for pair in sorted(set(observed) - set(expected))
        ]
        for pair, entry in sorted(expected.items()):
            if pair not in observed:
                if entry.rows > 0:
                    issues.append(
                        f"Missing pair: {pair} (manifest counts {entry.rows})"
                    )
                continue
            count, versions, generations = observed[pair]
            if generations != {manifest.import_id}:
                issues.append(f"Import generation mismatch: {pair}")
            if versions != {entry.version_start}:
                issues.append(f"Source version mismatch: {pair}")
            if pair in TRANSITION_BASELINE:
                delta = abs(count - entry.rows) / max(entry.rows, 1) * 100
                if delta > self.discrepancy_threshold or (
                    entry.rows > 0 and count == 0
                ):
                    issues.append(f"Population discrepancy: {pair} ({delta:.2f}%)")
            elif count != entry.rows:
                issues.append(
                    f"Population differs from manifest: {pair} "
                    f"({count} != {entry.rows})"
                )
        return issues

    @staticmethod
    def read_populations(conn) -> dict:
        rows = conn.execute("""
            SELECT o->>'source_slug', o->>'department',
                   count(DISTINCT numero_dpe), array_agg(DISTINCT o->>'source_version'),
                   array_agg(DISTINCT o->>'import_id')
            FROM diagnostics_dpe, jsonb_array_elements(source_observations) o
            WHERE NOT is_tombstone AND source_state = 'present'
              AND o->>'state' = 'present'
              AND o->>'department' = code_departement
            GROUP BY 1, 2
        """).fetchall()
        return {
            (s, d): (count, set(versions), set(generations))
            for s, d, count, versions, generations in rows
        }

    def run(self, dsn: str) -> ValidationResult:
        from app.etl.validation import persist_validation_run

        local_count = 0
        source_count = None
        try:
            manifest = self.load_manifest()
            with psycopg.connect(dsn) as conn:
                observed = self.read_populations(conn)
                local_count = conn.execute("""
                    SELECT count(*) FROM diagnostics_dpe
                    WHERE NOT is_tombstone AND source_state = 'present'
                """).fetchone()[0]
            issues = self.compare_populations(manifest, observed)
            source_count = sum(entry.rows for entry in manifest.files)
            pair_count = len(manifest.files)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            psycopg.Error,
        ):
            issues = [
                "DPE manifest or database unavailable/invalid; validation refused"
            ]
        notes = f"{TRANSITION_BASELINE_VERSION}; " + (
            "; ".join(issues)
            if issues
            else f"All {pair_count} comparable source populations pass"
        )
        passed = not issues
        persist_validation_run(
            dsn,
            self.dataset_name,
            row_count_before=None,
            row_count_after=local_count,
            null_rate_findings=[],
            passed=passed,
            blocked_swap=False,
            notes=notes,
        )
        return ValidationResult(
            self.dataset_name, local_count, source_count, None, passed, notes
        )
