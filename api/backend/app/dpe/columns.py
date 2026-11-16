"""Shared DPE column list for the two independent SELECT paths.

``diagnostics_dpe`` is read by two hand-written queries whose WHERE / ORDER BY
clauses legitimately differ but which must project the SAME business columns:

- ``app.dpe.queries.dpe_par_point``   — KNN point search
- ``app.zone.queries.zone_resolve_dpe`` — commune / departement listing

Before this constant existed the two lists drifted: commit 8ee847a enriched the
point path with 4 ADEME fields (``periode_construction``,
``type_energie_principale_chauffage``, ``cout_total_5_usages``,
``qualite_isolation_enveloppe``) but left the zone path behind, so the same
dataset served a poorer payload depending on the entry point. Both callers now
consume DPE_SELECT_COLUMNS so any future column lands in both paths at once.

The longitude/latitude projection is deliberately NOT part of this list: the
point query derives it from the PostGIS geometry (``ST_X/ST_Y(position)``) while
the zone query reads the stored ``longitude``/``latitude`` columns. Each caller
appends its own geo projection after these scalar columns.

The commune / scoring materialized views (``commune/matview_ddl.py``,
``scoring/matview_ddl.py``) stay independent by design — they are aggregates on
a refresh schedule, not row projections, and do not consume this list.
"""

DPE_SELECT_COLUMNS: tuple[str, ...] = (
    "numero_dpe",
    "date_etablissement_dpe",
    "annee_construction",
    "periode_construction",
    "type_batiment",
    "surface_habitable",
    "etiquette_dpe",
    "etiquette_ges",
    "type_installation_chauffage",
    "type_energie_principale_chauffage",
    "conso_5_usages_ep",
    "emission_ges_5_usages",
    "cout_total_5_usages",
    "qualite_isolation_enveloppe",
    "code_postal",
    "code_insee",
    "nom_commune",
)
