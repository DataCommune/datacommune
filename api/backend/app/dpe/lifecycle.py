"""Pure global replacement facts; calendar validity is evaluated only at reading."""

import sqlite3
import tempfile
from array import array
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

GRAPH_ISSUES = ("cycle", "fork", "missing_target", "truncated", "ambiguous_target")
DATE_ISSUES = ("invalid_establishment_date", "invalid_end_date")
MAX_GRAPH_NODES = 1000
# Node flag beside the date issue bits: the number is duplicated (ambiguous).
AMBIGUOUS = 1 << len(DATE_ISSUES)
Validity = Literal[
    "unknown",
    "expired",
    "not_expired_by_date",
    "inconsistent_dates",
    "future_establishment",
]


class LifecycleFacts(TypedDict):
    replacement_state: Literal[
        "not_checked", "no_successor_known", "replaced", "ambiguous"
    ]
    graph_issues: list[str]
    date_issues: list[str]


class _Components:
    """Union by size avoids rewalking a chain for every document (near linear).

    Integer arrays, not lists of Python integers: a few bytes per document.
    """

    def __init__(self, count: int) -> None:
        self.parent = array("i", range(count))
        self.size = array("i", [1]) * count
        self.issues = array("b", [0]) * count

    def root(self, node: int) -> int:
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def join(self, left: int, right: int, *, resolved: bool) -> None:
        left, right = self.root(left), self.root(right)
        if left == right:
            if resolved:
                self.issues[left] |= 1  # A functional directed graph has a cycle.
            return
        if self.size[left] < self.size[right]:
            left, right = right, left
        self.parent[right] = left
        self.size[left] += self.size[right]
        self.issues[left] |= self.issues[right]


class LifecycleGraph:
    """Replacement graph whose number -> node mapping lives on a private file.

    Nodes are numbered in first-reading order; a node keeps one byte of flags
    (date issues, duplicate number) in memory, never its number. References
    are kept on disk too and resolved by a SQLite join, never by a dictionary
    of strings. The temporary directory is private and removed on close.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="dpe-lifecycle-", dir=directory
        )
        self._connection = sqlite3.connect(Path(self._temporary.name) / "graph.sqlite")
        # A disposable file rebuilt from the staged corpus: no journal needed.
        self._connection.execute("PRAGMA journal_mode = OFF")
        self._connection.execute("PRAGMA synchronous = OFF")
        self._connection.execute(
            "CREATE TABLE nodes (node INTEGER PRIMARY KEY, number TEXT NOT NULL UNIQUE)"
        )
        self._connection.execute(
            "CREATE TABLE refs (node INTEGER NOT NULL, target TEXT NOT NULL)"
        )
        self._flags = array("b")
        self._indexed = False

    def __enter__(self) -> "LifecycleGraph":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()
        self._temporary.cleanup()

    def index(self, records: Iterable[Mapping[str, Any]]) -> None:
        """Read the entire staged corpus, including tombstones, exactly once."""
        if self._indexed:
            raise ValueError("DPE lifecycle graph already indexed")
        connection, flags = self._connection, self._flags
        for row in records:
            number, target = row.get("numero_dpe"), row.get("numero_dpe_remplace")
            if not isinstance(number, str) or not number or number != number.strip():
                raise ValueError("Invalid DPE graph identity")
            if target is not None and (
                not isinstance(target, str) or not target or target != target.strip()
            ):
                raise ValueError("Invalid DPE graph reference")
            issues = row.get("lifecycle_facts", {}).get("date_issues", ())
            date_mask = sum(
                1 << i for i, issue in enumerate(DATE_ISSUES) if issue in issues
            )
            node = len(flags)
            if connection.execute(
                "INSERT OR IGNORE INTO nodes VALUES (?, ?)", (node, number)
            ).rowcount:
                flags.append(date_mask)
            else:
                (node,) = connection.execute(
                    "SELECT node FROM nodes WHERE number = ?", (number,)
                ).fetchone()
                flags[node] |= date_mask | AMBIGUOUS
            if target is not None:
                connection.execute("INSERT INTO refs VALUES (?, ?)", (node, target))
        connection.commit()
        self._indexed = True

    def _components(self) -> tuple[_Components, array]:
        flags = self._flags
        components = _Components(len(flags))
        successors = array("i", [0]) * len(flags)
        # Edges between unique documents first: only they prove a cycle.
        for node, predecessor in self._connection.execute(
            "SELECT refs.node, nodes.node FROM refs "
            "JOIN nodes ON nodes.number = refs.target"
        ):
            if not (flags[node] | flags[predecessor]) & AMBIGUOUS:
                successors[predecessor] += 1
                components.join(node, predecessor, resolved=True)
        for node, predecessor in self._connection.execute(
            "SELECT refs.node, nodes.node FROM refs "
            "LEFT JOIN nodes ON nodes.number = refs.target"
        ):
            if predecessor is None:
                components.issues[components.root(node)] |= 4
            elif (flags[node] | flags[predecessor]) & AMBIGUOUS:
                components.join(node, predecessor, resolved=False)
        for node, node_flags in enumerate(flags):
            root = components.root(node)
            if node_flags & AMBIGUOUS:
                components.issues[root] |= 16
            if successors[node] > 1:
                components.issues[root] |= 2
        return components, successors

    def facts(self) -> Iterator[tuple[str, LifecycleFacts]]:
        """Yield (number, facts) once per number, in first-reading order."""
        if not self._indexed:
            raise ValueError("DPE lifecycle graph is not indexed")
        components, successors = self._components()
        for node, number in self._connection.execute(
            "SELECT node, number FROM nodes ORDER BY node"
        ):
            root = components.root(node)
            mask = components.issues[root]
            if components.size[root] > MAX_GRAPH_NODES:
                mask |= 8
            yield (
                number,
                LifecycleFacts(
                    replacement_state=(
                        "replaced"
                        if successors[node]
                        else "ambiguous"
                        if mask & 16
                        else "no_successor_known"
                    ),
                    graph_issues=[
                        issue for i, issue in enumerate(GRAPH_ISSUES) if mask & (1 << i)
                    ],
                    date_issues=[
                        issue
                        for i, issue in enumerate(DATE_ISSUES)
                        if self._flags[node] & (1 << i)
                    ],
                ),
            )


def iter_lifecycle_facts(
    records: Iterable[Mapping[str, Any]], *, directory: Path | None = None
) -> Iterator[tuple[str, LifecycleFacts]]:
    """Global replacement facts of the entire staged corpus, as a stream.

    Results are keyed by private number. Any duplicate number is defensively
    ambiguous; no duplicate row wins. Only edges between unique documents prove
    replacement/cycles/forks. Possible ambiguous edges connect reserves only.
    Issues propagate in both directions through the whole known component;
    missing numbers never become documents or connect unrelated components.

    The 1,000-distinct-node traversal budget is shared per component: union/find
    aggregates edges without document-by-document traversal and flags every
    component exceeding that budget as truncated. Known direct replacement and
    observed issues survive that reserve. Time is O((V+E) alpha(V)); memory
    holds integer arrays of a few bytes per document, the number -> node
    mapping and the references staying in a private temporary SQLite file
    (plan 04.1-11). No payload is retained, no network or PostgreSQL access.
    """
    with LifecycleGraph(directory) as graph:
        graph.index(records)
        yield from graph.facts()


def validity_at(
    established_at: date | None,
    end_at: date | None,
    as_of_date: date,
    *,
    date_issues: Iterable[str] = (),
) -> Validity:
    """Use explicit source dates, inclusive end date, and the caller's one date."""
    if any(date_issues) or (
        established_at is not None and end_at is not None and end_at < established_at
    ):
        return "inconsistent_dates"
    if established_at is not None and established_at > as_of_date:
        return "future_establishment"
    if established_at is None or end_at is None:
        return "unknown"
    return "expired" if end_at < as_of_date else "not_expired_by_date"


