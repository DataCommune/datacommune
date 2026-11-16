"""Pure DPE-row -> logement transforms (D-10). No I/O, no DB, no framework.

Floor parsing, complement normalisation, street-number discrimination and
deduplication all live here so each honesty rule (unknown floor, opaque
complement, non-distinguishable DPE, foreign street number) is unit-testable
offline. Nothing here ever guesses a value.
"""

import re
import unicodedata
from collections.abc import Sequence
from datetime import date
from typing import NamedTuple

# Floor stated as free text: "Etage : 2", "Etage 4", "2eme Etage" (accents
# stripped before matching -- see _strip_accents).
_ETAGE_TEXTE = re.compile(
    r"(?:^|[\s;,])(?:etage)\s*[:n°]?\s*(-?\d{1,2})|"
    r"(?:^|[\s;,])(-?\d{1,2})\s*(?:er|ere|eme|e)?\s*(?:etage)",
    re.IGNORECASE,
)
_RDC = re.compile(r"\b(rdc|rez[\s-]?de[\s-]?chauss)", re.IGNORECASE)
_SOUS_SOL = re.compile(r"\b(sous[\s-]?sol|ss)\b", re.IGNORECASE)

# Building/entrance and door/unit-number keywords, longest literal first so
# "appartement" is not truncated into "app"/"appt" by alternation order.
_BATIMENT_RE = re.compile(r"\bbat(?:iment)?\.?\s*([a-z0-9/]+)", re.IGNORECASE)
_PORTE_RE = re.compile(
    r"\b(?:appartement|appt|apt|app|porte|logement|lot)\b\.?\s*([a-z0-9]+)",
    re.IGNORECASE,
)
# "N/A"-style markers prove the logement exists but display nothing useful
# (Pitfall 2): they must never be read as a real batiment/porte value.
_OPAQUE_TOKEN_RE = re.compile(r"^n\W*a$", re.IGNORECASE)

# Abbreviations that must normalize to the SAME token (D-05 grouping key).
_ABREVIATIONS: dict[str, str] = {
    "apt": "appartement",
    "appt": "appartement",
    "app": "appartement",
    "bat": "batiment",
}


def _strip_accents(texte: str) -> str:
    """Remove diacritics via NFD decomposition (stdlib, no package added)."""
    decompose = unicodedata.normalize("NFD", texte)
    return "".join(ch for ch in decompose if unicodedata.category(ch) != "Mn")


def parse_etage(numero_etage: int | None, complement: str | None) -> int | None:
    """Return the floor, or None when unknown -- NEVER guesses (D-06/D-21).

    ADEME's structured field defaults to 0 when the surveyor leaves it
    blank: 34 411 of 36 154 filled values in dept 85 are 0, including rows
    whose free-text complement states another floor explicitly. So 0 alone
    can never mean ground floor -- only a textual proof does.
    """
    if numero_etage is not None and numero_etage != 0:
        return numero_etage
    if not complement:
        return None
    texte = _strip_accents(complement)
    if m := _ETAGE_TEXTE.search(texte):
        return int(m.group(1) or m.group(2))
    if _RDC.search(texte):
        return 0
    if _SOUS_SOL.search(texte):
        return -1
    return None


def normalise_complement(complement: str | None) -> str:
    """Stable, accent-free, lowercase form used as the D-05 grouping key.

    Abbreviations (Apt/Appt/Appartement, Bat/Batiment) collapse to the same
    token so a re-diagnosis phrased differently still groups correctly.
    """
    if not complement:
        return ""
    sans_accents = _strip_accents(complement).lower()
    nettoye = re.sub(r"[^a-z0-9]+", " ", sans_accents).strip()
    if not nettoye:
        return ""
    tokens = [_ABREVIATIONS.get(tok, tok) for tok in nettoye.split(" ")]
    return " ".join(tokens)


def numero_voie_ban(cle_ban: str | None) -> str | None:
    """Street number encoded in a BAN interoperability key, or None (T4).

    The key is ``INSEE_FANTOIR_NUMERO`` plus an optional suffix segment.
    Three formats are observed in ``diagnostics_dpe``: 3 segments
    (``44109_3912_00001``), 4 segments (``44109_3912_00001_bis`` / ``_ter``)
    and voie-level keys carrying no number at all (``44109_4552``). The last
    one returns None, which callers read as "cannot discriminate" -- never as
    "no number means number 0".

    The suffix is PART of the number: ``1 bis`` is a distinct postal address
    from ``1``, and only the RNB building widening (R4-02) may legitimately
    put the two back together.
    """
    if not cle_ban:
        return None
    segments = cle_ban.split("_")
    if len(segments) < 3 or not segments[2].isdigit():
        return None
    numero = segments[2].lstrip("0") or "0"
    suffixe = "_".join(segments[3:]).lower()
    return f"{numero}_{suffixe}" if suffixe else numero


