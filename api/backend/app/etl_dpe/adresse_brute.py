"""Exact raw-address attachment of orphan DPE documents (plan 04.1-13, D-127).

An orphan document carries an ADEME BAN key absent from the local BAN
(``cle_ban_orpheline``). When that key names only a commune (one segment) or a
street (two segments), the raw address typed by the diagnostician may still
name exactly one local BAN address. This module is the rule measured in block
H of ``04-MESURE-DPE.md`` (script ``scripts/mesure_dpe_documents.py``, which
imports it: one implementation for what is measured and what is imported):

- number, suffix and street read in ``adresse_brut``; else in
  ``adresse_complete_brut`` without its trailing raw postcode and commune;
  three distinct reading failures (no number, unreadable format, range);
- strict equality on (INSEE of the key prefix, normalised street, number,
  suffix) against the local BAN; identical BAN candidates deduplicated first;
- attached only for a single candidate, number and suffix not contradicted,
  street code identical to the key's (a code of another length stays
  indeterminate: no cross-vintage mapping is proved).

No similarity, no threshold, no distance. A numbered orphan key is never
attached (deduced in 04-MESURE-DPE: an identical candidate would have joined
the key). The attachment stays « declared »: it names the BAN address the raw
text designates, never a dwelling, and the raw address is kept as published.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

# --- Block H: exact raw-address match, no similarity, no distance -------------
# Street-type abbreviations only, replaced on the FIRST word of the street
# (the type position); nothing else is rewritten (no saint/st, no article).
TYPES_VOIE_ABREGES: dict[str, str] = {
    "all": "allee",
    "av": "avenue",
    "ave": "avenue",
    "bd": "boulevard",
    "bld": "boulevard",
    "boul": "boulevard",
    "bvd": "boulevard",
    "ch": "chemin",
    "che": "chemin",
    "chem": "chemin",
    "chm": "chemin",
    "crs": "cours",
    "fbg": "faubourg",
    "fg": "faubourg",
    "ham": "hameau",
    "imp": "impasse",
    "lot": "lotissement",
    "mte": "montee",
    "pas": "passage",
    "pl": "place",
    "prom": "promenade",
    "psg": "passage",
    "qu": "quai",
    "r": "rue",
    "res": "residence",
    "rpt": "rond point",
    "rte": "route",
    "sq": "square",
    "vla": "villa",
}
SUFFIXES_NUMERO: tuple[str, ...] = ("bis", "ter", "quater", "quinquies")
# On the normalised text: house number, optional suffix (a bis/ter word,
# attached or not, or ONE letter attached to the number), then a street that
# starts with a letter. "12-14 rue x" (a range) and "12 b rue x" (detached
# letter) are not rewritten: the first has no usable number, the second keeps
# "b" in the street and matches nothing.
_ADRESSE_RE = re.compile(
    r"^(?P<numero>[0-9]{1,5})"
    r"(?:\s*(?P<mot>" + "|".join(SUFFIXES_NUMERO) + r")(?![a-z0-9])"
    r"|(?P<lettre>[a-z])(?![a-z0-9]))?"
    r"\s+(?P<voie>[a-z].*)$"
)
REGLE_NORMALISATION = (
    "minuscules ; accents retires (NFKD, oe/ae) ; tout caractere hors [a-z0-9] "
    "devient une espace ; espaces reduites ; premier mot de la voie seul "
    "developpe s'il est une abreviation de type de voie ("
    + ", ".join(f"{k}={v}" for k, v in sorted(TYPES_VOIE_ABREGES.items()))
    + ") ; numero et suffixe (bis, ter, quater, quinquies, ou une lettre collee "
    "au numero) conserves et compares a l'egalite ; aucune similarite, aucune "
    "distance"
)
TYPES_CLE: tuple[str, ...] = ("commune", "voie", "numerotee")
VERDICTS_H: tuple[str, ...] = (
    "sans_numero",
    "format_non_reconnu",
    "plage_numeros",
    "correspondance_unique",
    "correspondances_multiples",
    "aucune_correspondance",
)
ECHECS_LECTURE: frozenset[str] = frozenset(
    {"sans_numero", "format_non_reconnu", "plage_numeros"}
)


def _texte(valeur: Any) -> str:
    return "" if valeur is None else str(valeur).strip()


def normaliser_libelle(texte: Any) -> str:
    """Case, accents and punctuation only: the words stay as written."""
    brut = _texte(texte)
    for lettre, remplacement in (("œ", "oe"), ("Œ", "OE"), ("æ", "ae"), ("Æ", "AE")):
        brut = brut.replace(lettre, remplacement)
    decompose = unicodedata.normalize("NFKD", brut)
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sans_accent.lower()).split())


def normaliser_voie(texte: Any) -> str:
    mots = normaliser_libelle(texte).split()
    if mots and mots[0] in TYPES_VOIE_ABREGES:
        mots[0] = TYPES_VOIE_ABREGES[mots[0]]
    return " ".join(mots)


def decouper_adresse(texte: Any) -> tuple[int, str, str] | None:
    """(number, suffix or '', normalised street) or None without a usable number."""
    trouve = _ADRESSE_RE.match(normaliser_libelle(texte))
    if trouve is None:
        return None
    suffixe = trouve.group("mot") or trouve.group("lettre") or ""
    return int(trouve.group("numero")), suffixe, normaliser_voie(trouve.group("voie"))


def adresse_du_document(
    document: Mapping[str, Any],
) -> tuple[tuple[int, str, str] | None, str]:
    """``adresse_brut`` first; else ``adresse_complete_brut`` without its
    trailing raw postcode and commune when they are written there."""
    brut = document.get("adresse_brut")
    decoupe = decouper_adresse(brut)
    if decoupe is not None:
        return decoupe, "adresse_brut"
    complete_brute = document.get("adresse_complete_brut")
    complete = normaliser_libelle(complete_brute)
    fin = " ".join(
        x
        for x in (
            normaliser_libelle(document.get("code_postal_brut")),
            normaliser_libelle(document.get("nom_commune_brut")),
        )
        if x
    )
    if fin and complete.endswith(" " + fin):
        complete = complete[: -len(fin) - 1]
    decoupe = decouper_adresse(complete)
    if decoupe is not None:
        return decoupe, "adresse_complete_brut"
    textes_bruts = [_texte(brut), _texte(complete_brute)]
    if any(re.search(r"(^|\s)\d+\s*[-/]\s*\d+(\s|$)", texte) for texte in textes_bruts):
        return None, "plage_numeros"
    if any(re.search(r"\d", texte) for texte in (_texte(brut), complete)):
        return None, "format_non_reconnu"
    return None, "sans_numero"


def cle_adresse_ban(ligne: Mapping[str, Any]) -> tuple[str, int, str, str] | None:
    """Match key of a local BAN address: (INSEE, number, suffix, street)."""
    numero = _texte(ligne.get("numero"))
    if not numero.isdigit():
        return None
    return (
        _texte(ligne.get("code_commune")),
        int(numero),
        normaliser_libelle(ligne.get("rep")),
        normaliser_voie(ligne.get("nom_voie")),
    )


def type_cle(cle: Any) -> str:
    segments = _texte(cle).split("_")
    return ("commune", "voie")[len(segments) - 1] if len(segments) < 3 else "numerotee"


def indexer_adresses(
    lignes: Iterable[Mapping[str, Any]], besoins: set[tuple[str, str]]
) -> dict[tuple[str, int, str, str], list[tuple[str, str]]]:
    """Addresses of the needed (INSEE, street) pairs: key -> [(ban_id, cp)]."""
    uniques: dict[
        tuple[str, int, str, str], set[tuple[Any, Any, Any, Any, Any, Any]]
    ] = defaultdict(set)
    voies: dict[str, str] = {}
    for ligne in lignes:
        nom = _texte(ligne.get("nom_voie"))
        if nom not in voies:
            voies[nom] = normaliser_voie(nom)
        if (_texte(ligne.get("code_commune")), voies[nom]) not in besoins:
            continue
        cle = cle_adresse_ban(ligne)
        if cle is not None:
            uniques[cle].add(
                (
                    ligne.get("ban_id"),
                    ligne.get("code_postal"),
                    ligne.get("code_commune"),
                    ligne.get("numero"),
                    ligne.get("rep"),
                    ligne.get("nom_voie"),
                )
            )
    return {
        cle: [
            (_texte(candidat[0]), _texte(candidat[1]))
            for candidat in sorted(
                candidats, key=lambda valeurs: tuple(map(repr, valeurs))
            )
        ]
        for cle, candidats in uniques.items()
    }


def classer_document(
    document: Mapping[str, Any],
    index: Mapping[tuple[str, int, str, str], list[tuple[str, str]]],
) -> dict[str, Any]:
    cle = _texte(document.get("identifiant_ban"))
    segments = cle.split("_")
    decoupe, source = adresse_du_document(document)
    classe: dict[str, Any] = {
        "type_cle": type_cle(cle),
        "source": source,
        "verdict": "sans_numero",
        "contradiction_numero": None,
        "contradiction_suffixe": None,
        "voie_de_cle": None,
        "code_postal_concordant": None,
        "cle_retrouvee": None,
    }
    if decoupe is None:
        classe["verdict"] = source
        return classe
    numero, suffixe, voie = decoupe
    if classe["type_cle"] == "numerotee" and segments[2].isdigit():
        classe["contradiction_numero"] = int(segments[2]) != numero
        suffixe_cle = normaliser_libelle("_".join(segments[3:]))
        if (
            not suffixe_cle
            or suffixe_cle in SUFFIXES_NUMERO
            or (len(suffixe_cle) == 1 and suffixe_cle.isalpha())
        ):
            classe["contradiction_suffixe"] = suffixe_cle != suffixe
    candidats = index.get((segments[0], numero, suffixe, voie), [])
    if not candidats:
        classe["verdict"] = "aucune_correspondance"
    elif len(candidats) > 1:
        classe["verdict"] = "correspondances_multiples"
    else:
        ban_id, code_postal = candidats[0]
        classe["verdict"] = "correspondance_unique"
        classe["cle_retrouvee"] = ban_id
        if classe["type_cle"] != "commune":
            # Different lengths are kept as indeterminate; this measurement
            # has no cross-vintage mapping proving that both codes name a way.
            voie_trouvee = ban_id.split("_")[1].lower() if "_" in ban_id else ""
            voie_cle = segments[1].lower()
            if voie_trouvee == voie_cle:
                classe["voie_de_cle"] = "identique"
            elif len(voie_trouvee) == len(voie_cle):
                classe["voie_de_cle"] = "differente_meme_format"
            else:
                classe["voie_de_cle"] = "format_different"
        brut = _texte(document.get("code_postal_brut"))
        classe["code_postal_concordant"] = bool(brut) and brut == code_postal
    return classe


def sans_contradiction(classe: Mapping[str, Any]) -> bool:
    """« Unique sans contradiction » of block H, the one acceptance rule.

    A single candidate, number not contradicted, suffix neither contradicted
    nor uninterpretable for a numbered key, street code of the key identical
    (a different code, of the same or another length, never attaches).
    """
    return (
        classe["verdict"] == "correspondance_unique"
        and not classe["contradiction_numero"]
        and classe["contradiction_suffixe"] is not True
        and not (
            classe["type_cle"] == "numerotee"
            and classe["contradiction_suffixe"] is None
        )
        and classe["voie_de_cle"] not in {"differente_meme_format", "format_different"}
    )


# --- Reading and attachment API of the import ------------------------------------

CasLecture = Literal["numero", "sans_numero", "format_non_reconnu", "plage_numeros"]


@dataclass(frozen=True, slots=True)
class AdresseLue:
    """What the rule reads in the raw address of one document.

    ``cas`` is ``numero`` with the number, suffix ('' when none) and the
    normalised street, or one of the three reading failures, kept distinct.
    """

    cas: CasLecture
    numero: int | None = None
    suffixe: str | None = None
    voie: str | None = None
    source: str | None = None


def lire_adresse_brute(
    adresse_brut: Any,
    adresse_complete_brut: Any,
    code_postal_brut: Any,
    nom_commune_brut: Any,
) -> AdresseLue:
    """Number, suffix and street of a raw address (block H reading)."""
    decoupe, source = adresse_du_document(
        {
            "adresse_brut": adresse_brut,
            "adresse_complete_brut": adresse_complete_brut,
            "code_postal_brut": code_postal_brut,
            "nom_commune_brut": nom_commune_brut,
        }
    )
    if decoupe is None:
        return AdresseLue(cas=source)  # type: ignore[arg-type]
    numero, suffixe, voie = decoupe
    return AdresseLue(
        cas="numero", numero=numero, suffixe=suffixe, voie=voie, source=source
    )


def verdict_etape(classe: Mapping[str, Any]) -> str:
    """Counter name of one classified document in the import step."""
    if sans_contradiction(classe):
        return "rattaches_adresse_brute"
    if classe["verdict"] == "correspondance_unique":
        return "indetermines_contradiction"
    return f"indetermines_{classe['verdict']}"


def rattacher_commune(
    documents: Sequence[Mapping[str, Any]],
    adresses_ban: Iterable[Mapping[str, Any]],
    compteurs: Counter[str] | None = None,
) -> Iterator[tuple[str, str]]:
    """``(numero_dpe, ban_id)`` of the orphans of ONE commune that attach.

    *documents* are orphan documents whose key prefix is that commune;
    *adresses_ban* the local BAN rows of the commune (read once, only the
    needed streets are kept). A numbered key is never attached. Each
    document is counted in *compteurs* under :func:`verdict_etape`, numbered
    keys under ``orphelins_cle_numerotee``.
    """
    eligibles = [
        d for d in documents if type_cle(d.get("identifiant_ban")) != "numerotee"
    ]
    if compteurs is not None:
        compteurs["orphelins_cle_numerotee"] += len(documents) - len(eligibles)
    besoins: set[tuple[str, str]] = set()
    for document in eligibles:
        decoupe, _ = adresse_du_document(document)
        if decoupe is not None:
            insee = _texte(document.get("identifiant_ban")).split("_")[0]
            besoins.add((insee, decoupe[2]))
    index = indexer_adresses(adresses_ban, besoins) if besoins else {}
    for document in eligibles:
        classe = classer_document(document, index)
        verdict = verdict_etape(classe)
        if compteurs is not None:
            compteurs[verdict] += 1
        if verdict == "rattaches_adresse_brute":
            yield _texte(document.get("numero_dpe")), classe["cle_retrouvee"]


# --- Import step on the DPE candidate ------------------------------------------

# Comment of the column once the step ran on a candidate: the swap renames the
# candidate over the served relation, the comment goes with it. The API reads
# it to tell « computed, none here » (zero) from « never computed » (a gap).
# The candidate inherits the served comment (``LIKE ... INCLUDING ALL``); the
# step always sets or clears it.
MARQUEUR_CALCUL = "dc:rattachement-adresse-brute:v1"

COMPTEURS_ETAPE: tuple[str, ...] = (
    "orphelins_examines",
    "orphelins_cle_numerotee",
    "rattaches_adresse_brute",
    "indetermines_sans_numero",
    "indetermines_format_non_reconnu",
    "indetermines_plage_numeros",
    "indetermines_aucune_correspondance",
    "indetermines_correspondances_multiples",
    "indetermines_contradiction",
)
# Present orphans of the candidate (``cle_ban_orpheline``: non-empty key absent
# from the BAN of the departement), in commune order so that one commune is
# held in memory at a time. Tombstones carry no raw address.
ORPHELINS_CANDIDATE_SQL = """
SELECT d.numero_dpe, d.code_departement, d.identifiant_ban, d.adresse_brut,
       d.adresse_complete_brut, d.code_postal_brut, d.nom_commune_brut,
       split_part(btrim(d.identifiant_ban), '_', 1) AS insee
