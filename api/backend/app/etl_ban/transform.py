"""BAN CSV transformation: column selection, renaming, geometry prep."""

from pathlib import Path

import pandas as pd

from app.etl_ban.config import (
    BAN_COLUMN_MAPPING,
    BAN_CSV_SEPARATOR,
    BAN_OUTPUT_COLUMNS,
)


def transform_ban_csv(
    input_path: Path,
    output_path: Path,
    dept_code: str,
) -> int:
    """Transform a raw BAN CSV into a clean CSV ready for COPY.

    Steps:
        1. Read CSV (all columns as strings to avoid auto-conversion)
        2. Select only the columns we need (BAN_COLUMN_MAPPING keys)
        3. Rename columns to match the adresses table schema
        4. Add code_departement column
        5. Filter rows missing lon/lat (useless without coordinates)
        6. Write clean CSV with comma separator

    Returns the number of rows written.
    """
    df = pd.read_csv(
        input_path,
        sep=BAN_CSV_SEPARATOR,
        dtype=str,
        low_memory=False,
    )

    # Normalize header to lowercase (validate_schema is case-insensitive)
    df.columns = df.columns.str.strip().str.lower()

    # Keep only columns we need
    source_cols = [c for c in BAN_COLUMN_MAPPING if c in df.columns]
    df = df[source_cols]

    # Rename to target schema
    df = df.rename(columns=BAN_COLUMN_MAPPING)

    # Add departement code
    df["code_departement"] = dept_code

    # Drop rows without coordinates (useless for geocoding)
    df = df.dropna(subset=["lon", "lat"])
    df = df[(df["lon"] != "") & (df["lat"] != "")]

    # Reorder columns to match BAN_OUTPUT_COLUMNS
    df = df[BAN_OUTPUT_COLUMNS]

    # Write transformed CSV (comma-separated, with header for COPY)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, sep=",")

    return len(df)
