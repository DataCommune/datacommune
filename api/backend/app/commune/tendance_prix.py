"""Price trend of a commune over the closed years of the DVF delivery.

Decision of Ianis of 2026-11-11. Pure: the rows of ``v_commune_dvf_annuel``
(``app.commune.matview_ddl``) come in, the served block comes out; no I/O.

What the block says: the median price per square metre of what was sold each
year, houses and flats apart. What it never says: the change of one dwelling,
nor a forecast -- a median follows the mix of the sales of the year.

Publication rule, measured in
internal note 2026-11-11-tendance-prix-mesure:

- ``evolution``: the first and the last year of the window each hold at least
  ``SEUIL_EVOLUTION_COMMUNE`` retained sales; the change between their medians
  is written;
- ``points``: at least two years have a median (``SEUIL_ND_COMMUNE`` sales, the
  view serves none under it): the medians are served, no change is written;
- ``echantillon_insuffisant``: fewer than two years have a median.

A year without a row is a true zero of the retained sales, in a commune whose
DVF source is hosted (the field is null otherwise, see ``couverture``).
"""

from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.dvf.couverture import SEUIL_EVOLUTION_COMMUNE, SEUIL_ND_COMMUNE

# Class of ``mutations_grain`` -> served type, houses first on equal counts.
_TYPES: dict[str, str] = {
    "residentiel_maison": "maison",
    "residentiel_appartement": "appartement",
}


class PrixAnnuel(BaseModel):
    """One closed year of the series."""

    model_config = ConfigDict(extra="forbid")

    annee: int = Field(description="Année civile close.")
    prix_m2_median: int | None = Field(
        description=(
            "Prix médian au m² des ventes retenues de l'année, en euros ; null "
            "sous le seuil seuil_point (la médiane n'est pas publiée)."
        )
    )
    nb_ventes: int = Field(
        description="Ventes retenues de l'année, effectif réel ; 0 : aucune."
    )


class TendancePrixType(BaseModel):
    """The series of one type of dwelling."""

    model_config = ConfigDict(extra="forbid")

    type_bien: Literal["maison", "appartement"]
    etat: Literal["evolution", "points", "echantillon_insuffisant"] = Field(
        description=(
            "evolution : la première et la dernière année ont chacune au moins "
            "seuil_evolution ventes, evolution_pct est servi. points : au moins "
            "deux années ont une médiane, sans évolution chiffrée. "
            "echantillon_insuffisant : moins de deux années ont une médiane."
        )
    )
    evolution_pct: float | None = Field(
        description=(
            "Évolution en pourcentage entre la médiane de annee_debut et celle "
            "de annee_fin ; null hors de l'état evolution. Médiane de ce qui "
            "s'est vendu chaque année : ni l'évolution d'un bien, ni une prévision."
        )
    )
    nb_ventes: int = Field(description="Ventes retenues du type sur toute la période.")
    annees: list[PrixAnnuel] = Field(
        description="Une ligne par année de la période, dans l'ordre."
    )


class TendancePrix(BaseModel):
    """Yearly median prices of a commune over the closed years of DVF."""

    model_config = ConfigDict(extra="forbid")

    annee_debut: int = Field(description="Première année close de la période.")
    annee_fin: int = Field(description="Dernière année close de la période.")
    seuil_evolution: int = Field(
        description=(
            "Ventes retenues exigées sur la première et sur la dernière année "
            "pour chiffrer une évolution."
        )
    )
    seuil_point: int = Field(
        description="Ventes retenues exigées sur une année pour publier sa médiane."
    )
    types: list[TendancePrixType] = Field(
        description="Maisons et appartements, le type le plus vendu d'abord."
    )


class _Annee(TypedDict):
    annee: int
    prix_m2_median: int | None
    nb_ventes: int


def _annee(annee: int, ligne: Mapping[str, Any] | None) -> _Annee:
    """One year of the window; a year without a row is a true zero."""
    if ligne is None:
        return {"annee": annee, "prix_m2_median": None, "nb_ventes": 0}
    mediane = ligne.get("prix_m2_median")
    return {
        "annee": annee,
        "prix_m2_median": None if mediane is None else int(mediane),
        "nb_ventes": int(ligne.get("nb_ventes") or 0),
    }


def _evolution_pct(premiere: _Annee, derniere: _Annee) -> float | None:
    """Change between two yearly medians, or None when it must not be written."""
    debut, fin = premiere["prix_m2_median"], derniere["prix_m2_median"]
    if debut is None or fin is None or debut <= 0:
        return None
    if premiere["annee"] >= derniere["annee"]:
        return None
    if min(premiere["nb_ventes"], derniere["nb_ventes"]) < SEUIL_EVOLUTION_COMMUNE:
        return None
    return round((fin - debut) * 100 / debut, 1)


def _type_block(
    type_bien: str, debut: int, fin: int, par_annee: Mapping[int, Mapping[str, Any]]
) -> dict[str, Any]:
    annees = [_annee(annee, par_annee.get(annee)) for annee in range(debut, fin + 1)]
    evolution = _evolution_pct(annees[0], annees[-1])
    if evolution is not None:
        etat = "evolution"
    elif sum(a["prix_m2_median"] is not None for a in annees) >= 2:
        etat = "points"
    else:
        etat = "echantillon_insuffisant"
    return {
        "type_bien": type_bien,
        "etat": etat,
        "evolution_pct": evolution,
        "nb_ventes": sum(a["nb_ventes"] for a in annees),
        "annees": annees,
    }


def build_tendance_prix(
    fenetre: tuple[int, int], lignes: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """The served block of one commune from its rows of the yearly view.

    *fenetre* is the first and last closed year the view holds, the same for
    every commune (an empty view has none: its caller serves the declared
    absence instead). Rows of another class or outside the window are ignored.
    """
    debut, fin = fenetre
    par_type: dict[str, dict[int, Mapping[str, Any]]] = {t: {} for t in _TYPES.values()}
    for ligne in lignes:
        type_bien = _TYPES.get(ligne["classe_mutation"])
        annee = ligne["annee"]
        if type_bien is not None and isinstance(annee, int) and debut <= annee <= fin:
            par_type[type_bien][annee] = ligne
    types = [
        _type_block(type_bien, debut, fin, par_annee)
        for type_bien, par_annee in par_type.items()
    ]
    # Stable sort: houses stay first on equal counts.
    types.sort(key=lambda t: -t["nb_ventes"])
    return {
        "annee_debut": debut,
        "annee_fin": fin,
        "seuil_evolution": SEUIL_EVOLUTION_COMMUNE,
        "seuil_point": SEUIL_ND_COMMUNE,
        "types": types,
    }
