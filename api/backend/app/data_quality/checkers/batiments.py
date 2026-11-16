"""Deep audit of the `batiments` table (BD TOPO buildings).

Source: IGN BD TOPO batiment layer, partitioned by department (102 partitions).
Reference: etl_bdtopo/pipeline.py, migrations/008_batiments_bdtopo.py.

Note: table has `code_departement` but NOT `code_commune`, so the commune-level
FK integrity check is skipped. Only departement coverage is verified.
"""

import psycopg

from app.data_quality.base import AuditResult, DatasetAuditor, Finding

REQUIRED_COLUMNS = [
    "cleabs",
    "code_departement",
    "geometrie",
]

EXPECTED_COLUMNS = [
    "nature",
    "usage_1",
    "hauteur",
    "nombre_d_etages",
    "altitude_minimale_sol",
    "altitude_maximale_toit",
]

# BD TOPO usage_1 enum (IGN reference).
VALID_USAGE = [
    "Agricole",
    "Annexe",
    "Commercial et services",
    "Indifferencie",
    "Industriel",
    "Religieux",
    "Residentiel",
    "Sportif",
    "Autre",
]

EXPECTED_DEPT_COUNT = 101

HAUTEUR_MIN = 0
HAUTEUR_MAX = 400  # metres (Burj Khalifa 828 -- no French building > 400).
ETAGES_MAX = 100


