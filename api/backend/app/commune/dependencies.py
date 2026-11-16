"""Exact source edges of the current thirteen-branch commune projection."""

COMMUNE_SOURCES = {
    "demographie": "demographie",
    "revenus": "revenus",
    "zonage_abc": "zonage_abc",
    "vacance": "vacance",
    "fiscalite": "fiscalite_locale",
    "loyers": "indicateurs_loyers",
    "dvf": "mutations_grain",
    "dpe": "diagnostics_dpe",
    "equipements": "equipements",
    "scolaire": "carte_scolaire",
    "monuments": "monuments_historiques",
    "foncier_pm": "foncier_pm",
    "connectivite": "eligibilite_fixe",
}

COMMUNE_INTERMEDIATES = {
    "dvf": "v_commune_dvf",
    "dpe": "v_commune_dpe",
    "equipements": "v_commune_equipements",
    "scolaire": "v_commune_scolaire",
    "monuments": "v_commune_monuments",
    "foncier_pm": "v_commune_foncier_pm",
    "connectivite": "v_commune_connectivite",
}

COMMUNE_EDGES = tuple(
    ("v_commune_synthese", COMMUNE_INTERMEDIATES.get(dataset, source))
    for dataset, source in COMMUNE_SOURCES.items()
) + tuple(
    (view, COMMUNE_SOURCES[dataset]) for dataset, view in COMMUNE_INTERMEDIATES.items()
)

# The departement view (plan 04.7-10) is rebuilt with the chain but reads its
# three sources directly: its own edges, checked by the same barrier. Kept
# apart from COMMUNE_EDGES, whose order the publication tests index.
DEPARTEMENT_VIEW_SOURCES = {
    dataset: COMMUNE_SOURCES[dataset] for dataset in ("dvf", "dpe", "demographie")
}
DEPARTEMENT_EDGES = tuple(
    ("v_departement_synthese", source) for source in DEPARTEMENT_VIEW_SOURCES.values()
)

# The yearly price series (migration 115) is rebuilt with the chain and reads
# the sales directly: its own edge, checked by the same barrier.
ANNUEL_EDGES = (("v_commune_dvf_annuel", COMMUNE_SOURCES["dvf"]),)

# One publication spans base swaps and derived-view rebuilds on multiple sessions.
PUBLICATION_LOCK_ID = 0x434F523230
