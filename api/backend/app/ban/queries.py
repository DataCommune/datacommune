"""Raw SQL queries for geocoding and reverse geocoding.

Uses full-text search (tsvector) for fast filtering with pg_trgm
word_similarity scoring, city detection for commune pre-filtering,
and PostGIS KNN for spatial nearest-neighbor.
"""

import csv
import errno
import logging
import re
import socket
import time
import unicodedata
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Literal

from sqlalchemy import Row, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import expand_code_insee, normalize_code_insee, resolve_plm_alias
from app.resolve import telemetry

logger = logging.getLogger(__name__)

_RE_POSTAL = re.compile(r"\b(\d{5})\b")
# The repetition group captures spelled-out forms AND single letters, glued
# or detached ("12 bis", "12 b", "12b", "2a"). A single letter is a real BAN
# repetition in its own right — "12 b" is NOT "12 bis", they are two distinct
# addresses (measured 2026-08-07: 9/14 corpus repetitions got a wrong ban_id
# because the old regex swallowed the letter as an abbreviation). The group
# only matches when followed by whitespace, so street words ("avenue",
# "terrasse", "quai") are never eaten thanks to regex backtracking.
_RE_NUMERO = re.compile(r"^(\d+)\s*(bis|ter|quater|[a-z])?\s+", re.IGNORECASE)

# "taire": a SQL failure is logged and degrades to "no result" (historical
# behaviour of /v1/geocode and /v1/resolve). "lever": an expected database
# failure raises GeocodageIndisponibleError, so a caller can tell an outage
# from a zero; any other error keeps its own type (C55, fix 4).
ModeErreurs = Literal["taire", "lever"]

# SQLSTATEs of an availability failure (C55 fix 4, C58 WR-01): a relation
# missing during a publication swap and a cancelled or timed-out statement
# (as app.commune.queries and app.common.publication_readiness), a server
# shutting down or refusing connections (57P01 to 57P03); and the classes
# connection exception (08) and insufficient resources (53).
_SQLSTATES_INDISPONIBLES = frozenset({"42P01", "57014", "57P01", "57P02", "57P03"})
_CLASSES_INDISPONIBLES = frozenset({"08", "53"})


class GeocodageIndisponibleError(Exception):
    """A geocoding SQL statement failed (strategy or city detection).

    Raised detached from the database error (C59 B1, see
    :func:`erreur_de_lecture`): a later error wrapping it (a failing session
    close) never carries the driver message, which may quote the input.
    """


class RechercheTropLargeError(GeocodageIndisponibleError):
    """Strategy D ran out of its budget: the search was too broad to finish.

    Neither an outage (the same input meets the same budget at every try, a
    retry reruns the same read) nor an absence (the read was stopped before
    it could tell): R-08, 2026-11-10. A subclass of the outage so that a
    caller that only knows the outage (``/v1/dvf/adresse``) keeps its 503
    unchanged; the address search reads it first, as its own state.
    """


class GeocodageDefautError(Exception):
    """A geocoding read failed on a defect, not an outage (C55 fix 4, C58).

    It follows the 500 path, whose middleware logs it: its message is a
    bounded code (exception class, SQLSTATE), never the input, the SQL
    parameters nor the driver message, which may quote the value (PRIV-01,
    CR-01). It is raised after the ``except`` block, so it has no
    ``__context__`` at all: ``from None`` alone only suppresses the context,
    which Starlette's ``BaseHTTPMiddleware`` re-attaches as the cause.
    """

    def __init__(self, exc: BaseException) -> None:
        sqlstate = _sqlstate(exc)
        code = telemetry.error_code_for(exc)
        super().__init__(f"{code} {sqlstate}" if sqlstate else code)


_RE_SQLSTATE = re.compile(r"^[0-9A-Z]{5}$")


def _sqlstate(exc: BaseException) -> str | None:
    """The five-character SQLSTATE of a database error, nothing else."""
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if isinstance(sqlstate, str) and _RE_SQLSTATE.match(sqlstate):
        return sqlstate
    return None


# Raw OS errors of a connection that could not be made or was lost (C59 I1):
# refused, reset, aborted (ConnectionError), name resolution, connect timeout,
# unreachable host or network. Any other OSError (a missing certificate, a
# permission refused, an invalid argument) is a lasting defect.
_ERRNOS_INDISPONIBLES = frozenset({errno.EHOSTUNREACH, errno.ENETUNREACH})
# A host with several addresses (IPv6 and IPv4), all refused: asyncio's
# create_connection (uvloop alike) folds the refusals into one bare OSError,
# errno None, whose only argument starts so (C60). Its parts are lost.
_ECHEC_AGREGE = "Multiple exceptions: "


