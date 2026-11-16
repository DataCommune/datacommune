"""Deep audit of the `diagnostics_dpe` table.

Source: ADEME DPE existant API (data.ademe.fr).
Reference: etl_dpe/pipeline.py, etl_dpe/config.py, migrations/006_dpe_diagnostics.py.

Coverage and volume are judged on the pairs of the served generation, read
from its verified import manifest (``DPE_VALIDATION_MANIFEST``, the durable
copy the drift validator reads), never on a fixed departement list. Without
that manifest they are reported « non jugés », never passed (04.1-07).
"""

import os
from datetime import date
from pathlib import Path

import psycopg

from app.data_quality.base import AuditResult, DatasetAuditor, Finding
from app.etl_dpe.sources import DpeImportManifest

# Versioned transition reference of 44/85 (scoped DQ floor of the two-source
# population, 58.2): kept for these departements only, never extrapolated to
# another one. Elsewhere the generation's manifest counts.
TRANSITION_DEPARTEMENTS = ("44", "85")
TRANSITION_FLOOR = 350_000

# Columns that must be populated for every DPE diagnostic.
REQUIRED_COLUMNS = [
    "numero_dpe",
    "code_departement",
    "date_etablissement_dpe",
    "etiquette_dpe",
]

# Columns expected populated most of the time but with legitimate gaps.
EXPECTED_COLUMNS = [
    "etiquette_ges",
    "surface_habitable",
    "conso_5_usages_ep",
    "emission_ges_5_usages",
    "code_insee",
    "code_postal",
    "annee_construction",
    "type_batiment",
]

VALID_ETIQUETTES = ["A", "B", "C", "D", "E", "F", "G", "N"]

# DPE created by decret 2006-1147 (14 sep 2006). First diagnostics ~nov 2006.
DPE_MIN_DATE = date(2006, 1, 1)

# The generation's manifest is independent from the observed table: a missing
# pair must fail.
DOCUMENTS = """(SELECT * FROM diagnostics_dpe WHERE NOT is_tombstone
    AND source_state NOT IN
    ('absent_from_complete_scope', 'explicitly_deactivated')) AS dpe_documents"""

# Surface physical bounds. Energy/emissions are annual totals, not intensities.
SURFACE_MIN = 0
SURFACE_MAX = 10_000  # m2


