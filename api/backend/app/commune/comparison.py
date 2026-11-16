"""Comparability of communes and gaps between them, served by the API (D-48).

One rule, here only: per criterion, a column 2 to 4 gets the gap to column 1,
or the reason why none is computed (COM-03). The browser presents it, it never
computes a gap nor judges comparability (D-51). Nothing is read here: the rule
works on the syntheses the route already projected (``apply_commune_coverage``).

A criterion is a value of the synthesis that has a contract entry
(``commune_field_id``), found by the same walk as the semantic audit: flat
columns, and inside objects the value-or-absence object of an indicator
(``…courante.mediane``, ``…variation_pct``, ``…quantiles.p10``,
``resultat.part_fg_pct``) or a plain value (``resultat.modes``, a rent
field). Each value of a commune carries:

- its period: the vintage of its source for a flat column, the window
  ``[début, fin[`` of a DVF value (both windows for a variation), the
  generation or calculation of the DPE block, year and quarter of a rent, or
  else the provenance period of the source (the same for every commune of the
  instance);
- its definition: the field id, plus ``version_contrat``, ``population`` and
  the ``methode_*`` fields of an indicator block, or the ``type_prediction``
  (commune, EPCI, maille) of a rent;
- its unit: the unit of the field (``unite_prix`` for a DVF price).

An empty list or a null leaf of a served object is never a value (CR-02): it
is an absence, whose state is the one declared on its sibling when the
producer names one (``_SIBLING_STATE``), else an undeclared absence (None).
Absences follow the single rule: ``donnee_absente``, and two absences of the
same state and period stay identical, as the fiche shows (D-51).
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from app.common.semantic_contract import FieldContract, load_registry
from app.commune.schemas import (
    COMMUNE_FIELD_SOURCES,
    NON_SEMANTIC_COMMUNE_COLUMNS,
    CelluleComparaison,
    CommuneComparee,
    CritereComparaison,
    Ecart,
    MotifNonCalcul,
    ResultatCommuneSynthese,
    ResultatComparaisonCommunes,
    commune_field_id,
)

__all__ = [
    "METHODE_INCONNUE",
    "Critere",
    "MotifDocument",
    "MotifHorsCouverture",
    "MotifNonCalcul",
    "ValeurServie",
    "Verdict",
    "build_comparison",
    "compare_cell",
    "criteria_of",
]

# Rate and share units of the contract (manque 7 of the API map): the gap
# between two of them is in points. The contract itself is not renamed.
_RATE_UNITS = frozenset({"percent_point", "percent_points", "percent"})
POINT = "point"
_NUMERIC_TYPES = frozenset({"integer", "decimal"})

# Vintage column of each source that has one: the period of its flat columns.
_VINTAGES: dict[str, str] = {
    "demographie": "annee_demographie",
    "revenus": "annee_revenus",
    "vacance": "annee_vacance",
    "fiscalite": "annee_fiscalite",
    "equipements": "annee_equipements",
}
# A flat rent is the prediction of its rent object: period and scale are read
# there (``annee_loyers`` is null when the two types have different years).
_RENT_OBJECTS: dict[str, str] = {
    "loyer_m2_appartement": "loyers_appartement",
    "loyer_m2_maison": "loyers_maison",
    "loyers_appartement": "loyers_appartement",
    "loyers_maison": "loyers_maison",
}
# Values that date other values: served as their period, never compared.
_PERIOD_COLUMNS = frozenset({*_VINTAGES.values(), "annee_loyers"})
_RENT_PERIOD_KEYS = frozenset({"annee", "trimestre_reference"})
_BLOCKS = frozenset({"indicateurs_dvf", "indicateurs_dpe"})
_QUANTILES = ("p10", "p50", "p90")


@dataclass(frozen=True)
class ValeurServie:
    """One commune's value for a criterion, as its synthesis serves it."""

    etat: str | None
    valeur: Any
    periode: str | None
    unite: str
    definition: tuple[str, ...]
    hors_couverture: bool = False
    # The period is read commune by commune (vintage, DVF window, rent year,
    # DPE calculation): a null one is unknown, never « the same ». False for a
    # provenance period, shared by every commune of the instance.
    periode_par_commune: bool = False
    # Served reason of an absence, when the compared object serves one as a
    # state of its own (an address: point outside every parcel, several
    # parcels). Two absences are identical only with the same reason; the
    # commune syntheses serve none here (None on both sides, rule unchanged).
    motif: str | None = None
    # Calculation method of a value that depends on it (a DPE class, a
    # consumption, an emission: before or after July 2021, 04-10, R06-09/7).
    # None: the value does not depend on a method (communes, addresses, a
    # date of a document). ``METHODE_INCONNUE``: it does, but the method of
    # this document cannot be established; no verdict is then comparable.
    methode: str | None = None
    # A value withheld by its publication rule (a declared address masked as
    # a whole, P-ADR-01): it is never compared, and never « the same » as
    # another, since its text is not known to the reader.
    non_affichee: bool = False


