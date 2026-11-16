"""Strict concordance of a DPE raw address with its BAN key (04-01, N-12).

The only served implementation of the rule measured by
``scripts/mesure_dpe_documents.py`` (corrected in 04-01): the raw number,
its suffix and the WHOLE street must equal the BAN ones; the verdict is
``non_evaluable`` as soon as one of them is missing or not interpretable.
SQL extracts the facts with the very fragments of the script (same regular
expressions, same ``unaccent`` normalisation), Python classifies them with
the CASE of the script. ``tests/integration/test_dpe_concordance_parity.py``
runs both on the same rows; the script stays the reference of the measured
figures and is not modified here.

:func:`extraire_adresse_brute` is a different, stricter reading used only by
the public projection (P-ADR-01): it decides whether a raw text is an
address without complement, never whether it matches.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Concordance = Literal["concordant", "ecart", "non_evaluable"]

# Same values as ``scripts.mesure_dpe_maison.NUMERO_TETE_RE`` and
# ``scripts.mesure_dpe_documents.ADRESSE_SQL_RE`` (equality-tested).
NUMERO_TETE_RE = r"^\s*([0-9]{1,5})(?![0-9])"
ADRESSE_SQL_RE = (
    "^[[:space:]]*0*[0-9]+([[:space:]]*(bis|ter|quater|quinquies)|[a-z])?[[:space:]]+"
)
SUFFIXES_MOTS: tuple[str, ...] = ("bis", "ter", "quater", "quinquies")


def normaliser_voie_sql(colonne: str) -> str:
    """Street normalisation of the script: lower, unaccent, non-alnum → space."""
    return (
        "regexp_replace(\n"
        f"    lower(unaccent(COALESCE({colonne}, '')))\n"
        "    , '[^a-z0-9]+', ' ', 'g'\n"
        ")"
    )


# Facts of one document ``d`` against the BAN row ``a`` of its key (the
# first one by ``a.id``, as ``adresse_cle`` of the script). Copied from the
# ``faits`` CTE of ``scripts/mesure_dpe_documents.py``; the raw texts never
# leave this select list.
_FAITS_GABARIT = r"""
           (regexp_match(d.adresse_brut, '__NUMERO_TETE_RE__'))[1]::integer
               AS numero_brut,
           (regexp_match(a.numero, '__NUMERO_TETE_RE__'))[1]::integer
               AS numero_ban,
           COALESCE(a.numero ~ '^[[:space:]]*0*[0-9]+[[:space:]]*$', false)
               AS numero_ban_interpretable,
           NULLIF(lower(btrim(a.rep)), '') AS suffixe_ban,
           COALESCE(
               (regexp_match(
                   lower(unaccent(COALESCE(d.adresse_brut, ''))),
                   '^[[:space:]]*0*[0-9]+[[:space:]]*(bis|ter|quater|quinquies)[[:space:]]+'
               ))[1],
               (regexp_match(
                   lower(unaccent(COALESCE(d.adresse_brut, ''))),
                   '^[[:space:]]*0*[0-9]+([a-z])[[:space:]]+'
               ))[1]
           ) AS suffixe_brut,
           lower(COALESCE(a.rep, '')) ~
               '^[[:space:]]*(bis|ter|quater|quinquies|[a-z])?[[:space:]]*$'
               AS suffixe_ban_interpretable,
           lower(unaccent(COALESCE(d.adresse_brut, ''))) ~
               '__ADRESSE_SQL_RE__[a-z0-9].*$'
               AS adresse_brute_interpretable,
           NULLIF(btrim(__VOIE_BAN__), '') AS voie_ban_normalisee,
           NULLIF(btrim(regexp_replace(
               __ADRESSE_BRUT__,
               '__ADRESSE_SQL_RE__', '', 'g'
           )), '') AS voie_brut_normalisee
"""

FAITS_CONCORDANCE_COLONNES_SQL = (
    _FAITS_GABARIT.replace("__NUMERO_TETE_RE__", NUMERO_TETE_RE)
    .replace("__ADRESSE_SQL_RE__", ADRESSE_SQL_RE)
    .replace("__VOIE_BAN__", normaliser_voie_sql("a.nom_voie"))
    .replace("__ADRESSE_BRUT__", normaliser_voie_sql("d.adresse_brut"))
)

# Stored complement columns of ``diagnostics_dpe`` (``complement_adresse_
# batiment`` is not imported, migration 075). The integration test compares
# this list with every ``complement_*`` column of the migrated table; the
# facts query below and the public projection read it (API IN-04).
COLONNES_COMPLEMENT: tuple[str, ...] = ("complement_adresse_logement",)

# Facts of the documents ``:ids`` of one partition, with the private columns
# the public projection needs (raw address, full raw address, complements).
# Read inside the snapshot of the page; never serialized.
FAITS_DOCUMENTS_SQL = f"""
    SELECT d.id,
           d.adresse_brut,
           d.adresse_complete_brut,
           {", ".join(f"d.{colonne}" for colonne in COLONNES_COMPLEMENT)},
           a.identifiant IS NOT NULL AS cle_jointe,
           {FAITS_CONCORDANCE_COLONNES_SQL}
    FROM diagnostics_dpe AS d
    LEFT JOIN LATERAL (
        SELECT ad.id AS identifiant, ad.numero, ad.rep, ad.nom_voie
        FROM adresses AS ad
        WHERE ad.ban_id = d.identifiant_ban
          AND ad.code_departement = d.code_departement
        ORDER BY ad.id
        LIMIT 1
    ) AS a ON true
    WHERE d.id = ANY(:ids)
      AND d.code_departement = :department
