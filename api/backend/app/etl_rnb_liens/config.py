"""RNB link export configuration: URLs and CSV schema.

Source: Referentiel National des Batiments departmental exports
(beta.gouv.fr / CSTB, Licence Ouverte 2.0). One zipped CSV per department,
~250 MB compressed / ~1 GB uncompressed for dept 44.
"""

import httpx

# -- Download settings --------------------------------------------------------

RNB_EXPORT_URL_TEMPLATE = (
    "https://rnb-opendata.s3.fr-par.scw.cloud/files/RNB_{dept}.csv.zip"
)

# ~250 MB per department: generous read timeout, same order as cadastre.
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=600.0)

# -- CSV schema (verified against RNB_44.csv.zip, published 2026-08-01) --------

CSV_SEPARATOR = ";"

# Real header: rnb_id;point;shape;status;ext_ids;addresses;plots;validated_by
# Only the four columns actually ingested are required. ``point``/``shape`` are
# geometry (we already hold BD TOPO footprints), ``ext_ids`` is BDNB/BD TOPO
# cross-referencing, and ``validated_by`` carries contributor usernames --
# personal data with no use here, never read.
EXPECTED_COLUMNS: list[str] = [
    "rnb_id",
    "status",
    "addresses",
    "plots",
]

# Columns of the transformed CSV handed to COPY (order matters).
OUTPUT_COLUMNS: list[str] = [
    "id_rnb",
    "cle_ban",
    "code_departement",
    "statut",
    "parcelles",
    "millesime",
]

# -- Quality gate -------------------------------------------------------------

# Absolute floor per department for the final staging table (GATE-B').
# Dept 44 yields ~1.15M (id_rnb, cle_ban) pairs (~980k buildings, 86 % of them
# addressed). 250k is ~20 % of that: a "quasi-empty staging" guard, not a
# regression gate -- the relative 50 %/20 % nets already cover regressions once
# a baseline exists.
MIN_PAIRS_PER_DEPARTEMENT = 250_000
