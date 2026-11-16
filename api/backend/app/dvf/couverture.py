"""Single source of truth for the DVF `n.d.` coverage thresholds (Phase 57, D-23/D-24).

ONE function decides whether an aggregate effectif is large enough to publish a
median rather than `n.d.`: :func:`couverture_suffisante`. Nothing here reads a
database, calls a service or touches the network -- the whole module is
offline-testable, and its two constants are the only place `v_commune_dvf`
(`app.commune.matview_ddl`) and the zone aggregate (D-25) may read a threshold
from. No caller ever hardcodes a number of its own.

Both thresholds were arbitrated in `57-VERDICT-COUVERTURE.md` (verdict rendu
le 2026-08-24 par Ianis), against the measured cost table of
`57-MESURE-DVF.md`:

- **C-01 (seuil n.d. commune) -- SEUIL_ND_COMMUNE = 5.** `57-MESURE-DVF.md`
  §4 (communes sous chaque palier d'effectif, fenetre 12 mois glissants)
  shows that at threshold 5, only 3/207 communes (44, 1.4 %) and 14/251
  (85, 5.6 %) would flip to `n.d.` -- the lowest floor above a 1- or 3-point
  sample the verdict judged statistically indefensible, for a marginal
  coverage cost.
- **C-02 (seuil n.d. zone) -- SEUIL_ND_ZONE = 5.** `57-MESURE-DVF.md` has no
  threshold table specific to the variable-radius zone aggregate (a radius is
  a per-request geometry, not a stable partition like a commune, so it cannot
  be tabulated the same way). The verdict therefore applies the C-01 number
  literally: both aggregates read the same underlying population
  (`mutations_grain`, identical residential mono-local filter) -- only the
  geometric boundary changes, and nothing in the measurement suggests a
  median at N points is more or less reliable depending on whether those N
  points come from a commune or a circle.

`CLASSES_STATISTIQUES` mirrors C-03 (taxonomy confirmed as-is): the only two
values of `app.etl_dvf.qualification.CLASSES_MUTATION` for which an
individual `prix_m2` is ever statistically meaningful (`ensemble_residentiel`,
`mixte`, `commerciale`, `terrain_seul` and `hors_perimetre` never carry one,
see `app.etl_dvf.qualification._prix_m2`).
"""

# C-01 -- see module docstring for the citation trail back to the measured
# number this constant is not allowed to drift from without a new verdict.
SEUIL_ND_COMMUNE: int = 5

# C-02 -- same verdict, same date; no table of its own (see module docstring).
SEUIL_ND_ZONE: int = 5

# Price trend of a commune (decision of Ianis, 2026-11-11). A change between two
# yearly medians is written only when the first and the last year hold at
# least this many retained sales each: measured on the national base in
# the internal note 2026-11-11-tendance-prix-mesure (10.9 % of the
# communes, 70.5 % of the inhabitants; the spread of the changes barely
# tightens above it).
SEUIL_EVOLUTION_COMMUNE: int = 30

# Closed calendar years of the series (`v_commune_dvf_annuel`).
ANNEES_TENDANCE: int = 5

_PORTEES: dict[str, int] = {"commune": SEUIL_ND_COMMUNE, "zone": SEUIL_ND_ZONE}

# C-03 -- residential mono-local classes retained in the statistics
# population. Order fixed by app.etl_dvf.qualification.CLASSES_MUTATION.
CLASSES_STATISTIQUES: tuple[str, ...] = (
    "residentiel_maison",
    "residentiel_appartement",
)


def couverture_suffisante(effectif: int, portee: str = "commune") -> bool:
    """True when *effectif* clears the `n.d.` threshold for *portee*.

    A null effectif (no mutation at all) is always insufficient -- it never
    raises and never defaults to True. An unknown *portee* is a closed
    vocabulary violation (D-23 has exactly two scopes) and raises
    ``ValueError`` rather than silently falling back to one of them.
    """
    if portee not in _PORTEES:
        raise ValueError(f"portee inconnue: {portee!r} (attendu: {sorted(_PORTEES)})")
    if not effectif:
        return False
    return effectif >= _PORTEES[portee]