def _connexion_impossible(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, socket.gaierror)):
        return True
    if type(exc) is not OSError:
        return False
    if exc.errno is None:
        return (
            len(exc.args) == 1
            and isinstance(exc.args[0], str)
            and exc.args[0].startswith(_ECHEC_AGREGE)
        )
    return exc.errno in _ERRNOS_INDISPONIBLES


def _base_indisponible(exc: BaseException) -> bool:
    """The database could not answer: an outage, not a defect (C55 fix 4, C58).

    Read from the SQLSTATE the driver error carries: the asyncpg adapter of
    SQLAlchemy never yields ``OperationalError``, a connection failure or an
    exhausted resource arrives as a plain ``DBAPIError`` (WR-01). A
    disconnection detected by SQLAlchemy sets ``connection_invalidated``. A
    server that cannot be reached arrives as a raw ``OSError`` of asyncpg,
    an outage only for an identified connection error (C59 I1), the refusal
    of every address of the host included (C60); an exhausted pool as
    SQLAlchemy's ``TimeoutError``. A Python error, another OSError or another
    SQL error is a defect.
    """
    if isinstance(exc, PoolTimeoutError) or _connexion_impossible(exc):
        return True
    if not isinstance(exc, DBAPIError):
        return False
    if exc.connection_invalidated:
        return True
    sqlstate = _sqlstate(exc)
    return sqlstate is not None and (
        sqlstate in _SQLSTATES_INDISPONIBLES or sqlstate[:2] in _CLASSES_INDISPONIBLES
    )


def erreur_de_lecture(exc: BaseException) -> Exception:
    """The bounded error of a failed read, to raise after the ``except`` block.

    An outage is the typed :class:`GeocodageIndisponibleError`, anything else
    the bounded :class:`GeocodageDefautError`. Neither keeps *exc*: raised
    outside the ``except`` block, ``from None``, it has no ``__cause__`` nor
    ``__context__`` (CR-01, C59 B1).
    """
    if _base_indisponible(exc):
        return GeocodageIndisponibleError()
    return GeocodageDefautError(exc)


# Safe cast of the `numero` column (varchar) to integer for proximity sorting.
# Strips non-digit chars, returns NULL for empty/non-numeric values.
_NUMERO_INT = "NULLIF(regexp_replace(numero, '[^0-9]', '', 'g'), '')::int"


def normalize_address_text(value: str) -> str:
    """Normalize typography only, without fuzzy spelling or suffix aliases."""
    decomposed = unicodedata.normalize("NFKD", value.casefold().replace("œ", "oe"))
    letters = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]|_", " ", letters).split())


def _extract_numero(street_part: str) -> tuple[str, int | None, str | None]:
    """Extract leading house number and repetition from a street string.

    Returns (voie_clean, numero_int, rep):
    - "2 rue verte"     -> ("rue verte", 2, None)
    - "12 b rue verte"  -> ("rue verte", 12, "b")
    - "12 bis rue verte"-> ("rue verte", 12, "bis")
    - "rue verte"       -> ("rue verte", None, None)

    The repetition is lowercased verbatim — never expanded ("b" stays "b",
    it is a different address than "bis").
    """
    m = _RE_NUMERO.match(street_part)
    if m:
        rep = m.group(2).lower() if m.group(2) else None
        return street_part[m.end() :].strip(), int(m.group(1)), rep
    return street_part, None, None


# ---------------------------------------------------------------------------
# City detection query -- fast equality check on unaccented nom_commune
# ---------------------------------------------------------------------------
_DETECT_CITY = text("""
    SELECT nom_commune, code_commune
    FROM communes_lookup
    WHERE nom_commune_norm = unaccent(lower(:candidate))
       OR nom_commune_norm LIKE unaccent(lower(:candidate)) || ' %'
    LIMIT 1
""")

