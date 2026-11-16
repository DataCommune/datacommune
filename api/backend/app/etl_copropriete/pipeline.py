"""RNIC copropriete concrete ETL pipeline.

Downloads the RNIC CSV from data.gouv.fr, transforms via pandas,
bulk-loads via COPY, and swaps atomically.

Data source: ANAH / RNIC, Licence Ouverte 2.0.
"""

import asyncio
import datetime
import logging
import re
import time
from pathlib import Path

import httpx
import pandas as pd
import psycopg
from pandas._libs.parsers import STR_NA_VALUES

from app.etl.base import BasePipeline
from app.etl.download import get_download_dir, streaming_download
from app.etl_copropriete.config import (
    COL_MAP,
    CSV_SEPARATOR,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_URL,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
    RANGS_PARCELLES_SECONDAIRES,
    SOURCE_COLUMNS,
)

logger = logging.getLogger(__name__)


def _valeur_nettoyee(valeur: object) -> str:
    """Strip a raw RNIC cell and treat the "non connu" sentinel as empty."""
    texte = str(valeur if valeur is not None else "").strip()
    return "" if texte.lower() == "non connu" else texte


def _sans_nan(valeur: object) -> object:
    """pandas reads an empty cell as NaN, which str() would turn into "nan"."""
    return "" if pd.isna(valeur) else valeur


# Read as written, validated in transform().
COLONNES_BRUTES = frozenset({"mandat_en_cours", "date_fin_dernier_mandat"})
MANDAT_NON_CONNU = "non_connu"
# Above 1 % of the dates written, unreadable dates fail the run.
SEUIL_DATES_ILLISIBLES = 0.01
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalize_mandat_en_cours(valeur: object) -> str:
    """Normalise the RNIC mandate status; ``non_connu`` when empty or unknown.

    Always returns a value, so a NULL in base can only mean "not imported yet".
    Seen at the source: "Mandat en cours", "Pas de mandat en cours",
    "Mandat expiré sans successeur déclaré" -- lower-cased, spaces collapsed.
    """
    texte = " ".join(_valeur_nettoyee(_sans_nan(valeur)).lower().split())
    return texte or MANDAT_NON_CONNU


def parse_date_fin_mandat(valeur: object) -> str | None:
    """Return the ISO date of *valeur*, "" when empty, None when unreadable."""
    texte = _valeur_nettoyee(_sans_nan(valeur))
    if not texte:
        return ""
    if not _ISO_DATE.fullmatch(texte):
        return None
    try:
        return datetime.date.fromisoformat(texte).isoformat()
    except ValueError:
        return None


def derive_id_parcelle(row: dict, rang: int) -> str:
    """Derive the 14-char canonical id of the parcel of rank *rang* (1 to 3).

    Priority: normalised ``reference_cadastrale_<rang>`` (RNIC), else
    recomposed from the parcel-part columns (``code_insee_parcelle_<rang>`` +
    prefixe + section + numero). Returns "" (NULL in base) when neither path
    produces exactly 14 characters -- matching a text address would be a guess
    (RNIC addresses are abbreviated, often without a house number), forbidden
    by the v3.5 truth doctrine (D-20). A rank never falls back on another one.
    """
    ref = _valeur_nettoyee(_sans_nan(row.get(f"reference_cadastrale_{rang}", "")))
    ref_normalise = ref.upper().replace(" ", "").replace("-", "")
    if len(ref_normalise) == 14:
        return ref_normalise

    def partie(nom: str) -> str:
        return _valeur_nettoyee(_sans_nan(row.get(f"{nom}_parcelle_{rang}", "")))

    code_insee = partie("code_insee")
    # prefixe is legitimately empty for most parcels (no old-commune-fusion
    # prefix) -- zfill defaults it to "000", it is not a required part.
    prefixe = partie("prefixe")
    section = partie("section")
    numero = partie("numero")
    if not (code_insee and section and numero):
        return ""

    recompose = (
        code_insee.zfill(5)[:5]
        + prefixe.zfill(3)[:3]
        + section.upper().rjust(2, "0")[:2]
        + numero.zfill(4)[:4]
    )
    return recompose.upper() if len(recompose) == 14 else ""


