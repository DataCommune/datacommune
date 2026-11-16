"""Unit tests for _extract_numero — house number + repetition extraction.

Measured 2026-08-07 (internal note MESURE-RESOLVE-REPETITIONS-2026-08-07):
9/14 repetition-bearing addresses of the private corpus got a WRONG ban_id
from /v1/resolve, 7 of them announced `exact` with score 1.0. Root cause in
this module: `_RE_NUMERO` treated `b|t|q` as abbreviations of bis/ter/quater
and swallowed the letter, so the repetition never reached the SQL layer —
and the SQL never used `rep` anyway (covered by the integration test
tests/integration/test_ban_rep_ordering.py).

Three failure modes, all proven red on the pre-fix code:

1. "12 b rue X"  -> the `b` was swallowed (silently read as "12 bis");
2. "2a rue X"    -> numero lost (None), "2a" polluted the street name;
3. "2 a rue X"   -> numero kept but the street name polluted to "a rue X".

All addresses below are synthetic — no corpus address ever leaves the
private file (PRIV doctrine).
"""

import pytest

from app.ban.queries import _extract_numero

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# The three measured failure modes (red before the 2026-08-07 fix)
# ---------------------------------------------------------------------------


def test_lettered_repetition_detached_is_captured_not_swallowed():
    # Failure mode 1: "12 b" is NOT "12 bis" — two distinct BAN addresses.
    assert _extract_numero("12 b rue des mimosas") == ("rue des mimosas", 12, "b")
    assert _extract_numero("12 B rue des mimosas") == ("rue des mimosas", 12, "b")


def test_lettered_repetition_glued_keeps_the_numero():
    # Failure mode 2: "2a" glued lost the numero entirely (None) and the
    # whole "2a" leaked into the street name.
    assert _extract_numero("2a rue des mimosas") == ("rue des mimosas", 2, "a")
    assert _extract_numero("2A rue des mimosas") == ("rue des mimosas", 2, "a")


def test_lettered_repetition_detached_does_not_pollute_the_street():
    # Failure mode 3: "2 a rue X" kept numero 2 but the street became
    # "a rue X", degrading the trigram similarity score.
    assert _extract_numero("2 a rue des mimosas") == ("rue des mimosas", 2, "a")


# ---------------------------------------------------------------------------
# Spelled-out repetitions are captured verbatim (never conflated with letters)
# ---------------------------------------------------------------------------


def test_spelled_out_repetitions_are_captured():
    assert _extract_numero("12 bis rue des mimosas") == ("rue des mimosas", 12, "bis")
    assert _extract_numero("12 ter rue des mimosas") == ("rue des mimosas", 12, "ter")
    assert _extract_numero("3 quater rue du stade") == ("rue du stade", 3, "quater")
    # Glued spelled-out form.
    assert _extract_numero("2bis rue des mimosas") == ("rue des mimosas", 2, "bis")


def test_letter_is_never_expanded_to_bis():
    # The old regex read "b" as an abbreviation of "bis". The repetition
    # must stay verbatim: "b" and "bis" are different BAN addresses.
    _, _, rep = _extract_numero("12 b rue des mimosas")
    assert rep == "b"
    _, _, rep = _extract_numero("12 t rue des mimosas")
    assert rep == "t"


# ---------------------------------------------------------------------------
# Non-regression: plain addresses keep the pre-fix behaviour
# ---------------------------------------------------------------------------


def test_plain_numero_without_repetition():
    assert _extract_numero("2 rue des mimosas") == ("rue des mimosas", 2, None)


def test_no_leading_numero():
    assert _extract_numero("rue des mimosas") == ("rue des mimosas", None, None)


def test_street_words_are_not_read_as_repetitions():
    # Words starting with a repetition letter or prefix must not be eaten:
    # the optional group only matches when followed by whitespace.
    assert _extract_numero("2 avenue de la paix") == ("avenue de la paix", 2, None)
    assert _extract_numero("2 terrasse des oliviers") == (
        "terrasse des oliviers",
        2,
        None,
    )
    assert _extract_numero("4 grand rue") == ("grand rue", 4, None)
    assert _extract_numero("7 quai des chartrons") == ("quai des chartrons", 7, None)


def test_repetition_is_normalized_lowercase():
    assert _extract_numero("5 BIS rue du stade") == ("rue du stade", 5, "bis")
