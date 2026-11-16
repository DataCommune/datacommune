"""The five states an agent receives, imposed by the server (D-143, D-05/D-06).

Every value a tool returns is a ``Mesure``: its field, one state among
``servi``, ``zero``, ``manquant``, ``hors_couverture``, ``panne`` (invariant
I-4: zero, not covered, not reported and outage are distinct; Ianis,
2026-11-15, « Cinq, comme I-4 »), its source, its date and its scale. A value
exists in ``servi`` and ``zero`` only; an absence carries no value at all (the
key is omitted, not null) and says why. The /v1 states map onto these five in
``projeter_etat`` and nowhere else; a /v1 value whose source or
scale cannot be established is set aside (``ValeurEcartee``), never returned.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.semantic_contract import FieldContract, load_registry
from app.common.source_catalog import DATASET_CATALOG

EtatMcp = Literal["servi", "zero", "manquant", "hors_couverture", "panne"]
Echelle = Literal[
    "adresse",
    "batiment",
    "parcelle",
    "rayon",
    "commune",
    "arrondissement",
    "departement",
]

DATE_INCONNUE = "inconnue"

# /v1 absence state -> reason of a ``manquant``. The outage (temporary_error) is
# not one of them: it is the state ``panne`` of its own (invariant I-4).
MOTIFS_DU_MANQUE: dict[str, str] = {
    "not_reported": "non_renseigne",
    "not_available": "non_disponible",
    "insufficient_sample": "effectif_insuffisant",
    "zero_denominator": "sans_base_de_calcul",
    "suppressed": "secret_statistique",
    "not_qualified": "non_qualifie",
    "not_applicable": "sans_objet",
}
MOTIF_ZERO_NON_PLAUSIBLE = "zero_non_plausible"
MOTIF_RAISON_NON_DECLAREE = "raison_non_declaree"
# Reason of a ``panne``: the source could not be read; asking again later may work.
MOTIF_PANNE = "source_indisponible"

# Attachment of a value (closed list of fiche-fields) -> its scale.
ECHELLE_DU_RATTACHEMENT: dict[str, Echelle] = {
    "adresse_saisie_diagnostiqueur": "adresse",
    "adresse_declaree_vente": "adresse",
    "adresse_brute_exacte": "adresse",
    "eligibilite_adresse": "adresse",
    "au_point": "adresse",
    "parcelle_du_point": "parcelle",
    "batiment_du_point": "batiment",
    "batiment_rnb_de_l_adresse": "batiment",
    "rayon_du_point": "rayon",
    "commune_de_l_adresse": "commune",
    "commune_reference": "commune",
    "departement": "departement",
}

REGLE_DE_LECTURE = (
    "Chaque valeur porte un état : « servi » (une valeur mesurée), « zero » (une "
    "valeur mesurée égale à zéro), « manquant » (aucune valeur : non renseignée, "
    "effectif insuffisant, sans objet… selon « motif »), « hors_couverture » (la "
    "source ne couvre pas ce territoire sur cette instance), « panne » (la source "
    "n'a pas pu être lue cette fois ; redemander plus tard). Seuls « servi » et "
    "« zero » ont une clé « valeur » ; un « manquant », un « hors_couverture » ou "
    "une « panne » ne vaut jamais zéro et ne doit pas être présenté comme tel, ni "
    "être confondu avec un autre de ces états. Chaque valeur "
    "dit aussi sa source, sa date (« inconnue » quand elle l'est) et son "
    "échelle : adresse, bâtiment, parcelle, rayon autour de l'adresse, commune, "
    "arrondissement ou département."
)


class ValeurEcartee(Exception):  # noqa: N818 -- a set-aside value, not an error
    """A /v1 value that cannot be returned as a ``Mesure``."""

    def __init__(self, raison: str) -> None:
        super().__init__(raison)
        self.raison = raison


class SourceMcp(BaseModel):
    """Producer of a value, from the source catalogue."""

    model_config = ConfigDict(extra="forbid")

    cle: str = Field(description="Clé du jeu de données dans le catalogue.")
    nom: str
    producteur: str
    url: str
    licence: str | None = None


class ElementMcp(BaseModel):
    """One item of a served list (a stop, a risk, a zone), as /v1 serves it."""

    model_config = ConfigDict(extra="forbid")

    libelle: str
    distance_m: int | None = Field(
        default=None, description="Distance à vol d'oiseau au point de l'adresse."
    )
    nature: str | None = None


class Mesure(BaseModel):
    """One value with its state, source, date and scale (D-05, D-06)."""

    model_config = ConfigDict(extra="forbid")

    champ: str = Field(description="Identifiant du champ (contrat fiche-fields).")
    cle: str | None = Field(
        default=None, description="Colonne de la réponse /v1 qui porte la valeur."
    )
    definition: str | None = None
    etat: EtatMcp
    valeur: bool | int | float | str | None = Field(
        default=None,
        description="Présente pour « servi » et « zero » seulement.",
    )
    unite: str | None = None
    motif: str | None = Field(
        default=None,
        description="Pour « manquant », « hors_couverture » et « panne » : pourquoi.",
    )
    precision: str | None = Field(
        default=None, description="Phrase de la source sur la cause ou la limite."
    )
    echelle: Echelle
    rayon_m: int | None = None
    source: SourceMcp
    date: str = Field(
        min_length=1,
        description="Millésime ou période de la valeur, « inconnue » si elle l'est.",
    )
    elements: list[ElementMcp] | None = None
    liste_incomplete: bool | None = None

    @model_validator(mode="after")
    def cinq_etats(self) -> Self:
        """A value exists in servi and zero only; an absence says why."""
        porte_une_valeur = self.etat in ("servi", "zero")
        if porte_une_valeur != (self.valeur is not None):
            raise ValueError("a value exists in the states servi and zero only")
        if (self.etat == "zero") != (self.valeur is not None and est_zero(self.valeur)):
            raise ValueError("zero is the state of a served value equal to 0")
        if (self.motif is not None) == porte_une_valeur:
            raise ValueError("a reason qualifies an absence, and every absence")
        if not porte_une_valeur and self.elements:
            raise ValueError("an absence lists no item")
        return self


class Ecartee(BaseModel):
    """A /v1 value set aside: named, never returned as a value."""

    model_config = ConfigDict(extra="forbid")

    champ: str
    raison: str


def est_zero(valeur: object) -> bool:
    """A number equal to 0 (a boolean is never a zero)."""
    return (
        isinstance(valeur, int | float) and not isinstance(valeur, bool) and valeur == 0
    )


def contrat_du_champ(champ: str) -> FieldContract | None:
    """The fiche-fields entry of *champ*, None when the contract has none."""
    return load_registry().get(champ)


def source_du_catalogue(cle: str | None) -> SourceMcp | None:
    """The catalogue entry *cle* as a ``SourceMcp``; None when unknown."""
    if not cle:
        return None
    entree = DATASET_CATALOG.get(cle)
    if entree is None:
        return None
    return SourceMcp(
        cle=cle,
        nom=entree.nom,
        producteur=entree.producteur,
        url=entree.url,
        licence=entree.licence,
    )


def echelle_du_rattachement(rattachement: str | None, champ: str) -> Echelle | None:
    """Scale of a value: its own attachment, else the one of its contract."""
    if rattachement is None:
        contrat = contrat_du_champ(champ)
        rattachement = contrat.rattachement if contrat else None
    return ECHELLE_DU_RATTACHEMENT.get(rattachement) if rattachement else None


def projeter_etat(
    etat_v1: str | None,
    valeur: object,
    motif_v1: str | None,
    contrat: FieldContract | None,
) -> tuple[EtatMcp, object, str | None, str | None]:
    """(state, value, reason, precision) of a /v1 value; the one mapping (D-05).

    ``etat_v1`` is the /v1 state (``value``, an absence, or None when /v1
    serves a null without declaring why). A value served by /v1 next to an
    absence state is dropped: the state, not the number, is the truth.
    """
    if etat_v1 == "value":
        if valeur is None:
            raise ValeurEcartee("valeur_absente_pour_l_etat_value")
        if not isinstance(valeur, bool | int | float | str):
            raise ValeurEcartee("valeur_non_scalaire")
        if not est_zero(valeur):
            return "servi", valeur, None, motif_v1
        if contrat is not None and contrat.zero_semantics == "implausible_zero":
            return "manquant", None, MOTIF_ZERO_NON_PLAUSIBLE, motif_v1
        return "zero", valeur, None, motif_v1
    if etat_v1 == "out_of_coverage":
        return "hors_couverture", None, motif_v1 or "non_precise", None
    if etat_v1 == "temporary_error":
        return "panne", None, MOTIF_PANNE, motif_v1
    if etat_v1 in MOTIFS_DU_MANQUE:
        return "manquant", None, MOTIFS_DU_MANQUE[etat_v1], motif_v1
    if etat_v1 is None:
        return "manquant", None, MOTIF_RAISON_NON_DECLAREE, motif_v1
    raise ValeurEcartee(f"etat_inconnu:{etat_v1}")


def mesure(
    *,
    champ: str,
    etat_v1: str | None,
    valeur: object,
    motif_v1: str | None,
    source: SourceMcp | None,
    date: str | None,
    echelle: Echelle | None,
    unite: str | None = None,
    rayon_m: int | None = None,
    elements: list[ElementMcp] | None = None,
    liste_incomplete: bool = False,
) -> Mesure:
    """A /v1 value as a ``Mesure``; set aside when source or scale is unknown."""
    if source is None:
        raise ValeurEcartee("source_inconnue")
    if echelle is None:
        raise ValeurEcartee("echelle_inconnue")
    contrat = contrat_du_champ(champ)
    etat, valeur, motif, precision = projeter_etat(etat_v1, valeur, motif_v1, contrat)
    servie = etat in ("servi", "zero")
    return Mesure(
        champ=champ,
        definition=contrat.description if contrat else None,
        etat=etat,
        valeur=valeur if servie else None,  # type: ignore[arg-type]
        unite=unite or (contrat.unit if contrat else None),
        motif=motif,
        precision=precision,
        echelle=echelle,
        rayon_m=rayon_m,
        source=source,
        date=date or DATE_INCONNUE,
        elements=elements if servie and elements else None,
        liste_incomplete=True if servie and liste_incomplete else None,
    )
