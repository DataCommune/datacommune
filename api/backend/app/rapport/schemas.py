"""Format of the address report (04.7-01): one shape for every theme.

A theme is one row of the address page: a title, a verdict of one sentence,
a badge (``etat`` and ``ton``), at most three highlighted figures and its
folded details. Every theme says its real granularity, how the entity it
describes is attached to the address and the sources it read, each with its
millésime and its mode (read live or imported).

Six states, never merged (CONTEXT, « Invariants »): an answer (``ok``), a
measured absence (``zero``), a gap (``manque``), a territory or source the
instance does not host (``hors_couverture``), an outage (``panne``) and « not
concerned » (``non_concerne``). An outage is never served as a zero.

A seventh state, ``en_cours``, is served only to a caller that asked for a
short wait (``attente=courte``, R-07): the upstream read of the theme has not
ended yet. It is neither an outage nor a gap, and it never lasts: the same
report asked with ``attente=complete`` carries the answer or the outage.

The model refuses a valence without an answer: ``manque``,
``hors_couverture``, ``panne`` and ``en_cours`` carry the tone ``info`` and no figure,
``info`` belongs to them only, and ``non_concerne`` is never a point to
check. A theme that answered while one of its sub-sources was down names
it in ``pannes_partielles``: an incomplete answer never reassures.
"""

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.adresse.schemas import CouvertureAdresse, IdentiteAdresse

Section = Literal["marche", "risques", "batiment", "vivre"]
EtatTheme = Literal[
    "ok", "zero", "manque", "hors_couverture", "panne", "non_concerne", "en_cours"
]
Ton = Literal["vigilance", "rassurant", "neutre", "info"]
# ``epci``: the intercommunality a value is published for (04.7-07);
# ``arrondissement``: Paris, Lyon or Marseille, read at the arrondissement
# (04.7-09).
NiveauGranularite = Literal[
    "adresse",
    "batiment",
    "parcelle",
    "rayon",
    "iris",
    "arrondissement",
    "commune",
    "epci",
    "departement",
]
Confiance = Literal["sure", "probable", "incertaine"]
ModeSource = Literal["direct", "import"]
IdChiffreCle = Literal[
    "prix_m2_median", "loyer_m2", "dpe_dominant", "annee_construction"
]

SECTIONS: tuple[Section, ...] = ("marche", "risques", "batiment", "vivre")
ETATS_SANS_REPONSE: frozenset[str] = frozenset(
    {"manque", "hors_couverture", "panne", "en_cours"}
)
ETATS_SERVIS: frozenset[str] = frozenset({"ok", "zero"})
VERDICT_MAX = 140
ACCROCHE_MAX = 40
CHIFFRES_MAX = 3


class ArrondissementRef(BaseModel):
    """An arrondissement of Paris, Lyon or Marseille (UI-SPEC § 7)."""

    code: str
    nom: str


class Granularite(BaseModel):
    """Real scale of a value; a radius carries its length in metres, an
    arrondissement its code and name."""

    niveau: NiveauGranularite
    rayon_m: int | None = None
    arrondissement: ArrondissementRef | None = None

    @model_validator(mode="after")
    def _rayon(self) -> "Granularite":
        if (self.niveau == "rayon") != (self.rayon_m is not None):
            raise ValueError("rayon_m is required for a radius, and only for it")
        if (self.niveau == "arrondissement") != (self.arrondissement is not None):
            raise ValueError(
                "arrondissement is required for its level, and only for it"
            )
        return self


class Chiffre(BaseModel):
    """One highlighted figure, with its unit and its millésime when known."""

    id: str
    libelle: str
    valeur: int | float | str
    unite: str
    millesime: str | None = None


class Maillon(BaseModel):
    """One link of the chain from the address to the entity a theme
    describes, with its confidence (UI-SPEC § 7)."""

    lien: str
    confiance: Confiance


class Rattachement(BaseModel):
    """How the entity a theme describes is attached to the address.

    ``entite`` names the rule (``parcelle_du_point``,
    ``adresse_saisie_diagnostiqueur``...), ``cle`` the retained identifier,
    ``candidats`` the number of candidates when there were several.
    """

    entite: str
    cle: str | None = None
    confiance: Confiance
    candidats: int | None = None
    motif: str | None = None
    # 04.7-17 (UI-SPEC § 7): the alert line, not null whenever the
    # confidence is not ``sure``, and the links; their minimum is
    # ``confiance``. One link until a theme declares its chain.
    alerte: str | None = None
    maillons: list[Maillon] = Field(default_factory=list)


class SourceTheme(BaseModel):
    """A source read by a theme: ``direct`` (called live) or ``import``."""

    cle: str
    nom: str
    producteur: str
    url: str
    licence: str
    millesime: str | None = None
    mode: ModeSource