# Width ceiling of the RNB building widening (R4-02). Recalibrated 2026-08-06
# (decision Ianis, internal note INSTRUMENTATION-PLAFOND-GEOMETRIQUE-2026-08-06)
# after the 2026-08-05 ceiling -- 2 streets / 4 keys measured on the widened
# set -- showed a proven false positive: a real corner building with 3
# entrances on 3 streets (angle-44-000006), 29 legitimate diagnostics
# withheld. The geometric criteria considered as replacements (disjoint
# carriers, multi-parcel footprint) discriminate nothing: 15 of the 16 capped
# fiches were single-carrier, and 67 % of carriers span several parcels --
# multi-parcel is the urban norm, not a signal.
#
# What separates the adjudicated pair is the WIDTH OF THE CARRIER itself, in
# distinct BAN keys carried in rnb_liens: 3 for the true corner building, 16
# for the gallery artefact that filled an empty fiche with 22 borrowed
# diagnostics. Every sane widened fiche of the corpus involves carriers of 4
# keys or less; carriers above 9 keys are 0,06 % of the 1,43 M-building
# population, and the corpus shows none between 10 and 15 -- any threshold in
# [9; 15] behaves identically on it. 9 is the conservative low end of that
# evidence-free interval.
PLAFOND_LARGEUR_PORTEUR = 9


class PlafondLargeur(NamedTuple):
    """Outcome of the width ceiling -- keys kept plus the width that decided.

    Same shape as :class:`FiltrageVoie` and for the same reason (ticket T5):
    a widening cancelled and a widening that was empty to begin with must
    never look alike from the outside. ``largeur_porteur`` is the widest
    carrier measured, i.e. what was compared against the threshold.
    """

    cles: tuple[str, ...]
    depasse: bool
    largeur_porteur: int


def applique_plafond_largeur(
    cles_elargies: Sequence[str], largeur_porteur: int
) -> PlafondLargeur:
    """Cancel the RNB widening when a carrying building is too WIDE (R4-02).

    ``largeur_porteur`` is the number of distinct BAN keys carried in
    ``rnb_liens`` by the WIDEST carrier of the resolved key -- an intrinsic
    property of the building, counted over the whole table and never
    restricted to the parcel entered by. The caller measures it in the same
    round trip as the widening itself
    (:func:`app.logements.queries.cles_ban_batiment_rnb`).

    Counting per CARRIER and not on the widened union is deliberate
    (instrumentation 2026-08-06): the union of several narrow carriers is the
    multi-wing residence the 2026-08-05 union decision protects -- capping the
    union would partially undo that decision -- whereas ONE building carrying
    16 keys over 4 streets is an RNB aggregation artefact whichever fiche it
    is entered from. Streets are not counted at all any more: 3 entrances on
    3 streets is what a corner building IS, not an anomaly.

    Above the threshold the widening is cancelled ENTIRELY -- ``()``, the
    exact strict single-key behaviour. Fail-closed on the WIDENING, never on
    the attachment: the resolved key still attaches its own diagnostics,
    which is the same doctrine as the "Guerande" fix ("no carrier -> nothing
    widens"). Deliberately not a partial widening (keep the narrow carriers'
    keys, drop the wide one's): which ones to keep is an arbitrary,
    unmeasured choice.
    """
    depasse = largeur_porteur > PLAFOND_LARGEUR_PORTEUR
    return PlafondLargeur(
        cles=() if depasse else tuple(cles_elargies),
        depasse=depasse,
        largeur_porteur=largeur_porteur,
    )


class FiltrageVoie(NamedTuple):
    """Outcome of the street-number discrimination -- rows kept plus counts.

    ``exclus`` and ``sans_numero`` exist to be OBSERVED (ticket T5): a row
    silently dropped and a row kept for want of a key must never look alike
    from the outside.
    """

    gardes: list[dict]
    exclus: int
    sans_numero: int


