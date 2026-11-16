"""Deep audit of the `mutations` table (DVF property transactions).

Source: data.gouv.fr DVF, partitioned by department (102 partitions).
Reference: etl_dvf/pipeline.py, migrations/005_dvf_mutations.py,
migrations/043_dvf_plus_bonus_columns.py.
"""

from datetime import date

import psycopg

from app.data_quality.base import AuditResult, DatasetAuditor, Finding

# NOT NULL in schema OR required by business logic.
REQUIRED_COLUMNS = [
    "id_mutation",
    "date_mutation",
    "code_departement",
    "code_commune",
    "valeur_fonciere",
    "nature_mutation",
]

# Expected populated most of the time; legitimate gaps allowed. ``position``:
# since phase 04.4 (D-01) every source line is loaded, a line without
# coordinates carries none; its NULL rate is a statistic, never an error.
EXPECTED_COLUMNS = [
    "type_local",
    "surface_reelle_bati",
    "nombre_pieces_principales",
    "code_postal",
    "adresse_nom_voie",
    "id_parcelle",
    "position",
]

# nature_mutation values in DVF open data.
VALID_NATURES = [
    "Vente",
    "Echange",
    "Adjudication",
    "Expropriation",
    "Vente en l'etat futur d'acheve",
    "Vente en l'etat futur d'achevement",
    "Vente terrain a batir",
]

DVF_MIN_DATE = date(2014, 1, 1)  # DVF open data starts 2014.
EXPECTED_DEPT_COUNT = 101  # 95 metro - 1 (20) + 2 (2A, 2B) + 5 DOM

# Value / surface bounds (sanity).
VALEUR_MIN_VENTE = 0  # Vente must be > 0; Echange can be 0.
VALEUR_MAX = 100_000_000  # 100 M euros outlier cap.
SURFACE_MIN = 0
SURFACE_MAX = 50_000
PRIX_M2_MIN = 100  # Below -> probable data error (terrain non bati counted as bati).
PRIX_M2_MAX = 50_000  # Above -> probable outlier.


