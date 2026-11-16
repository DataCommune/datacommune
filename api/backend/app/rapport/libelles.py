"""Wording of the report served by the API (04.7-17), ported from the front.

Until 04.7-17 the front worded what the API served (``adresse-rapport.ts``:
``nombreFr``, ``dateFr``, ``distanceFr``, ``libelleCode``, ``statutDe``,
``valeurChiffre``). The API now serves the words, with the same rules, so
that the front reads them instead of computing them (UI-SPEC § 7). A code
the table does not know is made readable, never dropped.
"""

import math
import re
from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.rapport.schemas import Chiffre

NBSP = "\u00a0"
NNBSP = "\u202f"
MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def arrondi(n: float | Decimal, decimales: int) -> Decimal:
    """Half up on the decimal writing, as ``Intl.NumberFormat`` rounds:
    14.35 is 14.4, where ``round`` works on the binary float and gives 14.3."""
    pas = Decimal(1).scaleb(-decimales)
    return Decimal(str(n)).quantize(pas, rounding=ROUND_HALF_UP)


def nombre_fr(n: float, decimales: int | None = None) -> str:
    """French number as ``Intl.NumberFormat('fr-FR')`` writes it: thousands
    split by a narrow no-break space, a decimal comma; ``decimales`` fixes
    the digits, otherwise up to two and never padded (98,5, not 98,50)."""
    valeur = arrondi(n, 2 if decimales is None else decimales)
    texte = f"{valeur:,f}"
    entier, _, fraction = texte.partition(".")
    if decimales is None:
        fraction = fraction.rstrip("0")
    entier = entier.replace(",", NNBSP)
    return f"{entier},{fraction}" if fraction else entier


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def date_fr(iso: str) -> str:
    """« 26 août 2026 », « août 2026 »; a text that is not a date as is
    (« 2026-W01-1 » included, which Python would read as a date)."""
    try:
        jour = date.fromisoformat(iso) if _ISO_DATE.fullmatch(iso) else None
    except ValueError:
        jour = None
    if jour is not None:
        quantieme = "1er" if jour.day == 1 else str(jour.day)
        return f"{quantieme} {MOIS[jour.month - 1]} {jour.year}"
    annee, _, mois = iso.partition("-")
    if len(iso) == 7 and annee.isdigit() and mois.isdigit() and 1 <= int(mois) <= 12:
        return f"{MOIS[int(mois) - 1]} {annee}"
    return iso


def distance_fr(m: float) -> str:
    if m >= 1000:
        # A whole number of kilometres has no « ,0 », as in ``distanceFr``.
        return f"{nombre_fr(m / 1000, 0 if m % 1000 == 0 else 1)}{NBSP}km"
    # Math.round of the front: half towards +∞ (-2.5 m is « -2 m »).
    return f"{math.floor(m + 0.5)}{NBSP}m"


# Reasons worded for a reader (the front table of 04.7-03, as is; the
# typographic apostrophe is written as an escape, ruff RUF001).
MOTIFS: dict[str, str] = {
    "dpe_plusieurs_batiments": "Ces DPE peuvent venir de plusieurs bâtiments",
    "plusieurs_batiments_sur_la_parcelle": (
        "Plusieurs bâtiments sur la parcelle : celui de l\u2019adresse "
        "n\u2019est pas établi"
    ),
    "plusieurs_batiments_rnb_a_l_adresse": (
        "Plusieurs bâtiments du registre national portent cette adresse"
    ),
    "point_hors_parcelle": "Le point de l\u2019adresse ne tombe dans aucune parcelle",
    "parcelle_indeterminee": "La parcelle de l\u2019adresse n\u2019est pas déterminée",
    "parcelles_multiples": "Plusieurs parcelles possibles",
    "parcelle_non_verifiee": "Parcelle non vérifiée",
    "position_indeterminee": "La position de l\u2019adresse n\u2019est pas déterminée",
    "liens_rnb_indisponibles": "Liens au registre des bâtiments indisponibles",
    "adresse_non_verifiable": "Adresse déclarée non vérifiable",
    "meme_annee_pour_tous": "Même année pour tous les bâtiments du groupe",
    "aucun_batiment_au_point": "Aucun bâtiment au point",
    "aucun_batiment_bdnb": "Aucun bâtiment connu de la BDNB",
    "annee_non_renseignee": "Année non renseignée",
    "aucun_document_publie": "Aucun document publié",
    "aucun_document_valide": "Aucun document valide",
    "aucune_offre_publiee": "Aucune offre publiée",
    "amont_indisponible": "La source n\u2019a pas répondu",
    "lecture_indisponible": "Lecture indisponible",
    # A source imported, but not (yet) for the departement of the address
    # (04.7-19): an outage, never « aucun bâtiment ».
    "import_absent": "Données pas encore importées pour ce département",
    "temporary_error": "La source n\u2019a pas répondu",
    "theme_absent": "Thème non servi",
    "hors_couverture": "Hors couverture",
    "out_of_coverage": "Hors couverture",
    "not_available": "Non publiée",
    "not_reported": "Non renseigné",
    "insufficient_sample": "Effectif insuffisant",
    "zero_denominator": "Sans base de calcul",
    "not_applicable": "Sans objet",
    "not_qualified": "Non qualifié",
    "suppressed": "Secret statistique",
    "loyer_non_publie": "Loyer non publié",
    "revenu_non_publie": "Revenu non publié",
    "taux_non_publie": "Taux non publié",
    "zone_non_publiee": "Zone non publiée",
    "categorie_non_couverte": "Catégorie non couverte",
    "categories_non_couvertes": "Catégories non couvertes",
    "commune_errial_differente": "L\u2019état des risques porte sur une autre commune",
    "parcelle_errial_absente": "L\u2019état des risques ne cite pas de parcelle",
    "parcelle_errial_differente": (
        "L\u2019état des risques porte sur une autre parcelle"
    ),
    "poids_incomplets": "Pondération incomplète",
    "zones_multiples": "Plusieurs zones au point",
    "indeterminee": "indéterminé",
    "unique": "unique",
}