# The column itself is out of coverage: a commune (03-09) or an address (04-07,
# CMP-01). Same rule, the reason names the compared object.
MotifHorsCouverture = Literal["commune_hors_couverture", "adresse_hors_couverture"]
Categorie = Literal["identique", "different"]
# Reasons of the documents compared (04-10, CMP-02): two methods of calculation
# (before and after July 2021) never compare; a method that cannot be
# established gives no verdict; a masked declared address is never compared.
MotifDocument = Literal[
    "methodes_differentes", "methode_indeterminee", "adresse_declaree_non_affichee"
]
METHODE_INCONNUE = "inconnue"


@dataclass(frozen=True)
class Verdict:
    """Gap to the first column, or why it is not computed.

    ``categorie``: the verdict served for a comparable non-numeric value when
    the caller asks for it (a zone, an exposure, a class), never a figure.
    """

    ecart: Ecart | None
    motif: MotifNonCalcul | Literal["adresse_hors_couverture"] | MotifDocument | None
    identique: bool
    categorie: Categorie | None = None


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _period_unknown(reference: ValeurServie, cellule: ValeurServie) -> bool:
    """A period read commune by commune is null on one side at least."""
    return (reference.periode_par_commune or cellule.periode_par_commune) and (
        reference.periode is None or cellule.periode is None
    )


def _methode_inconnue(reference: ValeurServie, cellule: ValeurServie) -> bool:
    """A method-dependent value whose method is not established on one side."""
    methodes = (reference.methode, cellule.methode)
    return any(m is not None for m in methodes) and (
        None in methodes or METHODE_INCONNUE in methodes
    )


def _identical(reference: ValeurServie, cellule: ValeurServie) -> bool:
    if reference.hors_couverture or cellule.hors_couverture:
        return False
    if reference.non_affichee or cellule.non_affichee:
        return False  # a withheld text is never « the same » as another
    if _methode_inconnue(reference, cellule):
        return False  # nothing is « the same » without its method
    if _period_unknown(reference, cellule) and (
        reference.valeur is not None or cellule.valeur is not None
    ):
        return False  # an undated value is never « the same » as another
    return (
        reference.etat == cellule.etat
        and reference.motif == cellule.motif
        and reference.valeur == cellule.valeur
        and reference.periode == cellule.periode
        and reference.unite == cellule.unite
        and reference.definition == cellule.definition
        and reference.methode == cellule.methode
    )


def _gap(field: FieldContract, reference: Any, cellule: Any, unite: str) -> Ecart:
    """Cell minus reference, in Decimal, rounded half away from zero."""
    gap = Decimal(str(cellule)) - Decimal(str(reference))
    digits = field.fraction_digits
    if digits is not None:
        gap = gap.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    if gap == 0:
        gap = abs(gap)  # a served 0, never a negative zero
    return Ecart(
        valeur=int(gap) if digits == 0 else float(gap),
        unite=POINT if unite in _RATE_UNITS else unite,
    )