def filtre_par_numero_voie(
    rows: list[dict], cles_reference: Sequence[str]
) -> FiltrageVoie:
    """Keep the DPE rows of the requested street number only (D-13, T4).

    Replaces a filter that could never work: it matched the caller's full
    postal address against ``complement_adresse_logement``, which holds
    floor/door/building and nothing else -- ``diagnostics_dpe`` carries no
    postal address field, so two street numbers of one parcel were never
    separable that way (verified in prod and in base, 2026-08-04). The number
    read from ``identifiant_ban`` is the only address-bearing signal a DPE
    row does carry.

    *cles_reference* is the union of the resolved BAN key and the other keys
    of the SAME RNB building (R4-02): a corner building has two entrances on
    two streets, hence two legitimate numbers. Taking the union here is what
    keeps discrimination and widening from cancelling each other out.

    Doctrine (Ianis, 2026-08-04), asymmetric on purpose:

    - fail-CLOSED on a row whose key yields a number: a foreign number is the
      neighbour's dwelling (the PR #47 regression), it is dropped and counted;
    - fail-OPEN and TRACED on a row with no key, or a key with no readable
      number: nothing proves it belongs elsewhere, so the pre-existing
      spatial attachment (12 m + non-ambiguity, Phase 45) still decides and
      the row is counted apart.

    When no reference number is readable either -- no ``ban_id``, or a
    voie-level one -- the filter is a no-op: a fiche never falls to zero card
    because the resolved key carries no number.
    """
    numeros = {n for n in (numero_voie_ban(c) for c in cles_reference) if n}
    if not numeros:
        return FiltrageVoie(rows, 0, 0)

    gardes: list[dict] = []
    exclus = 0
    sans_numero = 0
    for row in rows:
        numero = numero_voie_ban(row.get("identifiant_ban"))
        if numero is None:
            sans_numero += 1
            gardes.append(row)
        elif numero in numeros:
            gardes.append(row)
        else:
            exclus += 1
    return FiltrageVoie(gardes, exclus, sans_numero)


# Attachment-method instrumentation (PLAN-RATTACHEMENT-DPE-2026-08-05, Lot 0
# item 0.3). Each fetched DPE row carries the path that attached it, under a
# PRIVATE key: it is counted for the fiche, never published per row. Since
# phase 01-04 there is no per-row publication at all -- the logement card that
# could have carried it was removed (see docs/REPO-STATE.md).
CLE_RATTACHEMENT = "_rattachement"

RATTACHEMENT_EXACT = "exact"
RATTACHEMENT_ELARGI = "elargi_batiment"
RATTACHEMENT_SPATIAL = "spatial"
# Ordered by strength of evidence, and published in that order. A fourth bucket
# ``preuve_batiment`` existed from 2026-08-05 to 2026-08-06, for the Lot C2
# building-proof path removed after measurement (see the module docstring of
# ``app.logements.queries``). It is dropped rather than kept at a permanent 0:
# a counter nothing can ever increment reads as "this path serves fiches" to
# every later reader, and the vocabulary is a published contract.
METHODES_RATTACHEMENT = (
    RATTACHEMENT_EXACT,
    RATTACHEMENT_ELARGI,
    RATTACHEMENT_SPATIAL,
)


def marque_rattachement(rows: list[dict], methode: str) -> list[dict]:
    """Tag *rows* with the fetch path that attached them (0.3). Mutates.

    Determinism rule, applied everywhere: PROVENANCE wins over any property
    of the row itself. A row returned by the spatial path is ``spatial`` even
    when it carries a BAN key -- the full fallback runs precisely when the key
    path attached nothing, so its rows were attached by geometry, not by key.
    One row, one category, never two buckets.
    """
    for row in rows:
        row[CLE_RATTACHEMENT] = methode
    return rows


def marque_rattachement_ban(rows: list[dict], ban_id: str) -> list[dict]:
    """Split the BAN path between the resolved key and the RNB widening (0.3).

    ``exact`` = the row's ``identifiant_ban`` IS the resolved key of the
    requested address; ``elargi_batiment`` = any other key of the SAME RNB
    building (R4-02, corner building with two entrances). Unambiguous: a row
    carries exactly one key, and the widened set never contains the resolved
    one (``cles_ban_batiment_rnb`` excludes it).
    """
    for row in rows:
        row[CLE_RATTACHEMENT] = (
            RATTACHEMENT_EXACT
            if row.get("identifiant_ban") == ban_id
            else RATTACHEMENT_ELARGI
        )
    return rows