def humaniser(code: str) -> str:
    texte = code.replace("_", " ").strip()
    return texte[:1].upper() + texte[1:]


def libelle_code(code: str) -> str:
    """« plusieurs_batiments_sur_la_parcelle » → its sentence, or the code
    made readable."""
    return MOTIFS.get(code) or humaniser(code)


LIBELLE_STATUT = {
    "vigilance": "Vigilance",
    "rassurant": "Favorable",
    "zero": "Zéro",
    "manque": "Non publié",
    "hors_couverture": "Hors couverture",
    "panne": "Source indisponible",
    "non_concerne": "Sans objet",
    "en_cours": "Lecture en cours",
}
# A reason short enough to be a badge; a longer one is said « Non publié ».
BADGE_MAX = 24
LIBELLES_MOTIFS_COURTS = {
    "point_hors_parcelle": "Non rattaché",
    "parcelle_non_verifiee": "Non rattaché",
}


def libelle_court_motif(motif: str) -> str:
    if motif in LIBELLES_MOTIFS_COURTS:
        return LIBELLES_MOTIFS_COURTS[motif]
    libelle = libelle_code(motif)
    return libelle if len(libelle) <= BADGE_MAX else LIBELLE_STATUT["manque"]


# Singular of the plural labels a headline writes after their count: the
# detail keeps the label as is (« Bâtiments sur la parcelle : 1 »).
SINGULIERS = {
    "Arrêtés CatNat": "Arrêté CatNat",
    "Bâtiments sur la parcelle": "Bâtiment sur la parcelle",
    "Cavités à 500 m": "Cavité à 500 m",
    "Commerces": "Commerce",
    "DPE déclarés": "DPE déclaré",
    "Installations classées": "Installation classée",
    "Lots": "Lot",
    "Mouvements de terrain à 500 m": "Mouvement de terrain à 500 m",
    "Prescriptions au point": "Prescription au point",
    "Risques recensés": "Risque recensé",
    "Sites Seveso à 5 km": "Site Seveso à 5 km",
    "Sites sur la parcelle": "Site sur la parcelle",
    "Supports 5G": "Support 5G",
    "Ventes sur la parcelle": "Vente sur la parcelle",
}


def _texte_js(v: float) -> str:
    """``String(v)`` of the front: 2020.0 is « 2020 »."""
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


# Writing of a numeric figure by unit, as the detail shows it.
_UNITES: dict[str, Callable[[float], str]] = {
    "count": lambda v: nombre_fr(v, 0),
    "euro_per_m2": lambda v: f"{nombre_fr(v, 0)}{NBSP}€/m²",
    # Served with one decimal (``themes/loyers.py``), never padded.
    "euro_per_m2_mois": lambda v: f"{nombre_fr(v)}{NBSP}€/m²/mois",
    "euro_par_an": lambda v: f"{nombre_fr(v, 0)}{NBSP}€/an",
    "pourcent": lambda v: f"{nombre_fr(v)}{NBSP}%",
    "pour_mille": lambda v: f"{nombre_fr(v)}{NBSP}‰",
    "degre_francais": lambda v: f"{nombre_fr(v)}{NBSP}°f",
    "indice_sur_6": lambda v: f"{nombre_fr(v, 0)}{NBSP}sur{NBSP}6",
    "metre": distance_fr,
    "m2": lambda v: f"{nombre_fr(v, 0)}{NBSP}m²",
    "annee": lambda v: _texte_js(v),
    "classe_dpe": lambda v: _texte_js(v),
    "none": nombre_fr,
}


def valeur_chiffre(c: Chiffre) -> str:
    """A figure's value with its unit, as the detail shows it (no label)."""
    v = c.valeur
    if isinstance(v, str):
        return date_fr(v) if c.unite == "date" else v
    ecrire = _UNITES.get(c.unite)
    if ecrire is not None:
        return ecrire(v)
    return f"{nombre_fr(v)}{NBSP}{c.unite}" if c.unite else nombre_fr(v)
