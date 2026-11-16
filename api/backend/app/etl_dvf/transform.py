"""DVF CSV transformation: column selection, filtering, code_departement injection."""

import csv
import logging
from functools import cache
from pathlib import Path

import pandas as pd

from app.etl_dvf.config import COLUMNS_TO_KEEP, OUTPUT_COLUMNS

logger = logging.getLogger(__name__)

# KI-D7: geo-dvf writes the commune code of an older map of the communes, so the
# sales of a commune merged or recoded since stay under its former code and the
# fiche of the commune of today does not count them. Former code -> the one
# commune of today it became, same departement; a split keeps its former code.
# Written by scripts/build_codes_communes_actuels.py from the COG of the INSEE.
CODES_COMMUNES_ACTUELS_PATH = Path(__file__).with_name("codes_communes_actuels.csv")


@cache
def codes_communes_actuels() -> dict[str, str]:
    """Former commune code -> commune code of today (read once)."""
    if not CODES_COMMUNES_ACTUELS_PATH.exists():
        return {}
    with CODES_COMMUNES_ACTUELS_PATH.open(encoding="utf-8", newline="") as f:
        lignes = (ligne for ligne in f if not ligne.startswith("#"))
        return {
            row["ancien_code"]: row["code_actuel"] for row in csv.DictReader(lignes)
        }


def _derive_code_departement(code_commune: pd.Series) -> pd.Series:
    """Derive the departement code from the commune code.

    Rules:
    - DOM (97x): first 3 characters (971, 972, etc.)
    - Corse: communes starting with '2A' or '2B' -> '2A' or '2B'
    - Metro: first 2 characters (01, 02, ..., 95)
    """

    def _extract(val: str) -> str:
        if not isinstance(val, str) or len(val) < 2:
            return "00"
        if val.startswith("97") and len(val) >= 3:
            return val[:3]
        if val.startswith("2A") or val.startswith("2B"):
            return val[:2]
        return val[:2]

    return code_commune.apply(_extract)


def transform_dvf_csv(csv_path: Path) -> Path:
    """Transform a raw DVF CSV into a clean CSV ready for COPY.

    Steps:
        1. Read CSV (only columns we need)
        2. Handle valeur_fonciere decimal comma issue
        3. Count the rows without longitude or latitude -- they are KEPT
           (phase 04.4 D-01: the reconstruction of a mutation reads every
           source line; such a line gets a NULL position at the swap)
        4. Inject code_departement from code_commune
        5. Write clean CSV with comma separator

    Returns the path to the transformed CSV.
    """
    # Read with explicit dtypes for text-like columns to prevent coercion
    dtype_overrides: dict[str, str] = {
        "code_postal": "str",
        "code_commune": "str",
        "adresse_numero": "str",
        # C44: a suffix is a code (« B », « T »), never a number.
        "adresse_suffixe": "str",
        "id_parcelle": "str",
        "id_mutation": "str",
        "code_type_local": "str",
        "code_nature_culture": "str",
        "code_nature_culture_speciale": "str",
        # D-02: identifiers/codes, never numbers -- a numero_disposition
        # "000001" or a lotN_numero would lose its leading zeros if pandas
        # inferred it as an integer. nombre_lots and lotN_surface_carrez are
        # left untyped here (plain pass-through); their numeric conversion
        # happens in the INSERT SELECT (pipeline.py swap()), same pattern
        # as surface_reelle_bati today.
        "numero_disposition": "str",
        "lot1_numero": "str",
        "lot2_numero": "str",
        "lot3_numero": "str",
        "lot4_numero": "str",
        "lot5_numero": "str",
    }

    # Only read columns that exist in the file (usecols handles missing gracefully)
    df = pd.read_csv(
        csv_path,
        sep=",",
        dtype=dtype_overrides,
        low_memory=False,
        usecols=lambda c: c in COLUMNS_TO_KEEP,
    )

    total_rows = len(df)

    # Handle valeur_fonciere: source may use comma as decimal separator
    if "valeur_fonciere" in df.columns and df["valeur_fonciere"].dtype == object:
        df["valeur_fonciere"] = (
            df["valeur_fonciere"].astype(str).str.replace(",", ".", regex=False)
        )
        df["valeur_fonciere"] = pd.to_numeric(df["valeur_fonciere"], errors="coerce")

    # D-01: a row without coordinates is kept; only counted for the log.
    sans_coordonnees = int((df["longitude"].isna() | df["latitude"].isna()).sum())
    logger.info(
        "DVF transform %s: %d rows kept, %d without coordinates (NULL position)",
        csv_path.name,
        total_rows,
        sans_coordonnees,
    )

    # KI-D7: a former commune code becomes the code of today, before the
    # departement is derived from it (the table keeps the departement anyway).
    table = codes_communes_actuels()
    actuels = df["code_commune"].map(table)
    recodees = int(actuels.notna().sum())
    df["code_commune"] = actuels.fillna(df["code_commune"])
    logger.info(
        "DVF transform %s: %d rows recoded to the commune of today (COG)",
        csv_path.name,
        recodees,
    )

    # Inject code_departement from code_commune
    df["code_departement"] = _derive_code_departement(df["code_commune"].fillna(""))

    # Reorder columns to match OUTPUT_COLUMNS
    # Only include columns that actually exist
    cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
    df = df[cols]

    # Write transformed CSV
    output_dir = csv_path.parent.parent / "transformed"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{csv_path.stem}-transformed.csv"
    df.to_csv(output_path, index=False, sep=",")

    return output_path