class Theme(BaseModel):
    """One criterion of the report, in the shape shared by every theme."""

    id: str
    section: Section
    titre: str
    etat: EtatTheme
    ton: Ton
    granularite: Granularite
    verdict: str = Field(min_length=1, max_length=VERDICT_MAX)
    chiffres: list[Chiffre] = Field(default_factory=list, max_length=CHIFFRES_MAX)
    details: dict[str, Any] = Field(default_factory=dict)
    rattachement: Rattachement | None = None
    sources: list[SourceTheme] = Field(min_length=1)
    motif: str | None = None
    pannes_partielles: list[str] = Field(default_factory=list)
    # 04.7-17 (UI-SPEC § 7): the headline of the row (the first figure in
    # words, or the word of the state) and the reason of a gap, worded.
    accroche: str | None = Field(default=None, max_length=ACCROCHE_MAX)
    etat_libelle: str | None = None

    @model_validator(mode="after")
    def _ton_de_l_etat(self) -> "Theme":
        sans_reponse = self.etat in ETATS_SANS_REPONSE
        if sans_reponse and (self.ton != "info" or self.chiffres):
            raise ValueError(f"{self.etat} carries the tone info and no figure")
        if self.ton == "info" and not sans_reponse:
            raise ValueError("the tone info belongs to the states without answer")
        if self.etat == "non_concerne" and self.ton == "vigilance":
            raise ValueError("a theme that does not concern the address is no point")
        if self.pannes_partielles and self.ton == "rassurant":
            raise ValueError("an answer with a sub-source down never reassures")
        return self


class PointResume(BaseModel):
    """A theme named by the summary: its id, title, verdict and scale."""

    theme: str
    titre: str
    verdict: str
    granularite: Granularite


class ChiffreCle(BaseModel):
    """One of the four key figures, read from one theme by figure ids."""

    id: IdChiffreCle
    libelle: str
    theme: str
    etat: EtatTheme
    granularite: Granularite | None = None
    chiffres: list[Chiffre] = Field(default_factory=list, max_length=2)
    motif: str | None = None


class ParSection(BaseModel):
    """Points to check, outages, criteria that answered and criteria of one
    section."""

    vigilance: int
    pannes: int
    lus: int
    total: int


class ResumeRapport(BaseModel):
    """Points to check and reassuring points, split between the address
    (up to a radius of 1 km) and the area or commune; outages and the
    sources that did not answer; criteria that do not concern the address;
    counts by section; and the four key figures, in a fixed order.

    ``rassurants`` is both reassuring lists together, kept while the front
    reads it (04.7-17).
    """

    points_adresse: list[PointResume]
    points_commune: list[PointResume]
    rassurants: list[PointResume]
    rassurants_adresse: list[PointResume] = Field(default_factory=list)
    rassurants_commune: list[PointResume] = Field(default_factory=list)
    pannes: list[PointResume] = Field(default_factory=list)
    sources_en_panne: list[str] = Field(default_factory=list)
    non_concernes: list[PointResume] = Field(default_factory=list)
    # Served themes with a sub-source down; gaps and out of coverage.
    partiels: list[PointResume] = Field(default_factory=list)
    sans_valeur: list[PointResume] = Field(default_factory=list)
    # Themes whose upstream read has not ended (``attente=courte`` only):
    # counted neither as read, nor as outages, nor as gaps.
    en_cours: list[PointResume] = Field(default_factory=list)
    # Criteria that carry an answer (served, zero, or not applicable), all,
    # then at the address and for the area or commune.
    lus: int = 0
    lus_adresse: int = 0
    lus_secteur: int = 0
    total_criteres: int = 0
    par_section: dict[Section, ParSection] = Field(default_factory=dict)
    chiffres_cles: list[ChiffreCle] = Field(min_length=4, max_length=4)


class Position(BaseModel):
    """The BAN point of the address (WGS84), already public in the BAN."""

    lon: float
    lat: float


class ImagesCarte(BaseModel):
    plan: str
    parcelles: str


class CarteEntete(BaseModel):
    """The static map of the head (04.7-17): two images of the IGN
    Géoplateforme centred on the point, served by a cached proxy on the
    site's own origin, so that no address of the visitor reaches the IGN."""

    images: ImagesCarte
    emprise_m: int
    attribution: str
    licence: str
    geoportail: str


class RapportAdresse(BaseModel):
    """``GET /v1/adresse/{ban_id}/rapport``: the address, its summary, its themes.

    ``date`` is the day the report is computed for (Paris): the day of the
    live calls and of the document validities.
    """

    adresse: IdentiteAdresse
    couverture: CouvertureAdresse
    date: dt.date
    resume: ResumeRapport
    themes: list[Theme]
    # 04.7-17: the attachment sentence of the head, the BAN point and the
    # map; none without a point (the identity of other routes stays
    # without coordinate).
    rattachement_phrase: str | None = None
    position: Position | None = None
    carte: CarteEntete | None = None
    # Ids of the themes served ``en_cours``; empty for a complete report.
    lectures_en_cours: list[str] = Field(default_factory=list)