class DvfAuditor(DatasetAuditor):
    name = "dvf"
    table = "mutations"

    def run(self, conn: psycopg.Connection) -> AuditResult:
        # Long scans on 15 M partitioned rows -- raise statement timeout.
        try:
            conn.execute("SET LOCAL statement_timeout = '10min'")
        except psycopg.Error:
            conn.rollback()

        total = self.scalar(conn, f"SELECT count(*) FROM {self.table}") or 0
        result = AuditResult(dataset=self.name, table=self.table, row_count=total)

        self._check_volume(conn, result, total)
        self._check_required_nulls(conn, result, total)
        self._check_expected_nulls(conn, result, total)

        self._check_nature_mutation_format(conn, result)
        self._check_id_mutation_shape(conn, result)
        self._check_code_postal_format(conn, result)

        self._check_range_valeur_fonciere(conn, result)
        self._check_range_surface_bati(conn, result)
        self._check_date_range(conn, result)

        self._check_prix_m2_coherence(conn, result)

        self._check_dept_coverage(conn, result)
        self._check_geom_bbox(conn, result)

        self._check_code_commune_fk_demographie(conn, result)

        return result

    # -- Volume --------------------------------------------------------------

    def _check_volume(self, conn, result: AuditResult, total: int) -> None:
        result.stats["row_count"] = f"{total:,}".replace(",", " ")
        if total < 8_000_000:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="volume",
                    title="Volume anormalement bas",
                    description=(
                        f"{total:,} lignes, attendu ~15 M "
                        "(DVF depuis 2014). ETL partiel suspecte."
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
                        title=f"Colonne requise `{col}` contient des NULL",
                        description=(
                            f"{nulls:,} lignes NULL ({pct:.2f}%). "
                            "Colonne NOT NULL ou obligatoire metier."
                        ).replace(",", " "),
                        count=nulls,
                        sample_query=(
                            f"SELECT id_mutation, date_mutation FROM {self.table} "
                            f"WHERE {col} IS NULL LIMIT 10;"
                        ),
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
                        description=(
                            f"{nulls:,} / {total:,} NULL ({pct:.1f}%). "
                            "Suspect mapping ETL casse."
                        ).replace(",", " "),
                        count=nulls,
                    )
                )
            elif pct >= 50:
                result.findings.append(
                    Finding(
                        severity="WARN",
                        category="nulls",
                        title=(f"Colonne `{col}` >{int(pct)}% NULL"),
                        description=(
                            f"{nulls:,} / {total:,} NULL. "
                            "Normal pour terrains nus sur type_local/surface_bati "
                            "mais verifier le taux attendu."
                        ).replace(",", " "),
                        count=nulls,
                    )
                )

    # -- Format --------------------------------------------------------------

    def _check_nature_mutation_format(self, conn, result: AuditResult) -> None:
        distinct = self.rows(
            conn,
            f"SELECT DISTINCT nature_mutation FROM {self.table} "
            "WHERE nature_mutation IS NOT NULL",
        )
        values = [r[0] for r in distinct]
        result.stats["nature_mutation_values"] = ", ".join(sorted(values))
        unknown = [
            v
            for v in values
            if not any(
                v.lower().startswith(valid.lower()[:6]) for valid in VALID_NATURES
            )
        ]
        if unknown:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="format",
                    title="Valeurs inattendues pour nature_mutation",
                    description=(
                        f"Valeurs inconnues : {unknown}. "
                        f"Attendu sous-ensemble de {VALID_NATURES}."
                    ),
                    count=len(unknown),
                )
            )

    def _check_id_mutation_shape(self, conn, result: AuditResult) -> None:
        # DVF id_mutation format: "YYYY-NNNNNN" (annee + 6 digits). Check length.
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE id_mutation IS NOT NULL "
                "AND (length(id_mutation) < 6 OR length(id_mutation) > 30)",
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="format",
                    title=f"id_mutation de taille suspecte : {bad} lignes",
                    description=(
                        "id_mutation en dehors de [6, 30] caracteres. "
                        "Format DVF attendu ~ '2023-123456'."
                    ),
                    count=bad,
                )
            )

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
                    severity="WARN",
                    category="format",
                    title=f"code_postal longueur != 5 : {bad} lignes",
                    description="Code postal doit faire 5 chiffres.",
                    count=bad,
                )
            )

    # -- Range ---------------------------------------------------------------

    def _check_range_valeur_fonciere(self, conn, result: AuditResult) -> None:
        # Negatives or >100M are always suspicious.
        bad_neg = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE valeur_fonciere IS NOT NULL AND valeur_fonciere < 0",
            )
            or 0
        )
        if bad_neg > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="business",
                    title=f"valeur_fonciere negative : {bad_neg} lignes",
                    description="Une valeur fonciere ne peut etre < 0.",
                    count=bad_neg,
                )
            )
        bad_high = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                f"WHERE valeur_fonciere > {VALEUR_MAX}",
            )
            or 0
        )
        if bad_high > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="business",
                    title=f"valeur_fonciere > 100 M euros : {bad_high} lignes",
                    description=(
                        f"{bad_high} mutations > {VALEUR_MAX:,} euros. "
                        "Outliers legitimes possibles (gros fonciers), verifier."
                    ).replace(",", " "),
                    count=bad_high,
                    sample_query=(
                        "SELECT id_mutation, date_mutation, valeur_fonciere, "
                        f"nature_mutation FROM {self.table} "
                        f"WHERE valeur_fonciere > {VALEUR_MAX} "
                        "ORDER BY valeur_fonciere DESC LIMIT 10;"
                    ),
                )
            )

    def _check_range_surface_bati(self, conn, result: AuditResult) -> None:
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE surface_reelle_bati IS NOT NULL "
                f"AND (surface_reelle_bati <= {SURFACE_MIN} "
                f"OR surface_reelle_bati > {SURFACE_MAX})",
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="business",
                    title=f"surface_reelle_bati hors range : {bad} lignes",
                    description=(
                        f"Surface bati attendue dans ({SURFACE_MIN}, {SURFACE_MAX}] m2."
                    ),
                    count=bad,
                )
            )

    def _check_date_range(self, conn, result: AuditResult) -> None:
        today = date.today()
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE date_mutation < %s OR date_mutation > %s",
                (DVF_MIN_DATE, today),
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="temporal",
                    title=f"date_mutation hors range : {bad} lignes",
                    description=(
                        f"Attendu entre {DVF_MIN_DATE} (DVF open) et aujourd'hui."
                    ),
                    count=bad,
                )
            )

    def _check_prix_m2_coherence(self, conn, result: AuditResult) -> None:
        """Only on Vente with positive surface -- the reference set."""
        bad = (
            self.scalar(
                conn,
                f"""
            SELECT count(*) FROM {self.table}
            WHERE nature_mutation = 'Vente'
              AND valeur_fonciere IS NOT NULL
              AND surface_reelle_bati IS NOT NULL
              AND surface_reelle_bati > 0
              AND valeur_fonciere > 0
              AND (valeur_fonciere / surface_reelle_bati < {PRIX_M2_MIN}
                OR valeur_fonciere / surface_reelle_bati > {PRIX_M2_MAX})
            """,
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="business",
                    title=f"Prix au m2 aberrant sur Vente : {bad} lignes",
                    description=(
                        f"Prix/m2 hors [{PRIX_M2_MIN}, {PRIX_M2_MAX}] euros. "
                        "Probablement terrain nu compte comme bati ou saisie erronee."
                    ),
                    count=bad,
                    sample_query=(
                        "SELECT id_mutation, valeur_fonciere, surface_reelle_bati, "
                        "round(valeur_fonciere/surface_reelle_bati, 0) AS prix_m2 "
                        f"FROM {self.table} "
                        "WHERE nature_mutation = 'Vente' "
                        "AND surface_reelle_bati > 0 AND valeur_fonciere > 0 "
                        f"AND (valeur_fonciere/surface_reelle_bati < {PRIX_M2_MIN} "
                        f"OR valeur_fonciere/surface_reelle_bati > {PRIX_M2_MAX}) "
                        "ORDER BY prix_m2 DESC LIMIT 10;"
                    ),
                )
            )

    # -- Geographic ----------------------------------------------------------

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
                        "Couverture departementale incomplete "
                        f"({dept_count}/{EXPECTED_DEPT_COUNT})"
                    ),
                    description=(
                        "Note: Mayotte (976) souvent absente en DVF, -1 tolere."
                    ),
                    count=dept_count,
                )
            )

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

    # -- Integrity -----------------------------------------------------------

    def _check_code_commune_fk_demographie(self, conn, result: AuditResult) -> None:
        try:
            orphans = (
                self.scalar(
                    conn,
                    f"""
                SELECT count(*) FROM {self.table} m
                WHERE m.code_commune IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM demographie g WHERE g.code_insee = m.code_commune
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
                        f"code_commune sans commune dans demographie : {orphans} lignes"
                    ),
                    description=(
                        "Communes fusionnees / dissoutes probablement -- "
                        "DVF garde les codes historiques."
                    ),
                    count=orphans,
                )
            )