# Second chance, only for a candidate typed with a hyphen or an apostrophe
# that the equality above missed: the hyphens and apostrophes of both sides
# read as spaces (« les sables-d olonne » is Les Sables-d'Olonne). A candidate
# without punctuation is never compared so: « sainte anne » ending a street
# name never becomes the commune Sainte-Anne (0.87 % of the street names of 44
# and 85 would detect a commune, against 0.07 % with this narrow rule; second
# k6 firing, 2026-11-02). A statement of its own, run once for all the
# candidates: every other detection keeps its plan and its cost.
_DETECT_CITY_SANS_PONCTUATION = text("""
    SELECT translate(nom_commune_norm, '-''', '  ') AS cle, nom_commune, code_commune
    FROM communes_lookup
    WHERE translate(nom_commune_norm, '-''', '  ') = ANY(CAST(:cles AS text[]))
""")
# unaccent already reads the typographic apostrophe as an apostrophe.
_RE_PONCTUATION_COMMUNE = re.compile(r"[-'\u2019]")

TYPES_DE_VOIE: tuple[str, ...] = (
    "rue",
    "avenue",
    "av",
    "chemin",
    "allée",
    "impasse",
    "place",
    "boulevard",
    "bd",
    "route",
    "quai",
    "cours",
    "square",
    "sentier",
    "passage",
    "rond-point",
    "lotissement",
    "résidence",
    "hameau",
    "voie",
    "promenade",
    "esplanade",
    "parvis",
)
# Normalized word sequences ("rond-point" -> ("rond", "point")).
_VOIES: frozenset[tuple[str, ...]] = frozenset(
    tuple(normalize_address_text(voie).split()) for voie in TYPES_DE_VOIE
)

# Words that link a street name to a place name ("rue de Rennes"): the place
# that follows them belongs to the street, it is not the commune typed.
_LIAISONS = frozenset(
    {"a", "au", "aux", "d", "de", "des", "du", "en", "et", "l", "la", "le", "les"}
    | {"sous", "sur"}
)

# Former commune names of the INSEE COG 2026, each with the current commune
# it belongs to: « ancenis » names Ancenis-Saint-Géréon (44003) since their
# merger in 2019. Written by scripts/build_anciennes_communes.py. Read once and
# kept in memory: asked only once no current commune ends the query, the
# lookup adds no statement and leaves _DETECT_CITY as it is.
ANCIENNES_COMMUNES_PATH = Path(__file__).with_name("anciennes_communes.csv")


@lru_cache(maxsize=1)
def _anciennes_communes() -> dict[str, list[tuple[bool, str]]]:
    """Normalized former name -> (name punctuated, current commune code)."""
    referentiel: dict[str, list[tuple[bool, str]]] = {}
    with ANCIENNES_COMMUNES_PATH.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ponctue = _RE_PONCTUATION_COMMUNE.search(row["nom"]) is not None
            referentiel.setdefault(normalize_address_text(row["nom"]), []).append(
                (ponctue, row["code_commune"])
            )
    return referentiel


# ---------------------------------------------------------------------------
# Strategy A: FTS + city pre-filter (city detected)
# Pre-filters by commune (~10K-50K rows), then FTS within that subset.
# Weighted scoring: 0.6 nom_voie match + 0.4 nom_commune match.
# ---------------------------------------------------------------------------
_STRATEGY_A = text("""
    SELECT ban_id, numero, rep, nom_voie, code_postal,
           code_commune, nom_commune,
           ST_X(position) AS longitude,
           ST_Y(position) AS latitude,
           word_similarity(unaccent(nom_voie), unaccent(:voie_clean)) AS score
    FROM adresses
    WHERE code_commune = ANY(:code_communes)
      AND nom_voie % :voie_clean
      {postal_filter}
    ORDER BY score DESC {numero_order}, ban_id, id
    LIMIT :limit
""")

# ---------------------------------------------------------------------------
# Strategy B: FTS without city filter (no city detected, or A returned 0)
# word_similarity scoring on full search_text.
# ---------------------------------------------------------------------------
_STRATEGY_B = text("""
    SELECT ban_id, numero, rep, nom_voie, code_postal,
           code_commune, nom_commune,
           ST_X(position) AS longitude,
           ST_Y(position) AS latitude,
           word_similarity(search_text, unaccent(:query)) AS score
    FROM adresses
    WHERE to_tsvector('french', search_text)
          @@ websearch_to_tsquery('french', unaccent(:query))
      {postal_filter}
    ORDER BY score DESC {numero_order}, ban_id, id
    LIMIT :limit
""")

# ---------------------------------------------------------------------------
# Strategy C: Trigram + city pre-filter (city detected, A+B returned nothing)
# Still fast because commune pre-filter reduces to ~10K rows.
# ---------------------------------------------------------------------------
_STRATEGY_C = text("""
    SELECT ban_id, numero, rep, nom_voie, code_postal,
           code_commune, nom_commune,
           ST_X(position) AS longitude,
           ST_Y(position) AS latitude,
           word_similarity(unaccent(nom_voie), unaccent(:voie_clean)) AS score
    FROM adresses
    WHERE code_commune = ANY(:code_communes)
      AND nom_voie % :voie_clean
      {postal_filter}
    ORDER BY score DESC {numero_order}, ban_id, id
    LIMIT :limit
""")

