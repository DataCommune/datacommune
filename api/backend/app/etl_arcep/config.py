"""Arcep-specific configuration: URLs, columns, departements."""

import re
from collections.abc import Iterable

import httpx

# -- Download settings --------------------------------------------------------

ARCEP_BASE = "https://data.arcep.fr/fixe/maconnexioninternet"

# Per-department download URLs (smaller than national files)
# Arcep changed their URL scheme: dept code is now in the filename, not a subdirectory.
# old (404): .../departement/{dept}/base_imb.csv.gz
# new (ok):  .../departement/base_imb_{dept}.csv.gz
BASE_IMB_URL = f"{ARCEP_BASE}/base_imb/last/departement/base_imb_{{dept}}.csv.gz"
ELIGIBILITE_URL = f"{ARCEP_BASE}/eligibilite/last/departement/actuel_{{dept}}.csv.gz"


def arcep_urls(dept: str, millesime: str | None) -> tuple[str, str]:
    """(base_imb, eligibilite) URLs of *dept*: `last`, or quarter *millesime*.

    Quarterly folders (`2026_T1`...) stay online next to `last`, which
    redirects to the newest (index read on 2026-09-24). Plan 02-11, D-27.
    """
    base_imb, eligibilite = BASE_IMB_URL, ELIGIBILITE_URL
    if millesime is not None:
        base_imb = base_imb.replace("/last/", f"/{millesime}/")
        eligibilite = eligibilite.replace("/last/", f"/{millesime}/")
    return base_imb.format(dept=dept), eligibilite.format(dept=dept)


# Quarterly folder of a final download URL (after the 302 of `last`, served
# from the producer's object storage): `.../2026_T2/departement/...`.
_QUARTER_FOLDER = re.compile(r"/([0-9]{4}_T[1-4])/departement/")


def served_millesime(final_urls: Iterable[str]) -> str | None:
    """The quarter every downloaded file was read from (C24); else None.

    Read on the URL each body actually came from, never on the import date.
    No file, a URL without quarter or files from two quarters: unknown.
    """
    quarters = {
        match.group(1) if (match := _QUARTER_FOLDER.search(url)) else None
        for url in final_urls
    }
    if len(quarters) != 1 or None in quarters:
        return None
    return quarters.pop()


# Long timeouts -- Arcep files can be several hundred MB per department
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=600.0)

# -- Schema settings ----------------------------------------------------------

# Minimum required columns in base_imb CSV (Arcep renamed columns in 2025-T3)
BASE_IMB_EXPECTED_COLUMNS: list[str] = [
    "imb_id",
    "addr_code_insee",
    "addr_nom_voie",
    "addr_numero",
    "addr_rep",
]

# Minimum required columns in eligibilite/actuel CSV (Arcep renamed columns in 2025-T3)
ELIGIBILITE_EXPECTED_COLUMNS: list[str] = [
    "imb_id",
    "code_techno",
    "classe_debit_descendant",
]

# Mapping from new Arcep column names to our internal names
COLUMN_RENAME: dict[str, str] = {
    "imb_id": "id_imb",
    "addr_code_insee": "code_insee",
    "addr_nom_voie": "voie",
    "addr_numero": "numero",
    "addr_rep": "complement",
    "code_techno": "technologie",
    "classe_debit_descendant": "debit_max",
    "code_operateur": "operateur",
}

# Output columns for the merged transformed CSV (order matters for COPY)
OUTPUT_COLUMNS: list[str] = [
    "id_imb",
    "code_insee",
    "voie",
    "numero",
    "complement",
    "technologie",
    "debit_max",
    "operateur",
    "code_departement",
]