def compare_cell(  # noqa: C901
    field: FieldContract,
    reference: ValeurServie,
    cellule: ValeurServie,
    *,
    hors_couverture: MotifHorsCouverture = "commune_hors_couverture",
    categoriel: bool = False,
) -> Verdict:
    """Verdict of *cellule* against *reference* (column 1). Pure.

    Precedence: column out of coverage, withheld value, absent value (any
    absence state), method of calculation (unknown, then different), period
    unknown for a commune, periods, units, definitions, non-numeric value;
    only then the gap, cell minus reference. Two incompatible values never
    get a gap, not even 0.

    *hors_couverture* names the reason of a column out of coverage (the
    commune by default, the address for 04-07). *categoriel*: a comparable
    non-numeric value (same period, unit and definition) gets the verdict
    ``identique`` / ``different`` instead of ``non_numerique``; the commune
    comparator keeps the default.

    Documents (04-10, R06-09/7): a value that depends on the method of
    calculation gets ``methode_indeterminee`` when the method of one side is
    not established, ``methodes_differentes`` when the two differ (a DPE of
    before and one of after July 2021); a masked declared address gets
    ``adresse_declaree_non_affichee``. Communes and addresses carry no method
    and no withheld value: their verdicts are unchanged.
    """
    identique = _identical(reference, cellule)

    def reason(motif) -> Verdict:
        return Verdict(ecart=None, motif=motif, identique=identique)

    if reference.hors_couverture or cellule.hors_couverture:
        return reason(hors_couverture)
    if reference.non_affichee or cellule.non_affichee:
        return reason("adresse_declaree_non_affichee")
    if reference.valeur is None or cellule.valeur is None:
        return reason("donnee_absente")
    if _methode_inconnue(reference, cellule):
        return reason("methode_indeterminee")
    if reference.methode != cellule.methode:
        return reason("methodes_differentes")
    if _period_unknown(reference, cellule):
        return reason("periode_inconnue")
    if reference.periode != cellule.periode:
        return reason("periodes_differentes")
    if reference.unite != cellule.unite:
        return reason("unites_differentes")
    if reference.definition != cellule.definition:
        return reason("definitions_differentes")
    if not (
        field.value_type in _NUMERIC_TYPES
        and _is_number(reference.valeur)
        and _is_number(cellule.valeur)
    ):
        if categoriel:
            return Verdict(
                ecart=None,
                motif=None,
                identique=identique,
                categorie="identique" if identique else "different",
            )
        return reason("non_numerique")
    ecart = _gap(field, reference.valeur, cellule.valeur, cellule.unite)
    # C49: « Identique » is equal at display precision. The returns above
    # already guarantee equal coverage, withheld value, method, period, unit
    # and definition; only the state, the reason and the gap remain to check.
    identique = identique or (
        ecart.valeur == 0
        and reference.etat == cellule.etat
        and reference.motif == cellule.motif
    )
    return Verdict(ecart=ecart, motif=None, identique=identique)


# --- Criteria -----------------------------------------------------------------


@dataclass(frozen=True)
class Critere:
    """A compared value: its dotted key, where it is, and its contract entry.

    ``path`` leads to the value, or to its value-or-absence object when
    ``leaf`` names the member that carries the value (``valeur``, ``p10``…).
    """

    cle: str
    path: tuple[str, ...]
    leaf: str | None
    field: FieldContract