# ---------------------------------------------------------------------------
# Strategy D: Trigram without city (last resort -- no city, B returned nothing)
# This is the only potentially slow path but rare.
# ---------------------------------------------------------------------------
_STRATEGY_D = text("""
    SELECT ban_id, numero, rep, nom_voie, code_postal,
           code_commune, nom_commune,
           ST_X(position) AS longitude,
           ST_Y(position) AS latitude,
           word_similarity(search_text, unaccent(:query)) AS score
    FROM adresses
    WHERE search_text % unaccent(:query)
      {postal_filter}
    ORDER BY score DESC {numero_order}, ban_id, id
    LIMIT :limit
""")

# Server-side budget of Strategy D. On the national base the trigram index
# hands hundreds of thousands of candidates per partition to the recheck: the
# k6 firing of 2026-11-02 measured 10.4 s per completed call (max 14.8 s), the
# others cancelled by the 15 s cap of the engine. Past the budget the statement
# is cancelled (57014): under ``erreurs="lever"`` the search was too broad
# (RechercheTropLargeError), not down, since the same input meets the same
# budget at every try (R-08); no result under ``"taire"``. A cancellation
# before the budget has run out is not the budget's doing: an outage.
BUDGET_STRATEGIE_D_MS = 2000
_TIMEOUT_ACTUEL = text("SELECT current_setting('statement_timeout')")
_FIXER_TIMEOUT = text("SELECT set_config('statement_timeout', :valeur, true)")


def _budget_epuise(exc: BaseException, debut: float, budget_ms: int) -> bool:
    """The statement was cancelled by its own budget: 57014 once *budget_ms*
    has run out since *debut*. The SQLSTATE alone cannot tell it from an
    operator's cancellation, and its message is localized; the timing can."""
    ecoule_ms = (time.monotonic() - debut) * 1000
    return _sqlstate(exc) == "57014" and ecoule_ms >= budget_ms


async def _commune_sans_ponctuation(
    session: AsyncSession, tokens: list[str], max_city_tokens: int
) -> tuple[int, Row] | None:
    """Second chance of the city detection, after every exact candidate: the
    punctuated candidates in one statement, the shortest one winning as in the
    exact loop; (number of tokens, row), or None."""
    ponctuees = {
        normalize_address_text(" ".join(tokens[-n:])): n
        for n in range(max_city_tokens, 0, -1)
        if _RE_PONCTUATION_COMMUNE.search(" ".join(tokens[-n:]))
    }
    if not ponctuees:
        return None
    result = await session.execute(
        _DETECT_CITY_SANS_PONCTUATION, {"cles": list(ponctuees)}
    )
    lignes = result.all()
    if not lignes:
        return None
    row = min(lignes, key=lambda ligne: (ponctuees[ligne.cle], ligne.code_commune))
    return ponctuees[row.cle], row


async def _commune_en_fin(
    session: AsyncSession, tokens: list[str], max_city_tokens: int
) -> tuple[int, Row] | None:
    """The commune named by the last 1 to *max_city_tokens* tokens: exact
    names first, the shortest candidate winning, then the punctuated ones
    (:func:`_commune_sans_ponctuation`); (number of tokens, row), or None."""
    for n in range(1, max_city_tokens + 1):
        candidate = " ".join(tokens[-n:])
        result = await session.execute(_DETECT_CITY, {"candidate": candidate})
        row = result.first()
        if row is not None:
            return n, row
    return await _commune_sans_ponctuation(session, tokens, max_city_tokens)


def _commune_ancienne_en_fin(
    tokens: list[str], max_city_tokens: int
) -> tuple[int, list[str]] | None:
    """The current communes of the former commune named by the last 1 to
    *max_city_tokens* tokens, the shortest candidate winning; (number of
    tokens, codes), or None. The punctuation rule of the current names holds:
    a candidate without hyphen or apostrophe matches only a name without any
    (« saint gereon » ending a street name is not Saint-Géréon). A candidate
    after a linking word or a street type belongs to the street, as in the
    address search (« chemin de saint-géréon », « allée pierre »): a former
    name, absent from the lookup of the communes served, is a weaker sign."""
    referentiel = _anciennes_communes()
    for n in range(1, max_city_tokens + 1):
        precedent = normalize_address_text(tokens[-n - 1])
        if precedent in _LIAISONS or (precedent,) in _VOIES:
            continue
        candidate = " ".join(tokens[-n:])
        ponctuee = _RE_PONCTUATION_COMMUNE.search(candidate) is not None
        codes = {
            code
            for nom_ponctue, code in referentiel.get(
                normalize_address_text(candidate), []
            )
            if ponctuee or not nom_ponctue
        }
        if codes:
            return n, sorted({c for code in codes for c in expand_code_insee(code)})
    return None