# Label served before the national scope. The current front end (frozen during
# phase 4.1, its refonte pending) accepts only this value: a lifecycle founded
# on 44 or 85 keeps it, which stays true (their complete downloads lie within
# 44/85); any other departement is named by its code. Retired once the front
# end reads codes (04.1-REPORTS-UI.md, plan 08).
LEGACY_SOURCE_SCOPE = "44/85"
LEGACY_SOURCE_SCOPE_DEPARTEMENTS = frozenset({"44", "85"})


def source_scope(observations: Iterable[Mapping[str, Any]], source_state: str) -> str:
    """Departement whose complete download founds *source_state*.

    *observations* are already validated (``_previous_observations``). The
    founding observations carry the published state (all observations when
    none does); the earliest one names the departement. Two departements
    observed at that same earliest instant: the scope is unproven and nothing
    is published (``ValueError``), never a guessed departement.

    A row without any observation predates them: migration 091 added them
    "initially unknown", only a complete staged import writes them, and every
    import before it was 44/85 (instance scope, then the 091 constraint). Such
    a row keeps the 44/85 label it was served under.
    """
    entries = [
        (_instant(entry["observed_at"]), entry["department"], entry["state"])
        for entry in observations
    ]
    if not entries:
        return LEGACY_SOURCE_SCOPE
    founding = [
        (observed, code) for observed, code, state in entries if state == source_state
    ] or [(observed, code) for observed, code, _ in entries]
    earliest = min(observed for observed, _ in founding)
    departements = {code for observed, code in founding if observed == earliest}
    if len(departements) != 1:
        raise ValueError("ambiguous source scope")
    (departement,) = departements
    if departement in LEGACY_SOURCE_SCOPE_DEPARTEMENTS:
        return LEGACY_SOURCE_SCOPE
    return departement


def _instant(value: str | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)
