"""BD TOPO batiments ETL pipeline.

Downloads departement-level GeoPackage archives (.7z) from data.geopf.fr,
extracts and loads the batiment layer via ogr2ogr with Lambert93->WGS84
reprojection, and stores building height, floors, and footprint data.
"""
