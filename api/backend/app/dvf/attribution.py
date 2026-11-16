"""Pure DVF attribution policy (Phase 57, D-10/D-11/D-12/D-13/D-16, D-37).

ONE function decides what may be said about the DVF history of the consulted
property: :func:`decide_dvf_attribution`. It receives the mutations already
loaded by ``app.dvf.queries`` (mutation grain, D-01, sorted by date DESC) plus
the type of the consulted dwelling, and returns the closed contract of
``app.dvf.schemas``. Nothing here reads a database, calls a service or touches
the network -- the whole module is offline-testable, which is why the honesty
rules below can be proven one by one instead of being asserted through an
endpoint.

**« Parcelle exacte ne signifie pas local exact. »** That sentence is the whole
phase. ``dvf_par_id_parcelle`` sorts the mutations of the EXACT parcel by date,
and the fiche used to read ``[0]`` as this property's last sale -- which is how
a ground-floor shop became the KPI of a flat above it (DVF-04/DVF-05). The fix
is not a different sort: it is deciding explicitly, and being able to say no.

The rules it enforces, and the substitution each one removes:

- a mutation is a CANDIDATE only when it is residential mono-local, of a class
  compatible with the consulted dwelling, of nature « Vente » and not a VEFA
  (D-10/D-13). A candidate is published as ``compatible_parcelle`` -- « une
  transaction compatible observée sur cette parcelle », never « la vente de ce
  bien » (D-37);
- ``exacte`` requires an explicit local/lot proof. NO such proof source exists
  in this phase, so this module NEVER produces ``exacte``: not even for the
  ideal case (one compatible mono-local sale with its surface). The state
  exists for the day the proof does -- Phase 58 owns that question (D-38);
- a mixed sale, a commercial sale and a block sale are NOT attributable to the
  consulted dwelling however recent they are (D-11, DVF-03/04/05). They stay in
  the parcel history published by ``datasets.dvf`` (D-11/D-17) -- this decision
  qualifies that list, it never replaces it;
- a VEFA is NEVER a candidate, with or without surface (D-12/D-13): it is a
  sale of a different market and it never feeds this property's KPI. The
  absence it produces is named for what it is, though -- ``vefa_sans_surface``
  without a surface (DVF-06: an invented ratio is worse than a stated absence),
  ``vente_sur_plan`` with one (D-47). Answering « ce n'est pas une vente » on a
  VEFA was false: it IS a sale, simply not one this fiche may attribute;
- when the consulted dwelling's type is UNKNOWN, nothing is guessed: the
  decision says so (``type_bien_inconnu``) rather than attributing at random;
- every absence carries a closed reason, so it is explicable in one line
  (D-16), and every decision carries the rule version and the evidence that
  produced it (D-37);
- an unreadable or degraded input closes CONSERVATIVELY on ``error`` and NEVER
  raises (same doctrine as ``app.dpe.attribution``): ``/v1/resolve`` must not
  break on one dirty record, and a technical failure must never be readable as
  « aucune vente ici ».
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.dvf.schemas import (
    DVF_ATTRIBUTION_RAISONS,
    DVF_ATTRIBUTION_RULE_VERSION,
    DVF_TYPES_BIEN_CONSULTES,
    EVIDENCE_TYPE_BIEN_UTILISATEUR,
    DvfAttribution,
    ResultatDvf,
)
from app.etl_dvf.qualification import NATURE_VENTE

__all__ = [
    "CLASSE_PAR_TYPE_BIEN",
    "DVF_ATTRIBUTION_RAISONS",
    "DVF_ATTRIBUTION_RULE_VERSION",
    "TYPES_BIEN_ADMIS",
    "decide_dvf_attribution",
    "provenance_type_bien",
]

_LOGGER = logging.getLogger(__name__)

# Strict type compatibility (D-10): a maison sale never feeds an appartement
# KPI and conversely. The mapping is exhaustive on purpose -- any other value
# (including ``None``) is an UNKNOWN dwelling type, never a wildcard.
TYPE_BIEN_MAISON = "maison"
TYPE_BIEN_APPARTEMENT = "appartement"
CLASSE_PAR_TYPE_BIEN: dict[str, str] = {
    TYPE_BIEN_MAISON: "residentiel_maison",
    TYPE_BIEN_APPARTEMENT: "residentiel_appartement",
}

_CLASSES_RESIDENTIELLES_MONO: frozenset[str] = frozenset(CLASSE_PAR_TYPE_BIEN.values())

# THE closed vocabulary of dwelling types, published for the callers that must
# validate one (``/v1/resolve?type_bien=``, D-41). It is the CONTRACT's tuple,
# re-exported here and never re-declared: two vocabularies drifting apart by one
# release is exactly the defect class this phase closes. The mapping above must
# cover it exactly -- CASE-DVF-ATTR-25 proves it does.
TYPES_BIEN_ADMIS: tuple[str, ...] = DVF_TYPES_BIEN_CONSULTES

# Non-attributable classes and the sentence each one owes the reader (D-16).
_RAISON_PAR_CLASSE: dict[str, str] = {
    "mixte": "derniere_vente_mixte",
    "commerciale": "derniere_vente_commerciale",
    "ensemble_residentiel": "vente_ensemble",
}


def _lit(mutation: Any) -> dict[str, Any]:
    """Read ONE mutation as a plain mapping, whatever the caller handed over.

    ``/v1/resolve`` passes raw rows, other callers may pass already projected
    :class:`ResultatDvf` models. Anything else is unreadable and raises here --
    :func:`decide_dvf_attribution` turns that into a typed ``error``.
    """
    if isinstance(mutation, ResultatDvf):
        return mutation.model_dump()
    if isinstance(mutation, Mapping):
        return dict(mutation)
    raise TypeError("mutation illisible")


def _surface(lue: Mapping[str, Any]) -> int | None:
    """Built surface AT THE MUTATION GRAIN -- never a per-line surface (D-01)."""
    surface = lue.get("surface_bati_totale")
    return surface if surface else None


def _est_vente(lue: Mapping[str, Any]) -> bool:
    """Nature « Vente » only (D-13). A VEFA is a sale of a different market."""
    nature = (lue.get("nature_mutation") or "").strip()
    return nature == NATURE_VENTE and lue.get("hors_stats_vefa") is not True


def _est_residentielle_mono(lue: Mapping[str, Any]) -> bool:
    """Residential AND mono-local, both stated -- never inferred (fail-closed)."""
    return (
        lue.get("classe_mutation") in _CLASSES_RESIDENTIELLES_MONO
        and lue.get("mutation_mono_local") is True
    )


def _est_candidate(lue: Mapping[str, Any], classe_attendue: str) -> bool:
    """Whether this mutation may be published as a parcel-level candidate."""
    return (
        _est_residentielle_mono(lue)
        and lue.get("classe_mutation") == classe_attendue
        and _est_vente(lue)
    )


def _raison_refus(lue: Mapping[str, Any], classe_attendue: str) -> str:
    """Why the MOST RECENT mutation could not be attributed (D-16).

    The order matters: the reader is owed the first honest headline, not a
    technical cascade. A commercial or mixed sale is named for what it is
    before any consideration of type or nature.

    A VEFA is split in two on purpose (D-47). Without a surface, the headline
    is the missing surface (``vefa_sans_surface``, DVF-06). With one, the old
    fallback said ``nature_hors_vente`` -- « une transaction qui n'est pas une
    vente » -- which is simply FALSE: a VEFA is a sale. It is now named
    ``vente_sur_plan``. Naming it differently changes nothing to the doctrine:
    :func:`_est_vente` still excludes every VEFA from the candidates (D-12/
    D-13), so this reason is only ever a way to say no more honestly.
    """
    classe = lue.get("classe_mutation")
    raison = _RAISON_PAR_CLASSE.get(classe or "")
    if raison:
        return raison
    if not _est_residentielle_mono(lue):
        return "classe_non_residentielle"
    if classe != classe_attendue:
        return "type_incompatible"
    if lue.get("hors_stats_vefa") is True and _surface(lue) is None:
        return "vefa_sans_surface"
    if lue.get("hors_stats_vefa") is True:
        return "vente_sur_plan"
    return "nature_hors_vente"


def _perimetre(lue: Mapping[str, Any]) -> dict[str, Any]:
    """Flat perimeter of the mutation the decision examined (DVF-03).

    Counters are what lets a block sale be LABELLED « vente d'ensemble — N
    locaux, prix global » instead of silently disappearing.
    """
    maison = lue.get("nb_locaux_maison")
    appartement = lue.get("nb_locaux_appartement")
    commercial = lue.get("nb_locaux_commercial")
    residentiel = (
        (maison or 0) + (appartement or 0)
        if maison is not None or appartement is not None
        else None
    )
    ensemble = lue.get("classe_mutation") in {"ensemble_residentiel", "mixte"} or (
        (residentiel or 0) + (commercial or 0) > 1
    )
    return {
        "perimetre_ensemble": bool(ensemble),
        "nb_locaux_residentiel": residentiel,
        "nb_locaux_commercial": commercial,
        "nb_lots_source": lue.get("nb_lots_source"),
    }


def _preuves(lue: Mapping[str, Any]) -> list[str]:
    """What the policy itself VERIFIED on the retained candidate (D-37).

    ``parcelle_exacte`` is deliberately absent: it is the CALLER's fact (a
    canonical ``id_parcelle`` lookup), and this module never claims a proof it
    did not establish.
    """
    preuves = ["type_compatible", "mono_local", "nature_vente"]
    if _surface(lue) is not None:
        preuves.append("surface_presente")
    return preuves


def _declare(type_bien: str | None) -> bool:
    """Whether the caller DECLARED a type of the closed vocabulary (D-41).

    ``isinstance`` first: this helper is also called from the error path, where
    it must not be the thing that raises (the module NEVER raises).
    """
    return isinstance(type_bien, str) and type_bien in CLASSE_PAR_TYPE_BIEN


def _provenance(type_bien: str | None) -> dict[str, Any]:
    """Provenance of the type that fed the decision, on EVERY state produced.

    A declared type is traced on the absences too: « the type was declared AND
    no mutation is compatible » is a different fact from « the type is
    unknown », and a reader of the payload is owed the difference (D-41). The
    trace remains a DECLARATION -- it never promotes anything to ``exacte``.

    The provenance is COMPLETE or it is nothing: the source says WHERE the type
    came from, ``type_bien_consulte`` says WHICH value was consulted. Publishing
    the first without the second left the reader guessing what the decision was
    taken on; the schema now refuses that pair.
    """
    declare = _declare(type_bien)
    return {
        "type_bien_source": "utilisateur" if declare else "inconnu",
        "type_bien_consulte": type_bien if declare else None,
    }


def _marqueur(type_bien: str | None) -> list[str]:
    """The evidence entry attesting the declaration -- empty when there is none."""
    return [EVIDENCE_TYPE_BIEN_UTILISATEUR] if _declare(type_bien) else []


def provenance_type_bien(type_bien: str | None) -> dict[str, Any]:
    """The FULL provenance block of a state this module did not decide itself.

    ``absente``/``error`` states built OUTSIDE the policy -- an unresolved
    parcel, a caught failure, a block scrubbed by the parcel invariant -- are
    decisions all the same, and they owe the reader the same provenance as the
    ones decided here (D-41): source, declared value, and the evidence that
    attests the declaration. Splat it into the constructor; it is the only
    supported way to build such a state without dropping the caller's
    declaration on the floor.

    Never raises: ``_declare`` type-checks before it compares, because these
    call sites are exactly the ones that must not fail.
    """
    return {**_provenance(type_bien), "evidence": _marqueur(type_bien)}


def _decide(mutations: Any, *, type_bien: str | None) -> DvfAttribution:
    """The decision proper -- may raise; the public wrapper closes on error."""
    lues = [_lit(mutation) for mutation in mutations]
    provenance = _provenance(type_bien)
    marqueur = _marqueur(type_bien)

    if not lues:
        return DvfAttribution(
            status="absente",
            reason="aucune_mutation",
            evidence=marqueur,
            **provenance,
        )

    classe_attendue = CLASSE_PAR_TYPE_BIEN.get(type_bien or "")
    if classe_attendue is None:
        # No declared dwelling type: compatibility cannot be established at
        # all. Saying so is the honest answer; picking the latest sale is the
        # substitution this phase removes.
        return DvfAttribution(
            status="non_attribuable",
            reason="type_bien_inconnu",
            **_perimetre(lues[0]),
            **provenance,
        )

    for lue in lues:
        if _est_candidate(lue, classe_attendue):
            return DvfAttribution(
                status="compatible_parcelle",
                mutation=ResultatDvf.model_validate(lue),
                evidence=[*_preuves(lue), *marqueur],
                **_perimetre(lue),
                **provenance,
            )

    return DvfAttribution(
        status="non_attribuable",
        reason=_raison_refus(lues[0], classe_attendue),
        evidence=marqueur,
        **_perimetre(lues[0]),
        **provenance,
    )


def decide_dvf_attribution(
    mutations: Sequence[Any] | None,
    *,
    type_bien: str | None,
) -> DvfAttribution:
    """Decide what may be published about this property's last sale.

    *mutations* are the mutations of the property's parcel, at mutation grain
    and sorted by date DESCENDING (the contract of ``dvf_par_id_parcelle``);
    *type_bien* is the type of the CONSULTED dwelling as DECLARED by the caller
    (``/v1/resolve?type_bien=``, D-41) -- ``maison``, ``appartement``, or
    ``None`` when nobody stated it. ``None`` is a fact, not a wildcard: it
    produces an explained non-attribution, never a guess. A declared type is
    never INFERRED from the mutations themselves (that would be circular) nor
    from the DPE, and it is traced as a declaration on every state produced.

    The scan walks from the most recent mutation to the oldest and stops at the
    first candidate. Without a local/lot proof that candidate is published as
    ``compatible_parcelle`` and NEVER as ``exacte`` (D-37). When no mutation is
    a candidate, the absence is qualified by the MOST RECENT one -- that is
    what makes it explicable in one line (D-16).

    Never raises: an unreadable input closes on ``error``/``entree_illisible``,
    which is a distinct fact from « aucune vente ici ».
    """
    try:
        return _decide(mutations or (), type_bien=type_bien)
    except Exception:  # noqa: BLE001
        _LOGGER.warning("dvf_attribution_entree_illisible", exc_info=False)
        return DvfAttribution(
            status="error",
            reason="entree_illisible",
            evidence=_marqueur(type_bien),
            **_provenance(type_bien),
        )