def _walk(  # noqa: C901
    value: Any, path: tuple[str, ...], registry: Mapping[str, FieldContract]
) -> Iterator[Critere]:
    """Criteria under *value* at *path*, with the audit's field ids."""
    if path[0] in _RENT_OBJECTS and path[-1] in _RENT_PERIOD_KEYS:
        return
    if isinstance(value, Mapping):
        if "etat" in value:
            field_id = commune_field_id((*path, "valeur"))
            if field_id in registry:
                yield Critere(".".join(path), path, "valeur", registry[field_id])
                return
            leaves = [(leaf, commune_field_id((*path, leaf))) for leaf in _QUANTILES]
            if any(field_id in registry for _, field_id in leaves):
                for leaf, field_id in leaves:
                    if field_id in registry:
                        key = ".".join((*path, leaf))
                        yield Critere(key, path, leaf, registry[field_id])
                return
            if value.get("etat") != "value":
                return  # an absent sub-tree: its values come from other columns
        for key, item in value.items():
            if key not in {"etat", "motif"}:
                yield from _walk(item, (*path, str(key)), registry)
    elif isinstance(value, list):
        # A list is one value (the tied DPE classes), never compared item by item.
        field_id = commune_field_id((*path, "[0]"))
        if field_id in registry:
            yield Critere(".".join(path), path, None, registry[field_id])
    else:
        field_id = commune_field_id(path)
        if field_id in registry:
            yield Critere(".".join(path), path, None, registry[field_id])


def criteria_of(rows: Sequence[Mapping[str, Any]]) -> list[Critere]:
    """Criteria of the served syntheses *rows* (JSON form), in column order.

    Every flat column with a contract entry is a criterion, served or not;
    objects contribute the values found in any row, so a commune whose block
    is absent still gets a cell (with its absence) for the others' values.
    Vintage columns and rent periods are periods, not criteria.
    """
    registry = load_registry()
    criteria: dict[str, Critere] = {}
    for column in ResultatCommuneSynthese.model_fields:
        if column in NON_SEMANTIC_COMMUNE_COLUMNS | _PERIOD_COLUMNS | {"couverture"}:
            continue
        field_id = commune_field_id((column,))
        if field_id in registry:
            criteria.setdefault(
                column, Critere(column, (column,), None, registry[field_id])
            )
            continue
        for row in rows:
            if isinstance(row.get(column), Mapping):
                for critere in _walk(row[column], (column,), registry):
                    criteria.setdefault(critere.cle, critere)
    return list(criteria.values())


# --- Value, period, unit and definition of one commune -----------------------


def _is_absence(node: Any) -> bool:
    return (
        isinstance(node, Mapping)
        and isinstance(node.get("etat"), str)
        and node["etat"] != "value"
        and "motif" in node
    )


# The producer serves these leaves bare when no DPE class is known (``modes``
# empty, ``mediane_ordinale`` null): their cause is the state declared on the
# F/G share, exactly what the fiche reads (``commune-kpi.ts``, ``sansClasse``).
_SIBLING_STATE: dict[tuple[str, ...], str] = {
    ("indicateurs_dpe", "resultat", "modes"): "part_fg_pct",
    ("indicateurs_dpe", "resultat", "mediane_ordinale"): "part_fg_pct",
}


def _sibling_state(row: Mapping[str, Any], path: tuple[str, ...]) -> str | None:
    """State declared on the sibling of *path* that names its cause, else None."""
    sibling_key = _SIBLING_STATE.get(path)
    if sibling_key is None:
        return None
    parent: Any = row
    for key in path[:-1]:
        if not isinstance(parent, Mapping):
            return None
        parent = parent.get(key)
    if not isinstance(parent, Mapping):
        return None
    sibling = parent.get(sibling_key)
    return sibling["etat"] if _is_absence(sibling) else None


def _null_state(row: Mapping[str, Any], path: tuple[str, ...]) -> str | None:
    """State of a value served null at *path*: declared, or None (undeclared)."""
    column = path[0]
    if len(path) > 1:
        # A null inside a served object: its state is the one its sibling
        # declares (_SIBLING_STATE), else none is served for it.
        return _sibling_state(row, path)
    declared = (row.get("etats") or {}).get(column)
    if isinstance(declared, Mapping):
        return declared.get("etat")
    source = COMMUNE_FIELD_SOURCES.get(column)
    hosting = (row.get("couverture") or {}).get(source) or {}
    if hosting.get("etat") == "hors_couverture":
        return "out_of_coverage"
    if column in _RENT_OBJECTS:
        # A missing rent object is the absence of its flat rent (D-47 rule).
        flat = "loyer_m2_" + column.removeprefix("loyers_")
        state = (row.get("etats") or {}).get(flat)
        return state.get("etat") if isinstance(state, Mapping) else None
    return None


