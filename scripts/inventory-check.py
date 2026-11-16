#!/usr/bin/env python3
"""Consistency check between docs/INVENTORY-3394395a.md and the working tree.

The inventory is a decision, not a description: every row says `garder`,
`retirer` or `adapter`. This script proves the tree agrees with the decision.

Row format (five columns, exactly as written in the inventory):

    | <chemin>[#<ancre>] | <type> | <décision> | <motif> | <sha> |

Rules applied:

* `garder` / `adapter` without anchor -> the path MUST exist.
* `garder` / `adapter` with an anchor -> the file MUST exist AND contain the
  anchor string (a partial keep: symbol, key or route still in the file).
* `retirer` without anchor -> the path MUST NOT exist.
* `retirer` with an anchor -> the file MUST exist AND NOT contain the anchor
  string (a partial removal: only the named symbol went away).
* `privé` (no anchor) -> kept in the private working repository only (D-140 of
  docs/DECISIONS.md). The private repository is the public tree plus private
  paths, `.planning/` first: where `.planning/` exists the path MUST exist,
  elsewhere (a public export) it MUST NOT exist.

Structural floors: at least one row per decision, and at least 27 rows of
type `dataset` (the source catalog is the V1 perimeter, DOC-01).

Exit 0 and print `INVENTORY ok: ...` when everything agrees; otherwise list
every discrepancy and exit 1. stdlib only, Python 3.12.
"""

from __future__ import annotations

import sys
from pathlib import Path

DECISIONS = ("garder", "retirer", "adapter")
PRIVATE = "privé"
MIN_DATASET_ROWS = 27


def repo_root(start: Path) -> Path:
    """Nearest ancestor holding a `.git` entry, else the inventory's grandparent."""
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start.parent.parent


def split_row(line: str) -> list[str] | None:
    """Return the cells of a markdown table row, or None if it is not one."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def clean_path_cell(cell: str) -> tuple[str, str | None]:
    """Strip markdown emphasis from the path cell and split off the anchor."""
    value = cell.strip().strip("`").strip()
    if "#" in value:
        path_part, _, anchor = value.partition("#")
        return path_part.strip().strip("`"), anchor.strip().strip("`")
    return value, None


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: inventory-check.py <chemin de l'inventaire>", file=sys.stderr)
        return 2

    inventory = Path(argv[1])
    if not inventory.is_file():
        print(f"INVENTORY fail: fichier introuvable: {inventory}", file=sys.stderr)
        return 1

    root = repo_root(inventory.resolve())
    counts = dict.fromkeys((*DECISIONS, PRIVATE), 0)
    private_checkout = (root / ".planning").is_dir()
    type_counts: dict[str, int] = {}
    problems: list[str] = []
    rows = 0

    for lineno, line in enumerate(read_text(inventory).splitlines(), start=1):
        cells = split_row(line)
        if cells is None or len(cells) != 5:
            continue
        path_cell, type_cell, decision, _motif, sha = cells
        if decision not in counts:
            continue  # header, separator, or a table that is not the inventory

        rows += 1
        counts[decision] += 1
        type_counts[type_cell] = type_counts.get(type_cell, 0) + 1

        if len(sha.strip("`")) < 7:
            problems.append(f"ligne {lineno}: SHA trop court ou absent ({sha!r}) — {path_cell}")

        rel, anchor = clean_path_cell(path_cell)
        if not rel:
            problems.append(f"ligne {lineno}: chemin vide")
            continue
        target = root / rel
        exists = target.exists()

        if decision == PRIVATE:
            if anchor is not None:
                problems.append(f"ligne {lineno}: `privé` ne prend pas d'ancre — {rel}")
            elif private_checkout and not exists:
                problems.append(f"ligne {lineno}: `privé` mais absent du dépôt de travail — {rel}")
            elif not private_checkout and exists:
                problems.append(f"ligne {lineno}: `privé` mais présent dans l'arbre public — {rel}")
            continue

        if anchor is None:
            if decision in ("garder", "adapter") and not exists:
                problems.append(f"ligne {lineno}: `{decision}` mais absent du dépôt — {rel}")
            elif decision == "retirer" and exists:
                problems.append(f"ligne {lineno}: `retirer` mais toujours présent — {rel}")
            continue

        if not target.is_file():
            problems.append(
                f"ligne {lineno}: ancre `{anchor}` sur un fichier introuvable — {rel}"
            )
            continue
        present = anchor in read_text(target)
        if decision in ("garder", "adapter") and not present:
            problems.append(f"ligne {lineno}: ancre `{anchor}` absente de {rel}")
        elif decision == "retirer" and present:
            problems.append(f"ligne {lineno}: ancre `{anchor}` encore présente dans {rel}")

    for decision in DECISIONS:
        if counts[decision] == 0:
            problems.append(f"structure: aucune ligne `{decision}`")
    datasets = type_counts.get("dataset", 0)
    if datasets < MIN_DATASET_ROWS:
        problems.append(
            f"structure: {datasets} ligne(s) de type `dataset`, {MIN_DATASET_ROWS} attendues au minimum"
        )
    if rows == 0:
        problems.append("structure: aucune ligne de décision trouvée")

    if problems:
        print(f"INVENTORY fail: {len(problems)} écart(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        f"INVENTORY ok: {rows} rows "
        f"(garder={counts['garder']} retirer={counts['retirer']} adapter={counts['adapter']} "
        f"privé={counts[PRIVATE]}) "
        f"dataset={datasets}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