async def _requete_est_une_commune(session: AsyncSession, query: str) -> bool:
    """True when the whole query names a commune, current or former, and
    nothing else (« Olonne-sur-Mer », « Ancenis-Saint-Géréon »). The city
    detection keeps at least one token for the street, so such a query reaches
    Strategy D, cancelled by its budget on the national base: a 503 for a query
    that holds no street at all (searches checked on 2026-11-09). With no
    street part there is no street name to confuse with a commune: the
    punctuation is ignored on both sides. A failed lookup answers False, the
    search then goes on as before."""
    cle = normalize_address_text(query)
    if not cle or any(c.isdigit() for c in cle) or len(cle.split()) > 6:
        return False
    if cle in _anciennes_communes():
        return True
    try:
        async with session.begin_nested():
            result = await session.execute(
                _DETECT_CITY_SANS_PONCTUATION, {"cles": [cle]}
            )
            return result.first() is not None
    except Exception:  # noqa: BLE001 - a missed shortcut, never an outage
        return False


async def _detect_city(
    session: AsyncSession, query: str, *, erreurs: ModeErreurs = "taire"
) -> tuple[str, str, list[str]] | None:
    """Detect a city name at the end of the query string.

    Tries last 1 token, then last 2, then last 3 tokens against
    the communes_lookup matview. Returns (street_part, city_part,
    code_communes) or None if no city detected.

    code_communes is expanded via expand_code_insee so PLM cities
    (Paris, Lyon, Marseille) include all arrondissement codes.
    """
    tokens = query.strip().split()
    # A linking word ending the input belongs to the street being typed
    # (« 12 rue de la »): matched as a prefix (LIKE 'la %', no order) it
    # named a commune « La … » taken at random (R-15, 2026-11-10).
    if not tokens or normalize_address_text(tokens[-1]) in _LIAISONS:
        return None

    # PLM bareword short-circuit: when the user types "... paris" / "... lyon"
    # / "... marseille" with no explicit postal code, communes_lookup has no
    # plain-name row (only arrondissement rows), so Strategy A would miss and
    # Strategy B trigram would match "Le Touquet-Paris-Plage" (62826). Resolve
    # directly to the canonical commune code and expand to all arrondissements.
    # Guarded by "no postal in query" so "1 rue de la paix 75002" keeps its
    # explicit postal routing unchanged, and "paris 1er" / "paris 4e" still
    # flow through communes_lookup (multi-token candidate, helper returns None).
    if _RE_POSTAL.search(query) is None and len(tokens) >= 2:
        last_token = tokens[-1]
        plm_code = resolve_plm_alias(last_token)
        if plm_code is not None:
            street_part = " ".join(tokens[:-1])
            code_communes = expand_code_insee(plm_code)
            return (street_part, last_token, code_communes)

    # Try last N tokens (1, 2, 3) as potential city name.
    # Use a SAVEPOINT so that a failed lookup (e.g. missing matview)
    # does not poison the session transaction for subsequent queries.
    max_city_tokens = min(3, len(tokens) - 1)  # Keep at least 1 token for street
    erreur: Exception | None = None
    try:
        async with session.begin_nested():
            trouvee = await _commune_en_fin(session, tokens, max_city_tokens)
            if trouvee is not None:
                n, row = trouvee
                street_part = " ".join(tokens[:-n])
                city_part = " ".join(tokens[-n:])
                code_commune = row.code_commune
                # Normalize arrondissement -> commune before expanding.
                # Without this, "paris" matches 75101 (1er) and expand_code_insee
                # only returns [75101], causing Strategy A to miss all other
                # arrondissements and fall back to Strategy B which then matches
                # Le Touquet-Paris-Plage via trigram score.
                normalized = normalize_code_insee(code_commune)
                code_communes = expand_code_insee(normalized)
                return (street_part, city_part, code_communes)
    except Exception as exc:  # noqa: BLE001 - re-raised bounded, below
        # Bounded code only (PRIV-01): the query text and the exception
        # message may carry an address — see app.resolve.telemetry.
        telemetry.emit_resolve_event(
            logger,
            "city_detection_failed",
            level=logging.WARNING,
            error_code=telemetry.error_code_for(exc),
        )
        if erreurs == "lever":
            erreur = erreur_de_lecture(exc)
    else:
        # The base answered and no current commune ends the query: a former
        # one may (« ancenis »). Out of the SAVEPOINT: no statement.
        ancienne = _commune_ancienne_en_fin(tokens, max_city_tokens)
        if ancienne is not None:
            n, code_communes = ancienne
            return (" ".join(tokens[:-n]), " ".join(tokens[-n:]), code_communes)
    if erreur is not None:
        raise erreur from None

    return None