def _value(row: Mapping[str, Any], critere: Critere) -> tuple[str | None, Any]:
    """(state, value) of *critere* in the served *row*."""
    node: Any = row
    for depth, key in enumerate(critere.path):
        if _is_absence(node):
            return node["etat"], None
        if not isinstance(node, Mapping) or node.get(key) is None:
            return _null_state(row, critere.path[: depth + 1]), None
        node = node[key]
    if critere.leaf is not None:
        if _is_absence(node):
            return node["etat"], None
        leaf = node.get(critere.leaf) if isinstance(node, Mapping) else None
        return ("value", leaf) if leaf is not None else (None, None)
    if isinstance(node, list) and not node:
        # An empty list is an absence, never a served value (CR-02).
        return _sibling_state(row, critere.path), None
    return "value", node


def _window(fenetre: Any) -> str | None:
    if not isinstance(fenetre, Mapping):
        return None
    return f"[{fenetre['debut_inclus']}, {fenetre['fin_exclue']}["


def _block(row: Mapping[str, Any], column: str) -> Mapping[str, Any] | None:
    """The served indicator block of *column*, None when absent or null."""
    block = row.get(column)
    if not isinstance(block, Mapping) or "etat" in block:
        return None
    return block


def _dvf_period(block: Mapping[str, Any] | None, path: tuple[str, ...]) -> str | None:
    classe = (block or {}).get(path[1]) if len(path) > 2 else None
    if not isinstance(classe, Mapping):
        return None
    if path[2] == "variation_pct":
        before = _window((classe.get("precedente") or {}).get("fenetre"))
        current = _window((classe.get("courante") or {}).get("fenetre"))
        return f"{before} → {current}" if before and current else None
    return _window((classe.get(path[2]) or {}).get("fenetre"))


def _rent_period(rent: Any) -> str | None:
    if not isinstance(rent, Mapping) or rent.get("annee") is None:
        return None
    quarter = rent.get("trimestre_reference")
    return f"{rent['annee']}" if quarter is None else f"{rent['annee']} T{quarter}"


# C32 (Ianis, 2026-10-10, WINDOWS n° 92) and C34 (WINDOWS n° 95): the six flat
# columns of ``v_commune_dvf`` are computed on the same last 12 months (one
# WHERE), whatever the indicator setting: dated by those months, the rule of
# landmark ④ (C29), else by the period of the dvf source (C14).
_DVF_COLUMNS_12_MONTHS = frozenset(
    {
        "prix_m2_median",
        "nb_mutations_12m",
        "prix_m2_median_maison",
        "nb_mutations_12m_maison",
        "prix_m2_median_appartement",
        "nb_mutations_12m_appartement",
    }
)


def _period(
    row: Mapping[str, Any],
    critere: Critere,
    source_periods: Mapping[str, str | None],
    dvf_median_window: str | None = None,
) -> str | None:
    column = critere.path[0]
    if column in _DVF_COLUMNS_12_MONTHS:
        return dvf_median_window or source_periods.get("dvf")
    if column == "indicateurs_dvf":
        return _dvf_period(_block(row, column), critere.path)
    if column == "indicateurs_dpe":
        block = _block(row, column)
        if block is None:
            return None
        generation = block.get("generation")
        if generation is not None:
            return f"génération {generation}"
        return f"calcul du {block['date_calcul']}"
    if column in _RENT_OBJECTS:
        return _rent_period(row.get(_RENT_OBJECTS[column]))
    source = COMMUNE_FIELD_SOURCES.get(column)
    if source in _VINTAGES:
        vintage = row.get(_VINTAGES[source])
        return None if vintage is None else str(vintage)
    return source_periods.get(source) if source else None