"""


@dataclass(frozen=True)
class FaitsConcordance:
    """Facts extracted by :data:`FAITS_CONCORDANCE_COLONNES_SQL`."""

    numero_brut: int | None
    numero_ban: int | None
    numero_ban_interpretable: bool
    suffixe_brut: str | None
    suffixe_ban: str | None
    suffixe_ban_interpretable: bool
    adresse_brute_interpretable: bool
    voie_ban_normalisee: str | None
    voie_brut_normalisee: str | None

    @classmethod
    def depuis_ligne(cls, ligne) -> "FaitsConcordance":
        return cls(
            numero_brut=ligne["numero_brut"],
            numero_ban=ligne["numero_ban"],
            numero_ban_interpretable=bool(ligne["numero_ban_interpretable"]),
            suffixe_brut=ligne["suffixe_brut"],
            suffixe_ban=ligne["suffixe_ban"],
            suffixe_ban_interpretable=bool(ligne["suffixe_ban_interpretable"]),
            adresse_brute_interpretable=bool(ligne["adresse_brute_interpretable"]),
            voie_ban_normalisee=ligne["voie_ban_normalisee"],
            voie_brut_normalisee=ligne["voie_brut_normalisee"],
        )


def concordance_adresse(faits: FaitsConcordance) -> Concordance:
    """Verdict of the script's CASE (``concordance_adresse``), same order."""
    if faits.numero_brut is None or faits.numero_ban is None:
        return "non_evaluable"
    if (
        not faits.numero_ban_interpretable
        or not faits.suffixe_ban_interpretable
        or not faits.adresse_brute_interpretable
    ):
        return "non_evaluable"
    if faits.voie_ban_normalisee is None or faits.voie_brut_normalisee is None:
        return "non_evaluable"
    if (
        faits.numero_brut == faits.numero_ban
        and (faits.suffixe_brut or "") == (faits.suffixe_ban or "")
        and faits.voie_ban_normalisee == faits.voie_brut_normalisee
    ):
        return "concordant"
    return "ecart"


# --- Strict decomposition for the public projection (P-ADR-01) -------------

# Characters a raw address may carry once accents are removed: ASCII letters,
# digits, spaces and a few punctuation marks. Anything else (brackets, « n° »,
# @, a non-Latin script that the normalisation would erase) makes the text
# non verifiable: it could hide a name the decomposition cannot see.
_CARACTERES_ADMIS = re.compile(r"^[A-Za-z0-9 ,.'\u2019\-]*$")
_LIGATURES = (("œ", "oe"), ("Œ", "OE"), ("æ", "ae"), ("Æ", "AE"))
_TETE = re.compile(
    r"^(?P<numero>[0-9]{1,4})"
    r"(?:(?: (?P<mot>bis|ter|quater|quinquies))|(?: ?(?P<lettre>[a-z])))?"
    r" (?P<reste>.+)$"
)
_CODE_POSTAL = re.compile(r"^[0-9]{5}$")


def _sans_accents(texte: str) -> str:
    for lettre, remplacement in _LIGATURES:
        texte = texte.replace(lettre, remplacement)
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c))


def normaliser_libelle(texte: str | None) -> str:
    """Python reading of :func:`normaliser_voie_sql`: lower, unaccent, words."""
    if not texte:
        return ""
    sans = _sans_accents(texte).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sans).split())


@dataclass(frozen=True)
class AdresseBrute:
    """Complete decomposition of a raw address; every token is accounted for."""

    numero: str
    suffixe: str | None
    voie: str
    code_postal: str | None
    commune: str | None


def extraire_adresse_brute(
    texte: str | None, *, commune: str | None = None
) -> AdresseBrute | None:
    """Number (1 to 4 digits), closed-list suffix, street, optional postcode
    (5 digits) and optional commune name equal to *commune*, nothing else.

    The street keeps every remaining token: a caller compares it to the BAN
    street, so a trailing word (a name, a floor) is never cut away. None
    when the text has a character outside the admitted set, does not start
    with a number or leaves no street.
    """
    if not texte or not _CARACTERES_ADMIS.match(_sans_accents(texte)):
        return None
    tete = _TETE.match(normaliser_libelle(texte))
    if tete is None:
        return None
    jetons = tete.group("reste").split()
    nom_commune = normaliser_libelle(commune).split() if commune else []
    commune_lue = None
    if nom_commune and jetons[-len(nom_commune) :] == nom_commune:
        jetons = jetons[: -len(nom_commune)]
        commune_lue = " ".join(nom_commune)
    code_postal = None
    if jetons and _CODE_POSTAL.match(jetons[-1]):
        code_postal = jetons.pop()
    if not jetons:
        return None
    return AdresseBrute(
        numero=tete.group("numero"),
        suffixe=tete.group("mot") or tete.group("lettre"),
        voie=" ".join(jetons),
        code_postal=code_postal,
        commune=commune_lue,
    )