# Allowed postal_filter values (whitelist for safety)
_POSTAL_FILTERS = {
    "": "",
    "postal": "AND code_postal = :code_postal",
}

# Hosted departements, applied before ORDER BY and LIMIT so that rows of an
# excluded departement can never fill the limit (R02-05/2).
_DEPARTEMENT_FILTER = "AND code_departement = ANY(CAST(:departements AS text[]))"

# Resolve alone requests these filters. Autocomplete retains fuzzy numbers and
# its full list of suggestions. Filtering before LIMIT keeps a long numbered
# street from hiding the requested number or an actual unnumbered source row.
_IDENTITY_FILTERS = {
    "": "",
    "numbered": (
        "AND btrim(numero) = :identity_numero "
        "AND lower(btrim(coalesce(rep, ''))) IN ('', :identity_rep)"
    ),
    "unnumbered": "AND nullif(btrim(numero), '') IS NULL",
}


def _normalized_label_sql(expression: str) -> str:
    """Normalize only static SQL label expressions; input stays in bind params."""
    return (
        f"btrim(regexp_replace(unaccent(lower({expression})), "
        "'[^[:alnum:]]+', ' ', 'g'))"
    )


_STREET_LABELS_SQL = (
    "concat_ws(' ', nullif(btrim(numero), ''), nullif(btrim(rep), ''), nom_voie)",
    "concat(coalesce(btrim(numero), ''), coalesce(btrim(rep), ''), ' ', nom_voie)",
)
_PLM_NAME_SQL = """CASE
    WHEN code_commune = '75056' OR code_commune ~ '^751(0[1-9]|1[0-9]|20)$'
        THEN 'Paris'
    WHEN code_commune = '69123' OR code_commune ~ '^6938[1-9]$' THEN 'Lyon'
    WHEN code_commune = '13055' OR code_commune ~ '^132(0[1-9]|1[0-6])$'
        THEN 'Marseille' END"""
_PLM_DISTRICT_SQL = (
    f"regexp_replace({_normalized_label_sql('nom_commune')}, ' arrondissement$', '')"
)
_IDENTITY_LABELS_SQL = ", ".join(
    label
    for street in _STREET_LABELS_SQL
    for label in (
        _normalized_label_sql(f"concat_ws(' ', {street}, nom_commune)"),
        f"CASE WHEN ({_PLM_NAME_SQL}) IS NOT NULL THEN "
        + _normalized_label_sql(f"concat_ws(' ', {street}, {_PLM_NAME_SQL})")
        + " END",
        f"CASE WHEN ({_PLM_NAME_SQL}) IS NOT NULL THEN "
        + _normalized_label_sql(f"concat_ws(' ', {street}, {_PLM_DISTRICT_SQL})")
        + " END",
        f"CASE WHEN :identity_has_postal THEN {_normalized_label_sql(street)} END",
    )
)
_IDENTITY_LABEL_FILTER = f"AND :identity_query IN ({_IDENTITY_LABELS_SQL})"

# Allowed numero_order values (whitelist for safety). Every strategy then
# breaks the remaining ties by ``ban_id`` (plan 04.1-15, D-128), then by the
# row key ``id`` (one BAN key may carry several rows): a total order, so that
# ``limit=1`` (each page of ``/v1/dvf/adresse``) always retains the same point
# and the routes never differ on tied addresses.
# Among rows tied on street score and numero proximity ("12", "12 bis" and
# "12 b" all strip to 12), the row whose `rep` matches the requested
# repetition wins — including the empty repetition, so a plain "12" request
# prefers the bare address over "12 bis". Before 2026-08-07 the query
# selected `rep` but never used it: the tie was left to the FTS score alone.
_NUMERO_ORDERS = {
    "": "",
    "numero": (
        f", ABS({_NUMERO_INT} - :target_numero) ASC NULLS LAST"
        ", (lower(btrim(coalesce(rep, ''))) = :target_rep) DESC"
    ),
}