def _definition(row: Mapping[str, Any], critere: Critere) -> tuple[str, ...]:
    column = critere.path[0]
    definition = (critere.field.field_id,)
    if column in _BLOCKS:
        block = _block(row, column) or {}
        return definition + tuple(
            f"{key}={block[key]}"
            for key in sorted(block)
            if key in {"version_contrat", "population"} or key.startswith("methode_")
        )
    if column in _RENT_OBJECTS:
        rent = row.get(_RENT_OBJECTS[column])
        if isinstance(rent, Mapping):
            return (*definition, f"type_prediction={rent.get('type_prediction')}")
    return definition


def _unit(row: Mapping[str, Any], critere: Critere) -> str:
    if critere.path[0] == "indicateurs_dvf" and critere.field.unit == "EUR/m2":
        block = _block(row, "indicateurs_dvf") or {}
        return block.get("unite_prix") or critere.field.unit
    return critere.field.unit


def _served(
    critere: Critere,
    commune: CommuneComparee,
    row: Mapping[str, Any] | None,
    source_periods: Mapping[str, str | None],
    dvf_median_window: str | None = None,
) -> ValeurServie:
    field = critere.field
    if row is None:
        # Out of coverage: the column stays, empty. Hosted without a synthesis
        # row: nothing proves a row of the commune (D-47 not_reported).
        out = commune.hebergement.etat == "hors_couverture"
        return ValeurServie(
            etat="out_of_coverage" if out else "not_reported",
            valeur=None,
            periode=None,
            unite=field.unit,
            definition=(field.field_id,),
            hors_couverture=out,
        )
    etat, valeur = _value(row, critere)
    return ValeurServie(
        etat=etat,
        valeur=valeur,
        periode=_period(row, critere, source_periods, dvf_median_window),
        unite=_unit(row, critere),
        definition=_definition(row, critere),
        periode_par_commune=_dated_by_commune(critere),
    )


def _dated_by_commune(critere: Critere) -> bool:
    """The period of *critere* is read in the commune's own row."""
    column = critere.path[0]
    if column in _BLOCKS or column in _RENT_OBJECTS:
        return True
    return COMMUNE_FIELD_SOURCES.get(column) in _VINTAGES


def build_comparison(
    communes: Sequence[CommuneComparee],
    codes_inconnus: Sequence[str] = (),
    source_periods: Mapping[str, str | None] | None = None,
    dvf_median_window: str | None = None,
) -> ResultatComparaisonCommunes:
    """The comparison of *communes* (column 1 first), criterion by criterion.

    *source_periods*: provenance period of each source, for the values whose
    source has no vintage (identical for every commune of the instance).
    *dvf_median_window*: the 12 months the flat dvf columns are computed on
    (C32, C34), None when unknown.
    """
    periods = source_periods or {}
    rows = [
        None if commune.synthese is None else commune.synthese.model_dump(mode="json")
        for commune in communes
    ]
    criteres = []
    for critere in criteria_of([row for row in rows if row is not None]):
        served = [
            _served(critere, commune, row, periods, dvf_median_window)
            for commune, row in zip(communes, rows, strict=True)
        ]
        cellules = []
        identiques = not any(value.hors_couverture for value in served)
        for index, (commune, value) in enumerate(zip(communes, served, strict=True)):
            verdict = compare_cell(critere.field, served[0], value) if index else None
            identiques = identiques and (verdict is None or verdict.identique)
            cellules.append(
                CelluleComparaison(
                    code_insee=commune.code_insee,
                    etat=value.etat,
                    valeur=value.valeur,
                    periode=value.periode,
                    ecart=verdict.ecart if verdict else None,
                    motif_non_calcul=verdict.motif if verdict else None,
                    identique_a_la_reference=verdict.identique if verdict else None,
                )
            )
        if cellules:
            criteres.append(
                CritereComparaison(
                    cle=critere.cle,
                    field_id=critere.field.field_id,
                    unite=critere.field.unit,
                    identiques=identiques,
                    cellules=cellules,
                )
            )
    return ResultatComparaisonCommunes(
        communes=list(communes),
        criteres=criteres,
        codes_inconnus=list(codes_inconnus),
    )
