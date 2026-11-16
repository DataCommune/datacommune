"""Transport ETL pipeline.

Downloads consolidated GTFS stops from transport.data.gouv.fr and SNCF
train stations from ressources.data.sncf.com.  Deduplicates stops by
name + rounded coordinates, builds point geometry via ST_MakePoint,
and bulk-loads into arrets_transport and gares tables.
"""