class DpeAuditor(DatasetAuditor):
    name = "dpe"
    table = "diagnostics_dpe"

    def __init__(self, manifest: DpeImportManifest | None = None) -> None:
        self.manifest = manifest

    def generation_manifest(self) -> DpeImportManifest | None:
        """Manifest of the served generation, or None when unavailable."""
        if self.manifest is not None:
            return self.manifest
        path = os.environ.get("DPE_VALIDATION_MANIFEST")
        if not path:
            return None
        try:
            # Loading verifies every artifact: done once per auditor.
            self.manifest = DpeImportManifest.load(Path(path))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return self.manifest

    @staticmethod
    def _unjudged(result: AuditResult, check: str) -> None:
        result.findings.append(
            Finding(
                severity="ERROR",
                category="geo" if check == "couverture" else "volume",
                title=f"Contrôle DPE de {check} non jugé",
                description=(
                    "Manifeste de la génération indisponible ou invalide "
                    "(DPE_VALIDATION_MANIFEST) : aucune paire source x "
                    "département n'est présumée complète."
                ),
            )
        )

    def run(self, conn: psycopg.Connection) -> AuditResult:
        total = self.scalar(conn, f"SELECT count(*) FROM {self.table}") or 0
        result = AuditResult(dataset=self.name, table=self.table, row_count=total)

        documents = self.scalar(conn, f"SELECT count(*) FROM {DOCUMENTS}") or 0
        result.stats["documentary_count"] = documents
        result.stats["tombstone_count"] = (
            self.scalar(conn, f"SELECT count(*) FROM {self.table} WHERE is_tombstone")
            or 0
        )
        self._check_volume(conn, result, documents)
        self._check_required_nulls(conn, result, documents)
        self._check_expected_nulls(conn, result, documents)

        self._check_etiquette_format(conn, result, "etiquette_dpe")
        self._check_etiquette_format(conn, result, "etiquette_ges")

        self._check_numero_dpe_duplicates(conn, result)

        self._check_range_surface(conn, result)
        self._check_range_conso(conn, result)
        self._check_range_ges(conn, result)
        self._check_range_annee_construction(conn, result)
        self._check_date_range(conn, result)

        self._check_year_monotonicity(conn, result)
        self._check_dept_coverage(conn, result)
        self._check_geom_validity(conn, result)

        self._check_code_insee_fk_demographie(conn, result)

        return result

    # -- Volume --------------------------------------------------------------

    def _check_volume(self, conn, result: AuditResult, total: int) -> None:
        result.stats["row_count"] = f"{total:,}".replace(",", " ")
        manifest = self.generation_manifest()
        if manifest is None:
            self._unjudged(result, "volume")
            return
        documents = dict(
            self.rows(
                conn,
                f"SELECT code_departement, count(*) FROM {DOCUMENTS} "
                "GROUP BY code_departement",
            )
        )
        departements = sorted(manifest.departements)
        transition = [d for d in TRANSITION_DEPARTEMENTS if d in departements]
        if len(transition) == len(TRANSITION_DEPARTEMENTS):
            present = sum(documents.get(d, 0) for d in transition)
            if present < TRANSITION_FLOOR:
                self._low_volume(
                    result,
                    transition,
                    present,
                    f"plancher de transition {TRANSITION_FLOOR}",
                )
        else:
            transition = []
        for departement in departements:
            if departement in transition:
                continue
            # Each verified document of a source is one document of the
            # departement; a document in both sources counts once: the larger
            # source total is an exact lower bound, never a ratio.
            rows = {
                entry.source_slug: entry.rows
                for entry in manifest.files
                if entry.department == departement
            }
            floor = max(rows.values())
            present = documents.get(departement, 0)
            if present < floor:
                detail = ", ".join(
                    f"{slug} {count}" for slug, count in sorted(rows.items())
                )
                self._low_volume(
                    result,
                    [departement],
                    present,
                    f"le manifeste de la génération en compte au moins {floor} "
                    f"({detail})",
                )

    @staticmethod
    def _low_volume(
        result: AuditResult, departements: list[str], present: int, reference: str
    ) -> None:
        label = "département" if len(departements) == 1 else "départements"
        result.findings.append(
            Finding(
                severity="ERROR",
                category="volume",
                title=(
                    f"Volume DPE anormalement bas : {label} {', '.join(departements)}"
                ),
                description=f"{present} documents présents ; {reference}.",
                count=present,
            )
        )

    # -- NULL rates ----------------------------------------------------------

    def _check_required_nulls(self, conn, result: AuditResult, total: int) -> None:
        if total == 0:
            return
        rates = self.null_rates(conn, DOCUMENTS, REQUIRED_COLUMNS)
        for col, (nulls, _) in rates.items():
            if nulls > 0:
                pct = (nulls / total) * 100
                result.findings.append(
                    Finding(
                        severity="ERROR",
                        category="nulls",
                        title=f"Colonne requise `{col}` contient des NULL",
                        description=(
                            f"{nulls:,} lignes sur {total:,} ont `{col}` NULL "
                            f"({pct:.2f}%). Cette colonne doit toujours etre peuplee."
                        ).replace(",", " "),
                        count=nulls,
                        sample_query=(
                            f"SELECT numero_dpe, code_departement FROM {self.table} "
                            f"WHERE {col} IS NULL LIMIT 10;"
                        ),
                        fix_hint=(
                            f"Verifier dans etl_dpe/pipeline.py que `{col}` est "
                            "rempli avant le COPY. Si la source ADEME manque la "
                            "valeur, filtrer ces lignes."
                        ),
                    )
                )

    def _check_expected_nulls(self, conn, result: AuditResult, total: int) -> None:
        if total == 0:
            return
        rates = self.null_rates(conn, DOCUMENTS, EXPECTED_COLUMNS)
        for col, (nulls, _) in rates.items():
            pct = (nulls / total) * 100
            result.stats[f"null_pct_{col}"] = f"{pct:.1f}%"
            if pct >= 99:
                result.findings.append(
                    Finding(
                        severity="ERROR",
                        category="nulls",
                        title=f"Colonne fantome `{col}` (>=99% NULL)",
                        description=(
                            f"{nulls:,} / {total:,} NULL ({pct:.1f}%). "
                            "Colonne exposee par l'API mais jamais peuplee. "
                            "Suspect mapping ETL casse ou source ADEME manquante."
                        ).replace(",", " "),
                        count=nulls,
                        fix_hint=(
                            f"Verifier etl_dpe/config.py COLUMN_MAPPING pour `{col}`."
                        ),
                    )
                )
            elif pct >= 30:
                result.findings.append(
                    Finding(
                        severity="WARN",
                        category="nulls",
                        title=(
                            f"Colonne `{col}` partiellement peuplee ({pct:.1f}% NULL)"
                        ),
                        description=(
                            f"{nulls:,} / {total:,} NULL. "
                            "Taux eleve -- verifier le remplissage attendu."
                        ).replace(",", " "),
                        count=nulls,
                    )
                )

    # -- Format --------------------------------------------------------------

    def _check_etiquette_format(self, conn, result: AuditResult, col: str) -> None:
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                f"WHERE {col} IS NOT NULL AND {col} <> ALL(%s::text[])",
                (VALID_ETIQUETTES,),
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="format",
                    title=f"Valeurs invalides dans `{col}`",
                    description=(
                        f"{bad} lignes avec `{col}` hors du set attendu "
                        f"{VALID_ETIQUETTES}."
                    ),
                    count=bad,
                    sample_query=(
                        f"SELECT DISTINCT {col} FROM {self.table} "
                        f"WHERE {col} IS NOT NULL AND {col} NOT IN "
                        f"({', '.join(repr(e) for e in VALID_ETIQUETTES)});"
                    ),
                    fix_hint=(
                        f"Normaliser `{col}` dans etl_dpe/pipeline.py::transform "
                        "(upper + filter)."
                    ),
                )
            )

    def _check_numero_dpe_duplicates(self, conn, result: AuditResult) -> None:
        # Table has UNIQUE constraint on numero_dpe -- scan is cheap via index.
        dupes = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM ("
                f"SELECT numero_dpe FROM {self.table} "
                "GROUP BY numero_dpe HAVING count(*) > 1"
                ") sub",
            )
            or 0
        )
        if dupes > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="duplicates",
                    title=f"{dupes} doublons sur numero_dpe",
                    description=(
                        "La contrainte UNIQUE (numero_dpe) est cassee. "
                        "Migration drift ou insertion contournant la contrainte."
                    ),
                    count=dupes,
                    sample_query=(
                        f"SELECT numero_dpe, count(*) FROM {self.table} "
                        "GROUP BY numero_dpe HAVING count(*) > 1 LIMIT 10;"
                    ),
                )
            )

    # -- Range ---------------------------------------------------------------

    def _check_range_surface(self, conn, result: AuditResult) -> None:
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE surface_habitable IS NOT NULL "
                f"AND (surface_habitable <= {SURFACE_MIN} "
                f"OR surface_habitable > {SURFACE_MAX})",
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="business",
                    title=f"surface_habitable hors range : {bad} lignes",
                    description=(
                        f"Surface doit etre dans ({SURFACE_MIN}, {SURFACE_MAX}] m2. "
                        "Valeurs nulles/negatives ou > 10 000 m2 suspectes."
                    ),
                    count=bad,
                    sample_query=(
                        f"SELECT numero_dpe, surface_habitable FROM {self.table} "
                        "WHERE surface_habitable <= 0 OR surface_habitable > 10000 "
                        "ORDER BY surface_habitable DESC LIMIT 10;"
                    ),
                )
            )

    def _check_range_conso(self, conn, result: AuditResult) -> None:
        self._check_annual_total(conn, result, "conso_5_usages_ep", "kWhEP/an")

    def _check_range_ges(self, conn, result: AuditResult) -> None:
        self._check_annual_total(conn, result, "emission_ges_5_usages", "kgCO₂/an")

    def _check_annual_total(
        self, conn, result: AuditResult, col: str, unit: str
    ) -> None:
        # DATA-CONTRACT §2 retains annual totals with a qualified alias mapping.
        # No validated annual ceiling exists; per-area limits cannot be reused.
        predicate = (
            f"{col} IS NOT NULL AND ({col} < 0 "
            f"OR {col}::text IN ('NaN', 'Infinity', '-Infinity'))"
        )
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} WHERE {predicate}",
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="business",
                    title=f"`{col}` négatif ou non fini : {bad} lignes",
                    description=(
                        f"Le total annuel en {unit} doit être fini et positif "
                        "ou nul. Les valeurs NULL restent des absences."
                    ),
                    count=bad,
                    sample_query=(
                        f"SELECT numero_dpe, {col} FROM {self.table} "
                        f"WHERE {predicate} LIMIT 10;"
                    ),
                )
            )
        result.stats[f"{col}_annual_upper_bound"] = "not_evaluated"
        result.findings.append(
            Finding(
                severity="INFO",
                category="business",
                title=f"Plafond annuel non évalué pour `{col}`",
                description=(
                    f"Les valeurs sont conservées en {unit}, sans conversion "
                    "par surface. Aucun plafond annuel n'est validé. L'unité "
                    "retenue repose sur une inférence dimensionnelle corroborée ; "
                    "le renommage exact de l'alias source courant reste non "
                    "documenté officiellement. Les fortes valeurs positives "
                    "ne sont pas déclarées cohérentes par ce contrôle."
                ),
            )
        )

    def _check_range_annee_construction(self, conn, result: AuditResult) -> None:
        current_year = date.today().year
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE annee_construction IS NOT NULL "
                f"AND (annee_construction < 1700 "
                f"OR annee_construction > {current_year})",
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="business",
                    title=f"annee_construction hors range : {bad} lignes",
                    description=(
                        f"Annee attendue dans [1700, {current_year}]. "
                        "Valeurs aberrantes (an 0, futur)."
                    ),
                    count=bad,
                    sample_query=(
                        f"SELECT numero_dpe, annee_construction FROM {self.table} "
                        f"WHERE annee_construction < 1700 "
                        f"OR annee_construction > {current_year} "
                        "ORDER BY annee_construction LIMIT 10;"
                    ),
                )
            )

    def _check_date_range(self, conn, result: AuditResult) -> None:
        today = date.today()
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE date_etablissement_dpe IS NOT NULL "
                "AND (date_etablissement_dpe < %s "
                "OR date_etablissement_dpe > %s)",
                (DPE_MIN_DATE, today),
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="temporal",
                    title=f"date_etablissement_dpe hors range : {bad} lignes",
                    description=(
                        f"Date attendue entre {DPE_MIN_DATE} (creation DPE) "
                        f"et {today}. Valeurs en dehors = bug source ou parsing."
                    ),
                    count=bad,
                    sample_query=(
                        f"SELECT numero_dpe, date_etablissement_dpe FROM {self.table} "
                        f"WHERE date_etablissement_dpe < '{DPE_MIN_DATE}' "
                        f"OR date_etablissement_dpe > CURRENT_DATE LIMIT 10;"
                    ),
                )
            )

    # -- Temporal ------------------------------------------------------------

    def _check_year_monotonicity(self, conn, result: AuditResult) -> None:
        """Expect strictly more DPE per year than the previous year (post 2013).

        The reform of 2012 triggered a step-change in DPE production; we only
        require monotonicity after 2013 to avoid false positives.
        """
        current_year = date.today().year
        years = self.rows(
            conn,
            f"SELECT EXTRACT(YEAR FROM date_etablissement_dpe)::int AS y, "
            f"count(*) FROM {self.table} "
            "WHERE date_etablissement_dpe IS NOT NULL "
            "GROUP BY y ORDER BY y",
        )
        result.stats["dpe_par_annee"] = (
            ", ".join(f"{y}={c}" for y, c in years[-10:]) if years else "aucune"
        )
        # Check monotonicity excluding current year (partial).
        stable_years = [(y, c) for y, c in years if 2014 <= y < current_year]
        regressions = []
        for i in range(1, len(stable_years)):
            prev_y, prev_c = stable_years[i - 1]
            y, c = stable_years[i]
            if c < prev_c * 0.5:
                regressions.append((y, c, prev_y, prev_c))
        if regressions:
            detail = ", ".join(
                f"{y}={c} (< 50% de {prev_y}={prev_c})"
                for y, c, prev_y, prev_c in regressions
            )
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="temporal",
                    title="Regression annuelle du volume DPE",
                    description=(
                        f"Annees avec < 50% du volume precedent : {detail}. "
                        "Suspect ETL partiel pour cette annee ou parse date casse."
                    ),
                )
            )

    # -- Geographic ----------------------------------------------------------

    def _check_dept_coverage(self, conn, result: AuditResult) -> None:
        """Present pairs against the pairs of the served generation.

        A pair the manifest counts and the table lacks is « manquante » (never
        read as zero); a pair the manifest proves empty is a verified zero; a
        pair outside the generation, or present where the manifest counts
        none, is « inattendue ».
        """
        manifest = self.generation_manifest()
        if manifest is None:
            self._unjudged(result, "couverture")
            return
        populations = self.rows(
            conn,
            f"""
            SELECT observation->>'source_slug', observation->>'department',
                   count(DISTINCT numero_dpe)
            FROM {DOCUMENTS}, jsonb_array_elements(source_observations) observation
            WHERE observation->>'state' = 'present'
              AND observation->>'department' = code_departement
            GROUP BY 1, 2
        """,
        )
        present = {
            (source, department)
            for source, department, count in populations
            if count > 0
        }
        expected = {entry.pair: entry.rows for entry in manifest.files}
        missing = sorted(
            pair for pair, rows in expected.items() if rows > 0 and pair not in present
        )
        unexpected = sorted(pair for pair in present if expected.get(pair, 0) == 0)
        result.stats["source_department_pairs"] = len(present)
        result.stats["generation_pairs"] = len(expected)
        result.stats["verified_zero_pairs"] = sum(
            1 for rows in expected.values() if rows == 0
        )
        if missing:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="geo",
                    title="Paires DPE source x département manquantes",
                    description=(
                        f"Comptées par le manifeste de la génération, absentes "
                        f"de la table : {missing}."
                    ),
                    count=len(missing),
                )
            )
        if unexpected:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="geo",
                    title="Paires DPE source x département inattendues",
                    description=(
                        f"Présentes dans la table, sans document au manifeste de "
                        f"la génération : {unexpected}."
                    ),
                    count=len(unexpected),
                )
            )

    def _check_geom_validity(self, conn, result: AuditResult) -> None:
        # Missing geometry is documentary absence, not an invalid coordinate.
        # France metro + DOM envelope : lon [-62, 56], lat [-22, 52].
        try:
            out_of_bbox = (
                self.scalar(
                    conn,
                    f"""
                SELECT count(*) FROM {self.table}
                WHERE position IS NOT NULL
                  AND (NOT ST_IsValid(position) OR ST_IsEmpty(position)
                    OR ST_X(position)::text IN ('NaN', 'Infinity', '-Infinity')
                    OR ST_Y(position)::text IN ('NaN', 'Infinity', '-Infinity')
                    OR ST_X(position) < -62 OR ST_X(position) > 56
                    OR ST_Y(position) < -22 OR ST_Y(position) > 52)
                """,
                )
                or 0
            )
        except psycopg.Error:
            conn.rollback()
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="geo",
                    title="Contrôle géométrique DPE indisponible",
                    description="La requête a échoué ; aucune validité n'est présumée.",
                )
            )
            return
        if out_of_bbox > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="geo",
                    title=f"Coordonnees hors bbox France : {out_of_bbox} lignes",
                    description=(
                        "Points hors de l'envelope France metro + DOM "
                        "(lon [-62, 56], lat [-22, 52])."
                    ),
                    count=out_of_bbox,
                    sample_query=(
                        f"SELECT numero_dpe, ST_X(position), ST_Y(position) "
                        f"FROM {self.table} "
                        "WHERE ST_X(position) < -62 OR ST_X(position) > 56 "
                        "OR ST_Y(position) < -22 OR ST_Y(position) > 52 LIMIT 10;"
                    ),
                )
            )

    # -- Integrity -----------------------------------------------------------

    def _check_code_insee_fk_demographie(self, conn, result: AuditResult) -> None:
        """DPE.code_insee should resolve to a commune in demographie."""
        try:
            orphans = (
                self.scalar(
                    conn,
                    f"""
                SELECT count(*) FROM {self.table} d
                WHERE d.code_insee IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM demographie g WHERE g.code_insee = d.code_insee
                  )
                """,
                )
                or 0
            )
        except psycopg.Error:
            conn.rollback()
            return
        if orphans > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="integrity",
                    title=(
                        f"code_insee sans commune dans demographie : {orphans} lignes"
                    ),
                    description=(
                        "DPE.code_insee ne resout pas vers demographie. "
                        "Communes dissoutes/fusionnees ou code ADEME non normalise."
                    ),
                    count=orphans,
                    sample_query=(
                        f"SELECT DISTINCT d.code_insee FROM {self.table} d "
                        "WHERE NOT EXISTS (SELECT 1 FROM demographie g "
                        "WHERE g.code_insee = d.code_insee) LIMIT 10;"
                    ),
                )
            )
