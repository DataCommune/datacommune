"""Fill the empty source period labels of DVF and connectivite (C24, WINDOWS n° 86).

Without any reimport, schema change or file written, from what is already
stored or recorded:

- ``dvf``: the window of the mutation dates of the published ``mutations``
  table, the same rule as the import (:func:`app.etl_dvf.pipeline.window_label`);
- ``connectivite``: the quarter whose producer files have, **each one**, the
  SHA-256 and size journaled by the last successful run
  (``etl_runs.source_files``). Candidates: the quarter ``last`` redirects to,
  then the previous ones. The files are streamed and hashed, never stored.

Only an empty label is written; a present label is left as it is (equal:
nothing to do; different: refused, never overwritten). A label that cannot be
proved is not written: no period is invented (C14).

    python -m scripts.backfill_source_periods            # read, write nothing
    python -m scripts.backfill_source_periods --apply    # write the empty labels

Exit codes: 0 every label is established; 2 usage; 3 a label could not be
proved (nothing written for it); 4 a present label differs from the derived one.
Idempotent: a second ``--apply`` writes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx
import psycopg

from app.etl.base import BasePipeline
from app.etl_arcep.config import DOWNLOAD_TIMEOUT, arcep_urls, served_millesime
from app.etl_dvf.pipeline import DATE_WINDOW_SQL, window_label

EXIT_OK, EXIT_USAGE, EXIT_UNPROVED, EXIT_DIFFERS = 0, 2, 3, 4

_ARCEP_FILE = re.compile(r"^(actuel|base_imb)_([0-9]{2,3}|2A|2B)\.csv\.gz$")
_QUARTER = re.compile(r"^([0-9]{4})_T([1-4])$")
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Outcome:
    dataset: str
    before: str | None
    derived: str | None
    action: str  # filled | to_fill | unchanged | differs | unproved
    proof: str


def decide(before: str | None, derived: str | None) -> str:
    """What to do with a stored label: never overwrite, never invent."""
    if before:
        if derived is not None and before != derived:
            return "differs"
        return "unchanged"
    return "to_fill" if derived is not None else "unproved"


def previous_quarters(quarter: str, count: int) -> list[str]:
    """*quarter* then the *count* - 1 quarters before it, newest first."""
    match = _QUARTER.match(quarter)
    if match is None:
        return []
    year, number = int(match[1]), int(match[2])
    quarters = []
    for _ in range(count):
        quarters.append(f"{year}_T{number}")
        number -= 1
        if number == 0:
            year, number = year - 1, 4
    return quarters


def _file_url(name: str, quarter: str | None) -> str | None:
    match = _ARCEP_FILE.match(name)
    if match is None:
        return None
    base_imb, eligibilite = arcep_urls(match[2], quarter)
    return base_imb if match[1] == "base_imb" else eligibilite


def quarter_behind_last(client: httpx.Client, files: Sequence[dict]) -> str | None:
    """The quarter the producer's ``last`` folder redirects to today."""
    url = _file_url(str(files[0].get("name", "")), None) if files else None
    if url is None:
        return None
    response = client.head(url, follow_redirects=False)
    location = response.headers.get("location")
    return served_millesime([location]) if location else None


