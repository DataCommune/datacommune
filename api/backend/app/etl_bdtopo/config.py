"""BD TOPO specific configuration: URLs and layer settings."""

# -- Download settings --------------------------------------------------------

# URL template for BD TOPO departement archives (date may change with releases).
# {dept} must be zero-padded to 3 chars (e.g. "075", "02A", "971").
# Releases are quarterly: 03-15, 06-15, 09-15, 12-15.
BDTOPO_EDITION = "2025-12-15"
BDTOPO_URL_TEMPLATE = (
    "https://data.geopf.fr/telechargement/download/BDTOPO/"
    "BDTOPO_3-5_TOUSTHEMES_GPKG_{projection}_D{dept}_{edition}/"
    "BDTOPO_3-5_TOUSTHEMES_GPKG_{projection}_D{dept}_{edition}.7z"
)

# Projection of each territory (D-122): the IGN publishes every departement in
# its own projection, named in the URL, and the GPKG geometries are in it.
# Metropolitan France (Corsica included) is Lambert 93; each DROM has its own
# token and EPSG. Tokens checked by HEAD on the 2025-12-15 edition (plan
# 04.1-06: 200 and an archive of 30 to 184 MB each; LAMB93 answers 400 for
# a DROM). Keys are 3-char codes, as in the URL.
BDTOPO_METROPOLE: tuple[str, str] = ("LAMB93", "EPSG:2154")
BDTOPO_TERRITORIES: dict[str, tuple[str, str]] = {
    "971": ("RGAF09UTM20", "EPSG:5490"),  # Guadeloupe, RGAF09 / UTM 20N
    "972": ("RGAF09UTM20", "EPSG:5490"),  # Martinique, RGAF09 / UTM 20N
    "973": ("UTM22RGFG95", "EPSG:2972"),  # Guyane, RGFG95 / UTM 22N
    "974": ("RGR92UTM40S", "EPSG:2975"),  # La Réunion, RGR92 / UTM 40S
    "976": ("RGM04UTM38S", "EPSG:4471"),  # Mayotte, RGM04 / UTM 38S
}


def bdtopo_territory(dept: str) -> tuple[str, str]:
    """(URL projection token, source EPSG) of *dept* (any padding)."""
    return BDTOPO_TERRITORIES.get(dept.zfill(3), BDTOPO_METROPOLE)


def bdtopo_url(dept: str, millesime: str | None) -> str:
    """Archive URL of *dept* (3-char code), edition *millesime* or the default.

    Plan 02-11, D-27: the edition is a dated segment of the URL; D-122: the
    projection token is the territory's own.
    """
    projection, _ = bdtopo_territory(dept)
    return BDTOPO_URL_TEMPLATE.format(
        projection=projection, dept=dept, edition=millesime or BDTOPO_EDITION
    )


# -- GeoPackage settings ------------------------------------------------------

# Layer name inside the GeoPackage containing building data
GPKG_LAYER = "batiment"

# Columns to select via ogr2ogr -sql (BD TOPO batiment attributes).
# geometrie MUST be included explicitly when using -sql (vs -select).
COLUMNS_TO_SELECT = (
    "geometrie,cleabs,nature,usage_1,nombre_d_etages,"
    "hauteur,altitude_minimale_sol,altitude_maximale_toit"
)