async def _execute_query(
    session: AsyncSession,
    template: text,
    params: dict,
    postal_filter: str,
    numero_order: str = "",
    identity_filter: str = "",
    *,
    departements: bool = False,
    erreurs: ModeErreurs = "taire",
    budget_ms: int | None = None,
) -> list[dict]:
    """Execute a geocoding SQL template with postal/numero injection.

    postal_filter and numero_order are always selected from hardcoded
    whitelists -- never user-provided SQL. *departements* adds the hosted
    departement filter (bound as ``:departements``). *budget_ms* bounds the
    statement on the server; the previous ``statement_timeout`` is restored
    after it (a failure rolls the savepoint back, and the setting with it).
    """
    filter_sql = " ".join(
        (
            _POSTAL_FILTERS.get(postal_filter, ""),
            _IDENTITY_FILTERS.get(identity_filter, ""),
            _DEPARTEMENT_FILTER if departements else "",
        )
    )
    if identity_filter:
        filter_sql += " " + _IDENTITY_LABEL_FILTER
    order_sql = _NUMERO_ORDERS.get(numero_order, "")
    raw = template.text.replace("{postal_filter}", filter_sql)
    raw = raw.replace("{numero_order}", order_sql)
    sql = text(raw)
    debut = time.monotonic()
    try:
        async with session.begin_nested():
            if budget_ms is not None:
                precedent = (await session.execute(_TIMEOUT_ACTUEL)).scalar_one()
                await session.execute(_FIXER_TIMEOUT, {"valeur": str(budget_ms)})
                debut = time.monotonic()
            result = await session.execute(sql, params)
            rows = [dict(row._mapping) for row in result]
            if budget_ms is not None:
                await session.execute(_FIXER_TIMEOUT, {"valeur": precedent})
            return rows
    except Exception as exc:  # noqa: BLE001 - re-raised bounded, below
        # Bounded code only (PRIV-01): params contain the raw query/address.
        telemetry.emit_resolve_event(
            logger,
            "geocode_query_failed",
            level=logging.WARNING,
            error_code=telemetry.error_code_for(exc),
        )
        if erreurs != "lever":
            return []
        if budget_ms is not None and _budget_epuise(exc, debut, budget_ms):
            erreur: Exception = RechercheTropLargeError()
        else:
            erreur = erreur_de_lecture(exc)
    raise erreur from None