def _same_file(client: httpx.Client, url: str, recorded: dict) -> bool:
    digest, size = hashlib.sha256(), 0
    with client.stream("GET", url, follow_redirects=True) as response:
        if response.status_code != 200:
            return False
        for chunk in response.iter_bytes(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return size == recorded.get("bytes") and digest.hexdigest() == recorded.get(
        "sha256"
    )


def prove_quarter(
    client: httpx.Client, files: Sequence[dict], candidates: Sequence[str]
) -> tuple[str | None, str]:
    """The first candidate quarter serving every recorded file byte for byte."""
    if not files:
        return None, "aucun fichier journalise"
    tried = []
    for quarter in candidates:
        matched = 0
        for recorded in files:
            url = _file_url(str(recorded.get("name", "")), quarter)
            if url is None:
                return None, "nom de fichier inattendu dans etl_runs.source_files"
            if not _same_file(client, url, recorded):
                break
            matched += 1
        if matched == len(files):
            return quarter, f"sha256 {matched}/{len(files)} au dossier {quarter}"
        tried.append(f"{quarter}:{matched}/{len(files)}")
    return None, "aucun trimestre ne sert tous les fichiers (" + ", ".join(tried) + ")"


def _label(conn: psycopg.Connection, dataset: str) -> str | None:
    row = conn.execute(
        "SELECT source_period_label FROM dataset_metadata WHERE dataset_name = %s",
        (dataset,),
    ).fetchone()
    return row[0] if row else None


def dvf_outcome(conn: psycopg.Connection) -> Outcome:
    before = _label(conn, "dvf")
    first, last = conn.execute(DATE_WINDOW_SQL.format(table="mutations")).fetchone()
    derived = window_label(first, last)
    return Outcome(
        "dvf", before, derived, decide(before, derived), "mutations.date_mutation"
    )


def connectivite_outcome(
    conn: psycopg.Connection,
    client_factory: Callable[[], httpx.Client],
    quarters_back: int,
) -> Outcome:
    before = _label(conn, "connectivite")
    if before:
        return Outcome("connectivite", before, None, "unchanged", "libelle present")
    row = conn.execute(
        "SELECT id, source_files FROM etl_runs WHERE dataset_name = 'connectivite' "
        "AND status = 'success' ORDER BY completed_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return Outcome("connectivite", before, None, "unproved", "aucun run reussi")
    run_id, files = row[0], list(row[1] or [])
    with client_factory() as client:
        newest = quarter_behind_last(client, files)
        if newest is None:
            return Outcome(
                "connectivite",
                before,
                None,
                "unproved",
                "redirection de last illisible",
            )
        quarter, proof = prove_quarter(
            client, files, previous_quarters(newest, quarters_back)
        )
    return Outcome(
        "connectivite",
        before,
        quarter,
        decide(before, quarter),
        f"run {run_id}, {proof}",
    )


def apply(conn: psycopg.Connection, outcomes: Sequence[Outcome]) -> list[Outcome]:
    """Write the labels to fill, guarded on an empty label; one transaction."""
    done = []
    with conn.transaction():
        for outcome in outcomes:
            if outcome.action != "to_fill":
                done.append(outcome)
                continue
            written = conn.execute(
                "UPDATE dataset_metadata SET source_period_label = %s "
                "WHERE dataset_name = %s "
                "AND coalesce(source_period_label, '') = ''",
                (outcome.derived, outcome.dataset),
            ).rowcount
            action = "filled" if written == 1 else "unproved"
            done.append(
                Outcome(
                    outcome.dataset,
                    outcome.before,
                    outcome.derived,
                    action,
                    outcome.proof,
                )
            )
    return done


def exit_code(outcomes: Sequence[Outcome]) -> int:
    actions = {o.action for o in outcomes}
    if "differs" in actions:
        return EXIT_DIFFERS
    if "unproved" in actions:
        return EXIT_UNPROVED
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    connect: Callable[[str], psycopg.Connection] | None = None,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="write the empty labels")
    parser.add_argument(
        "--quarters-back",
        type=int,
        default=4,
        help="connectivite: quarters tried from the one behind last (default 4)",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    if args.quarters_back < 1:
        print("--quarters-back doit valoir au moins 1", file=sys.stderr)
        return EXIT_USAGE

    factory = client_factory or (lambda: httpx.Client(timeout=DOWNLOAD_TIMEOUT))
    with (connect or psycopg.connect)(BasePipeline.get_sync_dsn()) as conn:
        outcomes = [
            dvf_outcome(conn),
            connectivite_outcome(conn, factory, args.quarters_back),
        ]
        if args.apply:
            outcomes = apply(conn, outcomes)
    for o in outcomes:
        print(
            f"BACKFILL {o.dataset} avant={o.before or 'NULL'} "
            f"derive={o.derived or '-'} action={o.action} preuve={o.proof}"
        )
    code = exit_code(outcomes)
    print(f"BACKFILL_{'APPLY' if args.apply else 'READ'} exit={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
