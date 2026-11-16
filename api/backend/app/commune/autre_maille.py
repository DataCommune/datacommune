"""Paris, Lyon, Marseille: a value the source publishes at the other mesh (R-09).

Each source of the synthesis publishes either at the commune (75056) or at its
arrondissements (75101-75120), and the view joins on the code it is given
(internal note 2026-11-10-arrondissements). Two rules, both proven
row by row on the view (D-47), neither computing a value:

- **A, inheritance.** An arrondissement without a row of a source in
  ``SOURCES_HERITEES`` takes the row of its commune: these values apply as such
  to every part of the commune (the ABC zone is set by commune, the tax rates
  are voted for the whole commune, the intercommunality is the commune's). Each
  inherited column is named in ``heritages`` with its scale and its commune, so
  that it is never read as a measure of the arrondissement.
- **Renvoi.** A column served ``not_reported`` that the other mesh has keeps
  its absence and says where the value is published (``publiee_a``): a commune
  never receives a sum or a mean of its arrondissements (plan 04.7-09), and an
  arrondissement never takes a statistic of the whole commune (fibre).
"""

from collections.abc import Iterable, Mapping
from typing import Any

from app.common.geo import ARRONDISSEMENT_TO_COMMUNE, COMMUNE_TO_ARRONDISSEMENTS
from app.commune.absences import column_states

# Sources whose commune row applies to each of its arrondissements, and the
# columns read from that row (a source is taken whole or not at all).
SOURCES_HERITEES: dict[str, tuple[str, ...]] = {
    "zonage_abc": ("zone_abc", "zone_abc_reclassement"),
    "fiscalite": (
        "taux_tfpb",
        "taux_th_rs",
        "taux_teom",
        "nom_epci",
        "annee_fiscalite",
    ),
}

MOTIF_PUBLIEE_PAR_ARRONDISSEMENT = (
    "La source chargée n'a aucune ligne pour la commune entière\u202f; elle en a pour "
    "ses arrondissements."
)
MOTIF_PUBLIEE_POUR_LA_COMMUNE = (
    "La source chargée n'a aucune ligne pour l'arrondissement\u202f; elle en a pour sa "
    "commune entière."
)


def codes_autre_maille(codes: Iterable[str]) -> list[str]:
    """Codes of the other mesh of *codes*, not requested, in a stable order.

    An arrondissement reads its commune, a commune of Paris, Lyon or Marseille
    its arrondissements; any other code reads nothing more.
    """
    requested = list(codes)
    others: list[str] = []
    for code in requested:
        parent = ARRONDISSEMENT_TO_COMMUNE.get(code)
        for other in [parent] if parent else COMMUNE_TO_ARRONDISSEMENTS.get(code, []):
            if other not in requested and other not in others:
                others.append(other)
    return others


def _heberge(served: Mapping[str, Any], source: str) -> bool:
    hosting = (served.get("couverture") or {}).get(source)
    return getattr(hosting, "etat", None) == "heberge"


def _inherit(served: dict[str, Any], commune: Mapping[str, Any]) -> dict[str, dict]:
    """Copy the rows of ``SOURCES_HERITEES`` the arrondissement lacks; their origin."""
    origine = {
        "echelle": "commune",
        "code_insee": commune["code_insee"],
        "nom": commune.get("nom_commune"),
    }
    heritages: dict[str, dict] = {}
    for source, columns in SOURCES_HERITEES.items():
        if not _heberge(served, source) or not _heberge(commune, source):
            continue
        if any(served.get(column) is not None for column in columns):
            continue  # its own row: its values, and its gaps, stay its own
        if all(commune.get(column) is None for column in columns):
            continue
        for column in columns:
            served[column] = commune.get(column)
            if served[column] is not None:
                heritages[column] = dict(origine)
    return heritages


def apply_autre_maille(
    served: dict[str, Any], autres: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """*served* (a row of ``apply_commune_coverage``) completed from the other mesh.

    *autres*: the served rows of ``codes_autre_maille``, by code. Sets
    ``heritages`` (None when nothing is inherited) and recomputes ``etats``.
    """
    code = served["code_insee"]
    parent = ARRONDISSEMENT_TO_COMMUNE.get(code)
    if parent is not None:
        commune = autres.get(parent)
        heritages = _inherit(served, commune) if commune else {}
        others = [commune] if commune else []
        mesh = "commune"
    else:
        heritages = {}
        others = [
            autres[a] for a in COMMUNE_TO_ARRONDISSEMENTS.get(code, []) if a in autres
        ]
        mesh = "arrondissement"
    served["heritages"] = heritages or None
    if not others:
        return served
    if heritages:
        states = column_states(served, served["couverture"])
        served["etats"] = {
            column: state.model_dump() for column, state in states.items()
        } or None
    motif = (
        MOTIF_PUBLIEE_POUR_LA_COMMUNE
        if mesh == "commune"
        else MOTIF_PUBLIEE_PAR_ARRONDISSEMENT
    )
    for column, state in (served["etats"] or {}).items():
        if state["etat"] != "not_reported":
            continue
        if any(other.get(column) is not None for other in others):
            served["etats"][column] = {
                "etat": "not_reported",
                "motif": motif,
                "publiee_a": mesh,
            }
    return served
