"""Deep audit of the `adresses` table (BAN -- Base Adresse Nationale).

Source: adresse.data.gouv.fr, partitioned by department (102 partitions).
Reference: etl_ban/pipeline.py, migrations/001_initial, 002_ban_columns.
"""

import psycopg

from app.data_quality.base import AuditResult, DatasetAuditor, Finding

# NOT NULL in schema OR always expected for a usable address.
REQUIRED_COLUMNS = [
    "code_departement",
    "code_postal",
    "code_commune",
    "position",
]

# Either numero or nom_voie must be present (lieu-dit case tolerated).
EXPECTED_COLUMNS = [
    "nom_voie",
    "ban_id",
]

EXPECTED_DEPT_COUNT = 101


class BanAuditor(DatasetAuditor):
    name = "ban"
    table = "adresses"

    def run(self, conn: psycopg.Connection) -> AuditResult:
        try:
            conn.execute("SET LOCAL statement_timeout = '15min'")
        except psycopg.Error:
            conn.rollback()

        total = self.scalar(conn, f"SELECT count(*) FROM {self.table}") or 0
        result = AuditResult(dataset=self.name, table=self.table, row_count=total)

        self._check_volume(conn, result, total)
        self._check_required_nulls(conn, result, total)
        self._check_expected_nulls(conn, result, total)
        self._check_numero_or_nom_voie(conn, result, total)

        self._check_code_postal_format(conn, result)
        self._check_code_commune_format(conn, result)

        self._check_geom_bbox(conn, result)
        self._check_dept_coverage(conn, result)

        self._check_duplicates(conn, result)
        self._check_code_commune_fk_demographie(conn, result)

        return result

    # -- Volume --------------------------------------------------------------

    def _check_volume(self, conn, result: AuditResult, total: int) -> None:
        result.stats["row_count"] = f"{total:,}".replace(",", " ")
        # BAN ~25 M addresses. Tolerate 15 M floor (major loss) / 35 M ceiling.
        if total < 15_000_000:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="volume",
                    title="Volume anormalement bas",
                    description=(
                        f"{total:,} lignes, attendu ~25 M. "
                        "Suspect ETL partiel (certains departements manquants)."
                    ).replace(",", " "),
                    count=total,
                    sample_query=(
                        f"SELECT code_departement, count(*) FROM {self.table} "
                        "GROUP BY code_departement ORDER BY count(*);"
                    ),
                )
            )
        elif total > 35_000_000:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="volume",
                    title="Volume anormalement haut",
                    description=f"{total:,} lignes. Suspect doublons.".replace(
                        ",", " "
                    ),
                    count=total,
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
                        title=f"Colonne requise `{col}` contient des NULL",
                        description=(f"{nulls:,} lignes NULL ({pct:.2f}%).").replace(
                            ",", " "
                        ),
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

    def _check_numero_or_nom_voie(self, conn, result: AuditResult, total: int) -> None:
        if total == 0:
            return
        both_null = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE numero IS NULL AND nom_voie IS NULL",
            )
            or 0
        )
        if both_null > 0:
            pct = (both_null / total) * 100
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="nulls",
                    title=(f"numero ET nom_voie tous deux NULL : {both_null} lignes"),
                    description=(
                        f"{pct:.2f}% des adresses sans numero ni nom_voie. "
                        "Adresse inutilisable."
                    ),
                    count=both_null,
                )
            )

    # -- Format --------------------------------------------------------------

    def _check_code_postal_format(self, conn, result: AuditResult) -> None:
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE code_postal IS NOT NULL AND length(code_postal) != 5",
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="format",
                    title=f"code_postal longueur != 5 : {bad} lignes",
                    description="Code postal doit faire 5 chiffres.",
                    count=bad,
                )
            )

    def _check_code_commune_format(self, conn, result: AuditResult) -> None:
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE code_commune IS NOT NULL AND length(code_commune) != 5",
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="format",
                    title=f"code_commune longueur != 5 : {bad} lignes",
                    description="Code INSEE doit faire 5 caracteres.",
                    count=bad,
                )
            )

    # -- Geo -----------------------------------------------------------------

    def _check_geom_bbox(self, conn, result: AuditResult) -> None:
        try:
            out = (
                self.scalar(
                    conn,
                    f"""
                SELECT count(*) FROM {self.table}
                WHERE position IS NOT NULL
                  AND (ST_X(position) < -62 OR ST_X(position) > 56
                    OR ST_Y(position) < -22 OR ST_Y(position) > 52)
                """,
                )
                or 0
            )
        except psycopg.Error:
            conn.rollback()
            return
        if out > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="geo",
                    title=f"Coordonnees hors bbox France : {out} lignes",
                    description="Points hors France metro + DOM.",
                    count=out,
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

    # -- Duplicates / integrity ---------------------------------------------

    def _check_duplicates(self, conn, result: AuditResult) -> None:
        # Exact dupe on (numero, nom_voie, code_postal, code_commune).
        # Full scan -- expensive but runs once per audit.
        dupes = (
            self.scalar(
                conn,
                f"""
            SELECT count(*) FROM (
              SELECT 1 FROM {self.table}
              WHERE numero IS NOT NULL AND nom_voie IS NOT NULL
                AND code_postal IS NOT NULL AND code_commune IS NOT NULL
              GROUP BY numero, nom_voie, code_postal, code_commune
              HAVING count(*) > 1
            ) sub
            """,
            )
            or 0
        )
        if dupes > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="duplicates",
                    title=(
                        f"{dupes} groupes de doublons exacts "
                        "(numero, nom_voie, code_postal, code_commune)"
                    ),
                    description=(
                        "Adresses repetees dans la BAN. "
                        "Peut refleter plusieurs sources fusionnees "
                        "(BAN = composite IGN/DGFiP/communes) sans dedup."
                    ),
                    count=dupes,
                    sample_query=(
                        f"SELECT numero, nom_voie, code_postal, code_commune, "
                        f"count(*) FROM {self.table} "
                        "WHERE numero IS NOT NULL "
                        "GROUP BY numero, nom_voie, code_postal, code_commune "
                        "HAVING count(*) > 1 LIMIT 10;"
                    ),
                )
            )

    def _check_code_commune_fk_demographie(self, conn, result: AuditResult) -> None:
        try:
            orphans = (
                self.scalar(
                    conn,
                    f"""
                SELECT count(*) FROM {self.table} a
                WHERE a.code_commune IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM demographie g WHERE g.code_insee = a.code_commune
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
                        f"code_commune sans match dans demographie : {orphans} lignes"
                    ),
                    description=(
                        "BAN peut porter des codes historiques de communes fusionnees."
                    ),
                    count=orphans,
                )
            )
