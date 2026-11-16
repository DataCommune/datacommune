"""BDNB open export configuration: URLs, members read and CSV schema.

Source: Base de Données Nationale des Bâtiments (CSTB), open-data millesime
exports listed on data.gouv.fr, Licence Ouverte 2.0. One zip per departement
(605 MB for 85, 781 MB for 44 in 2026-02-b), 127 members; only the three
members below are ever opened, about 2.4 % of the zip.
"""

import httpx

# -- Download settings --------------------------------------------------------

# The current millesime is discovered in the resources of the data.gouv.fr
# dataset, whose URLs carry ``bdnb_millesime_<AAAA-MM-x>``: no ``latest``
# alias is known and the bucket listing is refused (D6).
DATAGOUV_DATASET_URL = (
    "https://www.data.gouv.fr/api/1/datasets/base-de-donnees-nationale-des-batiments/"
)
MILLESIME_IN_URL_RE = r"bdnb_millesime_([0-9]{4}-[0-9]{2}-[a-z])"
DATAGOUV_TIMEOUT = httpx.Timeout(30.0)

# Departements downloaded at once (each export is a few hundred MB).
DOWNLOAD_CONCURRENCY = 4

# Departemental export of one millesime. The millesime is written as in the
# URL (``2026-02-b``), the departement on two characters, Corsica lower case
# (``dep2a``); an absent object answers 403, the bucket listing being refused.
EXPORT_URL_TEMPLATE = (
    "https://open-data.s3.fr-par.scw.cloud/bdnb_millesime_{millesime}/"
    "millesime_{millesime}_dep{dept}/"
    "open_data_millesime_{millesime}_dep{dept}_csv.zip"
)

# ~600-800 MB per departement: generous read timeout, as for BD TOPO.
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=600.0)

# -- Members of the zip (dictionary v0.7.12, millesime 2026-02.b) -------------

MEMBER_RELATIONS = "csv/rel_batiment_groupe_parcelle.csv"
MEMBER_FFO = "csv/batiment_groupe_ffo_bat.csv"
MEMBER_VERSION = "csv/metadonnees_bdnb_version.csv"
MEMBERS_READ = (MEMBER_RELATIONS, MEMBER_FFO, MEMBER_VERSION)

CSV_SEPARATOR = ";"

# Caps of one member, checked on the zip directory before it is opened
# (T-19-04): 2 GiB uncompressed (the ffo member is 31 MB for 85), and a
# compression ratio of 50 at most (3 to 5 observed).
MAX_MEMBER_BYTES = 2 * 1024**3
MAX_COMPRESSION_RATIO = 50

# Real headers:
#   relations: batiment_groupe_id;parcelle_id;code_departement_insee;
#              parcelle_principale
#   ffo:       batiment_groupe_id;code_departement_insee;nb_niveau;
#              annee_construction;usage_niveau_1_txt;mat_mur_txt;mat_toit_txt;
#              nb_log
#   version:   millesime;version;date_publication;changelog_rapide;
#              changelog_detaille
# Only the columns actually ingested are required; the departement of a row
# is the one of its FILE, never ``code_departement_insee``.
RELATION_COLUMNS: tuple[str, ...] = (
    "batiment_groupe_id",
    "parcelle_id",
    "parcelle_principale",
)
FFO_COLUMNS: tuple[str, ...] = (
    "batiment_groupe_id",
    "annee_construction",
    "mat_mur_txt",
    "mat_toit_txt",
)
VERSION_COLUMNS: tuple[str, ...] = ("millesime", "version", "date_publication")

# Columns of the transformed CSV handed to COPY (order matters). One CSV holds
# both natures of rows: ``relation`` rows fill the relation columns,
# ``ffo`` rows the attribute columns; swap() joins them.
OUTPUT_COLUMNS: list[str] = [
    "nature",
    "batiment_groupe_id",
    "parcelle_id",
    "code_departement",
    "parcelle_principale",
    "annee_construction",
    "mat_mur_txt",
    "mat_toit_txt",
]
NATURE_RELATION = "relation"
NATURE_FFO = "ffo"