def derive_id_parcelle_1(row: dict) -> str:
    """The first parcel of a registration (:func:`derive_id_parcelle`)."""
    return derive_id_parcelle(row, 1)


class CoproprietePipeline(BasePipeline):
    """Concrete ETL for RNIC coproprietes (~528k rows)."""

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 480_000

    @property
    def dataset_name(self) -> str:
        return "copropriete"

    @property
    def has_department_partitions(self) -> bool:
        return False

    @property
    def expected_columns(self) -> list[str]:
        return SOURCE_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "copropriete_staging"

    @property
    def production_table_name(self) -> str:
        return "copropriete"

    @property
    def flat_staging_table_name(self) -> str:
        return "_copropriete_staging_flat"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {"_copropriete_staging_flat": "dc_departement_from_insee(code_insee)"}

    @property
    def production_departement_sql(self) -> str:
        return "dc_departement_from_insee(code_commune)"

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~600k production copropriete rows
        return self.scoped_min_rows(self.national_min_rows)

    def create_staging_table(self, dsn: str) -> None:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {self.staging_table_name} CASCADE")
                # Flat table too, as the base: stage() appends to it, and
                # a run failed before swap() would publish its rows again.
                cur.execute(
                    f"DROP TABLE IF EXISTS {self.flat_staging_table_name} CASCADE"
                )
                # EXCLUDING INDEXES (DB-HYGIENE-01): INCLUDING ALL copies
                # every production index under an auto-generated name, which
                # then survives the swap and gets re-copied on the next run.
                cur.execute(
                    f"CREATE TABLE {self.staging_table_name} "
                    f"(LIKE {self.production_table_name} "
                    f"INCLUDING ALL EXCLUDING INDEXES)"
                )
            conn.commit()

    def drop_staging_table(self, dsn: str) -> None:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {self.staging_table_name} CASCADE")
                # Flat table too, as the base: a run failed before swap() would
                # otherwise hand its flat rows to the next one.
                cur.execute(
                    f"DROP TABLE IF EXISTS {self.flat_staging_table_name} CASCADE"
                )
            conn.commit()

    def download(self) -> list[Path]:
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        raw_dir = get_download_dir("copropriete") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        csv_path = raw_dir / "rnic.csv"
        async with httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT,
        ) as client:
            success = await streaming_download(
                client,
                DOWNLOAD_URL,
                csv_path,
                max_retries=MAX_RETRIES,
            )
            if success:
                logger.info("Downloaded rnic.csv")
                return [csv_path]
            else:
                logger.error("Failed to download RNIC CSV")
                return []

    def validate_schema(self, file_path: Path) -> None:
        with open(file_path, encoding="utf-8-sig", errors="replace") as f:
            first_line = f.readline().strip()

        actual = {col.strip().strip('"') for col in first_line.split(CSV_SEPARATOR)}
        missing = set(SOURCE_COLUMNS) - actual
        if missing:
            msg = f"RNIC schema mismatch: missing {sorted(missing)}"
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        transformed_dir = get_download_dir("copropriete") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)
        output_path = transformed_dir / "copropriete-transformed.csv"

        # Plan 04.7-08: the mandate columns are read as written (« N/A » stays
        # « N/A », to be refused); every other column keeps the default
        # missing values of pandas, as before.
        df = pd.read_csv(
            file_path,
            sep=CSV_SEPARATOR,
            dtype=str,
            encoding="utf-8-sig",
            low_memory=False,
            keep_default_na=False,
        )
        for colonne in df.columns:
            if colonne not in COLONNES_BRUTES:
                df[colonne] = df[colonne].mask(df[colonne].isin(STR_NA_VALUES))

        df = df.rename(columns=COL_MAP)

        # D-20 root cause fix: the INSEE key comes from
        # code_insee_parcelle_1 (same source column derive_id_parcelle_1
        # already trusts), NEVER from code_officiel_commune, which
        # carries the postal code. zfill(5) matches the parcel
        # derivation; empty stays empty — a missing parcel INSEE is
        # never guessed from the postal code.
        insee = df["code_insee_parcelle_1"].fillna("").str.strip()
        insee = insee.mask(insee.str.lower() == "non connu", "")
        df["code_insee"] = insee.mask(insee != "", insee.str.zfill(5))

        # code_commune (legacy, still read by copropriete_par_codes)
        # receives the same corrected INSEE value as code_insee.
        df["code_commune"] = df["code_insee"]

        # D-20: derive the exact cadastral parcel BEFORE the fill-missing
        # loop below (id_parcelle_1 is an OUTPUT_COLUMNS entry -- the loop
        # would otherwise blank it out as "missing").
        df["id_parcelle_1"] = df.apply(
            lambda row: derive_id_parcelle_1(row.to_dict()), axis=1
        )
        # KI-20: an address on the second or the third parcel of a
        # registration used to read « no condominium ».
        for rang in RANGS_PARCELLES_SECONDAIRES:
            df[f"id_parcelle_{rang}"] = df.apply(
                lambda row, rang=rang: derive_id_parcelle(row.to_dict(), rang), axis=1
            )

        # Fill missing columns
        for col in OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        # nb_lots_commerciaux may not exist directly
        if "nb_lots_commerciaux" not in df.columns:
            df["nb_lots_commerciaux"] = ""

        # Extract year from reglement date if full date
        if "annee_reglement" in df.columns:
            df["annee_reglement"] = df["annee_reglement"].str.extract(
                r"(\d{4})", expand=False
            )

        # Normalize "non connu" sentinel values to empty string across
        # all columns — the 2026 RNIC source uses "non connu" where the
        # old schema used empty/NULL. This affects text, VARCHAR(5) AND
        # integer columns, so replace before any type casting happens.
        df = df.replace("non connu", "")

        # Plan 04.7-08: the mandate status is ALWAYS written (non_connu when
        # empty), so NULL in base means "not imported yet". The end date is
        # NULL when empty and NULL + counted when unreadable.
        df["mandat_en_cours"] = df["mandat_en_cours"].map(normalize_mandat_en_cours)
        dates = df["date_fin_dernier_mandat"].map(parse_date_fin_mandat)
        unreadable = int(dates.isna().sum())
        renseignees = int((dates != "").sum())
        if renseignees and unreadable > SEUIL_DATES_ILLISIBLES * renseignees:
            # A change of format at the source would empty every date silently.
            msg = (
                f"Copropriete: {unreadable} of {renseignees} "
                "date_fin_dernier_mandat unreadable"
            )
            raise ValueError(msg)
        if unreadable:
            logger.warning(
                "Copropriete: %d unreadable date_fin_dernier_mandat set to NULL",
                unreadable,
            )
        df["date_fin_dernier_mandat"] = dates.fillna("")

        # Clean code_commune
        df = df[
            df["numero_immatriculation"].notna()
            & (df["numero_immatriculation"].str.strip() != "")
        ]

        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed copropriete: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS
                    _copropriete_staging_flat (
                        numero_immatriculation TEXT,
                        nom_copropriete TEXT,
                        adresse TEXT,
                        code_commune TEXT,
                        code_postal TEXT,
                        nb_lots_total TEXT,
                        nb_lots_habitation TEXT,
                        nb_lots_commerciaux TEXT,
                        nb_lots_stationnement TEXT,
                        nom_syndic TEXT,
                        type_syndic TEXT,
                        periode_construction TEXT,
                        annee_reglement TEXT,
                        code_insee TEXT,
                        id_parcelle_1 TEXT,
                        longitude TEXT,
                        latitude TEXT,
                        nb_lots_habitation_strict TEXT,
                        mandat_en_cours TEXT,
                        date_fin_dernier_mandat TEXT,
                        id_parcelle_2 TEXT,
                        id_parcelle_3 TEXT,
                        nb_parcelles TEXT
                    )
                """)

                cur.execute("SELECT count(*) FROM _copropriete_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _copropriete_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _copropriete_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info("Staged copropriete: %d rows", row_count)
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production staging, rebuild indexes, swap.

        Phase A: Create staging + INSERT SELECT + index builds (own transaction).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — drop flat staging, rename sequence.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "copropriete"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                cur.execute("DROP TABLE IF EXISTS copropriete_staging CASCADE")
                cur.execute(
                    "DROP SEQUENCE IF EXISTS copropriete_staging_id_seq CASCADE"
                )
                cur.execute("""
                    CREATE TABLE copropriete_staging
                    (LIKE copropriete INCLUDING ALL EXCLUDING INDEXES)
                """)
                cur.execute("CREATE SEQUENCE copropriete_staging_id_seq")
                cur.execute("""
                    ALTER TABLE copropriete_staging
                    ALTER COLUMN id SET DEFAULT
                    nextval('copropriete_staging_id_seq')
                """)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO copropriete_staging
                        (numero_immatriculation,
                         nom_copropriete, adresse,
                         code_commune, code_postal,
                         nb_lots_total, nb_lots_habitation,
                         nb_lots_commerciaux,
                         nb_lots_stationnement,
                         nom_syndic, type_syndic,
                         periode_construction,
                         annee_reglement,
                         code_insee, id_parcelle_1,
                         longitude, latitude,
                         nb_lots_habitation_strict,
                         mandat_en_cours, date_fin_dernier_mandat,
                         id_parcelle_2, id_parcelle_3, nb_parcelles)
                    SELECT DISTINCT
                        NULLIF(numero_immatriculation, ''),
                        NULLIF(nom_copropriete, ''),
                        NULLIF(adresse, ''),
                        NULLIF(code_commune, ''),
                        NULLIF(code_postal, ''),
                        NULLIF(nb_lots_total, '')::integer,
                        NULLIF(nb_lots_habitation, '')::integer,
                        NULLIF(nb_lots_commerciaux, '')::integer,
                        NULLIF(nb_lots_stationnement, '')
                            ::integer,
                        NULLIF(nom_syndic, ''),
                        NULLIF(type_syndic, ''),
                        NULLIF(periode_construction, ''),
                        NULLIF(annee_reglement, '')::integer,
                        LEFT(NULLIF(code_insee, ''), 5),
                        LEFT(NULLIF(id_parcelle_1, ''), 14),
                        NULLIF(longitude, '')::double precision,
                        NULLIF(latitude, '')::double precision,
                        NULLIF(nb_lots_habitation_strict, '')::integer,
                        NULLIF(mandat_en_cours, ''),
                        NULLIF(date_fin_dernier_mandat, '')::date,
                        LEFT(NULLIF(id_parcelle_2, ''), 14),
                        LEFT(NULLIF(id_parcelle_3, ''), 14),
                        NULLIF(nb_parcelles, '')::integer
                    FROM _copropriete_staging_flat
                    WHERE NULLIF(
                        numero_immatriculation, ''
                    ) IS NOT NULL
                """)

                # Collapse identical published rows only. Conflicting values for
                # a natural key must fail here, before committing any generation.
                self.ensure_staging_constraints(cur)

                cur.execute("SELECT count(*) FROM copropriete_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                # Same departement scope as staging (D-23).
                old_count = self.count_production_rows(cur, "copropriete")

                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging "
                        f"{staging_count} "
                        f"vs production {old_count}"
                    )
                    raise RuntimeError(msg)

            conn.commit()  # Phase A commits before Phase B opens

        logger.info("Swap complete: %d rows (was %d)", staging_count, old_count)

        # --- Phase B: atomic swap via base class helper ----------------------
        self.swap_tables(dsn)  # logged guard + DROP IF EXISTS old + RENAME pair

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute("DROP TABLE IF EXISTS _copropriete_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # has_department_partitions = False — no partition rename needed
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete")

    # Production index names (migration 031 + 076 + 116) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_copropriete_code_commune", "(code_commune)"),
        ("idx_copropriete_code_postal", "(code_postal)"),
        ("idx_copropriete_id_parcelle_1", "(id_parcelle_1)"),
        ("idx_copropriete_code_insee", "(code_insee)"),
        ("idx_copropriete_id_parcelle_2", "(id_parcelle_2)"),
        ("idx_copropriete_id_parcelle_3", "(id_parcelle_3)"),
    )

    def _create_staging_indexes(
        self,
        cur: psycopg.Cursor,  # type: ignore[type-arg]
    ) -> None:
        """Create production-equivalent indexes on the staging table."""
        staging = self.staging_table_name
        for canonical, spec in self.STAGING_INDEX_SPECS:
            name = self.staging_index_name(canonical)
            logger.info("Creating index %s", name)
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            cur.execute(f"CREATE INDEX {name} ON {staging} {spec}")


# -- Registry registration ---------------------------------------------------

from app.etl.registry import registry  # noqa: E402

registry.register(CoproprietePipeline)
