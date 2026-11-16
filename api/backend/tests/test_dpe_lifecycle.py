"""Global replacement facts and independent, explicit calendar decisions."""

import hashlib
import json
from copy import deepcopy
from datetime import date
from itertools import permutations
from pathlib import Path

import pytest

from app.dpe.lifecycle import iter_lifecycle_facts, validity_at


def build_lifecycle_facts(records):
    """Small corpora only: the import streams the facts, never one dictionary."""
    return dict(iter_lifecycle_facts(records))


def record(number, replaces=None, **extra):
    return dict(numero_dpe=number, numero_dpe_remplace=replaces, **extra)


def test_global_chain_ignores_ban_regime_source_and_tombstone():
    rows = [
        record("a", identifiant_ban="A", regime_dpe="existant", is_tombstone=True),
        record("b", "a", identifiant_ban="B", regime_dpe="neuf"),
        record("c", "b", code_departement="85"),
        record("unrelated"),
    ]
    before = deepcopy(rows)
    facts = build_lifecycle_facts(iter(rows))
    assert facts["a"]["replacement_state"] == "replaced"
    assert facts["b"]["replacement_state"] == "replaced"
    assert facts["c"]["replacement_state"] == "no_successor_known"
    assert all(not item["graph_issues"] for item in facts.values())
    assert rows == before
    assert build_lifecycle_facts(rows[:3]) == {k: facts[k] for k in "abc"}


@pytest.mark.parametrize(
    "rows,issues",
    [
        ([record("a", "a")], ["cycle"]),
        ([record("a", "b"), record("b", "a"), record("c", "a")], ["cycle", "fork"]),
        ([record("a", "missing"), record("b", "a")], ["missing_target"]),
        ([record("a"), record("b", "a"), record("c", "a")], ["fork"]),
    ],
)
def test_component_reserves_are_deterministic_in_both_directions(rows, issues):
    expected = build_lifecycle_facts(rows)
    for ordered in permutations(rows):
        assert build_lifecycle_facts(ordered) == expected
    assert all(item["graph_issues"] == issues for item in expected.values())
    if any(row["numero_dpe_remplace"] == "a" for row in rows):
        assert expected["a"]["replacement_state"] == "replaced"
    assert "missing" not in expected


def test_ambiguous_target_is_not_selected_and_known_successor_survives():
    rows = [
        record("a", identifiant_ban="A"),
        record("a", identifiant_ban="B"),
        record("b", "a"),
        record("c", "b"),
    ]
    expected = build_lifecycle_facts(rows)
    assert expected["a"]["replacement_state"] == "ambiguous"
    assert expected["b"]["replacement_state"] == "replaced"
    assert expected["c"]["replacement_state"] == "ambiguous"
    assert all(
        item["graph_issues"] == ["ambiguous_target"] for item in expected.values()
    )
    for ordered in permutations(rows):
        assert build_lifecycle_facts(ordered) == expected


@pytest.mark.parametrize(
    "size,truncated", [(999, False), (1000, False), (1001, True), (3000, True)]
)
def test_traversal_bound_is_explicit_for_entire_component(size, truncated):
    facts = build_lifecycle_facts(
        record(str(i), str(i - 1) if i else None) for i in range(size)
    )
    assert len(facts) == size
    assert all(
        item["graph_issues"] == (["truncated"] if truncated else [])
        for item in facts.values()
    )
    assert facts["0"]["replacement_state"] == "replaced"
    assert facts[str(size - 1)]["replacement_state"] == "no_successor_known"


def test_private_facts_preserve_only_canonical_invalid_date_flags():
    facts = build_lifecycle_facts(
        [
            record(
                "a",
                lifecycle_facts={
                    "replacement_state": "replaced",
                    "graph_issues": ["cycle"],
                    "date_issues": [
                        "invalid_end_date",
                        "invalid_establishment_date",
                        "invalid_end_date",
                    ],
                },
            )
        ]
    )["a"]
    assert facts == {
        "replacement_state": "no_successor_known",
        "graph_issues": [],
        "date_issues": ["invalid_establishment_date", "invalid_end_date"],
    }


@pytest.mark.parametrize(
    "established,end,issues,expected",
    [
        (None, None, (), "unknown"),
        (None, "2030-01-01", (), "unknown"),
        ("2020-01-01", None, (), "unknown"),
        ("2020-01-01", "2026-09-06", (), "expired"),
        ("2020-01-01", "2026-09-07", (), "not_expired_by_date"),
        ("2020-01-01", "2030-01-01", (), "not_expired_by_date"),
        ("2027-01-01", None, (), "future_establishment"),
        ("2027-01-01", "2028-01-01", (), "future_establishment"),
        ("2027-01-01", "2020-01-01", (), "inconsistent_dates"),
        (None, None, ("invalid_end_date",), "inconsistent_dates"),
        ("2027-01-01", None, ("invalid_end_date",), "inconsistent_dates"),
    ],
)
def test_validity_precedence_and_inclusive_end(established, end, issues, expected):
    assert (
        validity_at(
            date.fromisoformat(established) if established else None,
            date.fromisoformat(end) if end else None,
            date(2026, 9, 7),
            date_issues=issues,
        )
        == expected
    )


