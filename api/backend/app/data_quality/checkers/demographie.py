"""Deep audit of the `demographie` table.

Source: INSEE Recensement 2022, merged from 3 CSVs (pop legales,
base logement, base evol-struct pop).

Reference: etl_demographie/pipeline.py, etl_demographie/config.py
"""

import psycopg

from app.data_quality.base import AuditResult, DatasetAuditor, Finding

# Columns that must be populated for each commune (from OUTPUT_COLUMNS).
REQUIRED_COLUMNS = [
    "code_insee",
    "nom_commune",
    "code_departement",
    "population",
    "annee",
]

# Columns that should normally be populated but may have legitimate gaps.
EXPECTED_COLUMNS = [
    "code_region",
    "population_hommes",
    "population_femmes",
    "nb_menages",
    "nb_logements",
    "nb_residences_principales",
    "nb_logements_vacants",
    "nb_residences_secondaires",
    "superficie",
    "densite",
]

PLM_CODES = ["75056", "13055", "69123"]  # Paris, Marseille, Lyon aggregates

# 101 departements INSEE : 01-95 minus 20 (Corse), plus 2A, 2B, DOM 971-976.
# Mayotte = 976. Saint-Pierre-et-Miquelon (975) usually absent from metro ETL.
EXPECTED_DEPT_COUNT = 101  # 95 metro - 1 (20) + 2 (2A, 2B) + 5 DOM