def compte_rattachement(rows: Sequence[dict]) -> dict[str, int]:
    """Count attachment methods over the rows that actually feed the fiche.

    The four buckets are ALWAYS present, zeros included: a fiche served with
    no spatial fallback must say ``spatial: 0`` out loud, never omit the key
    -- the Lot 0 sweep diffs these numbers between two runs. Untagged rows are
    ignored rather than guessed at (every fetch path tags its own rows).
    """
    compte = dict.fromkeys(METHODES_RATTACHEMENT, 0)
    for row in rows:
        methode = row.get(CLE_RATTACHEMENT)
        if methode in compte:
            compte[methode] += 1
    return compte


def _extrait_batiment(texte: str) -> str | None:
    """Extract a building/entrance token, discarding opaque markers (D-23)."""
    m = _BATIMENT_RE.search(texte)
    if not m or _OPAQUE_TOKEN_RE.match(m.group(1)):
        return None
    return m.group(1).upper()


def _extrait_porte(texte: str) -> str | None:
    """Extract a door/unit-number token, discarding opaque markers (D-23)."""
    m = _PORTE_RE.search(texte)
    if not m or _OPAQUE_TOKEN_RE.match(m.group(1)):
        return None
    return m.group(1)


def discriminants(row: dict) -> dict:
    """Extract the 3 structured discriminants read from a raw DPE row.

    A discriminant is present only when it is actually interpretable
    (D-07): a purely numeric or "N/A"-style complement proves the logement
    exists but displays nothing useful, so it stays opaque here (Pitfall 2)
    -- the same rule that governs est_distinguable (D-23).
    """
    complement = row.get("complement_adresse_logement")
    texte = _strip_accents(complement) if complement else ""
    return {
        "etage": parse_etage(row.get("numero_etage_appartement"), complement),
        "batiment": _extrait_batiment(texte) if texte else None,
        "porte": _extrait_porte(texte) if texte else None,
    }


def est_distinguable(row: dict) -> bool:
    """A DPE becomes a card only if a discriminant is readable (D-07/D-23)."""
    return any(v is not None for v in discriminants(row).values())


def _ecarte_remplaces(rows: list[dict]) -> list[dict]:
    """Drop DPE rows superseded by a re-survey in the same set (D-05 pass 1).

    ADEME's ``numero_dpe_remplace`` states explicitly that a new DPE
    replaces an older one of the SAME logement -- a stronger signal than
    any heuristic. Applied to every appartement row, distinguishable or
    not (D-05), so a re-diagnosed opaque logement never inflates the public
    "non distinguables" count (D-07).
    """
    remplaces = {r["numero_dpe_remplace"] for r in rows if r.get("numero_dpe_remplace")}
    return [r for r in rows if r.get("numero_dpe") not in remplaces]


def _groupe_par_etage_complement(rows: list[dict]) -> dict[tuple, list[dict]]:
    """Bucket distinguishable rows by exact (etage, normalized complement)."""
    groupes: dict[tuple, list[dict]] = {}
    for row in rows:
        cle = (
            discriminants(row)["etage"],
            normalise_complement(row.get("complement_adresse_logement")),
        )
        groupes.setdefault(cle, []).append(row)
    return groupes


def _dans_tolerance(ancre: float | None, valeur: float | None) -> bool:
    """True when *valeur* sits within +/-2 m2 of the cluster anchor (D-05)."""
    if ancre is None or valeur is None:
        return ancre is valeur
    return abs(ancre - valeur) <= 2.0


def _cluster_par_surface(rows: list[dict]) -> list[list[dict]]:
    """Cluster same-(etage, complement) rows by an absolute 2 m2 tolerance.

    Sorted ascending by surface; each cluster's anchor is its first
    (smallest) member, so the tolerance never drifts across a chain of
    small consecutive steps.
    """
    tri = sorted(
        rows,
        key=lambda r: (
            r.get("surface_habitable") is None,
            r.get("surface_habitable") or 0,
        ),
    )
    clusters: list[list[dict]] = []
    for row in tri:
        surface = row.get("surface_habitable")
        ancre = clusters[-1][0].get("surface_habitable") if clusters else None
        if clusters and _dans_tolerance(ancre, surface):
            clusters[-1].append(row)
        else:
            clusters.append([row])
    return clusters


