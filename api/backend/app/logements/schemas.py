"""Pydantic response schemas for the non-attributive logement context.

No monetary field (sale amount, asking price, transaction cost) exists
anywhere in this module OUTSIDE :class:`VenteImmeuble`, which carries the
price of a MUTATION, always labelled by its class -- Phase 57 D-17, which
knowingly revises the blanket interdiction of D-16 / addendum 48-04
(sign-off Ianis). A logement CARD still never carries an amount: that half
of the guarantee is unchanged and is what the CI gate keeps enforcing (see
``tests/test_logements_schemas.py::TestNoPriceField``). Truth vocabulary
(presence/absence_reason) is imported from ``app.resolve.schemas`` and
reused verbatim -- no new word of truth is introduced here (D-04 doctrine,
same rule as resolve).

The exact-BAN inventory is a document collection, never a list of dwellings.
``ResultatLogements`` therefore exposes the shared inventory and V2 attribution
contracts while making every legacy logement-card projection unconstructible.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dpe.schemas import DpeAttribution, DpeInventoryPage
from app.etl_dvf.qualification import CLASSES_MUTATION, RAISONS_PRIX_M2
from app.resolve.schemas import RESOLUTION_RAISONS

Presence = Literal["value", "absent", "error"]
Mode = Literal["immeuble", "indice", "bien"]


class SignalDetection(BaseModel):
    """One MVP detection signal -- copro/DPE/DVF (D-01/D-03).

    ``presence``/``absence_reason`` reuse the resolve taxonomy verbatim
    (``app.resolve.schemas.RESOLUTION_RAISONS``): ``absence_reason`` must be
    one of its keys, never a new invented word of truth.
    """

    positif: bool
    presence: Presence
    absence_reason: str | None = None
    compte: int

    @model_validator(mode="after")
    def _validate_absence_reason(self) -> "SignalDetection":
        if (
            self.absence_reason is not None
            and self.absence_reason not in RESOLUTION_RAISONS
        ):
            raise ValueError(
                f"absence_reason {self.absence_reason!r} is not part of the "
                "resolve taxonomy (app.resolve.schemas.RESOLUTION_RAISONS)"
            )
        return self


class BlocDetection(BaseModel):
    """3-signal detection block + verdict (D-01/D-02/D-03, ADR-0006).

    ``preuve_identites`` (ADR-0006) is True when >=2 distinct structured
    card identities -- at least one carrying a porte/batiment token --
    promoted the verdict to ``immeuble`` on the DPE proof alone. It is the
    ONLY legitimate way ``mode="immeuble"`` can coexist with
    ``signaux_positifs < 2``: the field keeps that combination auditable
    in the payload instead of looking like a detection bug (review
    2026-08-04, ticket T2).
    """

    signaux: dict[str, SignalDetection]
    signaux_positifs: int
    mode: Mode
    preuve_identites: bool = False


class Denominateur(BaseModel):
    """Honest denominator (D-03/D-07).

    ``lots_declares`` is preserved verbatim -- ``None`` stays ``None``, never
    coerced to 0: the "nombre total de lots non publie" copy variant depends
    on this (see ``detection.construit_denominateur``).
    """

    logements_connus: int
    lots_declares: int | None = None
    non_distinguables: int


class VenteImmeuble(BaseModel):
    """One DVF MUTATION observed on the parcel, priced and labelled (D-17).

    Purely informational: section 2 of the interstitiel never asserts a link
    between a sale row and a logement card (D-11), and a mutation merely
    compatible with the parcel is never « the sale of this dwelling » nor its
    certain price (D-37). This is the parcel's transaction history.

    WHY A PRICE IS BACK HERE. Phase 48 (D-16) forbade any amount in this
    module, and it was right to: the row was then a DVF LINE, so the
    mutation's total ``valeur_fonciere`` appeared beside a SINGLE local's
    surface -- a figure that is simply false on every multi-local sale. Phase
    57 removed that cause by reconstructing the mutation
    (``mutations_grain``): the amount below is the amount actually paid for
    what ``classe_mutation`` names.

    WHAT REPLACES THE INTERDICTION, and is strictly stronger than it: an
    amount can no longer exist here WITHOUT its label. The validator below
    makes an unlabelled price, an out-of-vocabulary label and a price per m2
    on anything but a mono-local mutation unconstructible -- so « vente
    d'ensemble -- prix global » cannot degrade into the price of one lot by
    forgetting a field. A logement card still carries no amount at all; that
    part of D-16 is untouched.
    """

    date_mutation: date | None = None
    type_local: str | None = None
    surface_reelle_bati: int | None = None
    nombre_pieces_principales: int | None = None

    # -- Additive mutation-grain fields (Phase 57, D-17). All optional and
    # defaulting to None: a caller that projects none of them (a fixture, a
    # pre-Phase-57 row) builds the same object as before.
    #
    # ``valeur_fonciere`` is the price of the WHOLE mutation, never a share
    # imputed to one local. ``classe_mutation`` is what makes it readable and
    # is therefore mandatory beside it.
    valeur_fonciere: float | None = None
    classe_mutation: str | None = None
    mono_local: bool | None = None
    hors_stats_vefa: bool | None = None
    # Honest price per m2 (D-15): exists only on a mono-local mutation, where
    # one local unambiguously owns both the price and the surface. Otherwise
    # NULL, with the closed reason that says why.
    prix_m2: float | None = None
    prix_m2_raison: str | None = None
    # Perimeter counters of an ensemble sale (DVF-03): how many residential
    # and commercial locals the price covers. ``nb_locaux_residentiel`` is the
    # sum of the maison and appartement counters of ``mutations_grain`` (which
    # stores no such column) -- a listing figure, never a statistical one.
    nb_locaux_residentiel: int | None = None
    nb_locaux_commercial: int | None = None

    @model_validator(mode="after")
    def _valide_prix_etiquete(self) -> "VenteImmeuble":
        """Make an unlabelled or over-claiming price unconstructible (D-17/D-40).

        Four rules, each of which has a negative test in
        ``tests/test_logements_schemas.py::TestPrixToujoursEtiquete``:

        1. a label, when present, belongs to the closed
           ``app.etl_dvf.qualification.CLASSES_MUTATION`` vocabulary;
        2. an amount requires that label -- this is the guarantee that
           replaces the Phase 48 interdiction;
        3. a price per m2 requires ``mono_local is True``: on a vente
           d'ensemble the price covers several locals, so dividing it by the
           mutation's total surface would recreate exactly the false figure
           D-16 was written against;
        4. ``prix_m2_raison`` belongs to the closed ``RAISONS_PRIX_M2``
           vocabulary and, ON A ROW THAT CARRIES AN AMOUNT, is filled if and
           only if ``prix_m2`` is null -- the same coherence
           ``app.dvf.schemas.ResultatDvf`` enforces. The condition is
           narrower than in ``ResultatDvf`` on purpose: there, every field is
           projected by the query, so both being null is always incoherent.
           Here, every field is optional, and a row carrying no amount at all
           (a fixture, a caller that projects only the contextual columns)
           legitimately carries neither the price per m2 nor its reason.
        """
        if (
            self.classe_mutation is not None
            and self.classe_mutation not in CLASSES_MUTATION
        ):
            raise ValueError(
                f"classe_mutation {self.classe_mutation!r} n'appartient pas au "
                "vocabulaire ferme (app.etl_dvf.qualification.CLASSES_MUTATION)"
            )
        if self.valeur_fonciere is not None and self.classe_mutation is None:
            raise ValueError(
                "valeur_fonciere exige une classe_mutation (D-17) -- un prix "
                "sans l'etiquette qui dit ce qui a ete vendu est le prix "
                "mensonger que la Phase 48 interdisait"
            )
        if self.prix_m2 is not None and self.mono_local is not True:
            raise ValueError(
                "prix_m2 exige mono_local is True (D-15/D-17) -- un prix au m2 "
                "sur une vente d'ensemble divise un prix global par la surface "
                "de plusieurs logements"
            )
        if (
            self.prix_m2_raison is not None
            and self.prix_m2_raison not in RAISONS_PRIX_M2
        ):
            raise ValueError(
                f"prix_m2_raison {self.prix_m2_raison!r} n'appartient pas au "
                "vocabulaire ferme (app.etl_dvf.qualification.RAISONS_PRIX_M2)"
            )
        if self.valeur_fonciere is not None and (self.prix_m2_raison is not None) != (
            self.prix_m2 is None
        ):
            raise ValueError(
                "sur une ligne portant un montant, prix_m2_raison doit etre "
                "renseigne si et seulement si prix_m2 est nul (D-15)"
            )
        return self


class RattachementDpe(BaseModel):
    """Attachment-method breakdown of the DPE rows serving one fiche (Lot 0).

    Instrumentation, not display (PLAN-RATTACHEMENT-DPE-2026-08-05, item
    0.3): the spatial fallback is invisible from the outside today, so an
    attachment regression can only be caught by re-reading a fiche by hand
    (3 corrective PR in 24 h, 2026-08-04). Counted on the rows that survive
    the street-number filter -- the SAME population as
    ``diagnostics_charges`` -- so the buckets always sum to it.

    Ordered by strength of evidence. A fourth bucket ``preuve_batiment`` was
    published between 2026-08-05 and 2026-08-06 for the Lot C2 building-proof
    path; both were removed after measurement (``app.logements.queries`` module
    docstring). The rows it would have counted fall back under ``spatial``,
    which is where they were counted before Lot C2 -- so the three buckets
    still sum to ``diagnostics_charges``, and no reader loses information.

    SINCE PHASE 51 ``spatial`` IS STRUCTURALLY 0: the fallback it counted is no
    longer reachable from the public route (D-18). The bucket is kept published
    for exactly that reason -- the sweep keeps diffing the same three keys, and
    a non-zero value would be immediate, machine-visible evidence that a
    geometric attachment path came back.
    """

    exact: int = Field(
        default=0,
        description=(
            "Nombre complet de documents DPE importés dont la clé source est "
            "égale à la BAN résolue. Ce compteur ne dénombre pas des logements."
        ),
    )
    elargi_batiment: int = Field(
        default=0,
        description=(
            "Diagnostics rattachés par une AUTRE clé BAN du même bâtiment RNB "
            "(immeuble d'angle : deux entrées sur deux rues, un seul bâtiment)."
        ),
    )
    spatial: int = Field(
        default=0,
        description=(
            "Diagnostics rattachés par le repli spatial (12 m + non-ambiguïté), "
            "y compris le complément restreint aux lignes sans clé BAN."
        ),
    )


class ResultatLogements(BaseModel):
    """Full response body for GET /v1/bien/logements.

    ``dpe_inventory`` contains exact-BAN documents. ``dpe_attribution`` only
    states whether such documents exist without assigning one to the dwelling.

    There is NO logement card here. The ``logements`` list and its
    ``LogementItem`` were removed in phase 01-04 of the reboot: the handler
    had hardcoded them to ``[]`` since 2026-08-19, this validator forbade a
    non-empty value, and the client reset it a third time. Recoverable from
    ``3394395a`` (D-03/D-05, docs/REPO-STATE.md).

    No monetary field on what this module publishes (D-16, unchanged). Since
    D-17 the sale rows of ``ventes_immeuble`` do carry the price of their
    mutation, always beside its class -- see :class:`VenteImmeuble`.
    ``diagnostics_charges`` (M3) counts DOCUMENTS read (exact identity plus
    the RNB carrier's other keys), never dwellings.

    ``diagnostics_exclus_adresse`` remains structurally zero because the shared
    inventory binds the source BAN equality before projecting any document.

    ``rattachement`` (Lot 0, item 0.3) is additive and follows the same
    doctrine for HOW those diagnostics were attached -- see
    :class:`RattachementDpe`.
    """

    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        json_schema_extra={
            "examples": [
                {
                    "id_parcelle": "85047000AB0042",
                    "detection": {
                        "signaux": {
                            "copro_rnc": {
                                "positif": True,
                                "presence": "value",
                                "absence_reason": None,
                                "compte": 1,
                            },
                            "dpe_appart": {
                                "positif": True,
                                "presence": "value",
                                "absence_reason": None,
                                "compte": 5,
                            },
                            "dvf_appart": {
                                "positif": False,
                                "presence": "absent",
                                "absence_reason": "not_reported",
                                "compte": 0,
                            },
                        },
                        "signaux_positifs": 2,
                        "mode": "immeuble",
                    },
                    "denominateur": {
                        "logements_connus": 0,
                        "lots_declares": 12,
                        "non_distinguables": 0,
                    },
                    "distribution_dpe": {},
                    "ventes_immeuble": [],
                    "dpe_inventory": {
                        "version": 2,
                        "state": "ready",
                        "context_ref": "opaque-context",
                        "items": [],
                        "total": 5,
                        "distribution": {
                            "A": 0,
                            "B": 0,
                            "C": 1,
                            "D": 3,
                            "E": 1,
                            "F": 0,
                            "G": 0,
                            "unknown": 0,
                        },
                        "page_size": 20,
                        "has_more": False,
                        "next_cursor": None,
                        "read_state": "complete",
                    },
                    "dpe_attribution": {
                        "version": 2,
                        "status": "unassigned",
                        "reason": "dwelling_identity_unproven",
                        "diagnostic": None,
                        "candidates": [],
                        "building_context": None,
                        "graph_state": "not_checked",
                    },
                    "diagnostics_charges": 5,
                    "diagnostics_exclus_adresse": 0,
                    "rattachement": {
                        "exact": 5,
                        "elargi_batiment": 0,
                        "spatial": 0,
                    },
                    "tronque": False,
                }
            ]
        },
    )

    id_parcelle: str
    detection: BlocDetection
    denominateur: Denominateur
    distribution_dpe: dict[str, int] = Field(
        description=(
            "Répartition A→G des diagnostics connus à l'échelle du bâtiment, "
            "recopiée telle quelle depuis "
            "`dpe_attribution.building_context.distribution` — vide dans tout "
            "autre état. Aucune classe principale n'en est élue, et le client "
            "ne la recalcule jamais : deux agrégats des mêmes lignes seraient "
            "deux vérités pour une seule phrase."
        ),
    )
    ventes_immeuble: list[VenteImmeuble]
    dpe_inventory: DpeInventoryPage = Field(
        description=(
            "Première page standard de l'inventaire documentaire exact-BAN. "
            "Elle reste indépendante de l'attribution au logement."
        ),
    )
    dpe_attribution: DpeAttribution = Field(
        description=(
            "Décision DPE V2 (`unassigned` | `absent` | `error`) partagée avec "
            "`/v1/resolve`. Elle ne contient ni diagnostic ni candidat."
        ),
    )
    diagnostics_charges: int
    diagnostics_exclus_adresse: int = 0
    rattachement: RattachementDpe = Field(
        default_factory=RattachementDpe,
        description=(
            "Répartition des méthodes de rattachement des diagnostics servis "
            "par cette fiche, additive : n'altère aucun champ existant. Même "
            "population documentaire que `diagnostics_charges`, donc "
            "exact + elargi_batiment + spatial = diagnostics_charges."
        ),
    )
    tronque: bool

    @model_validator(mode="after")
    def _valide_decision_dpe(self) -> "ResultatLogements":
        """Make document-to-dwelling promotion unconstructible."""
        decision = self.dpe_attribution
        if decision.status not in {"unassigned", "absent", "error"}:
            raise ValueError("logements only accepts the V2 non-attributive states")
        if decision.diagnostic is not None or decision.candidates:
            raise ValueError("DPE documents must not create logement identities")
        if self.detection.preuve_identites:
            raise ValueError("DPE documents must not prove logement identities")
        if (
            self.denominateur.logements_connus != 0
            or self.denominateur.non_distinguables != 0
        ):
            raise ValueError("document counts must not enter the logement denominator")
        contexte = decision.building_context
        attendue = contexte.distribution if contexte is not None else {}
        if self.distribution_dpe != attendue:
            raise ValueError(
                "distribution_dpe must mirror "
                "dpe_attribution.building_context.distribution (empty without a "
                "building aggregate) -- it is never recomputed beside it"
            )
        self._valide_inventaire_decision()
        self._valide_compteurs_documentaires()
        return self

    def _valide_inventaire_decision(self) -> None:
        page = self.dpe_inventory
        expected = {
            "ready": ("unassigned", "dwelling_identity_unproven"),
            "absent": ("absent", "not_reported"),
            "identity_unavailable": ("absent", "identity_unavailable"),
            "unavailable": ("error", "temporary_error"),
        }[page.state]
        decision = self.dpe_attribution
        if (decision.status, decision.reason) != expected:
            raise ValueError("DPE attribution must match the inventory read state")
        if decision.graph_state != page.graph_state and page.state != "unavailable":
            raise ValueError("DPE graph state must match the exact-BAN inventory")

    def _valide_compteurs_documentaires(self) -> None:
        total = self.dpe_inventory.total
        expected_exact = total if total is not None else 0
        if self.rattachement.exact != expected_exact:
            raise ValueError("exact attachment count must match the inventory total")
        if (
            self.rattachement.exact
            + self.rattachement.elargi_batiment
            + self.rattachement.spatial
            != self.diagnostics_charges
        ):
            raise ValueError("attachment counts must sum to diagnostics_charges")
        if self.diagnostics_exclus_adresse != 0:
            raise ValueError("the exact-BAN inventory cannot carry foreign documents")
        signal = self.detection.signaux.get("dpe_appart")
        if signal is not None and total is not None and signal.compte > total:
            raise ValueError("apartment document count cannot exceed inventory total")
