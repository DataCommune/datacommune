"""Carte Scolaire ETL pipeline.

Downloads the national CSV from data.gouv.fr (Carte Scolaire des Colleges
Publics), validates schema, filters rows with valid code_rne, and
bulk-loads into PostGIS carte_scolaire table.
"""
