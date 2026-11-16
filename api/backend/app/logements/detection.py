"""Detection signals, verdict, and honest denominator (D-01/D-02/D-03).

Pure functions only (D-10): they take counts already computed elsewhere and
return typed structures -- no I/O, no DB access, and no import of the
sibling dedup module (service.py), so the two stay independent and
parallelizable.
"""

from typing import Literal

MODE_IMMEUBLE = "immeuble"
MODE_INDICE = "indice"
MODE_BIEN = "bien"

Presence = Literal["value", "absent", "error"]


def _signal(positif: bool, compte: int) -> dict:
    """Build one signal entry using the resolve presence/absence vocabulary.

    ``presence``/``absence_reason`` reuse the taxonomy already defined in
    ``app.resolve.schemas`` (``value``/``absent``/``not_reported``) -- no
    new word of truth is introduced here.
    """
    presence: Presence = "value" if compte > 0 else "absent"
    absence_reason = None if compte > 0 else "not_reported"
    return {
        "positif": positif,
        "presence": presence,
        "absence_reason": absence_reason,
        "compte": compte,
    }


def calcule_signaux(
    copro_present: bool, nb_dpe_appartement: int, nb_ventes_appartement: int
) -> dict:
    """Build the 3 MVP detection signals (D-01/D-03): copro, DPE, DVF.

    ``nb_dpe_appartement`` MUST already equal
    ``len(logements) + non_distinguables`` (the full count returned by
    ``dedup_logements``), never ``len(logements)`` alone -- counting only
    displayed cards would make the >=2-signal, 0-card fallback
    (criterion 4) mechanically unreachable on a building where every
    complement is opaque (D-03, decision Ianis; wired by the caller,
    Plan 48-06). D-01 excludes the fourth candidate signal from this MVP:
    only these 3 keys ever exist.
    """
    return {
        "copro_rnc": _signal(copro_present, 1 if copro_present else 0),
        "dpe_appart": _signal(nb_dpe_appartement >= 2, nb_dpe_appartement),
        "dvf_appart": _signal(nb_ventes_appartement >= 1, nb_ventes_appartement),
    }


def preuve_identites_distinctes(logements: list[dict]) -> bool:
    """True when the deduplicated cards alone prove the building (ADR-0006).

    Two conditions, BOTH required:

    - at least 2 DISTINCT structured identities ``(etage, batiment, porte)``
      among the cards -- two surveys asserting two distinct dwellings at the
      same address point. Cards split by the surface clustering alone share
      one identity and count once: a re-measured dwelling is never two;
    - at least one card carries a ``porte`` or ``batiment`` token. A maison
      individuelle has floors but never a "porte de droite" or a "bat A":
      this is the anti-false-positive guard (D-02 hard constraint) against
      a mislabelled maison whose two surveys cite different floors --
      demonstrated promotable without this clause (review 2026-08-04, T3).

    Input rows are the ENRICHED cards from ``service.dedup_logements``
    (structured discriminants attached); non-distinguishable rows never
    reach this function and prove nothing here (an opaque row carries no
    distinctness).
    """
    identites = {(r.get("etage"), r.get("batiment"), r.get("porte")) for r in logements}
    if len(identites) < 2:
        return False
    return any(
        r.get("porte") is not None or r.get("batiment") is not None for r in logements
    )


def determine_mode(signaux: dict, preuve_identites: bool = False) -> str:
    """Verdict = simple count of positive signals (D-02, no numeric coherence).

    Lots, DPE and sales diverge naturally between sources; requiring them
    to agree numerically would produce false negatives.

    ``preuve_identites`` (ADR-0006, amendment to D-02) promotes the verdict
    to ``immeuble`` on the strength of the DPE cards alone: two distinct
    structured identities are two independent proofs that happen to transit
    through the same signal -- a reading of D-02's ">=2 concordant proofs",
    not its abandonment. The >=2-signal rule is unchanged for every other
    path, and the promotion can only ever start from ``indice`` (>=2 cards
    implies the DPE signal is already positive, never from ``bien``).
    """
    positifs = sum(1 for signal in signaux.values() if signal["positif"])
    if positifs >= 2 or preuve_identites:
        return MODE_IMMEUBLE
    if positifs == 1:
        return MODE_INDICE
    return MODE_BIEN


def construit_denominateur(
    logements_connus: int, lots_declares: int | None, non_distinguables: int
) -> dict:
    """Build the public denominator block (D-03/D-07).

    ``lots_declares`` is preserved verbatim -- ``None`` stays ``None``,
    never 0, never estimated: the "nombre total de lots non publie" copy
    variant depends on this. A ``non_distinguables`` of 0 is exposed as 0;
    the UI clause that mentions it is simply omitted in that case (a UI
    contract, not enforced here).
    """
    return {
        "logements_connus": logements_connus,
        "lots_declares": lots_declares,
        "non_distinguables": non_distinguables,
    }
