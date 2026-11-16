"""Shared retry classification; exception objects never become public payloads."""

import httpx
import psycopg

TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    ConnectionResetError,
    psycopg.OperationalError,
)


def transient_cause(error: BaseException | None) -> BaseException | None:
    """Find an eligible typed cause, without interpreting exception messages."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, TRANSIENT_ERRORS):
            return error
        error = error.__cause__
    return None


class EmptyScopeError(RuntimeError):
    """The instance scope hosts no departement: nothing may be imported."""

    def __init__(self) -> None:
        super().__init__(
            "Instance scope has no departement: nothing is hosted, no import "
            "runs. Write the scope (instance_scope_departements) first."
        )


class DatasetNotHostedError(RuntimeError):
    """The dataset is outside the instance scope: neither downloaded nor imported."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(
            f"Dataset '{key}' is not in the instance scope "
            "(instance_scope_datasets): not downloaded, not imported."
        )


class ScopePrerequisiteError(RuntimeError):
    """A reference the scope filter needs is missing: refused before download."""


class SourceWithoutCoverageError(ScopePrerequisiteError):
    """No source of the pipeline publishes a hosted departement (D-24)."""

    def __init__(self, dataset: str) -> None:
        self.dataset = dataset
        super().__init__(
            f"source sans couverture sur le périmètre: {dataset} (the catalogue "
            "source_coverage publishes none of the hosted departements): not "
            "downloaded, not imported."
        )


class ScopeLockError(ScopePrerequisiteError):
    """Configured departements exceed a schema lock: refused before download."""

    def __init__(
        self, key: str, outside: list[str], allowed: tuple[str, ...], origin: str
    ):
        self.key = key
        self.outside = outside
        super().__init__(
            f"Dataset '{key}' cannot host departements {outside}: its schema "
            f"only accepts {list(allowed)} ({origin}). Narrow the scope or "
            "extend the lock by a new migration."
        )


class MillesimeNotTargetableError(ValueError):
    """The pipeline cannot ask its producer for another vintage (plan 02-11)."""

    def __init__(self, dataset: str) -> None:
        self.dataset = dataset
        super().__init__(f"millésime non ciblable: {dataset}")


class MalformedMillesimeError(ValueError):
    """A vintage label outside the producer's format: refused before download."""

    def __init__(self, dataset: str, label: object, expected: str) -> None:
        self.dataset = dataset
        super().__init__(
            f"millésime mal formé: {dataset} {label!r} (attendu {expected})"
        )


class FatalInputError(RuntimeError):
    """An input failure that ends the import at once (D-122).

    The runner never skips the file as "one failed file among others": the
    run fails before publication, whatever the success ratio.
    """


class BdTopoDepartementMissingError(FatalInputError):
    """A hosted departement the BD TOPO producer did not deliver (D-122).

    404 or any refused download, empty archive, listing or extraction in
    failure: the import fails franchement, never a warning (ROADMAP 4.1,
    criterion 4).
    """

    def __init__(self, departement: str, reason: str) -> None:
        self.departement = departement
        self.reason = reason
        super().__init__(
            f"BD TOPO: département {departement} du périmètre non livré "
            f"ou inexploitable ({reason}) : import en échec, rien n'est publié"
        )


class StagingNotLoggedError(RuntimeError):
    """A candidate (or one of its partitions) is unlogged: it is never published.

    The swap publishes the candidate's own storage files, without rewriting
    them (plan 04.1-04): an unlogged relation would be emptied by the first
    crash after publication, so the candidate must be created logged.
    """

    def __init__(self, table: str, relations: list[str]) -> None:
        self.table = table
        self.relations = tuple(relations)
        super().__init__(
            f"Candidate {table} is not logged ({', '.join(relations)}): "
            "create it logged; the swap no longer converts it."
        )