class BatimentsAuditor(DatasetAuditor):
    name = "batiments"
    table = "batiments"

    def run(self, conn: psycopg.Connection) -> AuditResult:
        try:
            conn.execute("SET LOCAL statement_timeout = '20min'")
        except psycopg.Error:
            conn.rollback()

        total = self.scalar(conn, f"SELECT count(*) FROM {self.table}") or 0
        result = AuditResult(dataset=self.name, table=self.table, row_count=total)

        self._check_volume(conn, result, total)
        self._check_required_nulls(conn, result, total)
        self._check_expected_nulls(conn, result, total)

        self._check_cleabs_duplicates(conn, result)
        self._check_usage_format(conn, result)

        self._check_range_hauteur(conn, result)
        self._check_range_etages(conn, result)

        self._check_geom_validity(conn, result)
        self._check_geom_area(conn, result)
        self._check_dept_coverage(conn, result)

        return result

    # -- Volume --------------------------------------------------------------

    def _check_volume(self, conn, result: AuditResult, total: int) -> None:
        result.stats["row_count"] = f"{total:,}".replace(",", " ")
        # BD TOPO batiment ~40 M rows nationwide. Old estimate 500 k was pilot.
        # Accept wide bounds until production volume is established.
        if total < 100_000:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="volume",
                    title="Volume anormalement bas",
                    description=(
                        f"{total:,} lignes. BD TOPO couvre la France entiere, "
                        "attendu au moins plusieurs millions de batiments."
                    ).replace(",", " "),
                    count=total,
                    sample_query=(
                        f"SELECT code_departement, count(*) FROM {self.table} "
                        "GROUP BY code_departement ORDER BY count(*);"
                    ),
                )
            )

    # -- NULL ----------------------------------------------------------------

    def _check_required_nulls(self, conn, result: AuditResult, total: int) -> None:
        if total == 0:
            return
        rates = self.null_rates(conn, self.table, REQUIRED_COLUMNS)
        for col, (nulls, _) in rates.items():
            if nulls > 0:
                pct = (nulls / total) * 100
                result.findings.append(
                    Finding(
                        severity="ERROR",
                        category="nulls",
                        title=f"Colonne requise `{col}` NULL",
                        description=f"{nulls:,} NULL ({pct:.2f}%)".replace(",", " "),
                        count=nulls,
                    )
                )

    def _check_expected_nulls(self, conn, result: AuditResult, total: int) -> None:
        if total == 0:
            return
        rates = self.null_rates(conn, self.table, EXPECTED_COLUMNS)
        for col, (nulls, _) in rates.items():
            pct = (nulls / total) * 100
            result.stats[f"null_pct_{col}"] = f"{pct:.1f}%"
            if pct >= 99:
                result.findings.append(
                    Finding(
                        severity="ERROR",
                        category="nulls",
                        title=f"Colonne fantome `{col}` (>=99% NULL)",
                        description=f"{nulls:,} / {total:,} NULL ({pct:.1f}%)".replace(
                            ",", " "
                        ),
                        count=nulls,
                    )
                )
            elif pct >= 60:
                result.findings.append(
                    Finding(
                        severity="WARN",
                        category="nulls",
                        title=f"Colonne `{col}` majoritairement NULL ({pct:.1f}%)",
                        description=(
                            "BD TOPO ne peuple pas toujours hauteur/etages. "
                            "Verifier si le taux est stable dans le temps."
                        ),
                        count=nulls,
                    )
                )

    # -- Format --------------------------------------------------------------

    def _check_cleabs_duplicates(self, conn, result: AuditResult) -> None:
        dupes = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM ("
                f"SELECT cleabs FROM {self.table} "
                "GROUP BY cleabs HAVING count(*) > 1"
                ") sub",
            )
            or 0
        )
        if dupes > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="duplicates",
                    title=f"{dupes} doublons sur cleabs",
                    description=(
                        "cleabs est l'identifiant unique IGN. "
                        "Doublons = import repete ou swap partiel."
                    ),
                    count=dupes,
                )
            )

    def _check_usage_format(self, conn, result: AuditResult) -> None:
        distinct = self.rows(
            conn,
            f"SELECT DISTINCT usage_1 FROM {self.table} WHERE usage_1 IS NOT NULL",
        )
        values = [r[0] for r in distinct]
        result.stats["usage_1_values"] = ", ".join(sorted(values))
        unknown = [
            v
            for v in values
            if not any(v.lower().startswith(valid.lower()[:6]) for valid in VALID_USAGE)
        ]
        if unknown:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="format",
                    title="Valeurs inattendues pour usage_1",
                    description=(
                        f"Inconnues : {unknown}. "
                        f"Attendu sous-ensemble de {VALID_USAGE}."
                    ),
                    count=len(unknown),
                )
            )

    # -- Range ---------------------------------------------------------------

    def _check_range_hauteur(self, conn, result: AuditResult) -> None:
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE hauteur IS NOT NULL "
                f"AND (hauteur < {HAUTEUR_MIN} OR hauteur > {HAUTEUR_MAX})",
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="business",
                    title=f"hauteur hors range : {bad} lignes",
                    description=(
                        f"Hauteur attendue dans [{HAUTEUR_MIN}, {HAUTEUR_MAX}] m."
                    ),
                    count=bad,
                    sample_query=(
                        "SELECT cleabs, hauteur FROM batiments "
                        "WHERE hauteur > 400 ORDER BY hauteur DESC LIMIT 10;"
                    ),
                )
            )

    def _check_range_etages(self, conn, result: AuditResult) -> None:
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE nombre_d_etages IS NOT NULL "
                f"AND (nombre_d_etages < 0 OR nombre_d_etages > {ETAGES_MAX})",
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="business",
                    title=f"nombre_d_etages hors range : {bad} lignes",
                    description=f"Attendu dans [0, {ETAGES_MAX}].",
                    count=bad,
                )
            )

    # -- PostGIS -------------------------------------------------------------

    def _check_geom_validity(self, conn, result: AuditResult) -> None:
        try:
            invalid = (
                self.scalar(
                    conn,
                    f"SELECT count(*) FROM {self.table} "
                    "WHERE geometrie IS NOT NULL "
                    "AND NOT ST_IsValid(geometrie)",
                )
                or 0
            )
        except psycopg.Error:
            conn.rollback()
            return
        if invalid > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="geo",
                    title=f"Geometries invalides : {invalid} lignes",
                    description=(
                        "ST_IsValid renvoie FALSE (self-intersect, ring bad, etc.)."
                    ),
                    count=invalid,
                    sample_query=(
                        f"SELECT cleabs, ST_IsValidReason(geometrie) FROM {self.table} "
                        "WHERE NOT ST_IsValid(geometrie) LIMIT 10;"
                    ),
                    fix_hint=(
                        "Appliquer ST_MakeValid(geometrie) dans l'ETL avant COPY."
                    ),
                )
            )

    def _check_geom_area(self, conn, result: AuditResult) -> None:
        try:
            zero_area = (
                self.scalar(
                    conn,
                    f"SELECT count(*) FROM {self.table} "
                    "WHERE geometrie IS NOT NULL "
                    "AND ST_Area(geometrie::geography) <= 0",
                )
                or 0
            )
        except psycopg.Error:
            conn.rollback()
            return
        if zero_area > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="geo",
                    title=f"Surface nulle ou negative : {zero_area} lignes",
                    description="ST_Area <= 0. Geometrie degeneree.",
                    count=zero_area,
                )
            )

    def _check_dept_coverage(self, conn, result: AuditResult) -> None:
        dept_count = (
            self.scalar(
                conn,
                f"SELECT count(DISTINCT code_departement) FROM {self.table}",
            )
            or 0
        )
        result.stats["departements_presents"] = dept_count
        if dept_count < EXPECTED_DEPT_COUNT - 2:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="geo",
                    title=(
                        f"Couverture departementale incomplete "
                        f"({dept_count}/{EXPECTED_DEPT_COUNT})"
                    ),
                    count=dept_count,
                )
            )
