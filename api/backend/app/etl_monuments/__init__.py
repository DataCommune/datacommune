"""Monuments Historiques ETL pipeline.

Downloads the Merimee CSV published on data.gouv.fr (Ministere de la Culture),
filters monuments without coordinates, and bulk-loads with point
geometry into PostGIS.
"""