class DemographieAuditor(DatasetAuditor):
    name = "demographie"
    table = "demographie"

    def run(self, conn: psycopg.Connection) -> AuditResult:
        total = self.scalar(conn, f"SELECT count(*) FROM {self.table}") or 0
        result = AuditResult(dataset=self.name, table=self.table, row_count=total)

        # -- Volume ----------------------------------------------------------
        self._check_volume(conn, result, total)

        # -- NULL rates on required + expected columns ----------------------
        self._check_required_nulls(conn, result, total)
        self._check_expected_nulls(conn, result, total)

        # -- Format / duplicates --------------------------------------------
        self._check_code_insee_format(conn, result)
        self._check_duplicates(conn, result)

        # -- Geographic coverage --------------------------------------------
        self._check_dept_coverage(conn, result)
        self._check_plm_aggregates(conn, result)

        # -- Temporal --------------------------------------------------------
        self._check_year_consistency(conn, result)

        # -- Business rules --------------------------------------------------
        self._check_sex_split_coherence(conn, result)
        self._check_housing_split_coherence(conn, result)
        self._check_densite_coherence(conn, result)

        # -- Integrity (administrative geometry for superficie, D-07/D-09) --
        self._check_superficie_from_administrative_geometry(conn, result)

        return result

    # -- Volume --------------------------------------------------------------

    def _check_volume(self, conn, result: AuditResult, total: int) -> None:
        result.stats["row_count"] = f"{total:,}".replace(",", " ")
        # 34 945 communes France + 3 PLM aggregates = ~34 968.
        # Tolerate a -5% drop before erroring.
        if total < 33_000:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="volume",
                    title="Volume anormalement bas",
                    description=(
                        f"La table contient {total:,} lignes, attendu ~34 968 "
                        "(34 965 communes + 3 agregats PLM). "
                        "Suspect ETL partiel ou source INSEE amputee."
                    ).replace(",", " "),
                    count=total,
                    sample_query=f"SELECT count(*) FROM {self.table};",
                    fix_hint=(
                        "Rejouer `run_etl.py demographie` et verifier que les 3 ZIP "
                        "INSEE ont bien ete telecharges (pop legales, logement, "
                        "evol-struct-pop)."
                    ),
                )
            )
        elif total > 37_000:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="volume",
                    title="Volume anormalement haut",
                    description=(
                        f"{total:,} lignes, attendu ~34 968. "
                        "Suspect communes deleguees melangees ou doublons."
                    ).replace(",", " "),
                    count=total,
                )
            )

    # -- NULL rates ----------------------------------------------------------

    def _check_required_nulls(self, conn, result: AuditResult, total: int) -> None:
        if total == 0:
            return
        rates = self.null_rates(conn, self.table, REQUIRED_COLUMNS)
        for col, (nulls, _) in rates.items():
            if nulls > 0:
                result.findings.append(
                    Finding(
                        severity="ERROR",
                        category="nulls",
                        title=f"Colonne requise `{col}` contient des NULL",
                        description=(
                            f"{nulls:,} lignes sur {total:,} ont `{col}` NULL. "
                            "Cette colonne doit toujours etre peuplee."
                        ).replace(",", " "),
                        count=nulls,
                        sample_query=(
                            f"SELECT code_insee, nom_commune FROM {self.table} "
                            f"WHERE {col} IS NULL LIMIT 10;"
                        ),
                        fix_hint=(
                            f"Verifier dans l'ETL que `{col}` est bien rempli "
                            "pour tous les rows avant le COPY."
                        ),
                    )
                )

    def _check_expected_nulls(self, conn, result: AuditResult, total: int) -> None:
        if total == 0:
            return
        rates = self.null_rates(conn, self.table, EXPECTED_COLUMNS)
        for col, (nulls, _) in rates.items():
            pct = (nulls / total) * 100 if total else 0
            result.stats[f"null_pct_{col}"] = f"{pct:.1f}%"
            if pct >= 99:
                # This is the ghost column pattern from the 2026-04-14 audit.
                result.findings.append(
                    Finding(
                        severity="ERROR",
                        category="nulls",
                        title=f"Colonne fantome `{col}` (>=99% NULL)",
                        description=(
                            f"{nulls:,} / {total:,} NULL ({pct:.1f}%). "
                            "La colonne est exposee par l'API mais jamais peuplee "
                            "- probablement une source CSV manquante dans l'ETL."
                        ).replace(",", " "),
                        count=nulls,
                        sample_query=(
                            f"SELECT count(*) FROM {self.table} "
                            f"WHERE {col} IS NOT NULL;"
                        ),
                        fix_hint=(
                            f"Voir `etl_demographie/config.py` : la source qui "
                            f"fournit `{col}` doit etre dans DOWNLOAD_URL_* et "
                            "mappee dans pipeline.py::_merge_sources()."
                        ),
                    )
                )
            elif pct >= 20:
                result.findings.append(
                    Finding(
                        severity="WARN",
                        category="nulls",
                        title=(
                            f"Colonne `{col}` partiellement peuplee ({pct:.1f}% NULL)"
                        ),
                        description=(
                            f"{nulls:,} / {total:,} NULL. "
                            "Probablement normal (communes sans donnees INSEE) "
                            "mais verifier le taux attendu."
                        ).replace(",", " "),
                        count=nulls,
                    )
                )

    # -- Format --------------------------------------------------------------

    def _check_code_insee_format(self, conn, result: AuditResult) -> None:
        bad = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM {self.table} "
                "WHERE code_insee IS NULL OR length(code_insee) != 5",
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="format",
                    title="code_insee de longueur != 5",
                    description=(
                        f"{bad} lignes avec code_insee NULL ou longueur != 5. "
                        "Un code commune INSEE fait exactement 5 caracteres."
                    ),
                    count=bad,
                    sample_query=(
                        f"SELECT code_insee, nom_commune FROM {self.table} "
                        "WHERE code_insee IS NULL OR length(code_insee) != 5 "
                        "LIMIT 10;"
                    ),
                    fix_hint=(
                        "Filtrer les rows invalides dans transform() avant le COPY."
                    ),
                )
            )

    def _check_duplicates(self, conn, result: AuditResult) -> None:
        dupes = (
            self.scalar(
                conn,
                f"SELECT count(*) FROM ("
                f"SELECT code_insee, annee FROM {self.table} "
                "GROUP BY code_insee, annee HAVING count(*) > 1"
                ") sub",
            )
            or 0
        )
        if dupes > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="duplicates",
                    title=f"{dupes} doublons sur (code_insee, annee)",
                    description=(
                        "Plusieurs lignes pour le meme couple (commune, annee). "
                        "La contrainte d'unicite devrait empecher ca."
                    ),
                    count=dupes,
                    sample_query=(
                        f"SELECT code_insee, annee, count(*) FROM {self.table} "
                        "GROUP BY code_insee, annee HAVING count(*) > 1 LIMIT 10;"
                    ),
                    fix_hint=(
                        "Verifier la contrainte UNIQUE (code_insee, annee) dans "
                        "les migrations Alembic."
                    ),
                )
            )

    # -- Geographic ----------------------------------------------------------

    def _check_dept_coverage(self, conn, result: AuditResult) -> None:
        dept_count = (
            self.scalar(
                conn,
                f"SELECT count(DISTINCT code_departement) FROM {self.table} "
                "WHERE code_departement IS NOT NULL",
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
                        f"Seulement {dept_count} departements distincts, attendu "
                        f"~{EXPECTED_DEPT_COUNT} (metro + Corse 2A/2B + 5 DOM)."
                    ),
                    count=dept_count,
                    sample_query=(
                        f"SELECT DISTINCT code_departement FROM {self.table} "
                        "ORDER BY code_departement;"
                    ),
                    fix_hint=(
                        "Verifier que le CSV INSEE inclut les DOM et la Corse. "
                        "Voir transform() dans etl_demographie/pipeline.py."
                    ),
                )
            )

    def _check_plm_aggregates(self, conn, result: AuditResult) -> None:
        for code in PLM_CODES:
            row = self.rows(
                conn,
                f"SELECT population, nb_logements FROM {self.table} "
                "WHERE code_insee = %s",
                (code,),
            )
            if not row:
                result.findings.append(
                    Finding(
                        severity="ERROR",
                        category="geo",
                        title=f"Agregat PLM manquant : {code}",
                        description=(
                            f"La ligne pour le code commune {code} "
                            "(Paris/Lyon/Marseille globaux) est absente. "
                            "L'etape _insert_plm_aggregates() n'a pas tourne."
                        ),
                        sample_query=(
                            f"SELECT * FROM {self.table} WHERE code_insee = '{code}';"
                        ),
                        fix_hint=(
                            "Voir etl_demographie/pipeline.py::_insert_plm_aggregates. "
                            "Relancer l'ETL."
                        ),
                    )
                )

    # -- Temporal ------------------------------------------------------------

    def _check_year_consistency(self, conn, result: AuditResult) -> None:
        years = self.rows(
            conn,
            f"SELECT annee, count(*) FROM {self.table} GROUP BY annee ORDER BY annee",
        )
        result.stats["annees_distinctes"] = (
            ", ".join(f"{y}={c}" for y, c in years) if years else "aucune"
        )
        if len(years) > 1:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="temporal",
                    title="Plusieurs millesimes presents",
                    description=(
                        f"Annees distinctes : {years}. L'ETL charge un seul "
                        "millesime a la fois - des residus suggerent un swap "
                        "incomplet."
                    ),
                )
            )

    # -- Business rules ------------------------------------------------------

    def _check_sex_split_coherence(self, conn, result: AuditResult) -> None:
        mismatched = (
            self.scalar(
                conn,
                f"""
            SELECT count(*) FROM {self.table}
            WHERE population IS NOT NULL
              AND population_hommes IS NOT NULL
              AND population_femmes IS NOT NULL
              AND abs(population - (population_hommes + population_femmes))
                  > GREATEST(population * 0.05, 10)
            """,
            )
            or 0
        )
        if mismatched > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="business",
                    title=f"Incoherence H+F vs population : {mismatched} communes",
                    description=(
                        "Pour ces communes, |population - (H+F)| > max(5%, 10). "
                        "Confirme empiriquement 2026-04-15 : la plupart sont des "
                        "fusions de communes recentes (ex: Saint-Denis 93066 + "
                        "Pierrefitte). Les 3 fichiers INSEE n'ont pas exactement "
                        "le meme perimetre pour ces communes. Limitation source, "
                        "pas un bug data."
                    ),
                    count=mismatched,
                    sample_query=(
                        f"SELECT code_insee, nom_commune, population, "
                        "population_hommes, population_femmes, "
                        "(population - (population_hommes + population_femmes))"
                        " AS diff "
                        f"FROM {self.table} "
                        "WHERE population IS NOT NULL "
                        "AND population_hommes IS NOT NULL "
                        "AND population_femmes IS NOT NULL "
                        "AND abs(population - (population_hommes + population_femmes)) "
                        "> GREATEST(population * 0.05, 10) "
                        "ORDER BY diff DESC LIMIT 10;"
                    ),
                    fix_hint=(
                        "Les 3 sources INSEE utilisent le meme millesime 2022. "
                        "Si l'ecart persiste, possible que pop_legales (PMUN) "
                        "et base-evol-struct (POPH/POPF) divergent sur certaines "
                        "communes deleguees - documenter."
                    ),
                )
            )

    def _check_housing_split_coherence(self, conn, result: AuditResult) -> None:
        mismatched = (
            self.scalar(
                conn,
                f"""
            SELECT count(*) FROM {self.table}
            WHERE nb_logements IS NOT NULL
              AND nb_residences_principales IS NOT NULL
              AND nb_logements_vacants IS NOT NULL
              AND nb_residences_secondaires IS NOT NULL
              AND abs(nb_logements - (nb_residences_principales
                                    + nb_logements_vacants
                                    + nb_residences_secondaires))
                  > GREATEST(nb_logements * 0.05, 10)
            """,
            )
            or 0
        )
        if mismatched > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="business",
                    title=(
                        f"Incoherence decomposition logements : {mismatched} communes"
                    ),
                    description=(
                        "Pour ces communes, nb_logements != RP + vacants + secondaires "
                        "(ecart > 5%). INSEE respecte cette identite aux arrondis pres."
                    ),
                    count=mismatched,
                    sample_query=(
                        f"SELECT code_insee, nom_commune, nb_logements, "
                        "nb_residences_principales, nb_logements_vacants, "
                        "nb_residences_secondaires "
                        f"FROM {self.table} "
                        "WHERE nb_logements IS NOT NULL "
                        "AND abs(nb_logements - (nb_residences_principales "
                        "+ nb_logements_vacants + nb_residences_secondaires)) "
                        "> GREATEST(nb_logements * 0.05, 10) LIMIT 10;"
                    ),
                )
            )

    def _check_densite_coherence(self, conn, result: AuditResult) -> None:
        bad = (
            self.scalar(
                conn,
                f"""
            SELECT count(*) FROM {self.table}
            WHERE densite IS NOT NULL
              AND superficie IS NOT NULL
              AND population IS NOT NULL
              AND superficie > 0
              AND abs(densite - (population::numeric / superficie))
                  > GREATEST((population::numeric / superficie) * 0.1, 1)
            """,
            )
            or 0
        )
        if bad > 0:
            result.findings.append(
                Finding(
                    severity="WARN",
                    category="business",
                    title=(
                        "Densite incoherente avec population/superficie : "
                        f"{bad} communes"
                    ),
                    description=(
                        "densite ne correspond pas a population/superficie "
                        "(ecart > 10%, tolerance documentee ci-dessus). "
                        "La colonne est recalculee apres chaque ingestion "
                        "pertinente par "
                        "app.demographie.backfill.backfill_superficie_densite "
                        "(D-07/D-08) -- un ecart persistant indique un "
                        "backfill non rejoue apres la derniere ingestion."
                    ),
                    count=bad,
                    sample_query=(
                        f"SELECT code_insee, nom_commune, population, superficie, "
                        "densite, round(population::numeric/superficie, 1) AS expected "
                        f"FROM {self.table} "
                        "WHERE densite IS NOT NULL AND superficie > 0 "
                        "AND abs(densite - population::numeric/superficie) "
                        "> GREATEST(population::numeric/superficie * 0.1, 1) "
                        "LIMIT 10;"
                    ),
                )
            )

    # -- Integrity -----------------------------------------------------------

    def _check_superficie_from_administrative_geometry(
        self, conn, result: AuditResult
    ) -> None:
        """superficie is backfilled from geo_communes administrative
        geometry (D-07) at swap/import time by
        app.demographie.backfill.backfill_superficie_densite. A commune
        with a valid, non-empty matching geometry must never stay
        silently empty (D-09).

        PLM arrondissement rows (751xx/132xx/6938x) are excluded --
        documented tolerance: they have no distinct geo_communes entry by
        construction (see
        app.demographie.backfill.PLM_ARRONDISSEMENT_PREFIXES), only the
        three PLM global codes (75056/13055/69123) are expected to carry
        an administrative superficie. That gap is surfaced as a stat, not
        a Finding.
        """
        from app.demographie.backfill import PLM_ARRONDISSEMENT_PREFIXES

        arr_predicate = " OR ".join(
            f"d.code_insee LIKE '{prefix}%'" for prefix in PLM_ARRONDISSEMENT_PREFIXES
        )
        try:
            missing = (
                self.scalar(
                    conn,
                    f"""
                SELECT count(*) FROM {self.table} d
                JOIN geo_communes g ON g.code = d.code_insee
                WHERE d.superficie IS NULL
                  AND ST_IsValid(g.geometry) AND NOT ST_IsEmpty(g.geometry)
                  AND NOT ({arr_predicate})
                """,
                )
                or 0
            )
            arr_documented = (
                self.scalar(
                    conn,
                    f"SELECT count(*) FROM {self.table} d "
                    f"WHERE d.superficie IS NULL AND ({arr_predicate})",
                )
                or 0
            )
        except psycopg.Error:
            # geo_communes may not exist in this environment; skip check.
            conn.rollback()
            return

        result.stats["superficie_null_arrondissements_documente"] = arr_documented

        if missing > 0:
            result.findings.append(
                Finding(
                    severity="ERROR",
                    category="integrity",
                    title=(
                        "superficie NULL malgre geometrie administrative "
                        f"valide : {missing} communes"
                    ),
                    description=(
                        "Pour ces communes, geo_communes porte une "
                        "geometrie valide et non vide mais superficie n'a "
                        "pas ete calculee par "
                        "app.demographie.backfill.backfill_superficie_densite "
                        "-- une commune couverte ne doit jamais rester "
                        "silencieusement vide (D-09)."
                    ),
                    count=missing,
                    sample_query=(
                        f"SELECT d.code_insee, d.nom_commune FROM {self.table} d "
                        "JOIN geo_communes g ON g.code = d.code_insee "
                        "WHERE d.superficie IS NULL AND ST_IsValid(g.geometry) "
                        "AND NOT ST_IsEmpty(g.geometry) LIMIT 10;"
                    ),
                    fix_hint=(
                        "Rejouer scripts/preflight_demographie_geometry.py "
                        "puis app.demographie.backfill."
                        "backfill_superficie_densite (voir app/etl/hooks.py "
                        "ou scripts/import_geo_boundaries.py)."
                    ),
                )
            )