async def geocoder(  # noqa: C901
    session: AsyncSession,
    query: str,
    limit: int = 5,
    code_postal: str | None = None,
    *,
    strict_identity: bool = False,
    departements_heberges: Sequence[str] | None = None,
    erreurs: ModeErreurs = "taire",
) -> list[dict]:
    """Geocode a text address using 4-strategy approach.

    Strategy A: FTS + city pre-filter (when city detected)
    Strategy B: FTS without city filter (no city, or A returned 0)
    Strategy C: Trigram + city pre-filter (city detected, A+B empty)
    Strategy D: Trigram without city (last resort)

    City detection queries the DB for nom_commune matches on last tokens.
    Weighted scoring gives 0.6 weight to nom_voie and 0.4 to nom_commune
    when a city is detected, ensuring Paris queries return Paris results.

    ``strict_identity`` is reserved for resolve: full label equality is applied
    before LIMIT. The caller still verifies the returned identity and ambiguity.
    Autocomplete keeps its existing default behavior.

    ``departements_heberges`` (address search, 04-02) restricts every
    strategy to those departements before ORDER BY and LIMIT; an empty list
    reads nothing. ``erreurs="lever"`` raises GeocodageIndisponibleError on
    an expected database failure of a strategy or of the city detection
    instead of returning ``[]``; any other error propagates (C55, fix 4).
    Defaults keep /v1/geocode and /v1/resolve unchanged.
    """
    if departements_heberges is not None and not departements_heberges:
        return []
    if strict_identity:
        query = normalize_address_text(query)
    # Auto-extract postal code from query string if not provided explicitly
    if code_postal is None:
        m = _RE_POSTAL.search(query)
        if m:
            code_postal = m.group(1)
            query = query[: m.start()] + query[m.end() :]
            query = " ".join(query.split())  # normalize whitespace

    postal_filter = ""
    base_params: dict = {"query": query, "limit": limit}
    if code_postal is not None:
        postal_filter = "postal"
        base_params["code_postal"] = code_postal

    # Extract house number + repetition from the full query for tie-breaking
    _, query_numero, query_rep = _extract_numero(query.strip())
    numero_order = ""
    if query_numero is not None:
        numero_order = "numero"
        base_params["target_numero"] = query_numero
        base_params["target_rep"] = query_rep or ""

    # Keyword options of _execute_query, set only when they differ from the
    # defaults so the historical calls stay byte-identical.
    options: dict = {}
    if departements_heberges is not None:
        base_params["departements"] = list(departements_heberges)
        options["departements"] = True
    if erreurs != "taire":
        options["erreurs"] = erreurs

    identity_filter = ""
    if strict_identity:
        identity_filter = "unnumbered" if query_numero is None else "numbered"
        base_params["identity_numero"] = str(query_numero)
        base_params["identity_rep"] = query_rep or ""
        base_params["identity_query"] = query
        base_params["identity_has_postal"] = code_postal is not None

    # City detection selects one lookup row. Resolve must keep every possible
    # homonymous commune until full-label comparison, not silently preselect one.
    city_info = None
    if not strict_identity:
        city_info = await _detect_city(
            session, query, **({"erreurs": erreurs} if "erreurs" in options else {})
        )

    if city_info is not None:
        street_part, _city_part, code_communes = city_info

        # Strip the house number + repetition from street_part for better
        # voie similarity (a leftover "a"/"bis" polluted the trigram score)
        voie_clean, street_numero, street_rep = _extract_numero(street_part)
        # Prefer number from street_part if found, else from full query
        if street_numero is not None and query_numero is None:
            numero_order = "numero"
            base_params["target_numero"] = street_numero
            base_params["target_rep"] = street_rep or ""

        city_params = {
            **base_params,
            "voie_clean": voie_clean,
            "code_communes": code_communes,
        }

        # Strategy A: FTS + city pre-filter
        rows = await _execute_query(
            session,
            _STRATEGY_A,
            city_params,
            postal_filter,
            numero_order,
            identity_filter,
            **options,
        )
        if rows:
            return rows

        # Strategy B: FTS without city filter
        rows = await _execute_query(
            session,
            _STRATEGY_B,
            base_params,
            postal_filter,
            numero_order,
            identity_filter,
            **options,
        )
        if rows:
            return rows

        # Strategy C: Trigram + city pre-filter
        rows = await _execute_query(
            session,
            _STRATEGY_C,
            city_params,
            postal_filter,
            numero_order,
            identity_filter,
            **options,
        )
        if rows:
            return rows
    else:
        # No city detected
        # Strategy B: FTS without city filter
        rows = await _execute_query(
            session,
            _STRATEGY_B,
            base_params,
            postal_filter,
            numero_order,
            identity_filter,
            **options,
        )
        if rows:
            return rows

        # A commune name alone holds no street: no address to look for, and
        # Strategy D would only spend its budget (the commune options are
        # served by the commune search).
        if await _requete_est_une_commune(session, query):
            return []

        # Strategy D: Trigram without city (last resort), under its budget
        rows = await _execute_query(
            session,
            _STRATEGY_D,
            base_params,
            postal_filter,
            numero_order,
            identity_filter,
            **options,
            budget_ms=BUDGET_STRATEGIE_D_MS,
        )
        if rows:
            return rows

    return []


async def reverse_geocoder(
    session: AsyncSession,
    lat: float,
    lon: float,
    limit: int = 1,
    rayon_m: float = 200.0,
) -> list[dict]:
    """Find nearest addresses to GPS coordinates using PostGIS KNN.

    ST_DWithin filters by radius (uses spatial index), then the <->
    operator in ORDER BY triggers an index-assisted KNN scan on the
    GIST index -- no seq scan even on 26M rows. Addresses at the same
    distance are ordered by ``ban_id``, then ``id`` (plan 04.1-15, D-128).
    """
    sql = text("""
        SELECT ban_id, numero, rep, nom_voie, code_postal,
               code_commune, nom_commune,
               ST_X(position) AS longitude,
               ST_Y(position) AS latitude,
               ST_Distance(
                   position::geography,
                   ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
               ) AS distance_m
        FROM adresses
        WHERE ST_DWithin(
            position::geography,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
            :rayon_m
        )
        ORDER BY position <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
                 ban_id, id
        LIMIT :limit
    """)

    params = {"lat": lat, "lon": lon, "rayon_m": rayon_m, "limit": limit}
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result]