def test_replaced_and_expired_are_independent():
    assert (
        build_lifecycle_facts([record("a"), record("b", "a")])["a"]["replacement_state"]
        == "replaced"
    )
    assert (
        validity_at(date(2012, 1, 1), date(2022, 1, 1), date(2026, 9, 7)) == "expired"
    )


def test_ambiguous_edges_propagate_reserves_without_inventing_a_cycle():
    rows = [
        record("a", "b"),
        record("a", "c"),
        record("b", "c"),
        record("c", "missing"),
        record("unrelated"),
    ]
    facts = build_lifecycle_facts(rows)
    for number in "abc":
        assert facts[number]["graph_issues"] == ["missing_target", "ambiguous_target"]
    assert facts["c"]["replacement_state"] == "replaced"
    assert facts["unrelated"]["graph_issues"] == []


def test_empty_corpus():
    assert build_lifecycle_facts([]) == {}


# --- Equivalence with the code before plan 04.1-11 ---------------------------
# Facts below were recorded on 996ddbf (in-memory index, dictionary of results)
# and frozen in tests/fixtures/dpe_lifecycle_equivalence.json, in output order;
# they are never recomputed by the code under test.

EQUIVALENCE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "dpe_lifecycle_equivalence.json"
)
INVALID_END = {"date_issues": ["invalid_end_date"]}
INVALID_BOTH = {"date_issues": ["invalid_establishment_date", "invalid_end_date"]}


def _large_component():
    rows = [record(f"n{i}", f"n{i - 1}" if i else None) for i in range(1200)]
    rows += [record(f"leaf{i}", f"n{i * 7}") for i in range(30)]
    rows += [record(f"small{i}", f"small{i - 1}" if i else None) for i in range(5)]
    return rows


def _mixed():
    rows = [
        record("m-a"),
        record("m-b", "m-a", lifecycle_facts=INVALID_END),
        record("m-c", "m-b"),
        record("m-fork", "m-a"),
        record("m-cyc1", "m-cyc2"),
        record("m-cyc2", "m-cyc1", lifecycle_facts=INVALID_BOTH),
        record("m-dup", "m-cyc1"),
        record("m-dup", None, lifecycle_facts=INVALID_END),
        record("m-after-dup", "m-dup"),
        record("m-lost", "m-nowhere"),
        record("m-self", "m-self"),
        record("m-alone", is_tombstone=True),
    ]
    return [rows[(index * 5) % len(rows)] for index in range(len(rows))]


EQUIVALENCE_GRAPHS = {
    "simple_replacement": lambda: [
        record("a"),
        record("b", "a", lifecycle_facts=INVALID_END),
        record("c", "b"),
        record("d"),
    ],
    "fork": lambda: [record("a"), record("b", "a"), record("c", "a"), record("d", "c")],
    "cycle": lambda: [
        record("a", "b"),
        record("b", "c"),
        record("c", "a"),
        record("d", "a"),
        record("e", "e"),
    ],
    "ambiguous_duplicate": lambda: [
        record("a", "x"),
        record("a", None, lifecycle_facts=INVALID_BOTH),
        record("b", "a"),
        record("c", "b"),
        record("x"),
        record("y", "x"),
    ],
    "missing_reference": lambda: [
        record("a", "missing"),
        record("b", "a"),
        record("c", "ghost"),
        record("d"),
    ],
    # Unresolved edges read first must not turn a later resolved edge into a cycle.
    "ambiguous_edges_first": lambda: [
        record("dup", "x"),
        record("dup", "y"),
        record("x", "y"),
        record("y"),
    ],
    "large_component": _large_component,
    "mixed": _mixed,
}


def lifecycle_outputs():
    """Ordered (number, facts) per graph; the large one by digest and count."""
    outputs = {}
    for name, rows in EQUIVALENCE_GRAPHS.items():
        items = [
            [number, facts] for number, facts in build_lifecycle_facts(rows()).items()
        ]
        if len(items) > 100:
            encoded = json.dumps(items, separators=(",", ":")).encode("utf-8")
            items = {
                "count": len(items),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "first": items[0],
                "last": items[-1],
            }
        outputs[name] = items
    return outputs


def test_lifecycle_equals_the_frozen_pre_11_facts():
    expected = json.loads(EQUIVALENCE_FIXTURE.read_text(encoding="utf-8"))
    assert lifecycle_outputs() == expected