def _plus_recent(cluster: list[dict]) -> dict:
    """Pick the most recently established DPE within a duplicate cluster."""
    return max(cluster, key=lambda r: r.get("date_etablissement_dpe") or date.min)


def _enrichit_logement(row: dict) -> dict:
    """Attach structured discriminants and display metadata to a kept row.

    Discriminants are attached HERE, not recomputed downstream (n2), so the
    frontend never parses free text (D-10). Peremption is read from
    date_fin_validite_dpe, never recomputed from a fixed validity window
    (D-08).

    ``rattachement`` (Lot D, decision 2026-08-05) carries the per-row
    provenance. A cluster collapsing rows of mixed provenance keeps the
    picked row's tag -- the provenance of the diagnostic actually elected,
    never a merge of the two.

    NOTE (phase 01-04): nothing this function produces reaches a response any
    more. `LogementItem`, the card it fed through Plan 48-06, was removed with
    the always-empty ``logements`` list, and ``dedup_logements`` -- this
    function's only caller -- now has no applicative caller of its own. The
    chain is kept and tested as-is: retiring it is a wider lot than the field
    removal, and is NOT part of the D-03 list. See docs/REPO-STATE.md.
    """
    d = discriminants(row)
    date_fin = row.get("date_fin_validite_dpe")
    date_etab = row.get("date_etablissement_dpe")
    return {
        **row,
        "etage": d["etage"],
        "batiment": d["batiment"],
        "porte": d["porte"],
        "perime": bool(date_fin and date_fin < date.today()),
        "annee_diagnostic": date_etab.year if date_etab else None,
        "rattachement": row.get(CLE_RATTACHEMENT),
    }


def _trie_deterministe(logements: list[dict]) -> list[dict]:
    """Order kept logements deterministically: etage, surface, numero_dpe.

    None sorts before any known value, so the order never depends on dict
    or insertion order (D-05).
    """
    return sorted(
        logements,
        key=lambda r: (
            r["etage"] is not None,
            r["etage"] if r["etage"] is not None else 0,
            r.get("surface_habitable") is not None,
            r.get("surface_habitable") or 0,
            r.get("numero_dpe") or "",
        ),
    )


def _compte_classes_dpe(rows: list[dict]) -> dict[str, int]:
    """Count DPE energy classes across the full deduplicated set (n3).

    Includes both displayed cards and non-distinguishable rows: the banner
    copy says "known diagnostics", not "displayed logements" (decision
    Ianis). Absent classes are omitted entirely -- never present at 0.
    """
    compte: dict[str, int] = {}
    for row in rows:
        classe = row.get("etiquette_dpe")
        if not classe:
            continue
        compte[classe] = compte.get(classe, 0) + 1
    return compte


def dedup_logements(rows: list[dict]) -> tuple[list[dict], int, dict[str, int]]:
    """Collapse raw DPE rows into deduplicated logements (D-05/D-07/D-08).

    Order is load-bearing (it drives a publicly displayed count): drop
    ``immeuble`` rows (Pitfall 5), apply the EXACT ``numero_dpe_remplace``
    pass to every remaining appartement row, THEN split
    distinguishable/non-distinguishable, THEN cluster the distinguishable
    rows by the D-05 heuristic key. The returned distribution counts the
    full deduplicated set -- kept logements PLUS non-distinguishable rows
    -- because this is the only function that still holds the
    non-distinguishable rows (n3, decision Ianis): the banner copy says
    "diagnostics connus", not "logements affiches". Contrast with the count
    feeding detection signal D2 (``len(logements) + non_distinguables``,
    see detection.calcule_signaux): the DENOMINATOR shown here
    (``len(logements)``) is a DIFFERENT, deliberately narrower number --
    two locked definitions, not an inconsistency (see caller, Plan 48-06).
    """
    appartements = [r for r in rows if r.get("type_batiment") == "appartement"]
    apres_exact = _ecarte_remplaces(appartements)
    distinguables = [r for r in apres_exact if est_distinguable(r)]
    non_distinguables_rows = [r for r in apres_exact if not est_distinguable(r)]

    logements = [
        _enrichit_logement(_plus_recent(cluster))
        for groupe in _groupe_par_etage_complement(distinguables).values()
        for cluster in _cluster_par_surface(groupe)
    ]
    logements = _trie_deterministe(logements)
    distribution = _compte_classes_dpe(logements + non_distinguables_rows)
    return logements, len(non_distinguables_rows), distribution