FROM diagnostics_dpe_staging d
WHERE NULLIF(btrim(d.identifiant_ban), '') IS NOT NULL
  AND d.is_tombstone IS NOT TRUE
  AND NOT EXISTS (
      SELECT 1
      FROM adresses a
      WHERE a.ban_id = d.identifiant_ban
        AND a.code_departement = d.code_departement
  )
ORDER BY d.code_departement, insee, d.numero_dpe
"""
# Local BAN rows of one commune, as block H reads them (``ADRESSES_COMMUNES_SQL``).
ADRESSES_COMMUNE_SQL = """
SELECT DISTINCT a.code_commune, a.numero, a.rep, a.nom_voie, a.code_postal,
       a.ban_id
FROM adresses a
WHERE a.code_departement = %(departement)s
  AND a.code_commune = %(commune)s
"""


def rattacher_candidate(conn: Any) -> dict[str, int]:
    """Fill ``ban_id_adresse_brute`` of the DPE candidate; return the counters.

    Runs inside the candidate transaction (phase A1 of the DPE swap), after
    the reconciliation and before the indexes. Orphans are streamed in
    commune order; the BAN rows of one commune are streamed and only its
    needed streets kept. Results go to a private temporary table, applied by
    one UPDATE. Without a BAN relation, nothing is attached and the counters
    say so (``ban_absente``).
    """
    compteurs: Counter[str] = Counter(dict.fromkeys(COMPTEURS_ETAPE, 0))
    with conn.cursor() as cur:
        present = cur.execute("SELECT to_regclass('public.adresses')").fetchone()[0]
        if present is None:
            logger.warning("Raw-address attachment skipped: no BAN relation")
            cur.execute(
                "COMMENT ON COLUMN diagnostics_dpe_staging.ban_id_adresse_brute IS NULL"
            )
            return {**compteurs, "ban_absente": 1}
        # A departement of the candidate without any BAN row (DPE imported
        # before the BAN on an empty base): computing would serve 0 attached
        # documents as a value. Not computed, the API says not_available
        # (04.1-REVIEW CR-01).
        sans_ban = cur.execute(
            "SELECT count(*) FROM (SELECT DISTINCT d.code_departement "
            "FROM diagnostics_dpe_staging d WHERE d.is_tombstone IS NOT TRUE "
            "AND NOT EXISTS (SELECT 1 FROM adresses a "
            "WHERE a.code_departement = d.code_departement)) s"
        ).fetchone()[0]
        if sans_ban:
            logger.warning(
                "Raw-address attachment skipped: %d departement(s) without BAN rows",
                sans_ban,
            )
            cur.execute(
                "COMMENT ON COLUMN diagnostics_dpe_staging.ban_id_adresse_brute IS NULL"
            )
            return {**compteurs, "ban_absente_departements": sans_ban}
        cur.execute(
            "CREATE TEMP TABLE _dpe_adresse_brute "
            "(numero_dpe TEXT NOT NULL, ban_id TEXT NOT NULL) ON COMMIT DROP"
        )

    def appliquer(groupe: list[dict[str, Any]]) -> None:
        departement, commune = groupe[0]["code_departement"], groupe[0]["insee"]
        compteurs["orphelins_examines"] += len(groupe)
        with conn.cursor(name="dpe_ban_commune") as ban:
            ban.execute(
                ADRESSES_COMMUNE_SQL, {"departement": departement, "commune": commune}
            )
            colonnes = [c.name for c in ban.description]
            lignes = (dict(zip(colonnes, ligne, strict=True)) for ligne in ban)
            rattaches = list(rattacher_commune(groupe, lignes, compteurs))
        if rattaches:
            with conn.cursor() as writer:
                writer.execute(
                    "INSERT INTO _dpe_adresse_brute (numero_dpe, ban_id) "
                    "SELECT * FROM unnest(%s::text[], %s::text[])",
                    ([n for n, _ in rattaches], [b for _, b in rattaches]),
                )

    with conn.cursor(name="dpe_orphelins") as reader:
        reader.execute(ORPHELINS_CANDIDATE_SQL)
        colonnes = [c.name for c in reader.description]
        groupe: list[dict[str, Any]] = []
        for ligne in reader:
            document = dict(zip(colonnes, ligne, strict=True))
            if groupe and (
                document["code_departement"],
                document["insee"],
            ) != (groupe[0]["code_departement"], groupe[0]["insee"]):
                appliquer(groupe)
                groupe = []
            groupe.append(document)
        if groupe:
            appliquer(groupe)

    with conn.cursor() as cur:
        cur.execute("ANALYZE _dpe_adresse_brute")
        cur.execute(
            "UPDATE diagnostics_dpe_staging AS staged "
            "SET ban_id_adresse_brute = r.ban_id "
            "FROM _dpe_adresse_brute AS r "
            "WHERE staged.numero_dpe = r.numero_dpe"
        )
        cur.execute(
            "COMMENT ON COLUMN diagnostics_dpe_staging.ban_id_adresse_brute "
            f"IS '{MARQUEUR_CALCUL}'"
        )
    logger.info("Raw-address attachment: %s", dict(compteurs))
    return dict(compteurs)
